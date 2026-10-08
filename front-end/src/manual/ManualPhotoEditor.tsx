import {useEffect,useRef,useState} from 'react'
import {Button} from '../ui/Button'
import {Modal} from '../ui/Modal'
import {ManualFrame,ManualCard} from './ManualFrame'
import {ManualPhoto} from './ManualPhoto'
import {ManualPhotoSourceDialog,type PhotoSource} from './ManualPhotoSourceDialog'
import {ManualErrorDialog} from './ManualErrorDialog'
import {nextPhotoTitle} from './photos'
import {useManualTask} from './useManualTask'
import {errorMessage,type ManualService} from './service'
import type {ManualPhotoAttachment} from './types'
/** Both review and draft adapters capture their own revision before an edit. */
export function ManualPhotoEditor({service,photos,label,invalid=false,onUpload,onReplace,onClose,onSelectionCancel}:{service:ManualService;photos:ManualPhotoAttachment[];label:string;invalid?:boolean;onUpload:(file:File)=>(signal:AbortSignal)=>Promise<void>;onReplace:(photos:ManualPhotoAttachment[],signal:AbortSignal,key:string)=>Promise<void>;onClose:()=>void;onSelectionCancel?:()=>void}) {
 const inputs=useRef<Partial<Record<PhotoSource,HTMLInputElement|null>>>({}),task=useManualTask()
 const [sourceOpen,setSourceOpen]=useState(false),[deleting,setDeleting]=useState<string|null>(null),[validation,setValidation]=useState(''),[pending,setPending]=useState<{url:string;title:string}|null>(null),pendingUrl=useRef('')
 useEffect(()=>{const nodes=Object.values(inputs.current),cancel=()=>onSelectionCancel?.();nodes.forEach(node=>node?.addEventListener('cancel',cancel));return()=>nodes.forEach(node=>node?.removeEventListener('cancel',cancel))},[onSelectionCancel])
 useEffect(()=>()=>{if(pendingUrl.current)URL.revokeObjectURL(pendingUrl.current)},[])
 function clearPending(){if(pendingUrl.current)URL.revokeObjectURL(pendingUrl.current);pendingUrl.current='';setPending(null)}
 function upload(file:File){setValidation('');try{const attach=onUpload(file);clearPending();pendingUrl.current=URL.createObjectURL(file);setPending({url:pendingUrl.current,title:nextPhotoTitle(photos)});void task.run(async(signal)=>{await attach(signal);signal.throwIfAborted();clearPending()})}catch(e){setValidation(errorMessage(e))}}
 function remove(){if(!deleting)return;const next=photos.filter(p=>p.mediaId!==deleting);setDeleting(null);void task.run(async(signal,key)=>{await onReplace(next,signal,key)})}
 const blocked=task.busy||!!pending||invalid
 return <ManualFrame showProgress={false} title="사진 첨부" onBack={()=>{if(!task.busy)onClose()}} footer={<Button intent={photos.length?'primary':'secondary'} disabled={task.busy||!!pending} onClick={onClose}>{photos.length?'첨부 완료':'사진 없이 돌아가기'}</Button>}><div className="manual-stack manual-photo-editor">
  <h2 className="manual-heading">업무를 사진으로 보여주세요</h2><p className="manual-muted">연결된 내용: {label}</p>
  {!invalid&&<>
   {photos.map(photo=><section className="manual-card" key={photo.mediaId} aria-label={photo.title}><ManualPhoto photo={photo} service={service}/><Button intent="danger" disabled={blocked} onClick={()=>setDeleting(photo.mediaId)} aria-label={`${photo.title} 삭제`}>사진 삭제</Button></section>)}
   {pending&&<section className="manual-card" aria-label="연결 전 사진"><figure className="manual-photo"><img src={pending.url} alt={`${pending.title} 연결 전 미리보기`}/><figcaption>{pending.title} · 연결 전</figcaption></figure>{!task.busy&&<Button intent="secondary" onClick={()=>{clearPending();task.clearError()}}>이 첨부 취소</Button>}</section>}
   {!photos.length&&!pending?<ManualCard title="아직 첨부한 사진이 없어요"><p className="manual-muted">배치나 위치를 알려주는 사진을 추가해 주세요.<br/>사진 없이도 계속할 수 있어요.</p><Button onClick={()=>setSourceOpen(true)}>사진 선택</Button></ManualCard>:<Button intent="secondary" busy={task.busy} disabled={blocked||photos.length>=20} onClick={()=>setSourceOpen(true)}>＋사진 추가</Button>}
   {(['camera','album','file'] as const).map(source=><input key={source} ref={el=>{inputs.current[source]=el}} type="file" accept={source==='album'?'image/*':'image/jpeg,image/png,image/webp'} capture={source==='camera'?'environment':undefined} hidden aria-label={source==='file'?'첨부할 사진':source==='camera'?'촬영할 사진':'앨범 사진'} onChange={e=>{const file=e.currentTarget.files?.[0];e.currentTarget.value='';if(file&&!blocked)upload(file);else if(!file&&!blocked)onSelectionCancel?.()}}/>)}
   {!!photos.length&&<p className="manual-muted manual-caption">사진은 연결된 업무 내용과 함께 표시돼요.</p>}
  </>}
  <ManualPhotoSourceDialog open={sourceOpen} onClose={()=>{setSourceOpen(false);onSelectionCancel?.()}} onSelect={source=>{setSourceOpen(false);inputs.current[source]?.click()}}/>
  <Modal open={!!deleting} state="warning" title="사진을 삭제할까요?" description={'사진만 삭제돼요.\n작성한 업무 내용은 그대로 유지돼요.'} confirmLabel="사진 삭제" onClose={()=>setDeleting(null)} onConfirm={remove}/>
  <ManualErrorDialog error={invalid?'연결할 업무가 바뀌었어요. 요약을 다시 확인해 주세요.':validation||task.error} onClose={()=>{if(invalid)onClose();setValidation('');task.clearError()}} onRetry={task.canRetry?task.retry:undefined} retryLabel="첨부 요청 다시 시도" busy={task.busy}/>
 </div></ManualFrame>
}
