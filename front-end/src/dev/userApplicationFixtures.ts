import type { ApplicationListItem } from '../jobs/user/UserApplyStatusPage'

export const applyingList: readonly ApplicationListItem[] = [
    { id: 1, title: '주말 오픈 대타', desc: '컴포즈커피 광운대점', time: '9월 26일 ㅣ 09:00 – 14:00', badgeText: '신청 중', badgeType: 'blue' },
    { id: 2, title: '평일 저녁 홀 대타', desc: '국수천왕 광운대본점', time: '9월 29일 ㅣ 18:00 – 22:00', badgeText: '신청 중', badgeType: 'blue' },
    { id: 3, title: '야간 매장 관리 대타', desc: '세븐일레븐 광운스퀘어점', time: '9월 30일 ㅣ 22:00 – 다음 날 02:00', badgeText: '신청 중', badgeType: 'blue' },
  ];

export const confirmedList: readonly ApplicationListItem[] = [
    { id: 1, title: '주말 마감 대타', desc: '명랑핫도그 광운대점', time: '9월 27일 ㅣ 18:00 – 22:00', badgeText: '확정', badgeType: 'green' },
  ];

export const endedList: readonly ApplicationListItem[] = [
    { id: 1, title: '오전 매장 정리', desc: 'GS25 월계성북역점', time: '9월 20일 ㅣ 09:00 – 12:00', badgeText: '미선정', badgeType: 'gray' },
    { id: 2, title: '주말 홀 대타', desc: '국수천왕 광운대본점', time: '9월 19일 ㅣ 12:00 – 17:00', badgeText: '신청 취소', badgeType: 'gray' },
  ];

