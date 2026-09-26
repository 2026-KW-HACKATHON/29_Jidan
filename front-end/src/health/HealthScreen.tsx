import { useEffect, useState } from 'react'
import './HealthScreen.css'

type Health = { status: 'ok'; environment: string; database: string }
type State = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; health: Health }

function isHealth(value: unknown): value is Health {
  if (!value || typeof value !== 'object') return false
  const health = value as Record<string, unknown>
  return health.status === 'ok'
    && ['local', 'dev', 'production'].includes(String(health.environment))
    && ['ok', 'not_configured'].includes(String(health.database))
}

export default function HealthScreen() {
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 8000)
    async function check() {
      try {
        const response = await fetch(`${import.meta.env.VITE_API_BASE_URL || '/api'}/health`, {
          signal: controller.signal,
        })
        if (!response.ok) throw new Error('Health check failed')
        const health: unknown = await response.json()
        if (!isHealth(health)) throw new Error('Invalid response')
        setState({ kind: 'ready', health })
      } catch {
        if (!disposed) setState({ kind: 'error' })
      } finally {
        clearTimeout(timeout)
      }
    }
    let disposed = false
    void check()
    return () => { disposed = true; clearTimeout(timeout); controller.abort() }
  }, [attempt])

  return (
    <main className="health-page">
      <p className="brand">Jidan</p>
      <h1>서비스를 준비하고 있어요.</h1>
      <p className="description">프론트엔드와 API 연결 상태를 확인할 수 있어요.</p>
      <section aria-label="서비스 상태" aria-live="polite">
        {state.kind === 'loading' && <p>연결 확인 중…</p>}
        {state.kind === 'error' && <p role="alert">API에 연결할 수 없어요. 잠시 후 다시 확인해 주세요.</p>}
        {state.kind === 'ready' && <>
          <p className="success">API 연결 정상</p>
          <dl>
            <div><dt>환경</dt><dd>{state.health.environment}</dd></div>
            <div><dt>데이터베이스</dt><dd>{state.health.database === 'ok' ? '연결 정상' : '로컬 미설정'}</dd></div>
          </dl>
        </>}
      </section>
      <button disabled={state.kind === 'loading'} onClick={() => {
        setState({ kind: 'loading' }); setAttempt(value => value + 1)
      }}>다시 확인</button>
    </main>
  )
}
