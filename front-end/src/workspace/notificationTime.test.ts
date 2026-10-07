import {expect,it} from 'vitest'
import {notificationTime} from './notificationTime'
const now=Date.parse('2026-10-08T00:00:00Z')
it.each([['2026-10-08T00:00:01Z','방금 전'],['2026-10-07T23:59:01Z','방금 전'],['2026-10-07T23:59:00Z','1분 전'],['2026-10-07T23:30:00Z','30분 전'],['2026-10-07T23:00:00Z','1시간 전'],['2026-10-07T14:59:00Z','어제'],['2026-10-06T00:00:00Z','10월 6일'],['2025-10-06T00:00:00Z','2025년 10월 6일']])('알림 시각 %s의 경계·미래·한국 날짜를 표시한다',(created,label)=>expect(notificationTime(created,now)).toBe(label))
