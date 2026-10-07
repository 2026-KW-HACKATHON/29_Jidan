import { NotificationPreviewScreen, type NotificationPreviewState } from '../NotificationPreviewScreen'

export type UserNotificationState = NotificationPreviewState

export const UserNotificationPage = ({ state, onFilter, onBack }: {
  state: UserNotificationState
  onFilter: (state: 'ALL' | 'UNREAD') => void
  onBack: () => void
}) => {
  const allNotifications = [
    {
      id: 1,
      title: '대타 근무가 확정됐어요',
      desc: <>컴포즈커피 광운대점<br />9월 26일 09:00–14:00</>,
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

  return <NotificationPreviewScreen items={allNotifications} state={state} onFilter={onFilter} onBack={onBack} />
}
