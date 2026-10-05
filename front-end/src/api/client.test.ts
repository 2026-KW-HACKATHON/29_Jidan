import { afterEach, expect, it, vi } from 'vitest'
import { apiRequest, apiUrl, errorMessage } from './client'
const signal = () => new AbortController().signal
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
afterEach(() => vi.useRealTimers())
it('읽기는 동일 출처 /api와 쿠키를 사용한다', async () => {
  const fetch = vi.fn().mockResolvedValue(json({ ok: true })); vi.stubGlobal('fetch', fetch)
  expect(await apiRequest('/auth/session', { signal: signal() })).toEqual({ ok: true })
  expect(fetch).toHaveBeenCalledWith('/api/auth/session', expect.objectContaining({ credentials: 'include', cache: 'no-store', redirect: 'error' }))
  for (const path of ['https://jidan.leehyowon14.dev', '//other', '/auth/../session', '/auth?redirect=x']) expect(() => apiUrl(path)).toThrow()
})
it('쓰기 요청은 CSRF와 동일한 멱등성 key 및 JSON 본문을 전송한다', async () => {
  const fetch = vi.fn().mockImplementation(async (url: string) => url.endsWith('/csrf') ? json({ csrfToken: 'token' }) : json({ ok: true }, 201)); vi.stubGlobal('fetch', fetch)
  for (let i = 0; i < 2; i++) await apiRequest('/auth/registrations/workers', { signal: signal(), method: 'POST', body: { name: '김' }, idempotencyKey: 'same-key' })
  for (const [, init] of fetch.mock.calls.filter(([url]) => !url.endsWith('/csrf'))) {
    expect(init.headers.get('X-CSRF-Token')).toBe('token'); expect(init.headers.get('Idempotency-Key')).toBe('same-key'); expect(init.body).toBe('{"name":"김"}')
  }
})
it('CSRF 실패 시 쓰기를 보내지 않고 만료된 로그아웃만 토큰을 생략한다', async () => {
  const fetch = vi.fn().mockResolvedValue(json({ code: 'SESSION_EXPIRED' }, 401)); vi.stubGlobal('fetch', fetch)
  await expect(apiRequest('/auth/logout', { signal: signal(), method: 'POST' })).rejects.toHaveProperty('code', 'SESSION_EXPIRED'); expect(fetch).toHaveBeenCalledTimes(1)
  fetch.mockResolvedValueOnce(json({ code: 'SESSION_EXPIRED' }, 401)).mockResolvedValueOnce(new Response(null, { status: 204 }))
  expect(await apiRequest('/auth/logout', { signal: signal(), method: 'POST', allowExpiredCsrf: true })).toBeUndefined()
  expect(fetch.mock.calls.at(-1)?.[1].headers.has('X-CSRF-Token')).toBe(false)
})
it.each([401, 403, 409, 422, 429, 500])('오류 %s의 code와 필드 오류를 보존한다', async status => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ code: 'VALIDATION_ERROR', message: 'raw', fieldErrors: [{ field: 'name', code: 'INVALID', message: '성명 확인' }] }, status)))
  await expect(apiRequest('/auth/session', { signal: signal() })).rejects.toMatchObject({ status, code: 'VALIDATION_ERROR', message: '입력한 정보를 확인해 주세요.', fieldErrors: [{ field: 'name', code: 'INVALID', message: '성명 확인' }] })
  expect(errorMessage('UNRECOGNIZED')).toBe(errorMessage('UNKNOWN_ERROR'))
})
it('HTML 오류, 잘못된 JSON과 네트워크 실패를 처리한다', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response('<html>', { status: 502 })).mockResolvedValueOnce(new Response('invalid')).mockRejectedValueOnce(new TypeError('offline')); vi.stubGlobal('fetch', fetch)
  for (const code of ['UNKNOWN_ERROR', 'INVALID_RESPONSE', 'NETWORK_ERROR']) await expect(apiRequest('/auth/session', { signal: signal() })).rejects.toHaveProperty('code', code)
})
it('10초 제한은 CSRF와 쓰기를 합산하고 늦은 응답을 무시한다', async () => {
  vi.useFakeTimers(); let resolve!: (response: Response) => void
  const fetch = vi.fn().mockImplementation(() => new Promise<Response>(r => { resolve = r })); vi.stubGlobal('fetch', fetch)
  const request = apiRequest('/auth/logout', { signal: signal(), method: 'POST' }); const assertion = expect(request).rejects.toHaveProperty('code', 'CLIENT_WAIT_EXCEEDED')
  await vi.advanceTimersByTimeAsync(10000); await assertion; resolve(json({ csrfToken: 'late' })); await vi.advanceTimersByTimeAsync(0)
  expect(fetch).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0)
})
it('취소와 이미 취소된 요청은 늦은 결과를 전달하지 않는다', async () => {
  const controller = new AbortController(), fetch = vi.fn(() => new Promise(() => {})); vi.stubGlobal('fetch', fetch)
  const request = apiRequest('/auth/session', { signal: controller.signal }); const assertion = expect(request).rejects.toHaveProperty('name', 'AbortError'); controller.abort(); await assertion
  await expect(apiRequest('/auth/session', { signal: controller.signal })).rejects.toHaveProperty('name', 'AbortError'); expect(fetch).toHaveBeenCalledTimes(1)
})
