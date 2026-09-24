import { useState } from 'react'
import { PickerDialog } from '../../ui/PickerDialog'
import { InputField } from '../../ui/Field'
import { Button } from '../../ui/Button'
import { clockText } from './model'
/** Native controls inside the shared dialog; no separate calendar/time overlay is specified in Figma. */
export function ValuePicker({ title, value, type, min, max, onClose, onSave }: { title: string; value: string; type: 'month'|'time'; min?: string; max?: string; onClose: () => void; onSave: (value: string) => void }) {
  const [next,setNext]=useState(value)
  return <PickerDialog title={title} onClose={onClose}><div className="worker-fields">
    {type==='month' ? <InputField label={title} type="month" placeholder="YYYY-MM" value={next} min={min} max={max} onChange={e=>setNext(e.target.value)} /> : <label className="ds-field">{title}<select className="ds-field-control" value={next} onChange={e=>setNext(e.target.value)}><option value="">시간 선택</option>{Array.from({length:48},(_,i)=><option key={i} value={i*30}>{clockText(i*30)}</option>)}</select></label>}
    <Button disabled={!next} onClick={()=>{onSave(next);onClose()}}>선택 완료</Button>
  </div></PickerDialog>
}
