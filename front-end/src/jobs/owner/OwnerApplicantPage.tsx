import './OwnerApplicantPage.css';

export type OwnerApplicantState = 'WAITING' | 'NO_RESPONSE' | 'CONFIRMED';

export const OwnerApplicantPage = ({ state: uiState, showAlert = false, onBack, onViewApplication, onOtherApplicants, onOnboarding, onDismissAlert }: {
  state: OwnerApplicantState;
  showAlert?: boolean;
  onBack: () => void;
  onViewApplication: () => void;
  onOtherApplicants: () => void;
  onOnboarding: () => void;
  onDismissAlert: () => void;
}) => {
  return (
    <div className="my-isolated-wrapper">
      <div className="mobile-container">
        
        <div className="header-area">
          <button type="button" aria-label="뒤로 가기" onClick={onBack} style={{ border: 0, padding: 0, background: 'none', cursor: 'pointer' }}>&lt;</button>
          <span>지원자 확인</span>
        </div>

        <h1 className="main-title">지원자를 확인해 주세요</h1>

        <div className="blue-summary-box">
          <h4 className="summary-title" style={{ color: '#0A2C93' }}>주말 오픈 대타</h4>
          <p className="summary-text">
            명랑핫도그 광운대점<br />
            9월 27일 · 09:00~14:00
          </p>
        </div>

        <div className="section-label">
          {uiState === 'CONFIRMED' ? '확정된 근무자' : '요청한 지원자'}
        </div>

        <div className="applicant-card">
          <h3 style={{ margin: 0, fontSize: '16px', color: '#1F2124' }}>박지원</h3>
          <p className="summary-text">카페 근무 · 6개월</p>
          
          <p className="card-status-text" style={{ 
            color: uiState === 'CONFIRMED' ? '#255CF5' : '#5A6169', 
            fontWeight: '700' 
          }}>
            {uiState === 'NO_RESPONSE' && '1시간 동안 미응답'}
            {uiState === 'WAITING' && '수락 대기 · 요청한 지 20분'}
            {uiState === 'CONFIRMED' && '근무 확정'}
          </p>
          
          <div style={{ display: 'flex', gap: '8px', width: '100%' }}>
            <button className="card-inner-btn" style={{ flex: 1 }} onClick={onViewApplication}>
              지원서 보기
            </button>
            {uiState === 'CONFIRMED' && (
              <button 
                className="card-inner-btn" 
                style={{ flex: 1, background: '#255CF5', color: '#FFF', border: 'none', cursor: 'pointer' }}
                onClick={onOnboarding}
              >
                온보딩 보기
              </button>
            )}
          </div>
        </div>

        {uiState === 'NO_RESPONSE' && (
          <div style={{ 
            margin: '16px 16px 0 16px',
            display: 'flex',
            padding: '16px',
            flexDirection: 'column',
            alignItems: 'flex-start',
            gap: '12px',
            alignSelf: 'stretch',
            borderRadius: '12px',
            background: '#D6E3FD'
          }}>
            <p style={{ margin: 0, fontSize: '13px', color: '#5A6169', lineHeight: '1.5' }}>
              1시간 동안 응답이 없어요.<br />
              다른 지원자를 확인할 수 있어요.
            </p>
            <button onClick={onOtherApplicants} style={{ padding: '12px', width: '100%', borderRadius: '8px', background: '#255CF5', color: '#FFF', border: 'none', fontWeight: '700', fontSize: '14px', cursor: 'pointer' }}>
              다른 지원자 보기
            </button>
          </div>
        )}

        {uiState !== 'NO_RESPONSE' && (
          <p className="bottom-helper-text">
            수락하면 근무가 확정되고 온보딩이 연결돼요.
          </p>
        )}

        <button className="bottom-fixed-btn">
          {uiState === 'WAITING' && '요청 철회하기'}
          {uiState === 'NO_RESPONSE' && '지원자 선정 없이 모집 마감'}
          {uiState === 'CONFIRMED' && '확정 철회하기'}
        </button>

        {showAlert && (
          <div className="modal-overlay" onClick={onDismissAlert}>
            <div className="modal-box" role="dialog" aria-modal="true" aria-labelledby="no-response-title" onClick={(e) => e.stopPropagation()}>
              <h2 id="no-response-title" className="modal-title">근무 요청에 응답이 없어요</h2>
              <p className="modal-desc">
                박지원님이 1시간 동안 수락하지 않았어요.<br />
                다른 지원자를 확인해 주세요.
              </p>
              <div className="modal-btn-group">
                <button className="modal-btn confirm" onClick={onOtherApplicants}>지원자 확인</button>
              </div>
            </div>
          </div>
        )}

      </div>
    </div>
  );
};
