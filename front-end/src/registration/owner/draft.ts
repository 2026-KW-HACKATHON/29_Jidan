import { isDraft, type OwnerDraft } from './model'

const KEY = 'jidan.owner-draft.v1'
const TTL = 30 * 60 * 1000
export type DraftState = { draft: OwnerDraft; step: 1 | 2 | 3; requestKey: string }
export function restoreDraft(scope: string, storage: Storage = sessionStorage, now = Date.now()): DraftState | null {
  try {
    const raw = storage.getItem(KEY)
    if (!raw) return null
    const saved = JSON.parse(raw)
    if (saved.scope !== scope || !Number.isFinite(saved.savedAt) || now - saved.savedAt >= TTL || saved.savedAt > now
      || !isDraft(saved.draft) || ![1, 2, 3].includes(saved.step) || typeof saved.requestKey !== 'string' || !saved.requestKey) {
      storage.removeItem(KEY); return null
    }
    return { draft: saved.draft, step: saved.step, requestKey: saved.requestKey }
  } catch { return null }
}
export function saveDraft(scope: string, state: DraftState, storage: Storage = sessionStorage, now = Date.now()) {
  try { storage.setItem(KEY, JSON.stringify({ ...state, scope, savedAt: now })); return true } catch { return false }
}
export function clearDraft(storage: Storage = sessionStorage) { try { storage.removeItem(KEY) } catch { /* Storage may be disabled. */ } }
