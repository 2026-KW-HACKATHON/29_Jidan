import {useState} from 'react'
import {JobBrowse} from '../jobs/JobBrowse'
import {JobDetail} from '../jobs/JobDetail'
import {ApplicationDialog} from '../application/ApplicationDialog'
import {ApplicationComplete} from '../application/ApplicationComplete'
import type {Application} from '../application/model'
import {navigatePreview,usePreviewLocation} from './navigation'
import {jobToday,sampleJobs} from './jobFixtures'
import {createApplicationPreviewService} from './applicationPreviewService'
const exampleIntroduction='음료 제조와 고객 응대 경험이 있어요.\n안내받은 순서대로 꼼꼼하게 일하겠습니다.'
export default function JobsPreview() {
  const url=usePreviewLocation(),params=new URLSearchParams(url.split('?')[1])
  const view=params.get('view'),job=sampleJobs.find(job=>job.id===params.get('id'))
  const [applications,setApplications]=useState<Record<string,Application>>(()=>view==='complete' && job ? {[job.id]:{id:'preview-application-1',job,introduction:exampleIntroduction}} : {})
  const [service]=useState(()=>createApplicationPreviewService(Object.values(applications),params.has('fail')))
  const fixture=new URLSearchParams();for(const key of ['empty','filter','case','fail'])if(params.has(key))fixture.set(key,params.get(key)!)
  const base=fixture.size?`/__jobs?${fixture}`:'/__jobs'
  const browse=()=>navigatePreview(base)
  const route=(view:string,id:string)=>navigatePreview(`${base}${fixture.size?'&':'?'}view=${view}&id=${encodeURIComponent(id)}`)
  const current=job?applications[job.id]:undefined
  const displayedJobs=sampleJobs.map(value=>({...value,applicants:value.applicants+(applications[value.id]?1:0)}))
  const home=()=>navigatePreview('/__home/worker')
  return <div className="jobs-preview-flow">
    <div className="jobs-preview-browse" hidden={!!job && ['detail','apply','complete'].includes(view || '')}><JobBrowse jobs={displayedJobs} today={jobToday} onSelect={job=>route('detail',job.id)} onHome={home} onProfile={()=>navigatePreview('/__profile/worker')} initialQuery={params.has('empty')?'없는 매장':''} initialFilterOpen={params.has('filter') && !view}/></div>
    {job && (view==='detail' || view==='apply' && !current || view==='complete' && !current) && <JobDetail key={job.id} job={displayedJobs.find(value=>value.id===job.id)!} onBack={browse} onApply={()=>route(current?'complete':'apply',job.id)} applyLabel={current?'지원 확인하기':'지원하기'}/>}
    {view==='apply' && job && !current && <ApplicationDialog key={job.id} job={job} service={service} onClose={()=>route('detail',job.id)} initialIntroduction={params.get('case')==='written'?exampleIntroduction:''} onSuccess={application=>{setApplications(previous=>({...previous,[job.id]:application}));route('complete',job.id)}}/>}
    {job && current && (view==='complete' || view==='apply') && <ApplicationComplete key={current.id} application={current} service={service} onBack={browse} onHome={home} initialWithdrawOpen={params.get('case')==='withdraw' || params.get('case')==='withdraw-fail'} onWithdrawn={()=>{setApplications(previous=>{const next={...previous};delete next[job.id];return next});browse()}}/>}
  </div>
}
