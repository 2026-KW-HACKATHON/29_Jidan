import type { WorkerDraft } from '../registration/worker/model'
/** UI data and injectable effects only; no backend contract is implied. */
export type WorkerProfileData = { draft: WorkerDraft; email: string }
export type ProfileService = {
  read: (signal: AbortSignal) => Promise<WorkerProfileData>
  save: (value: WorkerProfileData, signal: AbortSignal) => Promise<void>
}
export const profileService: ProfileService = {
  read: async () => { throw new Error('PROFILE_NOT_CONFIGURED') },
  save: async () => { throw new Error('PROFILE_NOT_CONFIGURED') },
}
