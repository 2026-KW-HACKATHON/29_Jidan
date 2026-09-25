import { OwnerHome, type OwnerHomeData } from '../home/OwnerHome'

const sample: OwnerHomeData = {
  store: { name: '명랑핫도그 광운대점', status: 'operating' },
  jobs: [
    { id: '1', title: '주말 오픈 대타', schedule: '7월 19일 ㅣ 09:00–14:00', applicants: 3 },
    { id: '2', title: '평일 마감 대타', schedule: '7월 22일 ㅣ 18:00–22:00', applicants: 1 },
    { id: '3', title: '토요일 풀타임', schedule: '7월 26일 ㅣ 10:00–18:00', applicants: 0 },
    { id: '4', title: '일요일 오전 대타', schedule: '7월 27일 ㅣ 09:00–14:00', applicants: 0 },
  ],
  marks: [{ date: '2025-07-19' }, { date: '2025-07-22', kind: 'substitute' }, { date: '2025-07-26' }],
  events: [
    { id: '1', date: '2025-07-19', descriptions: ['큐카드 교체'] },
    { id: '2', date: '2025-07-22', descriptions: ['명랑핫도그 광운대점 - 김우동', '명랑핫도그 광운대점 - 김똥깡'], substitute: true },
    { id: '3', date: '2025-07-26', descriptions: ['본사 재고 전수조사'] },
  ],
  notificationCount: 3,
}

export default function OwnerHomePreview() {
  return <OwnerHome displayName="000" data={new URLSearchParams(location.search).has('empty') ? {} : sample} initialDate={new Date(2025, 6, 19)} />
}
