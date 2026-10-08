import { useState } from 'react'
import { MobileLayout } from '../ui/MobileLayout'
import { AppBar } from '../ui/AppBar'
import { InputField } from '../ui/Field'
import { Modal } from '../ui/Modal'
import { MemberNavigation } from '../home/MemberNavigation'
import { JobCard } from './JobCard'
import { JobFilterDialog } from './JobFilterDialog'
import { defaultFilters, filterJobs, type Job } from './model'
import './Jobs.css'
export function JobBrowse({jobs,today,onSelect,onHome,onProfile,onManual,initialQuery='',initialFilterOpen=false}: {
  jobs:readonly Job[];today:Date;onSelect:(job:Job)=>void;onHome:()=>void;onProfile:()=>void;onManual?:()=>void;initialQuery?:string;initialFilterOpen?:boolean
}) {
  const [query,setQuery]=useState(initialQuery),[filters,setFilters]=useState(defaultFilters)
  const [filterOpen,setFilterOpen]=useState(initialFilterOpen),[manualOpen,setManualOpen]=useState(false)
  const results=filterJobs(jobs,query,filters,today)
  const customFilters=filters.industry!==defaultFilters.industry || filters.date!==defaultFilters.date || filters.time!==defaultFilters.time
  return <><MobileLayout className="jobs-screen jobs-browse home-worker" header={<AppBar title="공고 찾기" compact onBack={onHome}/>} footer={<MemberNavigation active="jobs" onHome={onHome} onManual={onManual||(()=>setManualOpen(true))} onJobs={()=>{}} onProfile={onProfile}/>}>
    <div className="jobs-content"><h2>내 시간에 맞는 대타 찾기</h2><InputField label="매장·업무 검색" type="search" placeholder="매장명 또는 업무를 입력하세요" value={query} onChange={event=>setQuery(event.target.value)}/>
      <div className="jobs-results"><strong role="status">모집 중 공고 {results.length}개</strong><div className="jobs-results-options"><span>최신순</span><button type="button" className="jobs-filter-link" data-filtered={customFilters} aria-haspopup="dialog" aria-expanded={filterOpen} onClick={()=>setFilterOpen(true)}>필터</button></div></div>
      {results.map(job=><JobCard key={job.id} job={job} onSelect={onSelect}/>)}
      {!results.length && <p className="jobs-card jobs-secondary">조건에 맞는 공고가 없어요.<br/>검색어나 필터를 바꿔보세요.</p>}
    </div>
  </MobileLayout>
  {filterOpen && <JobFilterDialog filters={filters} onClose={()=>setFilterOpen(false)} onApply={value=>{setFilters(value);setFilterOpen(false)}}/>}
  <Modal open={manualOpen} title="매뉴얼 화면을 준비하고 있어요" description="현재 화면에서 계속 이용해 주세요." state="information" onClose={()=>setManualOpen(false)} cancelLabel="닫기"/>
  </>
}
