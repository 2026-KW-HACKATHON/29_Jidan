import type {ManualContent,ManualDraftCorrectionTarget} from './types'
import {ManualCard} from './ManualFrame'
import {Button} from '../ui/Button'
import {ManualPhoto} from './ManualPhoto'
import type {ManualService} from './service'
export function ManualContentView({content,onCorrect,service}:{content:ManualContent;service?:ManualService;onCorrect?:(target:ManualDraftCorrectionTarget)=>void}){
 return <div className="manual-stack">
  {!!content.shifts.length&&<ManualCard title="근무조와 시간">{content.shifts.length?content.shifts.map(shift=><div className="manual-content-row" key={shift.id}><p>{shift.name}　{shift.startTime??'미정'}–{shift.endsNextDay?'다음 날 ':''}{shift.endTime??'미정'}</p>{onCorrect&&<Button intent="secondary" onClick={()=>onCorrect({kind:'SHIFT',targetId:shift.id})}>{shift.name} 수정할게요</Button>}</div>):<p>근무 구조가 아직 정해지지 않았어요.</p>}</ManualCard>}
  {service&&!!content.structurePhotos?.length&&<ManualCard title="근무 구조 사진"><div className="manual-photo-grid">{content.structurePhotos.map(photo=><ManualPhoto key={photo.mediaId} photo={photo} service={service}/>)}</div></ManualCard>}
  {content.sections.map(section=><ManualCard key={section.id} title={section.title}><ol className="manual-steps">{section.steps.map(step=><li key={step.id}>{step.instruction}</li>)}</ol>{!section.steps.length&&<p>업무 절차가 아직 정해지지 않았어요.</p>}{service&&!!section.photos.length&&<div className="manual-photo-grid">{section.photos.map(photo=><ManualPhoto key={photo.mediaId} photo={photo} service={service}/>)}</div>}{onCorrect&&<Button intent="secondary" onClick={()=>onCorrect({kind:'SECTION',targetId:section.id})}>{section.title} 수정할게요</Button>}</ManualCard>)}
  {content.missingInformation?.map(missing=><p className="manual-muted" key={missing.id}>{missing.description}</p>)}
 </div>
}
