import { MobileLayout } from '../ui/MobileLayout'
import { AppBar } from '../ui/AppBar'
import { Modal } from '../ui/Modal'
import { useState } from 'react'
import home from '../registration/owner/assets/home.svg'
import book from '../registration/owner/assets/book.svg'
import brief from '../registration/owner/assets/brief.svg'
import chevron from '../home/assets/chevron.svg'
import plus from './assets/plus.svg'
import type { EmploymentData } from './Employment'
import '../home/Home.css'
import './StoreManagement.css'
export type ManagedWorker=EmploymentData & {id:string;task:string}
export function StoreManagement({storeName,statistics,workers,pendingInvitations,onInvitations,onWorkers,onHome,onBack}: {storeName:string;statistics:{jobs:number;review:number;pending:number};workers:readonly ManagedWorker[];pendingInvitations:number;onInvitations:()=>void;onWorkers:()=>void;onHome:()=>void;onBack:()=>void}) {
 const [unavailable,setUnavailable]=useState<string|null>(null)
 const active=workers.filter(worker=>worker.status!=='ended')
 return <><MobileLayout className="store-management" header={<AppBar title="매장 관리" onBack={onBack}/>} footer={<nav className="home-bottom-nav" aria-label="주 메뉴"><div className="home-nav-items">{[{label:'홈',icon:home,action:onHome},{label:'매뉴얼',icon:book,action:()=>setUnavailable('매뉴얼')},{label:'공고 관리',icon:brief,action:()=>setUnavailable('공고 관리')}].map(({label,icon,action},index)=><button type="button" key={label} onClick={action} aria-current={index===0?'page':undefined}><img src={icon} alt=""/><span>{label}</span></button>)}</div><span className="home-indicator" aria-hidden="true"/></nav>}>
 <div className="store-content"><div className="store-heading"><h2>{storeName}</h2><p>우리 매장 운영을 한눈에 확인하세요.</p></div>
 <section className="store-card store-statistics"><h3>운영 현황 요약</h3><div>{[['모집 중 공고',statistics.jobs],['확인 필요',statistics.review],['승인 대기',statistics.pending]].map(([label,value],index)=><div key={label}><p>{label}</p><strong className={index===1?'store-primary':undefined}>{value}건</strong></div>)}</div></section>
 <section className="store-actions"><h3>근무자 관리</h3>{[{title:'근무자 초대',description:`대기 중 초대 ${pendingInvitations}건`,label:'초대 관리',action:onInvitations},{title:'근무 상태 관리',description:`재직 중 ${active.length}명 · 만료 예정 ${active.filter(worker=>worker.status==='expiring').length}명`,label:'관리하기',action:onWorkers}].map(item=><button type="button" className="store-card store-menu" key={item.title} onClick={item.action}><img src={plus} alt="" width="16" height="16"/><span><strong>{item.title}</strong><small>{item.description}</small><b>{item.label}</b></span><img src={chevron} alt="" width="5.833" height="11.667"/></button>)}</section></div>
 </MobileLayout><Modal open={unavailable!==null} title={`${unavailable} 화면을 준비하고 있어요`} description="현재 화면에서 계속 이용해 주세요." onClose={()=>setUnavailable(null)}/></>
}
function expiryLabel(expiry:string) {
 const match=/^\d{4}\.\s*(\d{1,2})\.\s*(\d{1,2})$/u.exec(expiry)
 return match?`${Number(match[1])}월 ${Number(match[2])}일까지`:expiry
}
export function WorkerList({storeName,workers,onSelect,onBack}: {storeName:string;workers:readonly ManagedWorker[];onSelect:(worker:ManagedWorker)=>void;onBack:()=>void}) {
 const active=workers.filter(worker=>worker.status!=='ended')
 return <MobileLayout className="store-management" header={<AppBar title="근무 상태 관리" onBack={onBack}/>}><div className="store-content"><div className="store-heading"><h2>근무자를 선택해 주세요</h2><p>{storeName} · 재직 중 {active.length}명</p></div><div className="store-worker-list">{workers.map(worker=><button type="button" className="store-card store-worker-item" key={worker.id} onClick={()=>onSelect(worker)}><span><strong>{worker.name}</strong><small>{worker.task}</small><b>{worker.status==='ended'?'접근 종료':worker.type==='대타 근무'?`${worker.type} - ${expiryLabel(worker.expiry)}`:worker.type}</b></span><img src={chevron} alt="" width="5.833" height="11.667"/></button>)}{!workers.length&&<p className="store-card">등록된 근무자가 없어요.</p>}</div></div></MobileLayout>
}
