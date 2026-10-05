import {useRef,useState} from 'react'
import {Button} from '../ui/Button'
import {ManualFrame,ManualCard} from './ManualFrame'
import {ManualPhoto} from './ManualPhoto'
import {createPhotoAttachment,reviewPhotos,type PhotoTarget} from './photos'
import {useManualTask} from './useManualTask'
import {errorMessage,ManualError,type ManualService} from './service'
import type {ManualIntentReview,ManualPhotoAttachment} from './types'

export function ManualReviewPhotos({service,sessionId,review,target,onUpdate,onClose}:{service:ManualService;sessionId:string;review:ManualIntentReview;target:PhotoTarget;onUpdate:(review:ManualIntentReview)=>void;onClose:()=>void}) {
 const input=useRef<HTMLInputElement>(null),task=useManualTask(),[validation,setValidation]=useState('')
 let photos:ManualPhotoAttachment[]=[];let invalid=false
 try{photos=reviewPhotos(review,target)}catch{invalid=true}
 const label=target.target==='WORK_STRUCTURE'?'근무 구조':review.content?.sections.find(s=>s.id===target.sectionId)?.title
 function upload(file:File){setValidation('');try{const attach=createPhotoAttachment(service,sessionId,review,target,file);void task.run(async(signal)=>{const updated=await attach(signal);if(!signal.aborted)onUpdate(updated)})}catch(e){setValidation(errorMessage(e))}}
 function replace(next:ManualPhotoAttachment[]){const revision=review.revision;void task.run(async(signal,key)=>{const result=await service.call('replaceManualInterviewReviewPhotos',{sessionId,intentId:review.intentId},{...target,expectedRevision:revision,photos:next},{signal,key});if(result.data.intentId!==review.intentId)throw new ManualError('INVALID_RESPONSE');if(!signal.aborted)onUpdate(result.data)})}
 function move(index:number,offset:number){const next=[...photos];[next[index],next[index+offset]]=[next[index+offset],next[index]];replace(next)}
 return <ManualFrame showProgress={false} title="사진 첨부" onBack={onClose} footer={<Button disabled={task.busy} onClick={onClose}>첨부 완료</Button>}><div className="manual-stack manual-photo-editor">
  <h2 className="manual-heading">업무를 사진으로 보여주세요</h2><p className="manual-muted">연결된 내용: {label??'삭제되거나 변경된 업무'}</p>
  {invalid?<p role="alert">연결할 업무가 바뀌었어요. 요약을 다시 확인해 주세요.</p>:<>
   {photos.map((photo,index)=><section className="manual-card" key={photo.mediaId} aria-label={photo.title}><ManualPhoto photo={photo} service={service}/><div className="manual-button-row"><Button intent="secondary" disabled={task.busy||index===0} onClick={()=>move(index,-1)} aria-label={`${photo.title} 앞으로`}>앞으로</Button><Button intent="secondary" disabled={task.busy||index===photos.length-1} onClick={()=>move(index,1)} aria-label={`${photo.title} 뒤로`}>뒤로</Button></div><Button intent="danger" disabled={task.busy} onClick={()=>replace(photos.filter(p=>p.mediaId!==photo.mediaId))} aria-label={`${photo.title} 삭제`}>사진 삭제</Button></section>)}
   {!photos.length&&<ManualCard title="사진"><p>이 업무에 연결된 사진이 없어요.</p></ManualCard>}
   <input ref={input} type="file" accept="image/jpeg,image/png,image/webp" hidden aria-label="첨부할 사진" onChange={e=>{const file=e.currentTarget.files?.[0];e.currentTarget.value='';if(file)upload(file)}}/>
   <Button intent="secondary" busy={task.busy} disabled={photos.length>=20} onClick={()=>input.current?.click()}>＋사진 추가</Button>
   <p className="manual-muted">사진은 연결된 업무 내용과 함께 표시돼요. JPG·PNG·WebP, 10 MiB 이하 사진을 20장까지 첨부할 수 있어요.</p>
  </>}
  {(validation||task.error)&&<p role="alert">{validation||task.error}</p>}{task.error&&<Button intent="secondary" onClick={task.retry}>첨부 요청 다시 시도</Button>}
 </div></ManualFrame>
}
