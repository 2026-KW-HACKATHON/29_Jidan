import { useLayoutEffect, useRef } from 'react'
import './WheelPicker.css'

export function WheelPicker({ label, value, min, max, suffix, change, disabled = false }: { label: string; value: number; min: number; max: number; suffix: string; change: (n: number) => void; disabled?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  // Native scrolling owns the offset while the user is moving the wheel.
  // Only external changes (keyboard/click/year bounds) should reposition it.
  const scrolledValue = useRef<number | null>(null)
  useLayoutEffect(() => {
    if (scrolledValue.current === value) { scrolledValue.current = null; return }
    const frame = requestAnimationFrame(() => {
      if (ref.current) ref.current.scrollTop = (value - min) * 40
    })
    return () => cancelAnimationFrame(frame)
  }, [value, min])
  const select = (n: number) => { if (!disabled) change(Math.max(min, Math.min(max, n))) }
  return <div className={`ds-wheel-column${disabled ? ' is-disabled' : ''}`}><p>{label}</p><div className="ds-wheel-shell"><div ref={ref} className="ds-wheel" role="spinbutton" tabIndex={disabled ? -1 : 0} aria-disabled={disabled || undefined} aria-label={label} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} aria-valuetext={`${value}${suffix}`}
    onScroll={e => { if (disabled) return; const n = Math.max(min, Math.min(max, min + Math.round(e.currentTarget.scrollTop / 40))); if (n !== value) { scrolledValue.current = n; select(n) } }}
    onKeyDown={e => { const n = e.key === 'ArrowDown' ? value + 1 : e.key === 'ArrowUp' ? value - 1 : e.key === 'Home' ? min : e.key === 'End' ? max : null; if (n !== null) { e.preventDefault(); select(n) } }}>
    {Array.from({ length: max - min + 1 }, (_, i) => min + i).map(n => <div key={n} className={n === value ? 'is-selected' : ''} onClick={() => select(n)}>{String(n).padStart(2, '0')}{suffix}</div>)}
  </div></div></div>
}
