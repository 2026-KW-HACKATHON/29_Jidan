import { jobHours, jobTime, won, shortJobDate, type Job } from './model'
import './Jobs.css'
export function ApplicantsBadge({count}: {count:number}) { return <span className={`jobs-badge ${count ? 'jobs-applicants' : 'jobs-neutral'}`}>{count ? `지원자 ${count}명` : '지원자 없음'}</span> }
export function JobCard({job,onSelect}: {job:Job;onSelect:(job:Job)=>void}) {
  return <button type="button" className="jobs-card jobs-opportunity" onClick={()=>onSelect(job)}>
    <span className="jobs-card-heading"><span className="jobs-badge jobs-industry">{job.industry==='식당'?'음식점':job.industry}</span><strong>{job.title}</strong><ApplicantsBadge count={job.applicants}/></span>
    <span className="jobs-secondary">{job.storeName}</span>
    <span>{shortJobDate(job.date)} / {jobTime(job)}</span>
    <span className="jobs-pay-row"><strong>시급 {won(job.hourlyPay)}</strong><span>{jobHours(job)}시간 / {job.headcount}명</span></span>
  </button>
}
