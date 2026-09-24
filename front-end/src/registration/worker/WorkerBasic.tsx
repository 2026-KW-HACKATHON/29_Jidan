import { InputField } from '../../ui/Field'
import { Choice } from '../../ui/Choice'
import { NumberField } from '../owner/NumberField'
import { today, type WorkerDraft, type Errors } from './model'
export function WorkerBasic({ draft, email, errors, change, blur }: { draft: WorkerDraft; email: string; errors: Errors; change: (patch: Partial<WorkerDraft>) => void; blur: (key: string) => void }) {
  return <div className="worker-fields">
    <InputField label="이름 *" value={draft.name} autoComplete="name" maxLength={50} placeholder="예: 홍길동 / Alex Kim" error={errors.name} onChange={e=>change({name:e.target.value})} onBlur={()=>blur('name')} />
    <InputField label="이메일 | Google 연동" value={email} readOnly />
    <NumberField format="mobile" label="전화번호 *" value={draft.phone} placeholder="예: 010-1234-5678" autoComplete="tel" error={errors.phone} onValueChange={phone=>change({phone})} onBlur={()=>blur('phone')} />
    <InputField type="date" label="생년월일 *" value={draft.birth} max={today()} error={errors.birth} onChange={e=>change({birth:e.target.value})} onBlur={()=>blur('birth')} />
    <fieldset className="worker-group worker-basic-gender"><legend>성별 *</legend><div className="worker-row">{(['남성','여성'] as const).map(g=><Choice key={g} name="gender" checked={draft.gender===g} onChange={()=>change({gender:g})}>{g}</Choice>)}</div>{errors.gender && <p className="worker-error" role="alert">{errors.gender}</p>}</fieldset>
  </div>
}
