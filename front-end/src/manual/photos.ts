import {ManualError, newKey, type ManualService} from './service'
import type {ManualIntentReview, ManualInterviewPhotoUpdate, ManualPhotoAttachment} from './types'

export type PhotoTarget = {target:'WORK_STRUCTURE';sectionId:null} | {target:'SECTION';sectionId:string}
export function reviewPhotos(review:ManualIntentReview, target:PhotoTarget):ManualPhotoAttachment[] {
 if(review.status!=='READY'||!review.content) throw new ManualError('MANUAL_STATE_CONFLICT')
 if(target.target==='WORK_STRUCTURE') return review.content.structurePhotos??[]
 const section=review.content.sections.find(s=>s.id===target.sectionId)
 if(!section) throw new ManualError('MANUAL_REFERENCE_CONFLICT')
 return section.photos
}
export function validatePhoto(blob:Blob) {
 if(!['image/jpeg','image/png','image/webp'].includes(blob.type)||!blob.size||blob.size>10*1024*1024) throw new ManualError('PHOTO_INVALID')
}
export function nextPhotoTitle(photos:ManualPhotoAttachment[]) {
 let next=1
 while(photos.some(p=>p.title===`사진 ${next}`))next++
 return `사진 ${next}`
}
/** Keep the same upload and attachment request through response loss. */
export function createPhotoAttachment(service:ManualService, sessionId:string, review:ManualIntentReview, target:PhotoTarget, blob:Blob) {
 validatePhoto(blob)
 let photos=structuredClone(reviewPhotos(review,target))
 if(photos.length>=20)throw new ManualError('PHOTO_LIMIT')
 const uploadKey=newKey()
 let title=nextPhotoTitle(photos),linkKey=newKey(),revision=review.revision,refresh=false
 let mediaId:string|undefined
 return async(signal:AbortSignal)=>{
  if(refresh){
   const latest=await service.call('getManualIntentReview',{sessionId,intentId:review.intentId},undefined,{signal})
   signal.throwIfAborted()
   if(latest.data.intentId!==review.intentId)throw new ManualError('INVALID_RESPONSE')
   const current=reviewPhotos(latest.data,target)
   if(current.some(photo=>photo.mediaId===mediaId))return latest.data
   if(current.length>=20)throw new ManualError('PHOTO_LIMIT')
   photos=structuredClone(current);title=nextPhotoTitle(photos);revision=latest.data.revision;linkKey=newKey();refresh=false
  }
  if(!mediaId){const form=new FormData();form.set('purpose','MANUAL_PHOTO');form.set('file',blob,'photo');const result=await service.call('uploadManualMedia',{},form,{signal,key:uploadKey});if(result.data.purpose!=='MANUAL_PHOTO'||result.data.storeId!==service.storeId)throw new ManualError('INVALID_RESPONSE');mediaId=result.data.id}
  signal.throwIfAborted()
  if(photos.some(photo=>photo.mediaId===mediaId))throw new ManualError('PHOTO_ALREADY_ATTACHED')
  const body:ManualInterviewPhotoUpdate={...target,expectedRevision:revision,photos:[...photos,{mediaId,caption:null,title}]}
  try {
   const result=await service.call('replaceManualInterviewReviewPhotos',{sessionId,intentId:review.intentId},body,{signal,key:linkKey})
   if(result.data.intentId!==review.intentId)throw new ManualError('INVALID_RESPONSE')
   return result.data
  } catch(error) {
   // Only a definite revision rejection permits a new body/key; response loss replays the old request.
   if(error instanceof ManualError&&error.code==='REVISION_CONFLICT'){refresh=true;throw new ManualError('PHOTO_REVISION_CONFLICT')}
   throw error
  }
 }
}
