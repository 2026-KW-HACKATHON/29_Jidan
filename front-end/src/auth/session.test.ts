import { afterEach, expect, it, vi } from 'vitest'
import { createAuthService, registrationContext } from './session'
const signal = () => new AbortController().signal
const future = '2099-01-01T00:00:00Z'
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
export const member = (role: 'OWNER' | 'WORKER' = 'WORKER') => ({ user: { id: 'user-id', name: '김지수', role, stores: role === 'OWNER' ? [{ storeId: 'store-id', storeName: '매장', approvalStatus: 'PENDING', permissions: ['READ_STORE_STATUS'] }] : [] }, expiresAt: future, nextAction: role === 'OWNER' ? 'OWNER_APPROVAL_PENDING' : 'WORKER_HOME' })
const context = { identity: { provider: 'GOOGLE', email: 'member@example.com', emailVerified: true }, expiresAt: future, allowedRoles: ['WORKER', 'OWNER'] }
afterEach(() => vi.useRealTimers())
it.each(['OWNER', 'WORKER'] as const)('%s 세션을 서버 역할과 이동 지시에 따라 변환한다', async role => {
  const fetch = vi.fn().mockResolvedValue(json(member(role))); vi.stubGlobal('fetch', fetch)
  expect(await createAuthService().read(signal())).toMatchObject({ kind: 'authenticated', session: { displayName: '김지수', accountType: role, nextAction: role === 'OWNER' ? 'OWNER_APPROVAL_PENDING' : 'WORKER_HOME' } }); expect(fetch).toHaveBeenCalledTimes(1)
})
it.each(['REGISTRATION_REQUIRED', 'SESSION_EXPIRED'])('%s 뒤 가입 세션을 읽어 신규 회원을 분기한다', async code => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(json({ code }, 401)).mockResolvedValueOnce(json(context)))
  expect(await createAuthService().read(signal())).toEqual({ kind: 'registration', context })
})
it('회원과 가입 세션이 모두 없으면 guest이며 정지 계정은 가입으로 우회하지 않는다', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({ code: 'SESSION_EXPIRED' }, 401)).mockResolvedValueOnce(json({ code: 'SESSION_EXPIRED' }, 401)); vi.stubGlobal('fetch', fetch)
  expect(await createAuthService().read(signal())).toEqual({ kind: 'guest' })
  fetch.mockClear().mockResolvedValue(json({ code: 'ACCOUNT_SUSPENDED' }, 403)); await expect(createAuthService().read(signal())).rejects.toHaveProperty('code', 'ACCOUNT_SUSPENDED'); expect(fetch).toHaveBeenCalledTimes(1)
})
it('만료된 가입 세션과 잘못된 서버 응답을 수락하지 않는다', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({ ...context, expiresAt: '2000-01-01T00:00:00Z' })).mockResolvedValueOnce(json({ ...context, identity: { ...context.identity, emailVerified: false } })).mockResolvedValueOnce(json({ ...member(), nextAction: 'OWNER_HOME' })); vi.stubGlobal('fetch', fetch)
  await expect(registrationContext(signal())).rejects.toHaveProperty('code', 'SESSION_EXPIRED'); await expect(registrationContext(signal())).rejects.toHaveProperty('code', 'INVALID_RESPONSE'); await expect(createAuthService().read(signal())).rejects.toHaveProperty('code', 'INVALID_RESPONSE')
})
it('Google 로그인은 같은 출처로 페이지 이동하며 취소된 요청은 이동하지 않는다', async () => {
  const redirect = vi.fn(), service = createAuthService(redirect); await service.startGoogle(signal()); expect(redirect).toHaveBeenCalledWith('/api/auth/google')
  const controller = new AbortController(); controller.abort(); await expect(service.startGoogle(controller.signal)).rejects.toHaveProperty('name', 'AbortError'); expect(redirect).toHaveBeenCalledOnce()
})
it('로그아웃은 CSRF를 조회한 후 POST하고 204를 처리한다', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(json({ csrfToken: 'csrf' })).mockResolvedValueOnce(new Response(null, { status: 204 })); vi.stubGlobal('fetch', fetch)
  await createAuthService().logout!(signal()); expect(fetch).toHaveBeenLastCalledWith('/api/auth/logout', expect.objectContaining({ method: 'POST', credentials: 'include' }))
})
it('조회 지연과 취소를 전달하고 이후 재시도가 가능하다', async () => {
  vi.useFakeTimers(); const fetch = vi.fn().mockImplementation(() => new Promise(() => {})); vi.stubGlobal('fetch', fetch)
  const service = createAuthService(), request = service.read(signal()), assertion = expect(request).rejects.toHaveProperty('code', 'CLIENT_WAIT_EXCEEDED'); await vi.advanceTimersByTimeAsync(10000); await assertion
  const controller = new AbortController(), cancelled = service.read(controller.signal), cancelledAssertion = expect(cancelled).rejects.toHaveProperty('name', 'AbortError'); controller.abort(); await cancelledAssertion
  fetch.mockResolvedValue(json(member())); expect(await service.read(signal())).toHaveProperty('kind', 'authenticated')
})
