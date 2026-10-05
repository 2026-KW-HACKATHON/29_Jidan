import './tokens.css'
import './RegistrationProgress.css'

export function RegistrationProgress({ role = 'member', step, labels:customLabels, label='가입 진행 단계' }: { role?: 'owner' | 'member'; step: 1 | 2 | 3; labels?:readonly [string,string,string]; label?:string }) {
  const labels = customLabels ?? (role === 'owner' ? ['기본 정보', '매장 정보', '확인'] : ['기본 정보', '근무 정보', '가능 시간'])
  return <div className="ds-ui ds-progress" aria-label={label}>
    <ol>{labels.map((label, index) => <li key={label} aria-current={index + 1 === step ? 'step' : undefined}>{index + 1}. {label}</li>)}</ol>
    <div className="ds-progress-bars" aria-hidden="true">{labels.map((label, index) => <span key={label} data-complete={index < step} />)}</div>
  </div>
}
