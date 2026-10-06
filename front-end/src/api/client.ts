import { DeadlineExceeded, withDeadline } from '../async/deadline'

export type FieldError = { field: string; code: string; message: string }
const messages: Record<string, string> = {
  SESSION_EXPIRED: '로그인 시간이 만료됐어요. 다시 로그인해 주세요.',
  REGISTRATION_REQUIRED: '가입 유형을 선택하고 가입을 완료해 주세요.',
  ACCOUNT_SUSPENDED: '이용이 제한된 계정이에요. 운영자에게 문의해 주세요.',
  ALREADY_REGISTERED: '이미 가입된 계정이에요. 다시 로그인해 주세요.',
  GOOGLE_ACCESS_DENIED: 'Google 로그인이 취소됐어요. 다시 시작해 주세요.',
  OAUTH_STATE_INVALID: '로그인 요청이 만료됐어요. 다시 시작해 주세요.',
  OAUTH_CODE_INVALID: '로그인 요청을 확인할 수 없어요. 다시 시작해 주세요.',
  GOOGLE_IDENTITY_INVALID: 'Google 계정을 확인하지 못했어요. 다시 로그인해 주세요.',
  GOOGLE_UNAVAILABLE: 'Google 로그인 연결이 지연되고 있어요. 다시 시도해 주세요.',
  CSRF_INVALID: '요청을 확인하지 못했어요. 다시 시도해 주세요.',
  ORIGIN_NOT_ALLOWED: '이 주소에서는 요청할 수 없어요. 서비스 주소를 확인해 주세요.',
  FORBIDDEN: '이 기능을 이용할 권한이 없어요.',
  VALIDATION_ERROR: '입력한 정보를 확인해 주세요.',
  STORE_ALREADY_REGISTERED: '이미 등록된 사업자 번호예요. 번호를 확인해 주세요.',
  STORE_OUTSIDE_SERVICE_AREA: '월계1동에 있는 매장만 등록할 수 있어요.',
  IDEMPOTENCY_KEY_REUSED: '제출 내용이 변경됐어요. 입력 내용을 확인해 주세요.',
  STATE_CONFLICT: '같은 요청을 처리하고 있어요. 잠시 후 다시 시도해 주세요.',
  RATE_LIMITED: '요청이 많아요. 잠시 후 다시 시도해 주세요.',
  CLIENT_WAIT_EXCEEDED: '응답이 지연되고 있어요. 다시 시도해 주세요.',
  REGISTRATION_CONFLICT: '가입 요청을 처리하고 있어요. 잠시 후 다시 시도해 주세요.',
  INVALID_REQUEST: '요청 형식을 확인해 주세요.',
  INTERNAL_ERROR: '처리하지 못했어요. 잠시 후 다시 시도해 주세요.',
  NETWORK_ERROR: '서버에 연결하지 못했어요. 연결을 확인하고 다시 시도해 주세요.',
}
export const errorMessage = (code: string) => messages[code] || '처리하지 못했어요. 잠시 후 다시 시도해 주세요.'
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly fieldErrors: FieldError[]
  constructor(status: number, code: string, fieldErrors: FieldError[] = []) {
    super(errorMessage(code)); this.name = 'ApiError'; this.status = status; this.code = code; this.fieldErrors = fieldErrors
  }
}
// Relative URLs keep dev and production cookies/API requests on their own origin.
export function apiUrl(path: string) {
  if (!/^\/[a-zA-Z0-9][a-zA-Z0-9/_-]*$/.test(path)) throw new Error('INVALID_API_PATH')
  return `/api${path}`
}
export type RequestOptions = { signal: AbortSignal; method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'; body?: unknown; idempotencyKey?: string; allowExpiredCsrf?: boolean }
export async function apiRequest<T>(path: string, options: RequestOptions): Promise<T> {
  const controller = new AbortController()
  const cancel = () => controller.abort(options.signal.reason)
  if (options.signal.aborted) cancel()
  else options.signal.addEventListener('abort', cancel, { once: true })
  try {
    return await withDeadline(async signal => {
      const method = options.method || 'GET', headers = new Headers({ Accept: 'application/json' })
      if (method !== 'GET') {
        try {
          // A fresh lookup also handles session rotation and registration -> member transition.
          const csrf = await send<{ csrfToken: string }>('/auth/csrf', { method: 'GET', headers, signal })
          if (typeof csrf?.csrfToken !== 'string' || !csrf.csrfToken) throw new ApiError(0, 'INVALID_RESPONSE')
          headers.set('X-CSRF-Token', csrf.csrfToken)
        } catch (error) {
          if (!(options.allowExpiredCsrf && error instanceof ApiError && error.status === 401 && error.code === 'SESSION_EXPIRED')) throw error
        }
      }
      signal.throwIfAborted()
      if (options.idempotencyKey) headers.set('Idempotency-Key', options.idempotencyKey)
      if (options.body !== undefined) headers.set('Content-Type', 'application/json')
      return send<T>(path, { method, headers, signal, body: options.body === undefined ? undefined : JSON.stringify(options.body) })
    }, controller)
  } catch (error) {
    if (options.signal.aborted) throw options.signal.reason
    if (error instanceof DeadlineExceeded) throw new ApiError(0, 'CLIENT_WAIT_EXCEEDED')
    if (error instanceof ApiError) throw error
    throw new ApiError(0, 'NETWORK_ERROR')
  } finally { options.signal.removeEventListener('abort', cancel) }
}
async function send<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetch(apiUrl(path), { ...init, credentials: 'include', cache: 'no-store', redirect: 'error' })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const fields = Array.isArray(body?.fieldErrors) ? body.fieldErrors.filter((field: FieldError) => field && typeof field.field === 'string' && typeof field.message === 'string') : []
    throw new ApiError(response.status, typeof body?.code === 'string' ? body.code : 'UNKNOWN_ERROR', fields)
  }
  if (response.status === 204) return undefined as T
  try { return await response.json() as T } catch { throw new ApiError(response.status, 'INVALID_RESPONSE') }
}
