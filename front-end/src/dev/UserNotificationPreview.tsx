import { UserNotificationPage, type UserNotificationState } from '../jobs/user/UserNotificationPage'
import { navigatePreview, usePreviewLocation } from './navigation'

const states: Record<string, UserNotificationState> = {
  '/__user/noti': 'ALL',
  '/__user/noti/unread': 'UNREAD',
  '/__user/noti/read-all': 'READ_ALL',
}

export default function UserNotificationPreview() {
  const path = usePreviewLocation().split('?')[0]
  return <UserNotificationPage state={states[path] ?? 'ALL'}
    onFilter={state => navigatePreview(state === 'UNREAD' ? '/__user/noti/unread' : '/__user/noti')}
    onBack={() => navigatePreview('/__home/worker')} />
}
