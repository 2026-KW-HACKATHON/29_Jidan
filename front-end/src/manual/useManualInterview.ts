import {useEffect,useRef,useState} from 'react'
import {errorMessage,ManualError,type ManualService} from './service'
import type {ManualInterviewSession,ManualIntentReview,ManualIntentReviewList,ManualInterviewTurn} from './types'
import {createVoiceSubmission,type Recording} from './voice'
import {pause,waitVisible} from './poll'
import {useManualTask} from './useManualTask'
import type {PhotoTarget} from './photos'

export function useManualInterview({service,initial,onDraft}:{service:ManualService;initial:ManualInterviewSession;onFinal?:(session:ManualInterviewSession)=>void;onDraft?:(versionId:string)=>void}){
 const [session,setSession]=useState(initial),[reviews,setReviews]=useState<ManualIntentReviewList|null>(null),[selected,setSelected]=useState<string|null>(null),[correcting,setCorrecting]=useState(false),[pollError,setPollError]=useState(''),[reviewError,setReviewError]=useState(''),[turns,setTurns]=useState<ManualInterviewTurn[]>([]),[showHistory,setShowHistory]=useState(false),[refresh,setRefresh]=useState(0)
 const [photoTarget,setPhotoTarget]=useState<PhotoTarget|null>(null)
 const task=useManualTask(),historyTask=useManualTask(),voice=useRef<{recording:Recording;submission:ReturnType<typeof createVoiceSubmission>;revision:number;questionId?:string;intentId?:string}|null>(null),current=useRef(session)
 useEffect(()=>{current.current=session;if(['GENERATING','COMPLETED'].includes(session.phase))onDraft?.(session.draftVersionId)},[session,onDraft])
 useEffect(()=>{const controller=new AbortController(),signal=controller.signal
  void (async()=>{while(!signal.aborted){await waitVisible(signal);const results=await Promise.allSettled([service.call('getManualInterview',{sessionId:initial.id},undefined,{signal}),service.call('listManualIntentReviews',{sessionId:initial.id},undefined,{signal})]);if(signal.aborted)return
   const s=results[0],r=results[1];let delay=2000
   if(s.status==='fulfilled'){if(s.value.data.id!==initial.id)throw new ManualError('INVALID_RESPONSE');setSession(old=>s.value.data.revision>=old.revision?s.value.data:old);setPollError('');delay=s.value.retryAfterMs}else setPollError(errorMessage(s.reason))
   if(r.status==='fulfilled'){if(r.value.data.sessionId!==initial.id)throw new ManualError('INVALID_RESPONSE');setReviews(old=>({ ...r.value.data,items:r.value.data.items.map(item=>{const newer=old?.items.find(i=>i.intentId===item.intentId);return newer&&newer.revision>item.revision?newer:item})}));setReviewError('');delay=Math.max(delay,r.value.retryAfterMs)}else setReviewError(errorMessage(r.reason))
   await pause(delay,signal)
  }})().catch(e=>{if(!signal.aborted)setPollError(errorMessage(e))})
  return()=>controller.abort()
 },[service,initial.id,refresh])
 const review=reviews?.items.find(r=>r.intentId===selected),intent=session.intents.find(i=>i.id===(review?.intentId??session.currentIntentId)),stage=['WORK_STRUCTURE','COMMON_TASKS','SHIFT_TASKS','COMPLEMENTS'].indexOf(intent?.stage??'')
 function updateReview(updated:ManualIntentReview){setReviews(old=>old?{...old,items:old.items.map(r=>r.intentId===updated.intentId&&r.revision<=updated.revision?updated:r)}:old)}
 async function submit(recording:Recording,signal:AbortSignal){
  if(voice.current?.recording!==recording){voice.current={recording,submission:createVoiceSubmission(service,recording),revision:review?review.revision:session.revision,questionId:review?undefined:session.questions[0]?.id,intentId:review?.intentId}}
  const captured=voice.current;const input=await captured.submission.input(signal);signal.throwIfAborted()
  if(captured.intentId){const result=await service.call('correctManualInterviewUnderstanding',{sessionId:session.id,intentId:captured.intentId},{expectedRevision:captured.revision,input},{signal,key:captured.submission.key});if(!signal.aborted){updateReview(result.data);setCorrecting(false)}}
  else {if(!captured.questionId)throw new ManualError('INTERVIEW_STATE_CONFLICT');const result=await service.call('answerManualInterviewQuestion',{sessionId:session.id},{expectedRevision:captured.revision,questionId:captured.questionId,input},{signal,key:captured.submission.key});if(!signal.aborted)setSession(old=>result.data.revision>=old.revision?result.data:old)}
  voice.current=null
 }
 function reviewCommand(kind:'confirmManualInterviewUnderstanding'|'retryManualIntentReview'){if(!review)return;const captured=review;void task.run(async(signal,key)=>{const result=kind==='confirmManualInterviewUnderstanding'?await service.call(kind,{sessionId:session.id,intentId:captured.intentId},{expectedRevision:captured.revision,confirmed:true},{signal,key}):await service.call(kind,{sessionId:session.id,intentId:captured.intentId},{expectedRevision:captured.revision},{signal,key});if(signal.aborted)return;updateReview(result.data);if(kind==='confirmManualInterviewUnderstanding')setSelected(null)})}
 async function loadHistory(){void historyTask.run(async(signal)=>{let page=0,total=1;const items=new Map<string,ManualInterviewTurn>();while(page<total){const result=await service.call('getManualInterviewTurns',{sessionId:session.id,page:String(page)},undefined,{signal});total=result.data.totalPages;result.data.items.forEach(turn=>items.set(turn.id,turn));page++}if(!signal.aborted){setTurns([...items.values()].sort((a,b)=>a.sequence-b.sequence));setShowHistory(true)}})}
 const ready=session.phase==='READY_TO_GENERATE'&&reviews?.items.length===session.intents.length&&reviews.items.every(r=>r.status==='READY')
 return {session,reviews,selected,setSelected,correcting,setCorrecting,pollError,reviewError,turns,showHistory,setRefresh,photoTarget,setPhotoTarget,task,historyTask,current,review,stage,updateReview,submit,reviewCommand,loadHistory,ready,setSession}
}
