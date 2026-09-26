import { afterEach, describe, expect, it, vi } from 'vitest'
import { callbackMessage, hasSignupHint, readSession, SESSION_TIMEOUT_MS } from './session'

const me = { id: '00000000-0000-4000-8000-000000000001', displayName: '김근무', accountType: 'WORKER', sessionExpiresAt: '2099-01-01T00:00:00Z', csrfToken: 'test-only-csrf' }
afterEach(() => vi.useRealTimers())
describe('session contract', () => {
  it('uses a same-origin cookie and reads the documented envelope', async () => {
    const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: me })))
    vi.stubGlobal('fetch', request)
    expect(await readSession()).toEqual(me)
    expect(request).toHaveBeenCalledWith('/api/me', expect.objectContaining({ credentials: 'same-origin', cache: 'no-store', signal: expect.any(AbortSignal) }))
  })
  it('treats only 401 or an expired valid session as anonymous', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(null, { status: 401 })).mockResolvedValueOnce(new Response(JSON.stringify({ data: { ...me, sessionExpiresAt: '2000-01-01T00:00:00Z' } }))))
    expect(await readSession()).toBeNull()
    expect(await readSession()).toBeNull()
  })
  it.each([403, 404, 429, 500, 503])('does not turn HTTP %i into anonymous success', async status => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status })))
    await expect(readSession()).rejects.toThrow('SESSION_UNAVAILABLE')
  })
  it.each([{}, { data: { ...me, accountType: 'ADMIN' } }, { data: { ...me, csrfToken: '' } }, { data: { ...me, sessionExpiresAt: 'invalid' } }, { data: { ...me, id: 'bad' } }])('rejects malformed session %j', async body => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body))))
    await expect(readSession()).rejects.toThrow('SESSION_INVALID')
  })
  it('rejects HTML and network failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response('<html>proxy error</html>')).mockRejectedValueOnce(new TypeError('offline')))
    await expect(readSession()).rejects.toThrow()
    await expect(readSession()).rejects.toThrow('offline')
  })
  it('aborts a stalled request after the deadline', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))))))
    const assertion = expect(readSession()).rejects.toThrow('Aborted')
    await vi.advanceTimersByTimeAsync(SESSION_TIMEOUT_MS)
    await assertion
    expect(vi.getTimerCount()).toBe(0)
  })
  it('forwards caller cancellation and removes the timeout', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn((_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))))))
    const controller = new AbortController()
    const assertion = expect(readSession(controller.signal)).rejects.toThrow('Aborted')
    controller.abort()
    await assertion
    expect(vi.getTimerCount()).toBe(0)
  })
})
it('requires an exact nonempty signup hint cookie and never trusts role parameters', () => {
  expect(hasSignupHint('unrelated=x; signup_csrf=some-hint; other=y')).toBe(true)
  expect(hasSignupHint('signup_csrf=')).toBe(false)
  expect(hasSignupHint('fake_signup_csrf=token; role=OWNER')).toBe(false)
})
it('maps provider cancellation and unknown errors to safe UI messages', () => {
  expect(callbackMessage('?error=access_denied&error_description=private')).toBe('cancelled')
  expect(callbackMessage('?error=anything&redirect=https://evil.example')).toBe('failed')
  expect(callbackMessage('?role=OWNER')).toBeNull()
})
