import {Button} from '../ui/Button'
import {ManualFrame,ManualCard} from './ManualFrame'
import {ManualSectionSummary} from './ManualUnderstandingScreen'
import {ManualPhotoGroup} from './ManualPhotoGroup'
import type {SummaryGroup} from './ManualDraftSummary'
import type {ManualContent,ManualDraftCorrectionTarget} from './types'
import type {ManualService} from './service'
import type {PhotoTarget} from './photos'
export function ManualDraftDetail({content,group,service,disabled,onCorrect,onPhotos,onBack}:{content:ManualContent;group:SummaryGroup|'photos';service:ManualService;disabled:boolean;onCorrect:(target:ManualDraftCorrectionTarget)=>void;onPhotos:(target:PhotoTarget)=>void;onBack:()=>void}) {
 const sections=content.sections.filter(s=>group==='photos'||group==='common'&&s.category==='COMMON_TASK'||group==='shiftTasks'&&s.category==='SHIFT_TASK'||group==='rules'&&(s.category==='RULE'||s.category==='EQUIPMENT'))
 return <ManualFrame title={group==='photos'?'사진 관리':'매뉴얼 상세 확인'} stage={4} onBack={onBack} footer={<Button onClick={onBack}>전체 확인으로 돌아가기</Button>}><div className="manual-stack">
  {group==='shifts'&&<ManualCard title="근무조와 시간">{content.shifts.map(shift=><div className="manual-content-row" key={shift.id}><p>{shift.name}　{shift.startTime??'미정'}–{shift.endsNextDay?'다음 날 ':''}{shift.endTime??'미정'}</p><Button intent="secondary" disabled={disabled} onClick={()=>onCorrect({kind:'SHIFT',targetId:shift.id})}>{shift.name} 수정할게요</Button></div>)}</ManualCard>}
  {(group==='shifts'||group==='photos')&&<ManualPhotoGroup photos={content.structurePhotos??[]} service={service} label="근무 구조" disabled={disabled} onManage={()=>onPhotos({target:'WORK_STRUCTURE',sectionId:null})}/>}
  {sections.map(section=><div className="manual-stack" key={section.id}>{group!=='photos'&&<><ManualSectionSummary section={section}/><Button intent="secondary" disabled={disabled} onClick={()=>onCorrect({kind:'SECTION',targetId:section.id})}>{section.title} 수정할게요</Button></>}<ManualPhotoGroup photos={section.photos} service={service} label={section.title} disabled={disabled} onManage={()=>onPhotos({target:'SECTION',sectionId:section.id})}/></div>)}
  {group!=='shifts'&&group!=='photos'&&!sections.length&&<p>등록된 내용이 없어요.</p>}
 </div></ManualFrame>
}
