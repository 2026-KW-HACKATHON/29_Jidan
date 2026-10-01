import { useState } from 'react'
import { JobBrowse } from '../jobs/JobBrowse'
import { JobDetail } from '../jobs/JobDetail'
import { Modal } from '../ui/Modal'
import { navigatePreview,usePreviewLocation } from './navigation'
import { jobToday,sampleJobs } from './jobFixtures'
export default function JobsPreview() {
  const url=usePreviewLocation(),params=new URLSearchParams(url.split('?')[1])
  const view=params.get('view'),job=sampleJobs.find(job=>job.id===params.get('id'))
  const [applyOpen,setApplyOpen]=useState(false)
  const fixture=new URLSearchParams();if(params.has('empty'))fixture.set('empty','1');if(params.has('filter'))fixture.set('filter','1')
  const base=fixture.size ? `/__jobs?${fixture}` : '/__jobs'
  const browse=()=>navigatePreview(base)
  return <div className="jobs-preview-flow">
    <div className="jobs-preview-browse" hidden={view==='detail' && !!job}><JobBrowse jobs={sampleJobs} today={jobToday} onSelect={job=>navigatePreview(`${base}${fixture.size?'&':'?'}view=detail&id=${encodeURIComponent(job.id)}`)} onHome={()=>navigatePreview('/__home/worker')} onProfile={()=>navigatePreview('/__profile/worker')} initialQuery={params.has('empty')?'없는 매장':''} initialFilterOpen={params.has('filter')}/></div>
    {view==='detail' && job && <JobDetail key={job.id} job={job} onBack={browse} onApply={()=>setApplyOpen(true)}/>}
    <Modal open={applyOpen} title="지원 화면을 준비하고 있어요" description="후속 PR에서 자기소개 작성과 연결합니다." onClose={()=>setApplyOpen(false)}/>
  </div>
}
