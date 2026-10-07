import {ManualCard} from './ManualFrame'
import {ManualPhotoGroup} from './ManualPhotoGroup'
import type {ManualService} from './service'
import type {ManualInterviewIntent,ManualInterviewReview,ManualShift,ManualSection} from './types'
import type {PhotoTarget} from './photos'
import {categoryLabels} from './categoryLabels'
export function ManualShiftSummary({shifts}:{shifts:ManualShift[]}) {
 return <ManualCard title="근무조와 시간">{shifts.length?shifts.map(shift=><p key={shift.id}>✓ {shift.name}　{shift.startTime??'미정'}–{shift.endsNextDay?'다음 날 ':''}{shift.endTime??'미정'}</p>):<p>근무 구조가 아직 정해지지 않았어요.</p>}</ManualCard>
}
export function ManualSectionSummary({section}:{section:ManualSection}) {
 return <ManualCard title={section.title}>{section.steps.length?<ol className="manual-steps">{section.steps.map(step=><li key={step.id}>{step.instruction}</li>)}</ol>:<p>업무 절차가 아직 정해지지 않았어요.</p>}</ManualCard>
}
export function ManualUnderstandingScreen({content,stage,service,onPhotos,disabled=false}:{content:ManualInterviewReview;stage:ManualInterviewIntent['stage'];service:ManualService;onPhotos?:(target:PhotoTarget)=>void;disabled?:boolean}) {
 return <div className="manual-stack">
  {stage==='WORK_STRUCTURE'?<><ManualShiftSummary shifts={content.shifts}/><ManualPhotoGroup photos={content.structurePhotos??[]} service={service} label="근무 구조" disabled={disabled} onManage={onPhotos?()=>onPhotos({target:'WORK_STRUCTURE',sectionId:null}):undefined}/></>:content.sections.map(section=><div className="manual-stack" key={section.id}><ManualSectionSummary section={section}/><ManualPhotoGroup photos={section.photos} service={service} label={`${categoryLabels[section.category]} · ${section.title}`} disabled={disabled} onManage={onPhotos?()=>onPhotos({target:'SECTION',sectionId:section.id}):undefined}/></div>)}
 </div>
}
