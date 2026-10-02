import {useState,type ReactNode} from 'react'
import {MobileLayout} from '../../ui/MobileLayout'
import {AppBar} from '../../ui/AppBar'
import {Button} from '../../ui/Button'
import {shortJobDate,jobTime} from '../model'
import type {OwnerJob} from './model'
import './OwnerJobs.css'
export function OwnerJobSummary({job,children}:{job:OwnerJob;children?:ReactNode}){return <div className="owner-job-summary"><strong>{job.title}</strong><p>{job.storeName}<br/>{shortJobDate(job.date)} · {jobTime(job)}</p>{children}</div>}
export function OwnerJobList({jobs,storeName,onBack,onSelect,initialTab='recruiting'}:{jobs:readonly OwnerJob[];storeName:string;onBack:()=>void;onSelect:(job:OwnerJob)=>void;initialTab?:'recruiting'|'closed'}){
 const [tab,setTab]=useState(initialTab)
 const open=jobs.filter(job=>job.status==='recruiting'),closed=jobs.filter(job=>job.status!=='recruiting'),visible=(tab==='recruiting'?open:closed).toSorted((a,b)=>b.publishedAt.localeCompare(a.publishedAt)||a.id.localeCompare(b.id))
 return <MobileLayout className="owner-jobs owner-job-management" header={<AppBar compact title="공고 관리" onBack={onBack}/>}><div className="owner-job-list"><div className="owner-job-heading"><h2>등록한 공고를 확인하세요</h2><p>{storeName}</p></div><div className="owner-job-tabs" role="group" aria-label="공고 상태"><Button intent={tab==='recruiting'?'primary':'secondary'} aria-pressed={tab==='recruiting'} onClick={()=>setTab('recruiting')}>모집 중 {open.length}</Button><Button intent={tab==='closed'?'primary':'secondary'} aria-pressed={tab==='closed'} onClick={()=>setTab('closed')}>마감 {closed.length}</Button></div><div className="owner-job-list-meta"><strong>{tab==='recruiting'?'모집 중 공고':'마감된 공고'} {visible.length}건</strong><span>최근 등록순</span></div><div className="owner-job-cards">{visible.map(job=><button type="button" className="owner-job-card" key={job.id} onClick={()=>onSelect(job)}><span className="owner-job-card-heading"><strong>{job.title}</strong><span className={`owner-job-badge ${job.status==='selected'?'is-selected':job.status==='closed'||!job.applicants?'is-muted':''}`}>{job.status==='selected'?'선정 완료':job.status==='closed'?'모집 종료':job.applicants?`지원자 ${job.applicants}명`:'지원자 없음'}</span></span><span className="owner-job-card-date">{shortJobDate(job.date)} ㅣ {jobTime(job)}</span></button>)}{!visible.length&&<p className="owner-job-card" role="status">{tab==='recruiting'?'모집 중인 공고가 없어요.':'마감된 공고가 없어요.'}</p>}</div></div></MobileLayout>
}
