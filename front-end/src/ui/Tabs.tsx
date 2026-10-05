import { useRef } from 'react'
import './Tabs.css'
export function Tabs({labels,selected,onSelect,panelId,label}: {labels:readonly string[];selected:number;onSelect:(index:number)=>void;panelId:string;label:string}) {
 const buttons=useRef<(HTMLButtonElement|null)[]>([])
 return <div className="ds-ui ds-tabs" role="tablist" aria-label={label}>{labels.map((item,index)=><button type="button" role="tab" key={index} id={`${panelId}-tab-${index}`} aria-controls={panelId} aria-selected={selected===index} tabIndex={selected===index?0:-1} ref={element=>{buttons.current[index]=element}} onClick={()=>onSelect(index)} onKeyDown={event=>{let next:number;if(event.key==='ArrowRight')next=(index+1)%labels.length;else if(event.key==='ArrowLeft')next=(index-1+labels.length)%labels.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=labels.length-1;else return;event.preventDefault();onSelect(next);buttons.current[next]?.focus()}}>{item}</button>)}</div>
}
