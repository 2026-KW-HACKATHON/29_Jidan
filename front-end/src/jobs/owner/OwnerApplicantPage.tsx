import { AppBar } from '../../ui/AppBar'
import { Button } from '../../ui/Button'
import { MobileLayout } from '../../ui/MobileLayout'
import { Modal } from '../../ui/Modal'
import { OwnerJobSummary } from './OwnerJobList'
import type { JobApplicant } from './OwnerApplicants'
import type { OwnerJob } from './model'
import './OwnerApplicantPage.css'

export type OwnerApplicantState = 'WAITING' | 'NO_RESPONSE' | 'CONFIRMED'

export const OwnerApplicantPage = ({ state, job, applicant, showAlert = false, statusText, responseWindow = '1시간', actionDisabled = false, onAction, onBack, onViewApplication, onOtherApplicants, onOnboarding, onDismissAlert }: {
  state: OwnerApplicantState
  job: OwnerJob
  applicant: JobApplicant
  showAlert?: boolean
  statusText?: string
  responseWindow?: string
  actionDisabled?: boolean
  onAction?: () => void
  onBack: () => void
  onViewApplication: () => void
  onOtherApplicants: () => void
  onOnboarding: () => void
  onDismissAlert: () => void
}) => {
  const confirmed = state === 'CONFIRMED'
  return <>
    <MobileLayout className="preview-applicant-status" header={<AppBar title="지원자 확인" onBack={onBack} />}
      footer={<Button intent="danger" disabled={actionDisabled} onClick={onAction}>{confirmed ? '확정 철회하기' : state === 'NO_RESPONSE' ? '지원자 선정 없이 모집 마감' : '요청 철회하기'}</Button>}>
      <div className="applicant-content">
        <h2 className="applicant-title">지원자를 확인해 주세요</h2>
        <div className="applicant-summary"><OwnerJobSummary job={job} /></div>
        <h3 className="applicant-label">{confirmed ? '확정된 근무자' : '요청한 지원자'}</h3>
        <div className="applicant-list">
          <article className="applicant-card">
            <div className="owner-applicant-identity">
              <h3>{applicant.name}</h3>
              <p>{applicant.experience || '등록한 경력 없음'}</p>
            </div>
            <p className={`applicant-status${confirmed ? ' is-confirmed' : ''}`}>
              {statusText ?? (confirmed ? '근무 확정' : state === 'NO_RESPONSE' ? `${responseWindow} 동안 미응답` : '수락 대기')}
            </p>
            <div className="owner-job-actions">
              <Button intent="secondary" onClick={onViewApplication}>지원서 보기</Button>
              {confirmed && <Button onClick={onOnboarding}>온보딩 보기</Button>}
            </div>
          </article>
          {state === 'NO_RESPONSE' && <div className="applicant-no-response">
            <p>{responseWindow} 동안 응답이 없어요.<br />다른 지원자를 확인할 수 있어요.</p>
            <Button onClick={onOtherApplicants}>다른 지원자 보기</Button>
          </div>}
          {!confirmed && <p className="applicant-helper">수락하면 근무가 확정되고 온보딩이 연결돼요.</p>}
        </div>
      </div>
    </MobileLayout>
    <Modal open={showAlert} className="preview-applicant-alert" showIcon={false} dismissOnBackdrop
      title="근무 요청에 응답이 없어요" description={`${applicant.name}님이 ${responseWindow} 동안 수락하지 않았어요.\n다른 지원자를 확인해 주세요.`}
      confirmLabel="지원자 확인" closeOnConfirm={false} onClose={onDismissAlert} onConfirm={onOtherApplicants} />
  </>
}
