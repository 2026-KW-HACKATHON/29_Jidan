import { useState } from 'react'
import { PickerDialog } from '../ui/PickerDialog'
import { Button } from '../ui/Button'
import { Choice } from '../ui/Choice'
import { defaultFilters, type JobFilters } from './model'
import './Jobs.css'
export function JobFilterDialog({filters,onClose,onApply}: {filters:JobFilters;onClose:()=>void;onApply:(value:JobFilters)=>void}) {
  const [draft,setDraft]=useState(filters)
  function choices<K extends keyof JobFilters>(key:K,label:string,values:readonly JobFilters[K][]) {
    return <fieldset className={`jobs-filter-group jobs-filter-${key}`}><legend>{label}</legend><div>{values.map(value=><Choice key={value} name={`job-${key}`} value={value} checked={draft[key]===value} onChange={()=>setDraft({...draft,[key]:value})}>{value==='식당'?'음식점':value}</Choice>)}</div></fieldset>
  }
  return <PickerDialog title="공고 필터" onClose={onClose} className="jobs-filter-dialog">
    <div className="jobs-filter-content"><p className="jobs-secondary">월계1동 내 공고를 조건에 맞춰 찾아요.</p>
      {choices('industry','업종',['전체','카페','식당','편의점','기타'])}
      {choices('date','근무일',['전체','오늘','내일','일주일 이내','한달 이내'])}
      {choices('time','시간대',['전체','오전','오후','야간'])}
      <div className="jobs-actions"><Button intent="secondary" onClick={()=>setDraft({...defaultFilters})}>초기화</Button><Button onClick={()=>onApply(draft)}>공고 보기</Button></div>
    </div>
  </PickerDialog>
}
