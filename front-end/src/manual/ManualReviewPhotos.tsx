import {ManualPhotoEditor} from './ManualPhotoEditor'
import {createPhotoAttachment,reviewPhotos,type PhotoTarget} from './photos'
import {ManualError,type ManualService} from './service'
import {categoryLabels} from './ManualUnderstandingScreen'
import type {ManualIntentReview,ManualPhotoAttachment} from './types'
export function ManualReviewPhotos({service,sessionId,review,target,onUpdate,onClose}:{service:ManualService;sessionId:string;review:ManualIntentReview;target:PhotoTarget;onUpdate:(review:ManualIntentReview)=>void;onClose:()=>void}) {
 let photos:ManualPhotoAttachment[]=[],invalid=false
 try{photos=reviewPhotos(review,target)}catch{invalid=true}
 const section=review.content?.sections.find(s=>s.id===target.sectionId)
 const label=target.target==='WORK_STRUCTURE'?'근무 구조':section?`${categoryLabels[section.category]} · ${section.title}`:'삭제되거나 변경된 업무'
 return <ManualPhotoEditor service={service} photos={photos} label={label} invalid={invalid} onClose={onClose} onUpload={file=>{const attach=createPhotoAttachment(service,sessionId,review,target,file);return async signal=>{const updated=await attach(signal);signal.throwIfAborted();onUpdate(updated)}}} onReplace={async(next,signal,key)=>{
  const result=await service.call('replaceManualInterviewReviewPhotos',{sessionId,intentId:review.intentId},{...target,expectedRevision:review.revision,photos:next},{signal,key});signal.throwIfAborted();if(result.data.intentId!==review.intentId)throw new ManualError('INVALID_RESPONSE');onUpdate(result.data)
 }}/>
}
