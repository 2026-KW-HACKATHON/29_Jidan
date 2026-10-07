import React from 'react';
import { OwnerApplicantPage } from './jobs/owner/OwnerApplicantPage';
import { OwnerNotificationPage } from './jobs/owner/OwnerNotificationPage';
import { UserNotificationPage } from './jobs/user/UserNotificationPage';
import { UserApplyStatusPage } from './jobs/user/UserApplyStatusPage';

export default function App() {
  const path = window.location.pathname;

  if (path.includes('/owner/applicant')) return <OwnerApplicantPage />;
  if (path.includes('/owner/noti')) return <OwnerNotificationPage />;
  if (path.includes('/user/noti')) return <UserNotificationPage />;
  if (path.includes('/user/status')) return <UserApplyStatusPage />;

  return (
    <div style={{ padding: '40px', fontFamily: 'sans-serif' }}>
      <h2>🚀 현재까지 완성된 화면 목록</h2>
      <ul style={{ lineHeight: '2.5', fontSize: '18px' }}>
        <li>
          <strong>점주 화면</strong>
          <ul>
            <li><a href="/owner/applicant">지원자 확인 및 수락 (5장)</a></li>
            <li><a href="/owner/noti">점주 알림 (3장)</a></li>
          </ul>
        </li>
        <li>
          <strong>일반회원 화면</strong>
          <ul>
            <li><a href="/user/noti">일반회원 알림 (3장)</a></li>
            <li><a href="/user/status">신청한 공고 조회 (3장)</a></li>
          </ul>
        </li>
      </ul>
    </div>
  );
}