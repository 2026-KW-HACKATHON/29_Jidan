import { useEffect, useRef, useState, type ReactNode } from 'react'
import { LoginScreen } from './LoginScreen'
import { RoleSelectionScreen, type SignupRole } from './RoleSelectionScreen'
import { Modal } from '../ui/Modal'
import { authService, type AuthService, type AuthState, type Session } from './session'
export type AuthPath = '/' | '/login' | '/signup' | '/signup/owner' | '/signup/worker' | '/home'
export function AuthFlow({ path, navigate, renderHome, renderRegistration, service = authService }: {
  path: AuthPath; search?: string
  navigate: (path: AuthPath | '/login?error=signup_expired', replace?: boolean) => void
  renderHome: (session: Session) => ReactNode
  renderRegistration: (role: SignupRole) => ReactNode
  service?: AuthService
}) {
  const [state, setState] = useState<AuthState>({ kind: 'unavailable' })
  const [checking, setChecking] = useState(true)
  const [message, setMessage] = useState(false)
  const [busy, setBusy] = useState(false)
  const request = useRef<AbortController | null>(null)
  const locked = useRef(false)
  useEffect(() => {
    const controller = new AbortController()
    request.current = controller
    void service.read(controller.signal).then(value => {
      if (controller.signal.aborted) return
      setState(value)
      if (value.kind === 'authenticated' && path !== '/home') navigate('/home', true)
      else if ((path === '/home' && value.kind !== 'authenticated') || (path.startsWith('/signup') && value.kind !== 'registration')) navigate('/login', true)
    }).catch(() => { if (!controller.signal.aborted) setState({ kind: 'unavailable' }) })
      .finally(() => { if (!controller.signal.aborted) setChecking(false) })
    return () => { controller.abort(); request.current?.abort() }
  }, [service, path, navigate])
  async function start() {
    if (locked.current) return
    if (state.kind === 'unavailable') { setMessage(true); return }
    locked.current = true; setBusy(true)
    const controller = new AbortController(); request.current = controller
    try { await service.startGoogle(controller.signal) }
    catch { if (!controller.signal.aborted) setMessage(true) }
    finally { if (!controller.signal.aborted) { locked.current = false; setBusy(false) } }
  }
  if (state.kind === 'authenticated' && path === '/home') return renderHome(state.session)
  if (state.kind === 'registration' && path.startsWith('/signup')) {
    if (path === '/signup/owner' || path === '/signup/worker') return renderRegistration(path.endsWith('/owner') ? 'owner' : 'worker')
    return <RoleSelectionScreen onBack={() => navigate('/login')} onSelect={role => navigate(`/signup/${role}`)} />
  }
  return <><LoginScreen onStart={() => void start()} busy={checking || busy} />
    <Modal open={message} state="information" title="Google 로그인 연결을 준비하고 있어요" description="인증 API가 준비되면 Google 계정으로 시작할 수 있어요." onClose={() => setMessage(false)} />
  </>
}
