export type Session = {
  id: string
  displayName: string
  accountType: 'OWNER' | 'WORKER'
  sessionExpiresAt: string
  csrfToken: string
}

export const SSO_START_PATH = '/api/auth/kakao/start?returnPath=%2Fhome'
export const SESSION_TIMEOUT_MS = 8000

function isSession(value: unknown): value is Session {
  if (!value || typeof value !== 'object') return false
  const session = value as Record<string, unknown>
  return typeof session.id === 'string' && /^[\da-f]{8}(-[\da-f]{4}){3}-[\da-f]{12}$/i.test(session.id)
    && typeof session.displayName === 'string'
    && (session.accountType === 'OWNER' || session.accountType === 'WORKER')
    && typeof session.csrfToken === 'string' && session.csrfToken.length > 0
    && typeof session.sessionExpiresAt === 'string' && Number.isFinite(Date.parse(session.sessionExpiresAt))
}

/** Only 401 means anonymous. A missing/unavailable API never becomes a successful login. */
export async function readSession(signal?: AbortSignal): Promise<Session | null> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  if (signal?.aborted) abort()
  signal?.addEventListener('abort', abort, { once: true })
  const timeout = setTimeout(abort, SESSION_TIMEOUT_MS)
  try {
    const response = await fetch('/api/me', {
      credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }, signal: controller.signal,
    })
    if (response.status === 401) return null
    if (!response.ok) throw new Error('SESSION_UNAVAILABLE')
    const body: unknown = await response.json()
    if (!body || typeof body !== 'object' || !('data' in body) || !isSession(body.data)) throw new Error('SESSION_INVALID')
    if (Date.parse(body.data.sessionExpiresAt) <= Date.now()) return null
    return body.data
  } finally {
    clearTimeout(timeout)
    signal?.removeEventListener('abort', abort)
  }
}

/** UI hint only: the server must validate the HttpOnly registration_ticket on signup. */
export function hasSignupHint(cookie: string): boolean {
  return cookie.split(';').some(part => /^signup_csrf=\S+$/.test(part.trim()))
}

export function callbackMessage(search: string): 'cancelled' | 'failed' | null {
  const error = new URLSearchParams(search).get('error')
  if (!error) return null
  return error === 'access_denied' || error === 'cancelled' ? 'cancelled' : 'failed'
}
