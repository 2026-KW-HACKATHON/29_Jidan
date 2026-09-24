import { useCallback, useEffect, useState } from 'react'
import { AuthFlow, type AuthPath } from './auth/AuthFlow'
import { AppBar } from './ui/AppBar'
import { Button } from './ui/Button'
import { MobileLayout } from './ui/MobileLayout'
import { OwnerRegistration } from './registration/owner/OwnerRegistration'
import { WorkerRegistration } from './registration/worker/WorkerRegistration'
import { OwnerHome } from './home/OwnerHome'
import HealthScreen from './health/HealthScreen'

const authPaths: readonly string[] = ['/', '/login', '/signup', '/signup/owner', '/signup/worker', '/home']
const locationSnapshot = () => ({ path: window.location.pathname, search: window.location.search })

export default function App() {
  const [location, setLocation] = useState(locationSnapshot)
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
    if ((location.path === '/login' || location.path === '/') && location.search) window.history.replaceState(null, '', location.path)
  }, [location])

  if (location.path === '/status') return <HealthScreen />
  if (!authPaths.includes(location.path)) return <MobileLayout header={<AppBar title="페이지를 찾을 수 없어요" onBack={() => navigate('/')} />}><Button onClick={() => navigate('/')}>처음으로</Button></MobileLayout>

  return <AuthFlow key={`${location.path}${location.search}:${revision}`} path={location.path as AuthPath} search={location.search} navigate={navigate}
    renderHome={session => session.accountType === 'OWNER' ? <OwnerHome displayName={session.displayName} /> : <PendingScreen title="지단" description="로그인됐어요. 홈 화면을 준비하고 있어요." onBack={() => navigate('/login')} />}
    renderRegistration={role => role === 'owner' ? <OwnerRegistration onBack={() => navigate('/signup')} onExpired={() => navigate('/login?error=signup_expired', true)} /> : <WorkerRegistration onBack={() => navigate('/signup')} onExpired={() => navigate('/login?error=signup_expired', true)} onHome={() => navigate('/home')} />} />
}

/** Replaced by the dependent screen issues; never marks registration complete. */
function PendingScreen({ title, description, onBack }: { title: string; description: string; onBack: () => void }) {
  return <MobileLayout header={<AppBar title={title} onBack={onBack} />}><p role="status">{description}</p></MobileLayout>
}
