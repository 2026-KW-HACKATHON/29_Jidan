import { useId } from 'react'
import { AppBar } from '../../ui/AppBar'
import { MobileLayout } from '../../ui/MobileLayout'
import { Tabs } from '../../ui/Tabs'
import './UserApplyStatusPage.css'

export type UserApplicationTab = 'APPLYING' | 'CONFIRMED' | 'ENDED'
export type ApplicationListItem = { id: string | number; title: string; desc: string; time: string; badgeText: string; badgeType: 'blue' | 'green' | 'gray' }
const tabs: readonly UserApplicationTab[] = ['APPLYING', 'CONFIRMED', 'ENDED']

export const UserApplyStatusPage = ({ tab: tabState, lists, counts, onTabChange, onBack, onOpen, loading = false, error, onRetry }: {
  tab: UserApplicationTab
  lists: Record<UserApplicationTab, readonly ApplicationListItem[]>
  counts: { pending: number; confirmed: number; ended: number }
  onOpen?: (id: string | number) => void
  loading?: boolean
  error?: string
  onRetry?: () => void
  onTabChange: (tab: UserApplicationTab) => void
  onBack: () => void
}) => {
  const panelId = useId()
  const currentList = lists[tabState]

  const selected = tabs.indexOf(tabState)
  return <MobileLayout className="preview-applications" header={<AppBar compact title="신청한 공고" onBack={onBack} />}>
    <div className="application-content">
      <div className="application-introduction">
        <h2>신청 내역을 확인하세요</h2>
        <p>선정 결과와 확정된 근무를 모아봤어요.</p>
      </div>
      <Tabs labels={[`신청 중 ${counts.pending}`, `확정 ${counts.confirmed}`, `종료 ${counts.ended}`]} selected={selected}
        onSelect={index => onTabChange(tabs[index])} panelId={panelId} label="신청 공고 상태" />
      <div id={panelId} role="tabpanel" aria-labelledby={`${panelId}-tab-${selected}`} className="application-panel">
        {tabState === 'CONFIRMED' && <p className="application-notice">확정된 근무는 홈의 근무 캘린더에서도 볼 수 있어요.</p>}
        {loading && <p role="status">신청 내역을 불러오고 있어요.</p>}
        {error && <div role="alert">{error}<button type="button" onClick={onRetry}>다시 시도</button></div>}
        {!loading && !error && !currentList.length && <p role="status">이 상태의 신청 내역이 없어요.</p>}
        <div className="application-list">
          {currentList.map(item => <article key={item.id} className="application-card">
            <div className="application-card-header">
              <h3>{onOpen ? <button type="button" className="application-open" onClick={() => onOpen(item.id)}>{item.title}</button> : item.title}</h3>
              <span className={`application-badge is-${item.badgeType}`}>{item.badgeText}</span>
            </div>
            <p className="application-description">{item.desc}</p>
            <p className="application-time">{item.time}</p>
          </article>)}
        </div>
      </div>
    </div>
  </MobileLayout>
}
