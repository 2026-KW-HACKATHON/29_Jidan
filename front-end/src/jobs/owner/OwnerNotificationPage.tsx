import './OwnerNotificationPage.css';

export type OwnerNotificationState = 'ALL' | 'UNREAD' | 'READ_ALL';

export const OwnerNotificationPage = ({ state: uiState, onFilter, onBack }: {
  state: OwnerNotificationState;
  onFilter: (state: 'ALL' | 'UNREAD') => void;
  onBack: () => void;
}) => {
  // 알림 데이터 목록 
  const allNotifications = [
    { id: 1, title: '새 지원자가 있어요', desc: '주말 오픈 대타 공고에 지원자가 신청했어요.', time: '방금 전', isUnread: true },
    { id: 2, title: '근무자가 초대를 수락했어요', desc: '김지수 님이 명랑핫도그 광운대점에 합류했어요.', time: '30분 전', isUnread: true },
    { id: 3, title: '매장 운영이 승인됐어요', desc: '명랑핫도그 광운대점에서 운영을 시작할 수 있어요.', time: '1시간 전', isUnread: true },
    { id: 4, title: '초대가 만료됐어요', desc: '수락하지 않은 초대가 만료됐어요. 필요하면 다시 초대해 주세요.', time: '어제', isUnread: false },
  ];

  // 상태에 따라 보여줄 알림 필터링 로직
  let displayNotifications = allNotifications;
  if (uiState === 'UNREAD') {
    displayNotifications = allNotifications.filter(n => n.isUnread); // 안 읽은 것만 (3개)
  }
  if (uiState === 'READ_ALL') {
    // 모두 읽음 상태면 데이터의 isUnread를 전부 false로 변경해서 보여줌
    displayNotifications = allNotifications.map(n => ({ ...n, isUnread: false }));
  }

  return (
    <div className="my-isolated-wrapper">
      <div className="mobile-container">
        
        {/* 상단 헤더 */}
        <div className="header-area">
          <button type="button" aria-label="뒤로 가기" onClick={onBack} style={{ border: 0, padding: 0, background: 'none', cursor: 'pointer' }}>&lt;</button>
          <span>알림</span>
        </div>

        {/* 필터 텍스트 영역 (상태별로 다르게 렌더링) */}
        <div className="filter-header">
          {uiState === 'ALL' && (
            <>
              <h3 className="filter-title">전체 알림</h3>
              <button className="filter-btn" onClick={() => onFilter('UNREAD')}>
                안 읽은 알림만 보기
              </button>
            </>
          )}
          {uiState === 'UNREAD' && (
            <>
              <h3 className="filter-title">안 읽은 알림 3개</h3>
              <button className="filter-btn" onClick={() => onFilter('ALL')}>
                전체 알림 보기
              </button>
            </>
          )}
          {uiState === 'READ_ALL' && (
            <h3 className="filter-title">모두 읽었어요</h3>
          )}
        </div>

        {/* 알림 카드 리스트 영역 */}
        <div className="notification-list">
          {displayNotifications.map((noti) => (
            <div key={noti.id} className="notification-card">
              <div className="card-top-row">
                <h4 className="card-title">{noti.title}</h4>
                {noti.isUnread && <div className="unread-badge">안 읽음</div>}
              </div>
              <p className="card-desc">{noti.desc}</p>
              <p className="card-time">{noti.time}</p>
            </div>
          ))}
        </div>

      </div>
    </div>
  );
};
