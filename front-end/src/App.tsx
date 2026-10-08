import { useCallback, useEffect, useState } from 'react'
import { AuthFlow, type AuthPath } from './auth/AuthFlow'
import { canonicalAuthPath } from './auth/routes'
import { OwnerPending } from './registration/owner/OwnerPending'
import type { AuthService } from './auth/session'
import { AppBar } from './ui/AppBar'
import { Button } from './ui/Button'
import { MobileLayout } from './ui/MobileLayout'
import { OwnerRegistration } from './registration/owner/OwnerRegistration'
import { WorkerRegistration } from './registration/worker/WorkerRegistration'
import { Workspace } from './workspace/Workspace'
import {captureInvitationLink} from './invitation/link'
import HealthScreen from './health/HealthScreen'

const authPaths: readonly string[] = ['/', '/login', '/signup', '/signup/owner', '/signup/worker', '/home']
const locationSnapshot = () => ({ path: canonicalAuthPath(window.location.pathname), search: window.location.search })

export default function App({ authService }: { authService?: AuthService } = {}) {
  const [location, setLocation] = useState(()=>{captureInvitationLink();return locationSnapshot()})
  const [revision, setRevision] = useState(0)
  const navigate = useCallback((path: string, replace = false) => {
    window.history[replace ? 'replaceState' : 'pushState'](null, '', path)
    setLocation(locationSnapshot())
  }, [])

  useEffect(() => {
    const changed = () => setLocation(locationSnapshot())
    const restored = (event: PageTransitionEvent) => {
      if (event.persisted) { changed(); setRevision(value => value + 1) }
    }
    window.addEventListener('popstate', changed)
    window.addEventListener('pageshow', restored)
    return () => { window.removeEventListener('popstate', changed); window.removeEventListener('pageshow', restored) }
  }, [])

  useEffect(() => {
    // Consume UI error hints once. OAuth code/state belong to the backend callback.
    if (canonicalAuthPath(window.location.pathname) !== window.location.pathname) window.history.replaceState(null, '', location.path)
    if ((location.path === '/login' || location.path === '/') && location.search) window.history.replaceState(null, '', location.path)
  }, [location])

  if (location.path === '/status') return <HealthScreen />
  if (!authPaths.includes(location.path)) return <MobileLayout header={<AppBar title="페이지를 찾을 수 없어요" onBack={() => navigate('/')} />}><Button onClick={() => navigate('/')}>처음으로</Button></MobileLayout>

  return <AuthFlow key={`${location.path}:${revision}`} service={authService} path={location.path as AuthPath} search={location.search} navigate={navigate}
    renderHome={session => session.nextAction === 'OWNER_APPROVAL_PENDING' && session.stores?.[0] ? <OwnerPending receipt={{id:session.stores[0].storeId,ownerName:session.displayName,storeName:session.stores[0].storeName,status:'PENDING'}} /> : <Workspace session={session} search={location.search} navigate={navigate} />}
    renderRegistration={(role, onRegistered) => role === 'owner' ? <OwnerRegistration onRegistered={() => { onRegistered(); navigate('/home') }} onBack={() => navigate('/signup')} onExpired={() => navigate('/login?error=signup_expired', true)} /> : <WorkerRegistration onRegistered={onRegistered} onBack={() => navigate('/signup')} onExpired={() => navigate('/login?error=signup_expired', true)} onHome={() => navigate('/home')} />} />
}
