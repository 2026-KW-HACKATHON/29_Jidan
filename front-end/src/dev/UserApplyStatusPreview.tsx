import { UserApplyStatusPage, type UserApplicationTab } from '../jobs/user/UserApplyStatusPage'
import { applyingList, confirmedList, endedList } from './userApplicationFixtures'
import { navigatePreview, usePreviewLocation } from './navigation'

const paths: Record<UserApplicationTab, string> = {
  APPLYING: '/__user/status',
  CONFIRMED: '/__user/status/confirmed',
  ENDED: '/__user/status/ended',
}

export default function UserApplyStatusPreview() {
  const path = usePreviewLocation().split('?')[0]
  const tab = path === paths.CONFIRMED ? 'CONFIRMED' : path === paths.ENDED ? 'ENDED' : 'APPLYING'
  return <UserApplyStatusPage lists={{ APPLYING: applyingList, CONFIRMED: confirmedList, ENDED: endedList }} counts={{ pending: 3, confirmed: 1, ended: 2 }} tab={tab} onTabChange={tab => navigatePreview(paths[tab])}
    onBack={() => navigatePreview('/__home/worker')} />
}
