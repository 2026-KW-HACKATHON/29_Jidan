import type {ManualContent,ManualDraftCorrectionTarget} from './types'
import {ManualCard} from './ManualFrame'
import {Button} from '../ui/Button'
export function ManualContentView({content,onCorrect}:{content:ManualContent;onCorrect?:(target:ManualDraftCorrectionTarget)=>void}){
 return <div className="manual-stack">
  <ManualCard title="근무조와 시간">{content.shifts.length?content.shifts.map(shift=><div className="manual-content-row" key={shift.id}><p>{shift.name}　{shift.startTime??'미정'}–{shift.endsNextDay?'다음 날 ':''}{shift.endTime??'미정'}</p>{onCorrect&&<Button intent="secondary" onClick={()=>onCorrect({kind:'SHIFT',targetId:shift.id})}>{shift.name} 수정할게요</Button>}</div>):<p>근무 구조가 아직 정해지지 않았어요.</p>}</ManualCard>
  {content.sections.map(section=><ManualCard key={section.id} title={section.title}><ol className="manual-steps">{section.steps.map(step=><li key={step.id}>{step.instruction}</li>)}</ol>{!section.steps.length&&<p>업무 절차가 아직 정해지지 않았어요.</p>}{onCorrect&&<Button intent="secondary" onClick={()=>onCorrect({kind:'SECTION',targetId:section.id})}>{section.title} 수정할게요</Button>}</ManualCard>)}
  {content.missingInformation?.map(missing=><p className="manual-muted" key={missing.id}>{missing.description}</p>)}
 </div>
}
