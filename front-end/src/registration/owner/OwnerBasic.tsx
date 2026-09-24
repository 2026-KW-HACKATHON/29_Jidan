import { NumberField } from './NumberField'
import { InputField } from '../../ui/Field'
import { OwnerNotice } from './OwnerStep'
import type { OwnerDraft, OwnerErrors } from './model'

export type OwnerFieldsProps = { draft: OwnerDraft; errors: OwnerErrors; onChange: (key: keyof OwnerDraft, value: string) => void; onBlur?: (key: keyof OwnerDraft) => void }
export function OwnerBasic({ draft, errors, onChange, onBlur, email }: OwnerFieldsProps & { email: string }) {
  return <>
    <InputField id="owner-name" onBlur={() => onBlur?.('name')} label="점주 성명 *" placeholder="예: 홍길동 / Alex Kim" helper="신분증에 기재된 성명을 입력해 주세요." autoComplete="name" required maxLength={50} value={draft.name} error={errors.name} onChange={event => onChange('name', event.target.value)} />
    <InputField label="Google 이메일" value={email} readOnly helper="Google 계정에서 가져온 이메일이에요." />
    <NumberField format="mobile" id="owner-phone" onBlur={() => onBlur?.('phone')} label="연락처 *" placeholder="예: 010-1234-5678" autoComplete="tel" required value={draft.phone} error={errors.phone} onValueChange={value => onChange('phone', value)} />
    <OwnerNotice>매장 등록 및 운영 관련 안내를 받을 수 있는 연락처를 입력해 주세요.</OwnerNotice>
  </>
}
