import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AuthFlow, type AuthPath } from './AuthFlow'
import type { AuthService, AuthState } from './session'
const original = Object.getOwnPropertyDescriptors(HTMLDialogElement.prototype)
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value(this: HTMLDialogElement) { this.setAttribute('open', '') } })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value(this: HTMLDialogElement) { this.removeAttribute('open') } })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); document.cookie = 'signup_csrf=; Max-Age=0; Path=/'; for (const key of ['showModal','close']) { if (original[key]) Object.defineProperty(HTMLDialogElement.prototype,key,original[key]); else Reflect.deleteProperty(HTMLDialogElement.prototype,key) } })
function setup(path: AuthPath, state: AuthState = { kind: 'unavailable' }, search = '') {
  const navigate = vi.fn(), renderHome = vi.fn(() => <p>홈</p>), renderRegistration = vi.fn(role => <p>{role} 등록</p>)
  const service: AuthService = { read: vi.fn().mockResolvedValue(state), startGoogle: vi.fn().mockResolvedValue(undefined) }
  return { service, navigate, renderHome, renderRegistration, ...render(<AuthFlow path={path} search={search} service={service} navigate={navigate} renderHome={renderHome} renderRegistration={renderRegistration} />) }
}
it('API 미준비 상태는 준비 안내만 표시한다', async () => {
  const { service, renderHome } = setup('/')
  const button = screen.getByRole('button', { name: 'Google 계정으로 시작하기' })
  await waitFor(() => expect(button).toBeEnabled()); fireEvent.click(button)
  expect(screen.getByRole('dialog')).toHaveAccessibleName('요청을 완료하지 못했어요')
  expect(fetch).not.toHaveBeenCalled(); expect(service.startGoogle).not.toHaveBeenCalled(); expect(renderHome).not.toHaveBeenCalled()
})
it.each(['/signup','/signup/owner','/signup/worker','/home'] as const)('쿠키와 URL은 %s 진입 권한을 만들지 않는다', async path => {
  document.cookie = 'signup_csrf=hint; Path=/'
  const { navigate, renderHome, renderRegistration } = setup(path, { kind: 'guest' }, '?role=OWNER&error=access_denied')
  await waitFor(() => expect(navigate).toHaveBeenCalledWith(path.startsWith('/signup') ? '/login?error=signup_expired' : '/login', true))
  expect(renderHome).not.toHaveBeenCalled(); expect(renderRegistration).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled()
})
it.each(['OWNER','WORKER'] as const)('주입한 %s UI 상태로 역할별 홈을 표시한다', async accountType => {
  const session = { displayName: '검수', accountType }, { renderHome } = setup('/home', { kind: 'authenticated', session })
  await screen.findByText('홈'); expect(renderHome).toHaveBeenCalledWith(session)
})
it('주입한 등록 상태에서만 가입 유형을 선택한다', async () => {
  const { navigate } = setup('/signup', { kind: 'registration' })
  fireEvent.click(await screen.findByRole('button', { name: '점주로 가입' })); expect(navigate).toHaveBeenCalledWith('/signup/owner')
})
it('해제 후 늦은 결과로 이동하지 않는다', async () => {
  let resolve!: (state: AuthState) => void
  const navigate = vi.fn(), read = vi.fn(() => new Promise<AuthState>(done => { resolve = done }))
  const view = render(<AuthFlow path="/" navigate={navigate} service={{ read, startGoogle: vi.fn() }} renderHome={() => null} renderRegistration={() => null} />)
  view.unmount(); await act(async () => resolve({ kind: 'authenticated', session: { displayName: '검수', accountType: 'OWNER' } }))
  expect(navigate).not.toHaveBeenCalled()
})

it('상태 조회가 지연되어도 준비 안내를 확인할 수 있고 늦은 인증 응답을 무시한다',async()=>{
 vi.useFakeTimers();let resolve!:(value:AuthState)=>void;const navigate=vi.fn()
 render(<AuthFlow path="/" navigate={navigate} service={{read:()=>new Promise(r=>{resolve=r}),startGoogle:vi.fn()}} renderHome={()=>null} renderRegistration={()=>null}/>)
 await act(async()=>{await vi.advanceTimersByTimeAsync(10000)})
 const button=screen.getByRole('button',{name:'Google 계정으로 시작하기'});expect(button).toBeEnabled();fireEvent.click(button)
 expect(screen.getByRole('dialog')).toBeVisible()
 await act(async()=>resolve({kind:'authenticated',session:{displayName:'늦은 결과',accountType:'OWNER'}}));expect(navigate).not.toHaveBeenCalled()
})
it('로그인 시작 지연 후 버튼을 복구하고 중복 시작을 막는다',async()=>{
 const startGoogle=vi.fn(()=>new Promise<void>(()=>{}));render(<AuthFlow path="/" navigate={vi.fn()} service={{read:async()=>({kind:'guest'}),startGoogle}} renderHome={()=>null} renderRegistration={()=>null}/>)
 const button=screen.getByRole('button',{name:'Google 계정으로 시작하기'});await waitFor(()=>expect(button).toBeEnabled());vi.useFakeTimers();fireEvent.click(button);fireEvent.click(button);expect(startGoogle).toHaveBeenCalledTimes(1)
 await act(async()=>{await vi.advanceTimersByTimeAsync(10000)});expect(button).toBeEnabled();expect(screen.getByRole('dialog')).toBeVisible()
})

it('신규 가입 세션은 로그인 진입에서도 가입 유형 화면으로 이동한다',async()=>{
 const {navigate}=setup('/login',{kind:'registration'});await waitFor(()=>expect(navigate).toHaveBeenCalledWith('/signup',true))
})
it('가입 세션 만료 시 로그인으로 복귀하고 입력 화면을 숨긴다',async()=>{
 vi.useFakeTimers();const navigate=vi.fn()
 render(<AuthFlow path="/signup/worker" service={{read:async()=>({kind:'registration',context:{identity:{provider:'GOOGLE',email:'a@b.com',emailVerified:true},allowedRoles:['OWNER','WORKER'],expiresAt:new Date(Date.now()+600000).toISOString()}}),startGoogle:vi.fn()}} navigate={navigate} renderHome={()=>null} renderRegistration={()=><p>가입 입력</p>}/> )
 await act(async()=>{});expect(screen.getByText('가입 입력')).toBeVisible();await act(async()=>vi.advanceTimersByTimeAsync(600000));expect(navigate).toHaveBeenCalledWith('/login?error=signup_expired',true);expect(screen.queryByText('가입 입력')).not.toBeInTheDocument()
})
it('조회 오류는 코드별 안내와 재시도를 제공한다',async()=>{
 const {ApiError}=await import('../api/client');const read=vi.fn().mockRejectedValueOnce(new ApiError(403,'ACCOUNT_SUSPENDED')).mockResolvedValueOnce({kind:'guest'})
 render(<AuthFlow path="/login" service={{read,startGoogle:vi.fn()}} navigate={vi.fn()} renderHome={()=>null} renderRegistration={()=>null}/> )
 expect(await screen.findByRole('alert')).toHaveTextContent('이용이 제한된 계정');fireEvent.click(screen.getByRole('button',{name:'다시 시도'}));await waitFor(()=>expect(read).toHaveBeenCalledTimes(2));await waitFor(()=>expect(screen.queryByRole('alert')).not.toBeInTheDocument())
})
it('로그아웃 중 중복 제출을 막고 성공 후 로그인으로 이동한다',async()=>{
 let finish!:()=>void;const logout=vi.fn(()=>new Promise<void>(r=>{finish=r})),navigate=vi.fn()
 render(<AuthFlow path="/home" service={{read:async()=>({kind:'authenticated',session:{displayName:'김',accountType:'WORKER'}}),startGoogle:vi.fn(),logout}} navigate={navigate} renderHome={()=><p>홈</p>} renderRegistration={()=>null}/> )
 const button=await screen.findByRole('button',{name:'로그아웃'});fireEvent.click(button);fireEvent.click(button);expect(logout).toHaveBeenCalledOnce();await act(async()=>finish());expect(navigate).toHaveBeenCalledWith('/login',true);expect(screen.queryByText('홈')).not.toBeInTheDocument()
})
it('callback 공개 오류 힌트는 표시만 하고 인증 권한을 만들지 않는다',async()=>{
 setup('/login',{kind:'guest'},'?error=GOOGLE_ACCESS_DENIED&role=OWNER');expect(await screen.findByRole('alert')).toHaveTextContent('Google 로그인이 취소됐어요')
})

it('가입이 성공하면 기존 가입 만료 타이머를 취소한다',async()=>{
 vi.useFakeTimers();const navigate=vi.fn()
 render(<AuthFlow path="/signup/worker" service={{read:async()=>({kind:'registration',context:{identity:{provider:'GOOGLE',email:'a@b.com',emailVerified:true},allowedRoles:['OWNER','WORKER'],expiresAt:new Date(Date.now()+600000).toISOString()}}),startGoogle:vi.fn()}} navigate={navigate} renderHome={()=>null} renderRegistration={(_role,registered)=><button onClick={registered}>가입 성공</button>}/> )
 await act(async()=>{});fireEvent.click(screen.getByRole('button',{name:'가입 성공'}));await act(async()=>vi.advanceTimersByTimeAsync(600000));expect(navigate).not.toHaveBeenCalled()
})

it('가입 유형 화면의 로그아웃 실패는 안내 후 재시도가 가능하다',async()=>{
 const logout=vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(undefined),navigate=vi.fn()
 render(<AuthFlow path="/signup" service={{read:async()=>({kind:'registration'}),startGoogle:vi.fn(),logout}} navigate={navigate} renderHome={()=>null} renderRegistration={()=>null}/> )
 const back=await screen.findByRole('button',{name:'뒤로 가기'});fireEvent.click(back);expect(await screen.findByRole('dialog')).toBeVisible();expect(navigate).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'확인'}));await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());fireEvent.click(back);await waitFor(()=>expect(navigate).toHaveBeenCalledWith('/login',true))
})
