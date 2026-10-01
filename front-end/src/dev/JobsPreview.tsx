import { useState } from 'react'
import { JobBrowse } from '../jobs/JobBrowse'
import { Modal } from '../ui/Modal'
import { navigatePreview } from './navigation'
import { jobToday,sampleJobs } from './jobFixtures'
export default function JobsPreview() {
  const params=new URLSearchParams(location.search)
  const [selected,setSelected]=useState(false)
  return <><JobBrowse jobs={sampleJobs} today={jobToday} onSelect={()=>setSelected(true)} onHome={()=>navigatePreview('/__home/worker')} onProfile={()=>navigatePreview('/__profile/worker')} initialQuery={params.has('empty')?'없는 매장':''} initialFilterOpen={params.has('filter')}/><Modal open={selected} title="상세 화면을 준비하고 있어요" description="후속 PR에서 공고 상세와 연결합니다." onClose={()=>setSelected(false)}/></>
}
