import { useState } from 'react'
import { PickerDialog } from '../../ui/PickerDialog'
import { Button } from '../../ui/Button'
import { today } from './model'
import './ValuePicker.css'
import { WheelPicker } from '../../ui/WheelPicker'

export function ValuePicker({ title, value, type, min, max, overnight, current, onClose, onSave }: { title: string; value: string; type: 'month'|'time'; min?: string; max?: string; overnight?: boolean; current?: boolean; onClose: () => void; onSave: (value: string, selected?: boolean) => void }) {
  const lower = min || '0001-01', upper = max || today().slice(0, 7)
  const initial = value && value >= lower && value <= upper ? value : upper
  const [year, setYear] = useState(Number(initial.slice(0, 4)))
  const [month, setMonth] = useState(Number(initial.slice(5, 7)))
  const [time, setTime] = useState(value === '' ? 0 : Number(value))
  const [nextDay, setNextDay] = useState(overnight ?? false)
  const [stillWorking, setStillWorking] = useState(current ?? false)
  const minMonth = year === Number(lower.slice(0, 4)) ? Number(lower.slice(5, 7)) : 1
  const maxMonth = year === Number(upper.slice(0, 4)) ? Number(upper.slice(5, 7)) : 12
  const selectedMonth = Math.max(minMonth, Math.min(maxMonth, month))
  return <PickerDialog className="worker-value-picker" title={title} onClose={onClose}><div className="worker-picker-content">
  <div className="worker-picker-columns">
    {type === 'month' ? <><WheelPicker label="연도" value={year} min={Number(lower.slice(0, 4))} max={Number(upper.slice(0, 4))} suffix="년" change={setYear} disabled={stillWorking} /><WheelPicker label="월" value={selectedMonth} min={minMonth} max={maxMonth} suffix="월" change={setMonth} disabled={stillWorking} /></> : <><WheelPicker label="시 · 24시간제" value={Math.floor(time / 60)} min={0} max={23} suffix="시" change={h => setTime(h * 60 + time % 60)} disabled={nextDay} /><div className={`ds-wheel-column${nextDay ? ' is-disabled' : ''}`}><p>분</p><div className="worker-minute-choices">{[0, 30].map(m => <Button key={m} intent="secondary" aria-pressed={time % 60 === m} disabled={nextDay} onClick={() => setTime(Math.floor(time / 60) * 60 + m)}>{String(m).padStart(2, '0')}분</Button>)}</div></div></>}
  </div>
  {current !== undefined && <label className="worker-picker-option"><input type="checkbox" checked={stillWorking} onChange={e => setStillWorking(e.target.checked)} /><span className="worker-picker-checkbox" aria-hidden="true" />현재 근무 중</label>}
  {overnight !== undefined && <label className="worker-picker-option"><input type="checkbox" checked={nextDay} onChange={e => setNextDay(e.target.checked)} /><span className="worker-picker-checkbox" aria-hidden="true" />다음 날 종료</label>}
  <div className="worker-picker-action"><Button onClick={() => { onSave(type === 'month' ? `${year.toString().padStart(4, '0')}-${String(selectedMonth).padStart(2, '0')}` : String(time), type === 'month' ? stillWorking : nextDay); onClose() }}>선택 완료</Button></div>
  </div></PickerDialog>
}
