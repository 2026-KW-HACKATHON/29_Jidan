import type { ReactNode } from 'react'
import { AppBar } from '../../ui/AppBar'
import { Button } from '../../ui/Button'
import { MobileLayout } from '../../ui/MobileLayout'
import { RegistrationProgress } from '../../ui/RegistrationProgress'
import './OwnerStep.css'

export function OwnerStep({ step, title, description, action, busy = false, onBack, onNext, children }: {
  step: 1 | 2 | 3; title: string; description: string; action: string; busy?: boolean
  onBack: () => void; onNext: () => void; children: ReactNode
}) {
  return <MobileLayout className="owner-signup" header={<AppBar title="점주 가입" onBack={onBack} />}
    footer={<Button type="submit" form="owner-signup-form" busy={busy}>{action}</Button>}>
    <form id="owner-signup-form" className="owner-step" noValidate onSubmit={event => { event.preventDefault(); if (!busy) onNext() }}>
      <RegistrationProgress role="owner" step={step} />
      <div className="owner-intro"><h2>{title}</h2><p>{description}</p></div>
      {children}
    </form>
  </MobileLayout>
}
export function OwnerNotice({ children }: { children: ReactNode }) { return <p className="owner-notice">{children}</p> }
