import {useState} from 'react'
import './ManualWorkerPreview.css'
import {Button} from '../ui/Button'
import {ManualFrame,ManualCard} from './ManualFrame'
import {ManualPhoto} from './ManualPhoto'
import type {ManualService} from './service'
import type {ManualWorkerPreview as Preview} from './types'

export function ManualWorkerPreview({preview,service,onClose}:{preview:Preview;service:ManualService;onClose:()=>void}) {
 const [page,setPage]=useState(0),sections=preview.content.sections,section=sections[page]
 return <ManualFrame title="근무자 화면 미리보기" showProgress={false} onBack={onClose} footer={<div className="manual-button-row"><Button intent="secondary" onClick={onClose}>검토로 돌아가기</Button>{page>0&&<Button intent="secondary" onClick={()=>setPage(p=>p-1)}>이전</Button>}{page<sections.length-1&&<Button onClick={()=>setPage(p=>p+1)}>다음</Button>}</div>}><div className="manual-stack manual-worker-preview"><p className="manual-preview-label">게시 전 미리보기</p><h2>매장 업무 안내</h2>
  {section?<ManualCard title={section.title}><p className="manual-muted">{page+1} / {sections.length}</p><ol className="manual-steps">{section.steps.map(step=><li key={step.id}>{step.instruction}</li>)}</ol>{!section.steps.length&&<p>업무 절차가 아직 정해지지 않았어요.</p>}<div className="manual-photo-grid">{section.photos.map(photo=><ManualPhoto key={photo.mediaId} photo={photo} service={service}/>)}</div></ManualCard>:<ManualCard title="업무 안내"><p>등록된 업무가 없어요.</p></ManualCard>}
  {!!preview.content.structurePhotos?.length&&<ManualCard title="근무 구조 참고 사진"><div className="manual-photo-grid">{preview.content.structurePhotos.map(photo=><ManualPhoto key={photo.mediaId} photo={photo} service={service}/>)}</div></ManualCard>}
 </div></ManualFrame>
}
