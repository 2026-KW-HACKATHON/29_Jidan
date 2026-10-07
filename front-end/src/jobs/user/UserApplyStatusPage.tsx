import './UserApplyStatusPage.css';

export type UserApplicationTab = 'APPLYING' | 'CONFIRMED' | 'ENDED';

export const UserApplyStatusPage = ({ tab: tabState, onTabChange, onBack }: {
  tab: UserApplicationTab;
  onTabChange: (tab: UserApplicationTab) => void;
  onBack: () => void;
}) => {
  const applyingList = [
    { id: 1, title: '주말 오픈 대타', desc: '컴포즈커피 광운대점', time: '9월 26일 | 09:00~14:00', badgeText: '신청 중', badgeType: 'blue' },
    { id: 2, title: '평일 저녁 홀 대타', desc: '국수천왕 광운대본점', time: '9월 29일 | 18:00~22:00', badgeText: '신청 중', badgeType: 'blue' },
    { id: 3, title: '야간 매장 관리 대타', desc: '세븐일레븐 광운스퀘어점', time: '9월 30일 | 22:00~다음 날 02:00', badgeText: '신청 중', badgeType: 'blue' },
  ];

  const confirmedList = [
    { id: 1, title: '주말 마감 대타', desc: '명랑핫도그 광운대점', time: '9월 27일 | 18:00~22:00', badgeText: '확정', badgeType: 'green' },
  ];

  const endedList = [
    { id: 1, title: '오전 매장 정리', desc: 'GS25 월계성북역점', time: '9월 20일 | 09:00~12:00', badgeText: '미선정', badgeType: 'gray' },
    { id: 2, title: '주말 홀 대타', desc: '국수천왕 광운대본점', time: '9월 19일 | 12:00~17:00', badgeText: '신청 취소', badgeType: 'gray' },
  ];

  let currentList = applyingList;
  if (tabState === 'CONFIRMED') currentList = confirmedList;
  if (tabState === 'ENDED') currentList = endedList;

  return (
    <div className="my-isolated-wrapper">
      <div className="mobile-container">
        
        <div className="header-area">
          <button type="button" aria-label="뒤로 가기" onClick={onBack} style={{ border: 0, padding: 0, background: 'none', cursor: 'pointer' }}>&lt;</button>
          <span>신청한 공고</span>
        </div>

        <div className="page-title-area">
          <h1 className="page-main-title">신청 내역을 확인하세요</h1>
          <p className="page-sub-desc">선정 결과와 확정된 근무를 모아봤어요.</p>
        </div>

        <div className="tab-container">
          <button 
            className={`tab-btn ${tabState === 'APPLYING' ? 'active' : ''}`}
            onClick={() => onTabChange('APPLYING')}
          >
            신청 중 3
          </button>
          <button 
            className={`tab-btn ${tabState === 'CONFIRMED' ? 'active' : ''}`}
            onClick={() => onTabChange('CONFIRMED')}
          >
            확정 1
          </button>
          <button 
            className={`tab-btn ${tabState === 'ENDED' ? 'active' : ''}`}
            onClick={() => onTabChange('ENDED')}
          >
            종료 2
          </button>
        </div>

        {tabState === 'CONFIRMED' && (
          <div className="guide-text-box">
            확정된 근무는 홈의 근무 캘린더에서도 볼 수 있어요.
          </div>
        )}

        <div className="application-list">
          {currentList.map((item) => (
            <div key={item.id} className="application-card">
              <div className="card-top-row">
                <h4 className="card-title">{item.title}</h4>
                <div className={`status-badge ${item.badgeType}`}>{item.badgeText}</div>
              </div>
              <p className="card-desc">{item.desc}</p>
              <p className="card-time">{item.time}</p>
            </div>
          ))}
        </div>

      </div>
    </div>
  );
};
