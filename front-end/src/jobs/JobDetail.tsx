import type { ReactNode } from 'react'
import { MobileLayout } from '../ui/MobileLayout'
import { AppBar } from '../ui/AppBar'
import { Button } from '../ui/Button'
import { ApplicantsBadge } from './JobCard'
import { jobHours,jobTime,shortJobDate,won,type Job } from './model'
import './Jobs.css'
function DetailRow({label,children}: {label:string;children:ReactNode}) { return <div className="jobs-detail-row"><dt>{label}</dt><dd>{children}</dd></div> }
export function JobDetail({job,onBack,onApply,applyLabel='지원하기'}: {job:Job;onBack:()=>void;onApply:()=>void;applyLabel?:string}) {
  return <MobileLayout className="jobs-screen jobs-detail" header={<AppBar compact title="공고 상세" onBack={onBack}/>} footer={<Button onClick={onApply}>{applyLabel}</Button>}>
    <div className="jobs-content"><div><ApplicantsBadge count={job.applicants}/></div><h2>{job.title}</h2><p className="jobs-secondary">{job.storeName}{job.district ? ` · ${job.district}` : ''}</p>
      <section className="jobs-card jobs-detail-card" aria-label="급여 안내"><h3 className="jobs-pay-title">시급 {won(job.hourlyPay)}</h3><strong>{jobHours(job)}시간 기준 {won(jobHours(job)*job.hourlyPay)}</strong><p className="jobs-small jobs-secondary">휴게시간·실제 근무시간에 따라 달라질 수 있어요.</p></section>
      <section className="jobs-card jobs-detail-card"><h3>근무 조건</h3><dl className="jobs-detail-rows"><DetailRow label="근무일">{shortJobDate(job.date)}</DetailRow><DetailRow label="근무시간">{jobTime(job)}</DetailRow><DetailRow label="모집인원">{job.headcount}명</DetailRow><DetailRow label="경력">{job.experience}</DetailRow></dl></section>
      <section className="jobs-card jobs-detail-card"><h3>이런 일을 함께해요</h3><ul className="jobs-tasks">{job.tasks.map(task=><li key={task}>{task}</li>)}</ul><p className="jobs-manual-hint jobs-small jobs-secondary">처음 하는 업무는 매장 매뉴얼로 안내해 드려요.</p></section>
      <section className="jobs-card jobs-detail-card"><h3>근무 장소</h3><strong>{job.storeName}</strong><p className="jobs-secondary">{job.address}</p></section>
    </div>
  </MobileLayout>
}
