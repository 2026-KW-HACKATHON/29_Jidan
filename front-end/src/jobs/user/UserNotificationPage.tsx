import './UserNotificationPage.css';

export const UserNotificationPage = () => {
  const path = window.location.pathname;
  let uiState: 'ALL' | 'UNREAD' | 'READ_ALL' = 'ALL';
  
  if (path.includes('unread')) uiState = 'UNREAD';
  if (path.includes('read-all')) uiState = 'READ_ALL';

  const allNotifications = [
    { 
      id: 1, 
      title: '대타 근무가 확정됐어요', 
      desc: <>컴포즈커피 광운대점<br />9월 26일 09:00~14:00</>, 
      time: '방금 전', 
      isUnread: true 
    },
    { 
      id: 2, 
      title: '매장에서 초대했어요', 
      desc: '명랑핫도그 광운대점의 초대를 확인해 주세요.', 
      time: '30분 전', 
      isUnread: true 
    },
    { 
      id: 3, 
      title: '내일 근무가 있어요', 
      desc: '확정된 근무 시간과 출근 안내를 확인해 주세요.', 
      time: '1시간 전', 
      isUnread: true 
    },
    { 
      id: 4, 
      title: '새 매뉴얼이 등록됐어요', 
      desc: '명랑핫도그 광운대점의 업무 안내가 업데이트됐어요.', 
      time: '어제', 
      isUnread: false 
    },
  ];

  let displayNotifications = allNotifications;
  if (uiState === 'UNREAD') {
    displayNotifications = allNotifications.filter(n => n.isUnread);
  }
  if (uiState === 'READ_ALL') {
    displayNotifications = allNotifications.map(n => ({ ...n, isUnread: false }));
  }

  return (
    <div className="my-isolated-wrapper">
      <div className="mobile-container">
        
        <div className="header-area">
          <span style={{ cursor: 'pointer' }}>&lt;</span>
          <span>알림</span>
        </div>

        <div className="filter-header">
          {uiState === 'ALL' && (
            <>
              <h3 className="filter-title">전체 알림</h3>
              <button className="filter-btn" onClick={() => window.location.href = '/user/noti/unread'}>
                안 읽은 알림만 보기
              </button>
            </>
          )}
          {uiState === 'UNREAD' && (
            <>
              <h3 className="filter-title">안 읽은 알림 3개</h3>
              <button className="filter-btn" onClick={() => window.location.href = '/user/noti'}>
                전체 알림 보기
              </button>
            </>
          )}
          {uiState === 'READ_ALL' && (
            <h3 className="filter-title">모두 읽었어요</h3>
          )}
        </div>

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
