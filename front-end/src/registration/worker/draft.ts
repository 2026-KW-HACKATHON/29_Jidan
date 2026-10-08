import type { WorkerDraft } from './model'
const KEY = 'jidan.worker-draft.v1'
export function saveWorkerDraft(scope: string, draft: WorkerDraft, requestKey: string) {
  try { sessionStorage.setItem(KEY, JSON.stringify({scope,draft,requestKey,savedAt:Date.now()})) } catch { /* Optional UX state. */ }
}
export function restoreWorkerDraft(scope: string): {draft:WorkerDraft;requestKey:string} | null {
  try {
    const saved = JSON.parse(sessionStorage.getItem(KEY) || 'null')
    const draft = saved?.draft
    if (!saved || saved.scope !== scope || !Number.isFinite(saved.savedAt) || saved.savedAt > Date.now() || Date.now()-saved.savedAt >= 30*60*1000
      || typeof saved.requestKey !== 'string' || !saved.requestKey || !draft || !['name','phone','birth','gender','experience'].every(k=>typeof draft[k]==='string')
      || !Array.isArray(draft.careers) || !Array.isArray(draft.availability)
      || draft.careers.some((c:Record<string,unknown>)=>!c||!['id','industry','duties','store','start','end'].every(k=>typeof c[k]==='string')||typeof c.current!=='boolean')
      || draft.availability.some((a:Record<string,unknown>)=>!a||typeof a.id!=='string'||!Array.isArray(a.days)||a.days.some(d=>!Number.isInteger(d)||d<0||d>6)||!Number.isInteger(a.start)||!Number.isInteger(a.end)||typeof a.overnight!=='boolean')) return null
    return {draft,requestKey:saved.requestKey}
  } catch { return null }
}
export function clearWorkerDraft() { try { sessionStorage.removeItem(KEY) } catch { /* Optional UX state. */ } }
