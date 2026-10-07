import {ManualPhotoEditor} from './ManualPhotoEditor'
import {ManualError,newKey,type ManualService} from './service'
import {nextPhotoTitle,validatePhoto,type PhotoTarget} from './photos'
import {categoryLabels} from './categoryLabels'
import type {ManualDraft,ManualPhotoAttachment} from './types'
export function ManualDraftPhotos({service,draft,target,onUpdate,onClose}:{service:ManualService;draft:ManualDraft;target:PhotoTarget;onUpdate:(draft:ManualDraft)=>void;onClose:()=>void}) {
 const content=draft.content,section=content?.sections.find(s=>s.id===target.sectionId),photos=target.target==='WORK_STRUCTURE'?content?.structurePhotos??[]:section?.photos??[]
 const invalid=!content||draft.generationStatus!=='READY'||draft.latestCorrection?.status==='RUNNING'||target.target==='SECTION'&&!section
 async function replace(next:ManualPhotoAttachment[],signal:AbortSignal,key:string){
  if(invalid||!content)throw new ManualError('MANUAL_STATE_CONFLICT')
  const updated=target.target==='WORK_STRUCTURE'?{...content,structurePhotos:next}:{...content,sections:content.sections.map(s=>s.id===target.sectionId?{...s,photos:next}:s)}
  const result=await service.call('replaceManualDraftContent',{}, {expectedVersionId:draft.versionId,expectedRevision:draft.revision,content:updated},{signal,key})
  signal.throwIfAborted();if(result.data.versionId!==draft.versionId)throw new ManualError('MANUAL_VERSION_CONFLICT');onUpdate(result.data)
 }
 return <ManualPhotoEditor service={service} photos={photos} invalid={invalid} label={target.target==='WORK_STRUCTURE'?'근무 구조':section?`${categoryLabels[section.category]} · ${section.title}`:'삭제되거나 변경된 업무'} onClose={onClose} onReplace={replace} onUpload={file=>{
  validatePhoto(file);if(photos.length>=20)throw new ManualError('PHOTO_LIMIT')
  const uploadKey=newKey(),linkKey=newKey(),title=nextPhotoTitle(photos);let mediaId:string|undefined
  return async signal=>{if(!mediaId){const form=new FormData();form.set('purpose','MANUAL_PHOTO');form.set('file',file);const result=await service.call('uploadManualMedia',{},form,{signal,key:uploadKey});signal.throwIfAborted();if(result.data.purpose!=='MANUAL_PHOTO'||result.data.storeId!==service.storeId)throw new ManualError('INVALID_RESPONSE');mediaId=result.data.id}await replace([...photos,{mediaId,title,caption:null}],signal,linkKey)}
 }}/>
}
