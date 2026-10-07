import { useMemo } from 'react'
import { OwnerApplicantPage, type OwnerApplicantState } from '../jobs/owner/OwnerApplicantPage'
import { ApplicantReview, type JobApplicant } from '../jobs/owner/OwnerApplicants'
import { ManualWorkerPreview } from '../manual/ManualWorkerPreview'
import { ownerJobFixtures } from './ownerJobFixtures'
import { draftFixture } from './manualDraftFixtures'
import { createManualPreviewService } from './manualPreviewService'
import { navigatePreview, usePreviewLocation } from './navigation'

const states: Record<string, OwnerApplicantState> = {
  '/__owner/applicant': 'WAITING',
  '/__owner/applicant/no-response': 'NO_RESPONSE',
  '/__owner/applicant/no-response/alert': 'NO_RESPONSE',
  '/__owner/applicant/confirmed': 'CONFIRMED',
}
const applicant: JobApplicant = {
  id: 'park', name: '박지원', experience: '카페 근무 · 6개월',
  introduction: '음료 제조와 고객 응대 경험이 있어요. 안내받은 순서대로 꼼꼼하게 일하겠습니다.',
}

export default function OwnerApplicantPreview() {
  const current = usePreviewLocation()
  const path = current.split('?')[0], view = new URLSearchParams(current.split('?')[1]).get('view')
  const state = states[path] ?? 'WAITING'
  const service = useMemo(() => createManualPreviewService(true, 'draft'), [])
  const close = () => navigatePreview(path)
  if (view === 'application') return <ApplicantReview applicant={applicant} job={ownerJobFixtures[0]}
    readOnly onClose={close} onRequest={() => {}} />
  if (view === 'onboarding' && state === 'CONFIRMED') return <ManualWorkerPreview service={service}
    preview={{ preview: true, versionId: draftFixture.versionId, revision: draftFixture.revision, content: draftFixture.content }}
    onClose={close} />
  return <OwnerApplicantPage state={state} showAlert={path === '/__owner/applicant/no-response/alert'}
    onBack={() => navigatePreview('/__owner/jobs?view=applicants&id=open')}
    onViewApplication={() => navigatePreview(`${path}?view=application`)}
    onOtherApplicants={() => navigatePreview('/__owner/jobs?view=applicants&id=open')}
    onOnboarding={() => navigatePreview('/__owner/applicant/confirmed?view=onboarding')}
    onDismissAlert={() => navigatePreview('/__owner/applicant/no-response')} />
}
