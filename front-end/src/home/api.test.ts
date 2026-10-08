import {expect,it} from 'vitest'
import {seoulDate,workerHomeData} from './api'
it('서울 자정과 연도 경계 기준으로 달력 날짜를 계산한다',()=>{expect(seoulDate('2026-12-31T14:59:59Z')).toBe('2026-12-31');expect(seoulDate('2026-12-31T15:00:00Z')).toBe('2027-01-01')})
it('0건과 빈 추천·일정을 그대로 표시한다',()=>{const data=workerHomeData({name:'회원',pendingApplicationCount:0,favoriteStoreCount:0,regularStoreCount:0,unreadNotificationCount:0,recommendedJobs:[],asOf:'2026-10-08T00:00:00Z'},{month:'2026-10',timezone:'Asia/Seoul',events:[],asOf:'2026-10-08T00:00:00Z'});expect(data.activity).toEqual({applications:0,favoriteStores:0,regularStores:0});expect(data.shifts).toEqual([])})
