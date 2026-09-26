import { useState } from 'react'
import { Button } from '../../ui/Button'
import { Choice } from '../../ui/Choice'
import { PickerDialog } from '../../ui/PickerDialog'
import { industries, type Industry } from './model'
import check from './assets/industry-check.svg'
import './IndustryPicker.css'

export function IndustryPicker({ value, onClose, onConfirm }: { value: Industry; onClose: () => void; onConfirm: (value: Industry) => void }) {
  const [selected, setSelected] = useState(value)
  return <PickerDialog className="owner-industry-picker" title="업종 선택" onClose={onClose}>
    <p>매장에 해당하는 업종을 선택해 주세요.</p>
    <div className="owner-industry-options" role="radiogroup" aria-label="업종">
      {industries.map(industry => <Choice key={industry} name="owner-industry" checked={selected === industry} onChange={() => setSelected(industry)}>
        <span>{industry}</span>{selected === industry && <img src={check} alt="" width="10.5" height="7.5" />}
      </Choice>)}
    </div>
    <div className="owner-industry-action"><Button disabled={!selected} onClick={() => onConfirm(selected)}>선택 완료</Button></div>
  </PickerDialog>
}
