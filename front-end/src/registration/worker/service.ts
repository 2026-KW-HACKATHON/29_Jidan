import type { Errors, WorkerDraft } from './model'
export type WorkerIdentity = { email: string }
export type WorkerReceipt = { id: string; status: 'COMPLETE' }
export class WorkerFailure extends Error {
  code: 'unavailable'|'expired'|'validation'|'network'
  fields: Errors
  constructor(code: WorkerFailure['code'], fields: Errors = {}) { super(code); this.code=code;this.fields=fields }
}
export type WorkerService = {
  identity: (signal: AbortSignal)=>Promise<WorkerIdentity>
  submit: (draft: WorkerDraft, requestKey: string, signal: AbortSignal)=>Promise<WorkerReceipt>
}
/** Replace with the agreed Google/profile API adapter; never fabricate registration success. */
export const workerService: WorkerService = {
  identity: async()=>{throw new WorkerFailure('unavailable')},
  submit: async()=>{throw new WorkerFailure('unavailable')},
}
export function isWorkerReceipt(value: unknown): value is WorkerReceipt {
  if(!value||typeof value!=='object')return false
  const r=value as Partial<WorkerReceipt>
  return r.status==='COMPLETE'&&typeof r.id==='string'&&r.id.trim().length>0
}
