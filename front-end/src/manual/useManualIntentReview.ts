import {useRef,useState} from 'react'
import {createVoiceSubmission,type Recording} from './voice'
import {ManualError,type ManualService} from './service'
import {useManualTask} from './useManualTask'
import type {ManualIntentReview} from './types'
/** Review revision is independent of the interview session revision. */
export function useManualIntentReview(service:ManualService,sessionId:string,review:ManualIntentReview|undefined,onUpdate:(review:ManualIntentReview)=>void) {
 const [correcting,setCorrecting]=useState(false)
 const task=useManualTask()
 const voice=useRef<{recording:Recording;submission:ReturnType<typeof createVoiceSubmission>;review:ManualIntentReview}|null>(null)
 async function submit(recording:Recording,signal:AbortSignal) {
  if(!review)throw new ManualError('MANUAL_STATE_CONFLICT')
  if(voice.current?.recording!==recording)voice.current={recording,submission:createVoiceSubmission(service,recording),review}
  const captured=voice.current,input=await captured.submission.input(signal);signal.throwIfAborted()
  const result=await service.call('correctManualInterviewUnderstanding',{sessionId,intentId:captured.review.intentId},{expectedRevision:captured.review.revision,input},{signal,key:captured.submission.key})
  signal.throwIfAborted();if(result.data.intentId!==captured.review.intentId)throw new ManualError('INVALID_RESPONSE')
  onUpdate(result.data);voice.current=null;setCorrecting(false)
 }
 function command(kind:'confirmManualInterviewUnderstanding'|'retryManualIntentReview') {
  if(!review)return
  const captured=review
  void task.run(async(signal,key)=>{
   const params={sessionId,intentId:captured.intentId}
   const result=kind==='confirmManualInterviewUnderstanding'?await service.call(kind,params,{expectedRevision:captured.revision,confirmed:true},{signal,key}):await service.call(kind,params,{expectedRevision:captured.revision},{signal,key})
   signal.throwIfAborted();if(result.data.intentId!==captured.intentId)throw new ManualError('INVALID_RESPONSE')
   onUpdate(result.data);setCorrecting(false)
  })
 }
 return {correcting,setCorrecting,submit,command,task}
}
