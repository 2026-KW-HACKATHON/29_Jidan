import { ownerJobFixtures } from './ownerJobFixtures'
import { shortJobDate,jobTime } from '../jobs/model'
import { navigatePreview } from './navigation'
import { OwnerHome, type OwnerHomeData } from '../home/OwnerHome'

const sample: OwnerHomeData = {
  store: { name: '명랑핫도그 광운대점', status: 'operating' },
  jobs: ownerJobFixtures.filter(job=>job.status==='recruiting').map(job=>({id:job.id,title:job.title,schedule:`${shortJobDate(job.date)} ㅣ ${jobTime(job)}`,applicants:job.applicants})),
  marks: [{ date: '2025-07-19' }, { date: '2025-07-22', kind: 'substitute' }, { date: '2025-07-26' }],
  events: [
    { id: '1', date: '2025-07-19', descriptions: ['큐카드 교체'] },
    { id: '2', date: '2025-07-22', descriptions: ['명랑핫도그 광운대점 - 김우동', '명랑핫도그 광운대점 - 김똥깡'], substitute: true },
    { id: '3', date: '2025-07-26', descriptions: ['본사 재고 전수조사'] },
  ],
  notificationCount: 3,
}

export default function OwnerHomePreview() {
  return <OwnerHome onSelectJob={job=>navigatePreview(`/__owner/jobs?view=applicants&id=${encodeURIComponent(job.id)}`)} onCreateJob={()=>navigatePreview('/__owner/jobs?view=create')} onJobs={()=>navigatePreview('/__owner/jobs')} onManage={()=>navigatePreview('/__store/manage')} displayName="000" data={new URLSearchParams(location.search).has('empty') ? { store: sample.store } : sample} initialDate={new Date(2025, 6, 19)} />
}
