import {Button} from '../ui/Button'
import {ManualCard} from './ManualFrame'
import {ManualPhoto} from './ManualPhoto'
import type {ManualPhotoAttachment} from './types'
import type {ManualService} from './service'
export function ManualPhotoGrid({photos,service}:{photos:ManualPhotoAttachment[];service:ManualService}) {
 return <div className={`manual-photo-grid manual-photo-count-${Math.min(photos.length,3)}`}>{photos.map(photo=><ManualPhoto key={photo.mediaId} photo={photo} service={service}/>)}</div>
}
export function ManualPhotoGroup({photos,service,label,onManage,disabled=false}:{photos:ManualPhotoAttachment[];service:ManualService;label:string;onManage?:()=>void;disabled?:boolean}) {
 return <ManualCard className={photos.length?'manual-photo-group':''} title={photos.length?`첨부 사진 ${photos.length}장`:'함께 보여줄 사진이 있나요?'}>
  {photos.length?<ManualPhotoGrid photos={photos} service={service}/>:<p>업무의 위치나 배치를 사진으로 보여주세요.<br/>사진 없이도 다음으로 넘어갈 수 있어요.</p>}
  <p className="manual-muted manual-caption">{label}</p>
  {onManage&&<Button intent="secondary" disabled={disabled} onClick={onManage}>{photos.length?'사진 관리':'사진 첨부하기'}</Button>}
 </ManualCard>
}
