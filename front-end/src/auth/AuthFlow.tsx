import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { LoginScreen } from './LoginScreen'
import { RoleSelectionScreen, type SignupRole } from './RoleSelectionScreen'
import { Modal } from '../ui/Modal'
import { callbackMessage, hasSignupHint, readSession, SSO_START_PATH, type Session } from './session'

export type AuthPath = '/' | '/login' | '/signup' | '/signup/owner' | '/signup/worker' | '/home'
type Status = { kind: 'checking' | 'guest' | 'unavailable' } | { kind: 'authenticated'; session: Session }
type Message = 'cancelled' | 'failed' | 'expired' | null
const redirect = (path: string) => window.location.assign(path)

export function AuthFlow({ path, search = '', navigate, renderHome, renderRegistration, startSso = redirect }: {
  path: AuthPath
  search?: string
  navigate: (path: AuthPath | '/login?error=signup_expired', replace?: boolean) => void
  renderHome: (session: Session) => ReactNode
  renderRegistration: (role: SignupRole) => ReactNode
  startSso?: (path: string) => void
}) {
  const initialMessage: Message = new URLSearchParams(search).get('error') === 'signup_expired' ? 'expired' : callbackMessage(search)
  const [message, setMessage] = useState<Message>(initialMessage)
  const [status, setStatus] = useState<Status>({ kind: initialMessage ? 'unavailable' : 'checking' })
  const [starting, setStarting] = useState(false)
  const locked = useRef(false)
  const active = useRef(true)
  const request = useRef<AbortController | null>(null)

  const accept = useCallback((session: Session | null) => {
    setStatus(session ? { kind: 'authenticated', session } : { kind: 'guest' })
    if (session && path !== '/home') navigate('/home', true)
    if (!session && path === '/home') navigate('/login', true)
    if (!session && path.startsWith('/signup') && !hasSignupHint(document.cookie)) navigate('/login?error=signup_expired', true)
  }, [path, navigate])

  useEffect(() => {
    let current = true
    active.current = true
    const controller = new AbortController()
    request.current = controller
    if (!initialMessage) {
      void readSession(controller.signal).then(session => { if (current) accept(session) }).catch(() => {
        if (current) { setStatus({ kind: 'unavailable' }); if (path.startsWith('/signup') || path === '/home') setMessage('failed') }
      })
    }
    return () => { current = false; active.current = false; request.current?.abort() }
  }, [accept, initialMessage, path])

  async function start(fromModal = false) {
    if (locked.current) return
    locked.current = true
    setStarting(true)
    if (!fromModal) setMessage(null)
    const controller = new AbortController()
    request.current?.abort()
    request.current = controller
    try {
      const session = await readSession(controller.signal)
      if (!active.current) return
      if (session) accept(session)
      else startSso(SSO_START_PATH)
      // Remain locked during full-page navigation; pageshow remounts on bfcache return.
    } catch {
      if (active.current) {
        locked.current = false
        setStarting(false)
        setStatus({ kind: 'unavailable' })
        setMessage('failed')
        if (fromModal) throw new Error('SSO_FAILED')
      }
    }
  }

  if (status.kind === 'authenticated' && path === '/home') return renderHome(status.session)
  if (status.kind === 'guest' && path.startsWith('/signup') && hasSignupHint(document.cookie)) {
    if (path === '/signup/owner' || path === '/signup/worker') return renderRegistration(path.endsWith('/owner') ? 'owner' : 'worker')
    return <RoleSelectionScreen onBack={() => navigate('/login')} onSelect={role => navigate(`/signup/${role}`)} />
  }
  return <>
    <LoginScreen onStart={() => { void start() }} busy={starting || status.kind === 'checking'} />
    <Modal open={message !== null} state={message === 'cancelled' ? 'information' : 'error'}
      title={message === 'cancelled' ? '로그인이 취소됐어요' : message === 'expired' ? '다시 로그인해 주세요' : '로그인에 연결하지 못했어요'}
      description={message === 'cancelled' ? '원하실 때 SSO 계정으로 다시 시작할 수 있어요.' : message === 'expired' ? '가입을 계속하려면 SSO 인증을 다시 진행해 주세요.' : '잠시 후 다시 시도해 주세요.'}
      cancelLabel="닫기" confirmLabel={message === 'cancelled' ? '확인' : '다시 시도'}
      onClose={() => setMessage(null)} onConfirm={message === 'cancelled' ? undefined : () => start(true)} />
  </>
}
