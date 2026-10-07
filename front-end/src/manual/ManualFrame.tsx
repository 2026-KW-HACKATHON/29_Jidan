import type { ReactNode } from 'react'
import { MobileLayout } from '../ui/MobileLayout'
import { AppBar } from '../ui/AppBar'
import './Manual.css'
const labels=['1. 근무','2. 공통','3. 근무별','4. 보완','5. 확인']
export function ManualFrame({children,footer,onBack,stage=-1,showProgress=true,title='AI와 매뉴얼 만들기',compactFooter=false}:{children:ReactNode;footer?:ReactNode;onBack:()=>void;stage?:number;showProgress?:boolean;title?:string;compactFooter?:boolean}){
 return <MobileLayout className={`manual-screen ${showProgress?'':'manual-no-progress'} ${compactFooter?'manual-compact-footer':''}`} header={<><AppBar title={title} onBack={onBack}/>{showProgress&&<ol className="manual-progress" aria-label="매뉴얼 작성 진행">{labels.map((label,i)=><li key={label} aria-current={i===stage?'step':undefined} className={i<=stage?'manual-progress-active':''}><span>{label}</span><div/></li>)}</ol>}</>} footer={footer}>{children}</MobileLayout>
}
export function ManualCard({title,children,className=''}:{title:string;children:ReactNode;className?:string}){return <section className={`manual-card ${className}`}><h3>{title}</h3>{children}</section>}
