import back from './assets/back.svg'
import './tokens.css'
import './AppBar.css'

export function AppBar({ title, onBack, compact = false, backIcon = back }: { title: string; onBack: () => void; compact?: boolean; backIcon?: string }) {
  return <div className={`ds-ui ds-app-bar ${compact ? 'ds-app-bar-compact' : ''}`}>
    <button type="button" onClick={onBack} aria-label="뒤로 가기"><img src={backIcon} alt="" width="7" height="14" /></button>
    <h1>{title}</h1>
  </div>
}
