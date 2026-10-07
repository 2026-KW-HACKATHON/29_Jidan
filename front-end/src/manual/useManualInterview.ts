import {useEffect,useRef,useState} from 'react'
import {errorMessage,ManualError,type ManualService} from './service'
import type {ManualInterviewSession,ManualIntentReview,ManualIntentReviewList,ManualInterviewQuestion} from './types'
import {createVoiceSubmission,type Recording} from './voice'
import {pause,waitVisible} from './poll'
import {useManualTask} from './useManualTask'

export function useManualInterview({service,initial,onDraft}:{service:ManualService;initial:ManualInterviewSession;onDraft?:(versionId:string)=>void}) {
 const [snapshot,setSnapshot]=useState<{session:ManualInterviewSession;latestQuestion:ManualInterviewQuestion|null}>({session:initial,latestQuestion:initial.questions[0]??initial.lastAnsweredQuestion??null})
 const {session,latestQuestion}=snapshot
 function applySession(updated:ManualInterviewSession){setSnapshot(old=>updated.revision>=old.session.revision?{session:updated,latestQuestion:updated.questions[0]??old.latestQuestion}:old)}
 const [reviews,setReviews]=useState<ManualIntentReviewList|null>(null),[readError,setReadError]=useState(''),[refresh,setRefresh]=useState(0)
 const task=useManualTask()
 const voice=useRef<{recording:Recording;submission:ReturnType<typeof createVoiceSubmission>;revision:number;questionId:string}|null>(null)
 useEffect(()=>{if(['GENERATING','COMPLETED'].includes(session.phase))onDraft?.(session.draftVersionId)},[session,onDraft])
 useEffect(()=>{
  const controller=new AbortController(),signal=controller.signal
  void (async()=>{while(!signal.aborted){
   await waitVisible(signal)
   const [s,r]=await Promise.all([service.call('getManualInterview',{sessionId:initial.id},undefined,{signal}),service.call('listManualIntentReviews',{sessionId:initial.id},undefined,{signal})])
   signal.throwIfAborted()
   if(s.data.id!==initial.id||r.data.sessionId!==initial.id)throw new ManualError('INVALID_RESPONSE')
   applySession(s.data)
   setReviews(old=>old&&old.sessionRevision>r.data.sessionRevision?old:{...r.data,items:r.data.items.map(item=>{const previous=old?.items.find(i=>i.intentId===item.intentId);return previous&&previous.revision>item.revision?previous:item})})
   setReadError('');await pause(Math.max(s.retryAfterMs,r.retryAfterMs),signal)
  }})().catch(e=>{if(!signal.aborted)setReadError(errorMessage(e))})
  return()=>controller.abort()
 },[service,initial.id,refresh])
 function updateReview(updated:ManualIntentReview) {setReviews(old=>old?{...old,items:[...old.items.filter(r=>r.intentId!==updated.intentId),old.items.find(r=>r.intentId===updated.intentId&&r.revision>updated.revision)??updated]}:old)}
 async function submit(recording:Recording,signal:AbortSignal) {
  if(voice.current?.recording!==recording){const question=session.questions[0];if(!question||session.phase!=='COLLECTING')throw new ManualError('INTERVIEW_STATE_CONFLICT');voice.current={recording,submission:createVoiceSubmission(service,recording),revision:session.revision,questionId:question.id}}
  const captured=voice.current,input=await captured.submission.input(signal);signal.throwIfAborted()
  const result=await service.call('answerManualInterviewQuestion',{sessionId:session.id},{expectedRevision:captured.revision,questionId:captured.questionId,input},{signal,key:captured.submission.key})
  signal.throwIfAborted();if(result.data.id!==session.id)throw new ManualError('INVALID_RESPONSE')
  applySession(result.data);voice.current=null
 }
 function retryProcessing(){const captured=session;void task.run(async(signal,key)=>{const result=await service.call('retryManualInterviewProcessing',{sessionId:captured.id},{expectedRevision:captured.revision},{signal,key});signal.throwIfAborted();applySession(result.data)})}
 // Display completed intents in server order before exposing the next question.
 const pendingIntent=session.intents.find(intent=>intent.finishedAt&&!reviews?.items.find(r=>r.intentId===intent.id&&r.confirmedAt&&r.status==='READY'))
 const review=reviews?.items.find(r=>r.intentId===pendingIntent?.id)
 const waitingForReview=!!pendingIntent&&!review
 const question=session.questions[0]??session.lastAnsweredQuestion??((session.phase==='PROCESSING'||session.phase==='ERROR')&&session.processing?.kind==='EVALUATION'?latestQuestion:null)
 const activeIntent=pendingIntent??session.intents.find(i=>i.id===session.currentIntentId)
 const stage=['WORK_STRUCTURE','COMMON_TASKS','SHIFT_TASKS','COMPLEMENTS'].indexOf(activeIntent?.stage??'')
 const ready=session.phase==='READY_TO_GENERATE'&&!pendingIntent&&reviews?.items.length===session.intents.length&&reviews.items.every(r=>r.status==='READY'&&r.confirmedAt)
 return {session,reviews,review,waitingForReview,activeIntent,stage,question,ready,submit,updateReview,task,readError,reload:()=>{setReadError('');setRefresh(v=>v+1)},retryProcessing}
}
