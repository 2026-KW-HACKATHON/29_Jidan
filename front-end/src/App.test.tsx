import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './App'
import type { AuthService } from './auth/session'
beforeEach(() => { window.history.replaceState(null,'','/'); vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({code:'SESSION_EXPIRED'}),{status:401}))) })
afterEach(() => { cleanup(); window.history.replaceState(null,'','/') })
it('일반 로그인 진입은 회원과 가입 세션을 조회한다', async () => {
  render(<App />); await waitFor(() => expect(screen.getByRole('button',{name:'Google 계정으로 시작하기'})).toBeEnabled())
  expect(fetch).toHaveBeenCalledWith('/api/auth/session', expect.objectContaining({credentials:'include'})); expect(fetch).toHaveBeenCalledWith('/api/auth/registration',expect.anything())
})
it('뒤로가기와 bfcache 복원은 주입한 상태 경계만 다시 읽는다', async () => {
  const service: AuthService = { read: vi.fn().mockResolvedValue({ kind:'unavailable' }), startGoogle:vi.fn() }
  render(<App authService={service} />); await waitFor(() => expect(service.read).toHaveBeenCalledTimes(1))
  act(() => { window.history.replaceState(null,'','/login'); window.dispatchEvent(new PopStateEvent('popstate')) })
  await waitFor(() => expect(service.read).toHaveBeenCalledTimes(2))
  act(() => window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})))
  await waitFor(() => expect(service.read).toHaveBeenCalledTimes(3)); expect(fetch).not.toHaveBeenCalled()
})
it('임의 경로는 인증 화면으로 취급하지 않는다', () => {
  window.history.replaceState(null,'','/unknown'); render(<App />)
  expect(screen.getByRole('heading')).toHaveTextContent('페이지를 찾을 수 없어요'); expect(fetch).not.toHaveBeenCalled()
})
it.each(['OWNER','WORKER'] as const)('mock %s 상태로 샘플 매장 없는 홈을 표시한다', async accountType => {
  window.history.replaceState(null,'','/home')
  render(<App authService={{read:vi.fn().mockResolvedValue({kind:'authenticated',session:{displayName:'김지수',accountType}}),startGoogle:vi.fn()}} />)
  await screen.findByRole('heading',{name:accountType==='OWNER'?'안녕하세요, 김지수 점주님':'안녕하세요, 김지수님'})
  expect(screen.queryByText('명랑핫도그 광운대점')).not.toBeInTheDocument(); expect(fetch).not.toHaveBeenCalled()
})
