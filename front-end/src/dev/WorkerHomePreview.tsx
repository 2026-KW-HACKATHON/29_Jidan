import { navigatePreview } from './navigation'
import { WorkerArea } from '../profile/WorkerArea'
import { useState } from 'react'
import { createProfilePreviewService } from './profilePreviewService'
import { type WorkerHomeData } from '../home/WorkerHome'

const sample: WorkerHomeData = {
  activity: { applications: 3, favoriteStores: 5, regularStores: 2 },
  recommendations: [
    { id: '1', title: '컴포즈커피 광운대80주년기념관점', schedule: '카페 ㅣ 7월 19일 ㅣ 09:00 – 14:00', status: '지원자 3명' },
    { id: '2', title: '국수천왕 광운대본점', schedule: '식당 ㅣ 7월 22일 ㅣ 18:00 – 22:00', status: '지원자 없음' },
    { id: '3', title: '컴포즈커피 광운대80주년기념관점', schedule: '편의점 ㅣ 7월 26일 ㅣ 10:00 – 18:00', status: '지원자 2명' },
  ],
  marks: [{ date: '2025-07-19' }, { date: '2025-07-22', kind: 'substitute' }, { date: '2025-07-28' }],
  shifts: [
    { id: '1', date: '2025-07-19', storeName: 'GS25 월계성북역점', kind: 'regular' },
    { id: '2', date: '2025-07-22', storeName: '명랑핫도그 광운대점', kind: 'substitute' },
    { id: '3', date: '2025-07-26', storeName: 'GS25 월계성북역점', kind: 'regular' },
  ],
  notificationCount: 3,
}

export default function WorkerHomePreview() {
  const [service]=useState(()=>createProfilePreviewService())
  return <WorkerArea onApplications={()=>navigatePreview('/__user/status')} onNotifications={()=>navigatePreview('/__user/noti')} onJobs={()=>navigatePreview('/__jobs')} service={service} displayName="김지수" data={new URLSearchParams(location.search).has('empty') ? {} : sample} initialDate={new Date(2025, 6, 19)} />
}
