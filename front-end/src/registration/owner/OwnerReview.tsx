import { Button } from '../../ui/Button'
import { OwnerNotice } from './OwnerStep'
import type { OwnerDraft } from './model'

function Summary({ title, rows, onEdit, busy }: { title: string; rows: [string, string][]; onEdit: () => void; busy: boolean }) {
  return <section className="owner-summary" aria-label={title}><h3>{title}</h3><dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || '입력하지 않음'}</dd></div>)}</dl><Button intent="secondary" aria-label={`${title} 수정`} disabled={busy} onClick={onEdit}>수정</Button></section>
}
export function OwnerReview({ draft, email, onEdit, busy }: { draft: OwnerDraft; email: string; onEdit: (step: 1 | 2) => void; busy: boolean }) {
  return <>
    <Summary title="점주 정보" busy={busy} onEdit={() => onEdit(1)} rows={[
      ['이름', draft.name], ['Google 이메일', email], ['연락처', draft.phone],
    ]} />
    <Summary title="매장 정보" busy={busy} onEdit={() => onEdit(2)} rows={[
      ['매장명', draft.storeName], ['업종', draft.industry], ['매장 주소', draft.address], ['상세주소', draft.detailAddress], ['사업자 번호', draft.businessNumber], ['매장 연락처', draft.storePhone],
    ]} />
    <OwnerNotice>신청 후 운영자 확인 전까지 근무자 초대와 공고 운영은 사용할 수 없어요.</OwnerNotice>
  </>
}
