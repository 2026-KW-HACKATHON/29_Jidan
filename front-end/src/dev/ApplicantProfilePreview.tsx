import { ApplicantProfile, type ApplicantProfileData } from '../store/ApplicantProfile'
import { navigatePreview } from './navigation'

const sampleApplicant: ApplicantProfileData = {
  name: '박서연',
  birth: '2007-03-14',
  experienceSummary: '카페 경력 1년 2개월',
  application: { title: '주말 오픈 대타', schedule: '9월 26일 · 09:00–14:00' },
  introduction: '음료 제조와 고객 응대 경험이 있어요.\n안내받은 순서대로 꼼꼼하게 일하겠습니다.',
  experiences: [{ id: 'cafe', title: '카페 · 1년 2개월', period: '2024. 03 – 2025. 04', duties: '음료 제조 · 주문 접수 · 매장 정리' }],
}
export default function ApplicantProfilePreview() {
  return <ApplicantProfile data={sampleApplicant} onBack={() => navigatePreview('/__home/owner')} />
}
