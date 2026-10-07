import { useId } from 'react'
import { AppBar } from '../../ui/AppBar'
import { MobileLayout } from '../../ui/MobileLayout'
import { Tabs } from '../../ui/Tabs'
import './UserApplyStatusPage.css'

export type UserApplicationTab = 'APPLYING' | 'CONFIRMED' | 'ENDED'
const tabs: readonly UserApplicationTab[] = ['APPLYING', 'CONFIRMED', 'ENDED']

export const UserApplyStatusPage = ({ tab: tabState, onTabChange, onBack }: {
  tab: UserApplicationTab
  onTabChange: (tab: UserApplicationTab) => void
  onBack: () => void
}) => {
  const panelId = useId()
  const applyingList = [
    { id: 1, title: '주말 오픈 대타', desc: '컴포즈커피 광운대점', time: '9월 26일 ㅣ 09:00–14:00', badgeText: '신청 중', badgeType: 'blue' },
    { id: 2, title: '평일 저녁 홀 대타', desc: '국수천왕 광운대본점', time: '9월 29일 ㅣ 18:00–22:00', badgeText: '신청 중', badgeType: 'blue' },
    { id: 3, title: '야간 매장 관리 대타', desc: '세븐일레븐 광운스퀘어점', time: '9월 30일 ㅣ 22:00–다음 날 02:00', badgeText: '신청 중', badgeType: 'blue' },
  ];

  const confirmedList = [
    { id: 1, title: '주말 마감 대타', desc: '명랑핫도그 광운대점', time: '9월 27일 ㅣ 18:00–22:00', badgeText: '확정', badgeType: 'green' },
  ];

  const endedList = [
    { id: 1, title: '오전 매장 정리', desc: 'GS25 월계성북역점', time: '9월 20일 ㅣ 09:00–12:00', badgeText: '미선정', badgeType: 'gray' },
    { id: 2, title: '주말 홀 대타', desc: '국수천왕 광운대본점', time: '9월 19일 ㅣ 12:00–17:00', badgeText: '신청 취소', badgeType: 'gray' },
  ];

  let currentList = applyingList;
  if (tabState === 'CONFIRMED') currentList = confirmedList;
  if (tabState === 'ENDED') currentList = endedList;

  const selected = tabs.indexOf(tabState)
  return <MobileLayout className="preview-applications" header={<AppBar compact title="신청한 공고" onBack={onBack} />}>
    <div className="application-content">
      <div className="application-introduction">
        <h2>신청 내역을 확인하세요</h2>
        <p>선정 결과와 확정된 근무를 모아봤어요.</p>
      </div>
      <Tabs labels={['신청 중 3', '확정 1', '종료 2']} selected={selected}
        onSelect={index => onTabChange(tabs[index])} panelId={panelId} label="신청 공고 상태" />
      <div id={panelId} role="tabpanel" aria-labelledby={`${panelId}-tab-${selected}`} className="application-panel">
        {tabState === 'CONFIRMED' && <p className="application-notice">확정된 근무는 홈의 근무 캘린더에서도 볼 수 있어요.</p>}
        <div className="application-list">
          {currentList.map(item => <article key={item.id} className="application-card">
            <div className="application-card-header">
              <h3>{item.title}</h3>
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
