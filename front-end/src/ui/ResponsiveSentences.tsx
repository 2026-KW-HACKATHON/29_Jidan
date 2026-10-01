import { useLayoutEffect, useRef, useState } from 'react'
import './ResponsiveSentences.css'
/** Keep authored breaks only while every authored line fits on one line. */
export function ResponsiveSentences({lines}:{lines:readonly string[]}) {
 const root=useRef<HTMLSpanElement>(null),[joined,setJoined]=useState(false)
 useLayoutEffect(()=>{const element=root.current!;let alive=true;const measure=()=>{if(!alive)return;const width=element.getBoundingClientRect().width;setJoined([...element.querySelectorAll<HTMLElement>('.sentence-measure > span')].some(line=>line.getBoundingClientRect().width>width+0.5))};const observer=typeof ResizeObserver==='undefined'?null:new ResizeObserver(measure);observer?.observe(element);window.addEventListener('resize',measure);measure();void document.fonts?.ready.then(measure);document.fonts?.addEventListener('loadingdone',measure);return()=>{alive=false;observer?.disconnect();window.removeEventListener('resize',measure);document.fonts?.removeEventListener('loadingdone',measure)}},[lines])
 return <span className="responsive-sentences" ref={root}><span className="sentence-measure" aria-hidden="true">{lines.map((line,i)=><span key={i}>{line}</span>)}</span><span>{lines.map((line,i)=><span key={i}>{i>0&&(joined?' ':<br/>)}{line}</span>)}</span></span>
}
