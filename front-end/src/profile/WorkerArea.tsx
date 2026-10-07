import { useEffect, useRef, useState } from 'react'
import { DeadlineExceeded, withDeadline } from '../async/deadline'
import { WorkerHome, type WorkerHomeData } from '../home/WorkerHome'
import { Modal } from '../ui/Modal'
import { WorkerProfile } from './WorkerProfile'
import { profileService, type ProfileService, type WorkerProfileData } from './service'
/** Reads only through an explicitly supplied service; no API contract is assumed. */
export function WorkerArea({ displayName, data, initialDate, onJobs, service = profileService }: {
  displayName: string; data?: WorkerHomeData; initialDate?: Date; service?: ProfileService; onJobs?: () => void
}) {
  const [profile, setProfile] = useState<WorkerProfileData | null>(null)
  const [error, setError] = useState(false), [busy, setBusy] = useState(false)
  const request = useRef<AbortController | null>(null), locked = useRef(false)
  useEffect(() => () => request.current?.abort(), [])
  async function open() {
    if (locked.current) return
    locked.current = true; setBusy(true)
    const controller = new AbortController(); request.current = controller
    try {
      const value = await withDeadline(signal => service.read(signal), controller)
      if (!controller.signal.aborted) setProfile(value)
    } catch (failure) {
      if (!controller.signal.aborted || failure instanceof DeadlineExceeded) setError(true)
    } finally {
      if (request.current === controller && (!controller.signal.aborted || controller.signal.reason instanceof DeadlineExceeded)) {
        setBusy(false); locked.current = false
      }
    }
  }
  if (profile) return <WorkerProfile initialProfile={profile} service={service} onBack={() => setProfile(null)} />
  return <><WorkerHome displayName={displayName} data={data} initialDate={initialDate} onProfile={() => void open()} onJobs={onJobs} />
    {busy && <p role="status">프로필을 확인하고 있어요.</p>}
    <Modal open={error} state="information" title="프로필을 불러오지 못했어요" description="서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요." cancelLabel="닫기" onClose={() => setError(false)} />
  </>
}
