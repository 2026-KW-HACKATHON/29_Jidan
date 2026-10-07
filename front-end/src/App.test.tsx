import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './App'
import type { AuthService } from './auth/session'
beforeEach(() => { window.history.replaceState(null,'','/'); vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({code:'SESSION_EXPIRED'}),{status:401}))) })
afterEach(() => { cleanup(); window.history.replaceState(null,'','/') })

function mockHome(name:string){vi.stubGlobal('fetch',vi.fn(async(url:string)=>new Response(JSON.stringify(url.includes('/calendar/')?{month:new Date().toISOString().slice(0,7),timezone:'Asia/Seoul',events:[],asOf:'2026-10-08T00:00:00Z'}:url.includes('/owners/')?{name,stores:[],selectedStoreId:null,recruitingCount:0,recruitingJobs:[],unreadNotificationCount:0,asOf:'2026-10-08T00:00:00Z'}:{name,pendingApplicationCount:0,favoriteStoreCount:0,regularStoreCount:0,unreadNotificationCount:0,recommendedJobs:[],asOf:'2026-10-08T00:00:00Z'}))))}
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
  window.history.replaceState(null,'','/home');mockHome('김지수')
  render(<App authService={{read:vi.fn().mockResolvedValue({kind:'authenticated',session:{displayName:'김지수',accountType}}),startGoogle:vi.fn()}} />)
  await screen.findByRole('heading',{name:accountType==='OWNER'?'안녕하세요, 김지수 점주님':'안녕하세요, 김지수님'})
  expect(screen.queryByText('명랑핫도그 광운대점')).not.toBeInTheDocument(); expect(fetch).toHaveBeenCalled()
})

it.each([['/__auth/session','/home','authenticated'],['/__auth/signup','/signup','registration']] as const)('OAuth 복귀 %s는 실제 인증 경계로 진입한다',async(path,expected,kind)=>{
 window.history.replaceState(null,'',path);if(kind==='authenticated')mockHome('김')
 const state=kind==='authenticated'?{kind,session:{displayName:'김',accountType:'WORKER' as const}}:{kind}
 render(<App authService={{read:async()=>state,startGoogle:vi.fn()}}/>);await waitFor(()=>expect(window.location.pathname).toBe(expected));await screen.findByRole(kind==='authenticated'?'heading':'button',{name:kind==='authenticated'?'안녕하세요, 김님':'점주로 가입'});if(kind==='registration')expect(fetch).not.toHaveBeenCalled()
})
it('서버 이동 지시가 승인 대기이면 일반 점주 홈 대신 승인 대기 화면을 표시한다',async()=>{
 window.history.replaceState(null,'','/home')
 render(<App authService={{read:async()=>({kind:'authenticated',session:{displayName:'김',accountType:'OWNER',nextAction:'OWNER_APPROVAL_PENDING',stores:[{storeId:'store',storeName:'내 매장',approvalStatus:'PENDING',permissions:['READ_STORE_STATUS']}]}}),startGoogle:vi.fn()}}/>);await screen.findByText('승인 대기 중');expect(screen.getByRole('heading',{name:'내 매장'})).toBeInTheDocument()
})
