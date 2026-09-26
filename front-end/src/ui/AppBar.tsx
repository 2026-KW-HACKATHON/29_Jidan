import back from './assets/back.svg'
import './tokens.css'
import './AppBar.css'

export function AppBar({ title, onBack, compact = false }: { title: string; onBack: () => void; compact?: boolean }) {
  return <div className={`ds-ui ds-app-bar ${compact ? 'ds-app-bar-compact' : ''}`}>
    <button type="button" onClick={onBack} aria-label="뒤로 가기"><img src={back} alt="" width="7" height="14" /></button>
    <h1>{title}</h1>
  </div>
}
