import { expect, it, vi } from 'vitest'
import { authService } from './session'
it('미확정 인증 경로를 호출하거나 성공 상태를 만들지 않는다', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
  const signal = new AbortController().signal
  expect(await authService.read(signal)).toEqual({ kind: 'unavailable' })
  await expect(authService.startGoogle(signal)).rejects.toThrow('AUTH_NOT_CONFIGURED')
  expect(fetch).not.toHaveBeenCalled()
})
