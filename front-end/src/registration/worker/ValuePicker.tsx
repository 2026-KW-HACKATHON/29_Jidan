import { useLayoutEffect, useRef, useState } from 'react'
import { PickerDialog } from '../../ui/PickerDialog'
import { Button } from '../../ui/Button'
import { today } from './model'
import './ValuePicker.css'

function Wheel({ label, value, min, max, suffix, change }: { label: string; value: number; min: number; max: number; suffix: string; change: (n: number) => void }) {
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
  const select = (n: number) => change(Math.max(min, Math.min(max, n)))
  return <div className="worker-wheel-column"><p>{label}</p><div className="worker-wheel-shell"><div ref={ref} className="worker-wheel" role="spinbutton" tabIndex={0} aria-label={label} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} aria-valuetext={`${value}${suffix}`}
    onScroll={e => { const n = Math.max(min, Math.min(max, min + Math.round(e.currentTarget.scrollTop / 40))); if (n !== value) { scrolledValue.current = n; select(n) } }}
    onKeyDown={e => { const n = e.key === 'ArrowDown' ? value + 1 : e.key === 'ArrowUp' ? value - 1 : e.key === 'Home' ? min : e.key === 'End' ? max : null; if (n !== null) { e.preventDefault(); select(n) } }}>
    {Array.from({ length: max - min + 1 }, (_, i) => min + i).map(n => <div key={n} className={n === value ? 'is-selected' : ''} onClick={() => select(n)}>{String(n).padStart(2, '0')}{suffix}</div>)}
  </div></div></div>
}
export function ValuePicker({ title, value, type, min, max, overnight, onClose, onSave }: { title: string; value: string; type: 'month'|'time'; min?: string; max?: string; overnight?: boolean; onClose: () => void; onSave: (value: string, overnight?: boolean) => void }) {
  const lower = min || '0001-01', upper = max || today().slice(0, 7)
  const initial = value && value >= lower && value <= upper ? value : upper
  const [year, setYear] = useState(Number(initial.slice(0, 4)))
  const [month, setMonth] = useState(Number(initial.slice(5, 7)))
  const [time, setTime] = useState(value === '' ? 0 : Number(value))
  const [nextDay, setNextDay] = useState(overnight ?? false)
  const minMonth = year === Number(lower.slice(0, 4)) ? Number(lower.slice(5, 7)) : 1
  const maxMonth = year === Number(upper.slice(0, 4)) ? Number(upper.slice(5, 7)) : 12
  const selectedMonth = Math.max(minMonth, Math.min(maxMonth, month))
  return <PickerDialog className="worker-value-picker" title={title} onClose={onClose}><div className="worker-picker-content"><div className="worker-picker-columns">
    {type === 'month' ? <><Wheel label="연도" value={year} min={Number(lower.slice(0, 4))} max={Number(upper.slice(0, 4))} suffix="년" change={setYear} /><Wheel label="월" value={selectedMonth} min={minMonth} max={maxMonth} suffix="월" change={setMonth} /></> : <><Wheel label="시 · 24시간제" value={Math.floor(time / 60)} min={0} max={23} suffix="시" change={h => setTime(h * 60 + time % 60)} /><div className="worker-wheel-column"><p>분</p><div className="worker-minute-choices">{[0, 30].map(m => <Button key={m} intent="secondary" aria-pressed={time % 60 === m} onClick={() => setTime(Math.floor(time / 60) * 60 + m)}>{String(m).padStart(2, '0')}분</Button>)}</div></div></>}
  </div>
  {overnight !== undefined && <label className="worker-picker-overnight"><input type="checkbox" checked={nextDay} onChange={e => setNextDay(e.target.checked)} /><span className="worker-picker-checkbox" aria-hidden="true" />다음 날 종료</label>}
  <div className="worker-picker-action"><Button onClick={() => { onSave(type === 'month' ? `${year.toString().padStart(4, '0')}-${String(selectedMonth).padStart(2, '0')}` : String(time), nextDay); onClose() }}>선택 완료</Button></div>
  </div></PickerDialog>
}
