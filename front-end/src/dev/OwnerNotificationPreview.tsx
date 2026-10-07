import { OwnerNotificationPage, type OwnerNotificationState } from '../jobs/owner/OwnerNotificationPage'
import { navigatePreview, usePreviewLocation } from './navigation'

const states: Record<string, OwnerNotificationState> = {
  '/__owner/noti': 'ALL',
  '/__owner/noti/unread': 'UNREAD',
  '/__owner/noti/read-all': 'READ_ALL',
}

export default function OwnerNotificationPreview() {
  const path = usePreviewLocation().split('?')[0]
  return <OwnerNotificationPage state={states[path] ?? 'ALL'}
    onFilter={state => navigatePreview(state === 'UNREAD' ? '/__owner/noti/unread' : '/__owner/noti')}
    onBack={() => navigatePreview('/__home/owner')} />
}
