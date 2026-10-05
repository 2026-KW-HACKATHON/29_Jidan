import { useId,useState } from 'react'
import { Button } from './Button'
import { Choice } from './Choice'
import { PickerDialog } from './PickerDialog'
import check from '../registration/owner/assets/industry-check.svg'
import './SingleSelectDialog.css'
export function SingleSelectDialog<T extends string>({title,description,options,value,onClose,onConfirm}:{title:string;description:string;options:readonly T[];value:T|'';onClose:()=>void;onConfirm:(value:T)=>void}){
 const [selected,setSelected]=useState<T|''>(value),name=useId()
 return <PickerDialog className="ds-single-select-picker" title={title} onClose={onClose}><p>{description}</p><div className="ds-single-select-options" role="radiogroup" aria-label={title}>{options.map(option=><Choice key={option} name={name} checked={selected===option} onChange={()=>setSelected(option)}><span>{option}</span>{selected===option&&<img src={check} alt="" width="10.5" height="7.5"/>}</Choice>)}</div><div className="ds-single-select-action"><Button disabled={!selected} onClick={()=>{if(selected)onConfirm(selected)}}>선택 완료</Button></div></PickerDialog>
}
