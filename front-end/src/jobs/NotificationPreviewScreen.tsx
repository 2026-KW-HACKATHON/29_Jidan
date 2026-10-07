import type { ReactNode } from 'react'
import { AppBar } from '../ui/AppBar'
import { MobileLayout } from '../ui/MobileLayout'
import './NotificationPreviewScreen.css'

export type NotificationPreviewState = 'ALL' | 'UNREAD' | 'READ_ALL'
export type NotificationPreviewItem = {
  id: string | number
  title: string
  desc: ReactNode
  time: string
  isUnread: boolean
}

/** Figma List/Notification, shared by live and preview routes. */
export function NotificationPreviewScreen({ items, state, onFilter, onBack, onOpen, busy = false, error }: {
  items: readonly NotificationPreviewItem[]
  state: NotificationPreviewState
  onFilter: (state: 'ALL' | 'UNREAD') => void
  onBack: () => void
  onOpen?: (id: string | number) => void
  busy?: boolean
  error?: string
}) {
  const unread = items.filter(item => item.isUnread)
  const displayed = state === 'UNREAD' ? unread : items
  return <MobileLayout className="preview-notifications" header={<AppBar compact title="알림" onBack={onBack} />}>
    <div className="notification-content">
      <div className="notification-filter">
        <h2>{state === 'READ_ALL' ? '모두 읽었어요' : state === 'UNREAD' ? `안 읽은 알림 ${unread.length}개` : '전체 알림'}</h2>
        {state !== 'READ_ALL' && <button type="button" onClick={() => onFilter(state === 'UNREAD' ? 'ALL' : 'UNREAD')}>
          {state === 'UNREAD' ? '전체 알림 보기' : '안읽은 알림만 보기'}
        </button>}
      </div>
      <div className="notification-list">
        {!displayed.length && <p role="status">{state === 'UNREAD' ? '안 읽은 알림이 없어요.' : '도착한 알림이 없어요.'}</p>}
        {error && <p role="alert">{error}</p>}
        {displayed.map(item => <article key={item.id} className="notification-card">
          <div className="notification-card-header">
            <h3>{onOpen ? <button type="button" className="notification-open" disabled={busy} onClick={() => onOpen(item.id)}>{item.title}</button> : item.title}</h3>
            {state !== 'READ_ALL' && item.isUnread && <span className="unread-badge">안 읽음</span>}
          </div>
          <p className="notification-description">{item.desc}</p>
          <p className="notification-time">{item.time}</p>
        </article>)}
      </div>
    </div>
  </MobileLayout>
}
