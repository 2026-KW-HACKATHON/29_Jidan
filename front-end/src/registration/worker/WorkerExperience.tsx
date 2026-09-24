import chevron from './assets/chevron.svg'
import { useState } from 'react'
import { Choice } from '../../ui/Choice'
import { Button } from '../../ui/Button'
import { Checkbox } from '../../ui/Checkbox'
import { InputField, SelectField } from '../../ui/Field'
import { IndustryPicker } from '../owner/IndustryPicker'
import { WorkerFrame } from './WorkerFrame'
import { ValuePicker } from './ValuePicker'
import { careerPeriod, today, validateCareer, type Career, type WorkerDraft, type Errors } from './model'
export function WorkerExperience({ draft, errors, change, edit }: { draft: WorkerDraft; errors: Errors; change: (patch: Partial<WorkerDraft>)=>void; edit: (index: number)=>void }) {
  return <fieldset className="worker-group"><legend>근무 경력 *</legend><div className="worker-row">{(['신입','경력 있음'] as const).map(v=><Choice key={v} name="experience" checked={draft.experience===v} onChange={()=>change({experience:v})}>{v}</Choice>)}</div>
    {draft.experience==='신입' && <p className="worker-notice">처음이어도 괜찮아요.<br />배우고 싶은 일부터 시작해 보세요.</p>}
    {draft.experience==='경력 있음' && <>{draft.careers.map((c,i)=><article className="worker-card" key={c.id}><h3>{c.industry} · {c.duties}</h3><p className="worker-muted">{careerPeriod(c)}</p><button type="button" className="worker-link" onClick={()=>edit(i)}>경력 수정</button></article>)}<Button intent="secondary" onClick={()=>edit(-1)}>+ 경력 추가</Button></>}
    {(errors.experience || errors.careers) && <p role="alert" className="worker-error">{errors.experience || errors.careers}</p>}
  </fieldset>
}
export function CareerEditor({ value, birth, onBack, onSave }: { value: Career; birth: string; onBack: ()=>void; onSave: (value: Career)=>void }) {
  const [draft,setDraft]=useState(value),[errors,setErrors]=useState<Errors>({}),[picker,setPicker]=useState<'industry'|'start'|'end'|null>(null)
  const change=(patch:Partial<Career>)=>{setDraft(d=>({...d,...patch}));setErrors({})}
  const save=()=>{const e=validateCareer(draft,birth);setErrors(e);if(!Object.keys(e).length) onSave({...draft,id:draft.id||crypto.randomUUID()})}
  return <WorkerFrame title={value.id?'경력 수정':'경력 추가'} heading="해보신 일을 알려주세요" description="업종과 담당 업무를 중심으로 작성해 주세요." action="경력 저장" onBack={onBack} onNext={save}>
    <SelectField indicatorSrc={chevron} label="업종 *" error={errors.industry} onClick={()=>setPicker('industry')}>{draft.industry||'업종을 선택해 주세요'}</SelectField>
    <InputField label="담당 업무 *" placeholder="예: 음료 제조, 고객 응대" value={draft.duties} maxLength={200} error={errors.duties} onChange={e=>change({duties:e.target.value})} />
    <InputField label="매장명 · 선택" placeholder="근무했던 매장명" value={draft.store} maxLength={100} error={errors.store} onChange={e=>change({store:e.target.value})} />
    <div className="worker-row worker-period"><SelectField indicatorSrc={chevron} label="시작 연월 *" error={errors.start} onClick={()=>setPicker('start')}>{draft.start.replace('-','. ')||'연월 선택'}</SelectField><SelectField indicatorSrc={chevron} label="종료 연월 *" disabled={draft.current} error={errors.end} onClick={()=>setPicker('end')}>{draft.current?'현재 근무 중':draft.end.replace('-','. ')||'연월 선택'}</SelectField></div>
    <label className="worker-check"><Checkbox checked={draft.current} onChange={e=>change({current:e.target.checked})} />현재 근무 중</label>
    <p className="worker-notice">다른 업종의 경험도 등록할 수 있어요.<br />여러 경력은 한 건씩 추가해 주세요.</p>
    {picker==='industry' && <IndustryPicker value={draft.industry} onClose={()=>setPicker(null)} onConfirm={industry=>{change({industry});setPicker(null)}} />}
    {(picker==='start'||picker==='end') && <ValuePicker title={picker==='start'?'시작 연월':'종료 연월'} type="month" value={draft[picker]} min={picker==='start'?birth.slice(0,7):draft.start} max={today().slice(0,7)} onClose={()=>setPicker(null)} onSave={v=>change({[picker]:v})} />}
  </WorkerFrame>
}
