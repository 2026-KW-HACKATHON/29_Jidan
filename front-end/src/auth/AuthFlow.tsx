import {LoadingState} from '../ui/LoadingState'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { DeadlineExceeded, withDeadline } from '../async/deadline'
import { ApiError, errorMessage } from '../api/client'
import { MobileLayout } from '../ui/MobileLayout'
import { Button } from '../ui/Button'
import { LoginScreen } from './LoginScreen'
import { RoleSelectionScreen, type SignupRole } from './RoleSelectionScreen'
import { Modal } from '../ui/Modal'
import { authService, type AuthService, type AuthState, type Session } from './session'
export type AuthPath = '/' | '/login' | '/signup' | '/signup/owner' | '/signup/worker' | '/home'
function failureMessage(error: unknown) {
  return error instanceof ApiError ? error.message : errorMessage(error instanceof DeadlineExceeded ? 'CLIENT_WAIT_EXCEEDED' : 'UNKNOWN_ERROR')
}
export function AuthFlow({ path, search = '', navigate, renderHome, renderRegistration, service = authService }: {
  path: AuthPath; search?: string
  navigate: (path: AuthPath | '/login?error=signup_expired', replace?: boolean) => void
  renderHome: (session: Session) => ReactNode
  renderRegistration: (role: SignupRole, onRegistered: () => void) => ReactNode
  service?: AuthService
}) {
  const [state, setState] = useState<AuthState>({ kind: 'unavailable' })
  const [checking, setChecking] = useState(true)
  const [message, setMessage] = useState('')
  const [readError, setReadError] = useState('')
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const [registered, setRegistered] = useState(false)
  const request = useRef<AbortController | null>(null)
  const commandRequest = useRef<AbortController | null>(null)
  const live = useRef(false)
  const locked = useRef(false)
  const hint = new URLSearchParams(search).get('error')
  const callbackError = hint ? errorMessage(hint === 'signup_expired' ? 'SESSION_EXPIRED' : hint) : ''
  useEffect(() => {
    live.current = true
    return () => { live.current = false; commandRequest.current?.abort() }
  }, [])
  useEffect(() => {
    const controller = new AbortController()
    request.current = controller
    void withDeadline(signal => service.read(signal), controller).then(value => {
      if (controller.signal.aborted) return
      setState(value)
      if (value.kind === 'authenticated' && path !== '/home') navigate('/home', true)
      else if (value.kind === 'registration' && !path.startsWith('/signup')) navigate('/signup', true)
      else if ((path === '/home' && value.kind !== 'authenticated') || (path.startsWith('/signup') && value.kind !== 'registration')) navigate(path.startsWith('/signup') ? '/login?error=signup_expired' : '/login', true)
    }).catch(error => {
      if (!controller.signal.aborted || error instanceof DeadlineExceeded) { setState({ kind: 'unavailable' }); setReadError(failureMessage(error)) }
    }).finally(() => { if (request.current === controller && (!controller.signal.aborted || controller.signal.reason instanceof DeadlineExceeded)) setChecking(false) })
    return () => controller.abort()
  }, [service, path, navigate, revision])
  const expiresAt = state.kind === 'registration' ? state.context?.expiresAt : state.kind === 'authenticated' ? state.session.expiresAt : undefined
  useEffect(() => {
    if (!expiresAt || checking || registered) return
    const timer = setTimeout(() => {
      if (state.kind === 'registration') { setState({ kind: 'guest' }); navigate('/login?error=signup_expired', true) }
      else { setChecking(true); setRevision(value => value + 1) }
    }, Math.max(0, Date.parse(expiresAt) - Date.now()))
    return () => clearTimeout(timer)
  }, [expiresAt, checking, registered, state.kind, navigate])
  async function command(kind: 'start' | 'logout') {
    if (locked.current) return
    if (kind === 'start' && state.kind === 'unavailable') { setMessage(readError || errorMessage('UNKNOWN_ERROR')); return }
    locked.current = true; setBusy(true)
    const controller = new AbortController(); commandRequest.current = controller
    try {
      await withDeadline(signal => kind === 'start' ? service.startGoogle(signal) : service.logout!(signal), controller)
      if (!controller.signal.aborted && kind === 'logout') { setState({ kind: 'guest' }); navigate('/login', true) }
    } catch (error) { if (!controller.signal.aborted || error instanceof DeadlineExceeded) setMessage(failureMessage(error)) }
    finally { locked.current = false; if (live.current) setBusy(false) }
  }
  const modal = <Modal open={!!message} state="information" title="요청을 완료하지 못했어요" description={message} onClose={() => setMessage('')} />
  if (!checking && state.kind === 'authenticated' && path === '/home') return <>{renderHome(state.session)}{service.logout && <Button intent="secondary" busy={busy} onClick={() => void command('logout')}>로그아웃</Button>}{modal}</>
  if (!checking && state.kind === 'registration' && path.startsWith('/signup')) {
    if (path === '/signup/owner' || path === '/signup/worker') return <>{renderRegistration(path.endsWith('/owner') ? 'owner' : 'worker', () => setRegistered(true))}{modal}</>
    return <><RoleSelectionScreen onBack={() => { if (service.logout) void command('logout'); else navigate('/login') }} onSelect={role => { if (!locked.current) navigate(`/signup/${role}`) }} />{modal}</>
  }
  if (checking) return <MobileLayout><LoadingState message="로그인 정보를 확인하고 있어요."/></MobileLayout>
  return <><LoginScreen onStart={() => void command('start')} busy={checking || busy} />
    {(readError || callbackError) && <p role="alert">{readError || callbackError}</p>}
    {readError && <Button intent="secondary" disabled={checking} onClick={() => { setChecking(true); setReadError(''); setRevision(value => value + 1) }}>다시 시도</Button>}
    {modal}
  </>
}
