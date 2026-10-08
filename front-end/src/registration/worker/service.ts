import { ApiError, apiRequest } from '../../api/client'
import { parseSession, registrationContext, type ServerSession } from '../../auth/session'
import { clockText } from './model'
import type { Errors, WorkerDraft } from './model'
export type WorkerIdentity = { email: string; draftScope?: string }
export type WorkerReceipt = { id: string; status: 'COMPLETE' }
export class WorkerFailure extends Error {
  code: 'unavailable'|'expired'|'validation'|'network'
  fields: Errors
  constructor(code: WorkerFailure['code'], fields: Errors = {}, message = code as string) { super(message); this.code=code;this.fields=fields }
}
export type WorkerService = {
  identity: (signal: AbortSignal)=>Promise<WorkerIdentity>
  submit: (draft: WorkerDraft, requestKey: string, signal: AbortSignal)=>Promise<WorkerReceipt>
}
const industryCodes = { '음식점': 'RESTAURANT', '카페': 'CAFE', '편의점': 'CONVENIENCE_STORE', '기타': 'OTHER', '': '' }
const weekdays = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
const fieldNames: Record<string, string> = { name:'name', phoneNumber:'phone', birthDate:'birth', gender:'gender', experienceLevel:'experience', careers:'careers', availabilities:'availability' }
function failure(error: unknown): never {
  if (!(error instanceof ApiError)) throw error
  const code = error.status === 401 || error.code === 'ALREADY_REGISTERED' ? 'expired' : error.status === 422 ? 'validation' : 'network'
  const fields: Errors = {}
  for (const field of error.fieldErrors) {
    const name = fieldNames[field.field.split('.')[0].split('[')[0]]
    if (name) fields[name] = field.message
  }
  throw new WorkerFailure(code, fields, error.message)
}
export const workerService: WorkerService = {
  async identity(signal) {
    try { const context = await registrationContext(signal); return { email: context.identity.email, draftScope: context.identity.email } }
    catch (error) { return failure(error) }
  },
  async submit(draft, requestKey, signal) {
    try {
      const response = parseSession(await apiRequest<ServerSession>('/auth/registrations/workers', { method: 'POST', signal, idempotencyKey: requestKey, body: {
        name: draft.name.trim(), phoneNumber: draft.phone.replace(/[\s-]/g,''), birthDate: draft.birth,
        gender: draft.gender === '남성' ? 'MALE' : draft.gender === '여성' ? 'FEMALE' : '',
        experienceLevel: draft.experience === '신입' ? 'NEW' : draft.experience === '경력 있음' ? 'EXPERIENCED' : '',
        careers: draft.experience === '신입' ? [] : draft.careers.map(c => ({ industry: industryCodes[c.industry], duties: c.duties.trim(),
          ...(c.store.trim() ? {storeName:c.store.trim()} : {}), startMonth: c.start, endMonth: c.current ? null : c.end, isCurrent: c.current })),
        availabilities: draft.availability.map(a => ({ days: a.days.map(day => weekdays[day]), startTime: clockText(a.start), endTime: clockText(a.end), endsNextDay: a.overnight })),
      } }))
      if (response.user.role !== 'WORKER' || response.nextAction !== 'WORKER_HOME') throw new ApiError(0, 'INVALID_RESPONSE')
      return { id: response.user.id, status: 'COMPLETE' }
    } catch (error) { return failure(error) }
  },
}
export function isWorkerReceipt(value: unknown): value is WorkerReceipt {
  if(!value||typeof value!=='object')return false
  const r=value as Partial<WorkerReceipt>
  return r.status==='COMPLETE'&&typeof r.id==='string'&&r.id.trim().length>0
}
