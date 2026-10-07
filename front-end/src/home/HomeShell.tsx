import { Brand } from '../brand/Brand'
import { useState, type ReactNode } from 'react'
import { MobileLayout } from '../ui/MobileLayout'
import { Modal } from '../ui/Modal'
import bell from '../registration/owner/assets/bell.svg'
import home from '../registration/owner/assets/home.svg'
import book from '../registration/owner/assets/book.svg'
import brief from '../registration/owner/assets/brief.svg'
import memberBell from './assets/member-bell.svg'
import { MemberNavigation } from './MemberNavigation'
import './Home.css'

export type HomeRole = 'owner' | 'worker'

const ownerItems = [
  { label: '홈', icon: home },
  { label: '매뉴얼', icon: book },
  { label: '공고 관리', icon: brief },
]

export function HomeShell({ role, children, notificationCount = 0, onProfile, onJobs, onManual, onNotifications }: {
  role: HomeRole
  children: ReactNode
  notificationCount?: number
  onManual?:()=>void
  onNotifications?:()=>void
  onProfile?: () => void
  onJobs?: () => void
}) {
  const [unavailable, setUnavailable] = useState<string | null>(null)
  const items = ownerItems

  return <>
    <MobileLayout className={`home-screen home-${role}`}
      header={<div className="home-app-bar"><h1><Brand /></h1><button type="button" className="home-notification" aria-label="알림" onClick={onNotifications||(() => setUnavailable('알림'))}>
        {notificationCount > 0 && <span className="home-notification-badge">미확인 알림 {notificationCount}개</span>}
        <img src={role === 'owner' ? bell : memberBell} alt="" />
      </button></div>}
      footer={role === 'worker' ? <MemberNavigation active="home" onHome={() => {}} onJobs={onJobs || (() => setUnavailable('공고 찾기'))} onProfile={onProfile || (() => setUnavailable('프로필'))} onManual={onManual||(() => setUnavailable('매뉴얼'))} /> : <nav className="home-bottom-nav" aria-label="주 메뉴"><div className="home-nav-items">{items.map(({ label, icon }, index) => <button type="button" key={label} aria-current={index === 0 ? 'page' : undefined} onClick={() => { if (label === '매뉴얼' && onManual) onManual(); else if (label === '공고 관리' && onJobs) onJobs(); else if (label === '프로필' && onProfile) onProfile(); else if (index) setUnavailable(label) }}>
        <img src={icon} alt="" /><span>{label}</span>
      </button>)}</div><span className="home-indicator" aria-hidden="true" /></nav>}>
      <div className="home-content">{children}</div>
    </MobileLayout>
    <Modal open={unavailable !== null} state="information" title={`${unavailable} 화면을 준비하고 있어요`} description="현재 화면에서 계속 이용해 주세요." cancelLabel="닫기" onClose={() => setUnavailable(null)} />
  </>
}
