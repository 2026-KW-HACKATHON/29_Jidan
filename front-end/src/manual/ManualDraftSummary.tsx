import {ManualPhotoGroup} from './ManualPhotoGroup'
import type {ManualContent} from './types'
import type {ManualService} from './service'
export type SummaryGroup='shifts'|'common'|'shiftTasks'|'rules'
export function ManualDraftSummary({content,service,onSelect,onPhotos,disabled}:{content:ManualContent;service:ManualService;onSelect:(group:SummaryGroup)=>void;onPhotos:()=>void;disabled:boolean}) {
 const common=content.sections.filter(s=>s.category==='COMMON_TASK'),shiftTasks=content.sections.filter(s=>s.category==='SHIFT_TASK'),rules=content.sections.filter(s=>s.category==='RULE'||s.category==='EQUIPMENT'),photos=[...(content.structurePhotos??[]),...content.sections.flatMap(s=>s.photos)]
 const cards=[
  {group:'shifts' as const,title:'근무 구조 · 수정 ›',lines:content.shifts.map(s=>`${s.name} ${s.startTime??'미정'} – ${s.endsNextDay?'다음 날 ':''}${s.endTime??'미정'}`)},
  {group:'common' as const,title:`공통 업무 · ${common.length}개 ›`,lines:[common.map(s=>s.title).join(' · ')||'등록된 공통 업무가 없어요.']},
  {group:'shiftTasks' as const,title:'근무조별 업무 · 자세히 ›',lines:content.shifts.map(shift=>`${shift.name}: ${shiftTasks.filter(s=>s.shiftId===shift.id).map(s=>s.title).join(' · ')||'별도 업무 없음'}`)},
  {group:'rules' as const,title:`매장 규정 · 사진 ${rules.reduce((sum,s)=>sum+s.photos.length,0)}장 ›`,lines:[rules.map(s=>s.title).join(' · ')||'등록된 규정이 없어요.']},
 ]
 return <div className="manual-stack">{cards.map(card=><button className="manual-card manual-summary-card" key={card.group} onClick={()=>onSelect(card.group)} disabled={disabled}><span className="manual-summary-title">{card.title}</span>{card.lines.map((line,i)=><span key={i}>{line}</span>)}</button>)}<ManualPhotoGroup photos={[...new Map(photos.map(p=>[p.mediaId,p])).values()]} service={service} label="매뉴얼에 연결된 사진" disabled={disabled} onManage={onPhotos}/></div>
}
