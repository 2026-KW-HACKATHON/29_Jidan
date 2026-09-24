import back from './assets/back.svg'
import careerBack from './assets/career-back.svg'
import careerEditorBack from './assets/career-editor-back.svg'
import timeBack from './assets/time-back.svg'
import timeEditorBack from './assets/time-editor-back.svg'
import type { ReactNode } from 'react'
import { MobileLayout } from '../../ui/MobileLayout'
import { AppBar } from '../../ui/AppBar'
import { Button } from '../../ui/Button'
import { RegistrationProgress } from '../../ui/RegistrationProgress'
import './WorkerRegistration.css'
import { WorkerStatusIcon } from './WorkerReview'
export function WorkerFrame({ title = '프로필 등록', heading, description, step, action, busy, onBack, onNext, children, complete = false }: {
  title?: string; heading: string; description: string; step?: 1|2|3; action: string; busy?: boolean; onBack: () => void; onNext: () => void; children: ReactNode; complete?: boolean
}) {
  return <MobileLayout className={`worker-signup ${complete ? 'worker-complete' : ''}`} header={<AppBar backIcon={step===2?careerBack:step===3?timeBack:title.startsWith('경력')?careerEditorBack:title.startsWith('가능 시간')?timeEditorBack:back} compact title={title} onBack={onBack} />} footer={<Button form="worker-form" type="submit" busy={busy}>{action}</Button>}>
    <form id="worker-form" noValidate onSubmit={e=>{e.preventDefault();if(!busy) onNext()}} className="worker-content">
      {step && <RegistrationProgress step={step} />}
      <div className="worker-intro">{complete && <WorkerStatusIcon />}<h2>{heading}</h2><p>{description}</p></div>
      {children}
    </form>
  </MobileLayout>
}
