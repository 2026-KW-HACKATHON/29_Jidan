import { UserApplyStatusPage, type UserApplicationTab } from '../jobs/user/UserApplyStatusPage'
import { navigatePreview, usePreviewLocation } from './navigation'

const paths: Record<UserApplicationTab, string> = {
  APPLYING: '/__user/status',
  CONFIRMED: '/__user/status/confirmed',
  ENDED: '/__user/status/ended',
}

export default function UserApplyStatusPreview() {
  const path = usePreviewLocation().split('?')[0]
  const tab = path === paths.CONFIRMED ? 'CONFIRMED' : path === paths.ENDED ? 'ENDED' : 'APPLYING'
  return <UserApplyStatusPage tab={tab} onTabChange={tab => navigatePreview(paths[tab])}
    onBack={() => navigatePreview('/__home/worker')} />
}
