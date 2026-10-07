import { NotificationPreviewScreen, type NotificationPreviewState } from '../NotificationPreviewScreen'

export type OwnerNotificationState = NotificationPreviewState

export const OwnerNotificationPage = ({ state, onFilter, onBack }: {
  state: OwnerNotificationState
  onFilter: (state: 'ALL' | 'UNREAD') => void
  onBack: () => void
}) => {
  const allNotifications = [
    { id: 1, title: '새 지원자가 있어요', desc: '주말 오픈 대타 공고에 지원자가 신청했어요.', time: '방금 전', isUnread: true },
    { id: 2, title: '근무자가 초대를 수락했어요', desc: '김지수 님이 명랑핫도그 광운대점에 합류했어요.', time: '30분 전', isUnread: true },
    { id: 3, title: '매장 운영이 승인됐어요', desc: '명랑핫도그 광운대점에서 운영을 시작할 수 있어요.', time: '1시간 전', isUnread: true },
    { id: 4, title: '초대가 만료됐어요', desc: '수락하지 않은 초대가 만료됐어요. 필요하면 다시 초대해 주세요.', time: '어제', isUnread: false },
  ];

  return <NotificationPreviewScreen items={allNotifications} state={state} onFilter={onFilter} onBack={onBack} />
}
