import chevron from './assets/chevron.svg'
import { useRef, useState } from 'react'
import { Button } from '../../ui/Button'
import { Choice } from '../../ui/Choice'
import { SelectField } from '../../ui/Field'
import { WorkerFrame } from './WorkerFrame'
import { ValuePicker } from './ValuePicker'
import { availabilityFromSlots, clockText, daysText, duration, rangeText, slots, validateAvailability, weekdays, weekHours, type Availability, type Errors } from './model'
export function AvailabilityEditor({ value, others, onBack, onSave }: { value: Availability; others: Availability[]; onBack:()=>void; onSave:(a:Availability)=>void }) {
  const [draft,setDraft]=useState(value),[errors,setErrors]=useState<Errors>({}),[picker,setPicker]=useState<'start'|'end'|null>(null)
  const change=(patch:Partial<Availability>)=>{setDraft(d=>({...d,...patch}));setErrors({})}
  const save=()=>{const e=validateAvailability(draft,others);setErrors(e);if(!Object.keys(e).length) onSave({...draft,id:draft.id||crypto.randomUUID(),days:[...draft.days].sort((a,b)=>a-b)})}
  const valid=!Object.keys(validateAvailability(draft)).length
  return <WorkerFrame title={value.id?'가능 시간 수정':'가능 시간 추가'} heading="요일과 시간을 선택해 주세요" description="같은 시간을 여러 요일에 한 번에 적용할 수 있어요." action={value.id?'시간 저장':'시간 추가'} onBack={onBack} onNext={save}>
    <fieldset className="worker-group"><legend>가능한 요일 *</legend><div className="worker-row worker-days">{weekdays.map((day,i)=><Choice type="checkbox" key={day} checked={draft.days.includes(i)} onChange={()=>change({days:draft.days.includes(i)?draft.days.filter(d=>d!==i):[...draft.days,i]})}>{day}</Choice>)}</div>{errors.days&&<p role="alert" className="worker-error">{errors.days}</p>}</fieldset>
    <div className="worker-row worker-period">{(['start','end'] as const).map(key=><SelectField indicatorSrc={chevron} key={key} label={key==='start'?'시작 시간 *':'종료 시간 *'} onClick={()=>setPicker(key)}>{draft[key]<0?'시간 선택':clockText(draft[key])}</SelectField>)}</div>
    {errors.time&&<p role="alert" className="worker-error">{errors.time}</p>}
    <p className="worker-notice">30분 단위로 등록해 주세요.</p>
    {valid&&<article className="worker-card worker-time-card"><h3>매주 {daysText(draft.days)}</h3><p className="worker-muted">{rangeText(draft)} · 하루 {duration(draft)/60}시간</p></article>}
    {picker&&<ValuePicker type="time" title={picker==='start'?'시작 시간':'종료 시간'} value={draft[picker]<0?'':String(draft[picker])} onClose={()=>setPicker(null)} overnight={picker==='end'?draft.overnight:undefined} onSave={(v,nextDay)=>change({[picker]:Number(v),...(picker==='end'?{overnight:nextDay}: {})})} />}
  </WorkerFrame>
}
export function WorkerAvailability({ values, error, change, edit }: { values: Availability[]; error?: string; change:(values:Availability[])=>void; edit:(index:number)=>void }) {
  const selected=new Set(values.flatMap(slots))
  const drag=useRef<{day:number;half:number;add:boolean;base:Set<number>;last:number}|null>(null)
  const [paint,setPaint]=useState<Set<number>|null>(null)
  function preview(half:number) {
    const d=drag.current;if(!d)return
    d.last=half
    const next=new Set(d.base)
    for(let i=Math.min(d.half,half);i<=Math.max(d.half,half);i++) {const key=d.day*48+i; if(d.add)next.add(key);else next.delete(key)}
    setPaint(next);return next
  }
  function finish() {const d=drag.current;if(!d)return;const next=preview(d.last);drag.current=null;setPaint(null);if(next)change(availabilityFromSlots(next))}
  return <>
    <div className="worker-grid" onPointerMove={e=>{if(!drag.current)return;const cell=document.elementFromPoint(e.clientX,e.clientY)?.closest<HTMLElement>('[data-half]');if(cell && Number(cell.dataset.day)===drag.current.day)preview(Number(cell.dataset.half))}} onPointerUp={finish} onPointerCancel={()=>{drag.current=null;setPaint(null)}}>
      <div className="worker-grid-header"><span>시간</span>{weekdays.map(d=><span key={d}>{d}</span>)}</div>
      {Array.from({length:36},(_,row)=>{const half=row+12;return <div className="worker-grid-row" key={half}><span>{row%2===0?String(half/2).padStart(2,'0'):''}</span>{weekdays.map((day,i)=><button type="button" className="worker-cell" key={day} data-half={half} data-day={i} aria-label={`${day} ${clockText(half*30)} – ${clockText((half+1)*30)}`} aria-pressed={(paint||selected).has(i*48+half)}
        onPointerDown={e=>{if(e.button!==0)return;e.preventDefault();e.currentTarget.focus();e.currentTarget.setPointerCapture(e.pointerId);drag.current={day:i,half,last:half,add:!selected.has(i*48+half),base:selected};preview(half)}}
        onClick={e=>{if(e.detail!==0)return;const next=new Set(selected),key=i*48+half;if(next.has(key))next.delete(key);else next.add(key);change(availabilityFromSlots(next))}} />)}</div>})}
      <p className="worker-grid-label">파란색 · 근무 가능한 시간</p>
    </div>
    {error&&<p role="alert" className="worker-error">{error}</p>}
    <article className="worker-card"><h3>선택한 시간 · 주 {weekHours(values)}시간</h3>{values.map((a,i)=><div className="worker-summary-row" key={a.id}><p className="worker-muted">{a.days.map(d=>weekdays[d]).join(' / ')}　{rangeText(a)}</p><button type="button" className="worker-link" onClick={()=>edit(i)}>시간 수정</button></div>)}</article>
    <Button intent="secondary" onClick={()=>edit(-1)}>+ 시간 추가</Button>
    <p className="worker-time-help">드래그해 30분 단위로 선택할 수 있어요.<br />심야 시간은 시간 추가에서 입력 가능해요.</p>
  </>
}
