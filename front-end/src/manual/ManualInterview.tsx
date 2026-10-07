import {useState} from 'react'
import {Button} from '../ui/Button'
import {ManualFrame} from './ManualFrame'
import {ManualVoiceComposer} from './ManualVoiceComposer'
import {ManualContentView} from './ManualContentView'
import {ManualQuestionScreen} from './ManualQuestionScreen'
import {ManualReviewPhotos} from './ManualReviewPhotos'
import {ManualErrorDialog} from './ManualErrorDialog'
import {useManualInterview} from './useManualInterview'
import {useManualIntentReview} from './useManualIntentReview'
import type {ManualService} from './service'
import type {ManualInterviewSession,ManualGuidancePhotoTarget,ManualIntentReview} from './types'
import type {PhotoTarget} from './photos'
export function ManualInterview({service,initial,onBack,onFinal,onDraft}:{service:ManualService;initial:ManualInterviewSession;onBack:()=>void;onFinal?:(session:ManualInterviewSession)=>void;onDraft?:(versionId:string)=>void}) {
 const state=useManualInterview({service,initial,onDraft})
 const {session,review,stage,question,ready,waitingForReview,readError}=state
 const understanding=useManualIntentReview(service,session.id,review,state.updateReview)
 const [photos,setPhotos]=useState<{review:ManualIntentReview;target:PhotoTarget}|null>(null)
 async function openPhotos(target:ManualGuidancePhotoTarget){await state.task.run(async(signal)=>{const result=await service.call('getManualIntentReview',{sessionId:session.id,intentId:target.intentId},undefined,{signal});signal.throwIfAborted();if(result.data.status!=='READY')return;setPhotos({review:result.data,target:target.target==='WORK_STRUCTURE'?{target:'WORK_STRUCTURE',sectionId:null}:{target:'SECTION',sectionId:target.sectionId!}})})}
 if(photos)return <ManualReviewPhotos service={service} sessionId={session.id} review={photos.review} target={photos.target} onUpdate={updated=>{state.updateReview(updated);setPhotos({...photos,review:updated})}} onClose={()=>setPhotos(null)}/>
 const footer=review?understanding.correcting?<ManualVoiceComposer key={review.intentId+':correction'} onRecording={understanding.submit}/>:<div className="manual-button-row"><Button intent="secondary" disabled={understanding.task.busy||review.status!=='READY'} onClick={()=>understanding.setCorrecting(true)}>수정할게요</Button><Button busy={understanding.task.busy} disabled={review.status!=='READY'} onClick={()=>understanding.command('confirmManualInterviewUnderstanding')}>네, 맞아요</Button></div>:waitingForReview?<p role="status">내용을 정리하고 있어요</p>:session.phase==='COLLECTING'?<ManualVoiceComposer key={session.questions[0]?.id} onRecording={state.submit} disabled={!!readError||!state.reviews||state.reviews.sessionRevision<session.revision}/>:session.phase==='READY_TO_GENERATE'?<Button disabled={!ready||!onFinal} onClick={()=>onFinal?.(session)}>최종 검토로</Button>:<div className="manual-voice"><p className="manual-voice-title" role="status">답변을 정리하고 있어요</p><p className="manual-muted">잠시만 기다려 주세요.<br/>정리한 내용을 곧 보여드릴게요.</p></div>
 const taskError=understanding.task.error||state.task.error
 return <ManualFrame onBack={understanding.correcting?()=>understanding.setCorrecting(false):onBack} stage={stage} footer={footer}><div className="manual-stack">
  {review?<><h2 className="manual-heading">{understanding.correcting?'어떤 부분을\n고치면 될까요?':'이렇게 이해했어요'}</h2><p className="manual-muted">{understanding.correcting?'다르게 정리된 내용을 말해 주세요.':'내용이 맞는지 확인해 주세요.'}</p>{review.content&&<ManualContentView content={review.content} service={service}/>}</>:question?<ManualQuestionScreen question={question} onPhotos={openPhotos}/>:<h2 className="manual-heading">{session.phase==='READY_TO_GENERATE'?'모든 질문에 답했어요.':'내용을 정리하고 있어요'}</h2>}
  <ManualErrorDialog error={readError} onClose={state.reload} onReload={state.reload}/>
  <ManualErrorDialog error={taskError} onClose={()=>{understanding.task.clearError();state.task.clearError()}} onRetry={understanding.task.canRetry?understanding.task.retry:state.task.canRetry?state.task.retry:undefined} onReload={state.reload}/>
  <ManualErrorDialog error={review?.status==='ERROR'?'요약을 정리하지 못했어요. 다시 시도해 주세요.':''} onClose={onBack} onRetry={()=>understanding.command('retryManualIntentReview')} retryLabel="요약 다시 시도" busy={understanding.task.busy}/>
  <ManualErrorDialog error={session.phase==='ERROR'?'답변을 정리하지 못했어요. 다시 시도해 주세요.':''} onClose={onBack} onRetry={state.retryProcessing} retryLabel="답변 처리 다시 시도" busy={state.task.busy}/>
 </div></ManualFrame>
}
