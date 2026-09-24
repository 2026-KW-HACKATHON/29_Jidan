import { AppBar } from '../ui/AppBar'
import { Button } from '../ui/Button'
import { MobileLayout } from '../ui/MobileLayout'
import store from './assets/store.svg'
import user from './assets/user.svg'
import './RoleSelectionScreen.css'

export type SignupRole = 'owner' | 'worker'
const roles = [
  { role: 'owner', title: '점주로 가입', description: <>매장을 등록하고 근무자를 관리하며<br />대타 공고를 게시할 수 있어요.</>, icon: store },
  { role: 'worker', title: '일반회원으로 가입', description: <>대타 공고를 탐색하고 신청하며<br />업무 매뉴얼을 확인할 수 있어요.</>, icon: user },
] as const

export function RoleSelectionScreen({ onBack, onSelect }: { onBack: () => void; onSelect: (role: SignupRole) => void }) {
  return <MobileLayout className="auth-role-selection" header={<AppBar title="가입 유형 선택" compact onBack={onBack} />}>
    <div className="auth-role-content">
      <div className="auth-role-introduction"><h2>어떤 역할로<br />지단과 함께하시나요?</h2><p>나에게 맞는 유형으로 시작해 보세요.</p></div>
      <div className="auth-role-options">{roles.map(({ role, title, description, icon }) => <section className={`auth-role-card auth-role-${role}`} key={role} aria-labelledby={`role-${role}`}>
        <div className="auth-role-heading"><span className="auth-role-icon"><img src={icon} alt="" /></span><h3 id={`role-${role}`}>{title}</h3></div>
        <p>{description}</p>
        <Button intent={role === 'owner' ? 'primary' : 'secondary'} onClick={() => onSelect(role)}>{title}</Button>
      </section>)}</div>
      <p className="auth-role-hint">이용할 서비스에 맞는 가입 유형을 선택해 주세요.</p>
    </div>
  </MobileLayout>
}
