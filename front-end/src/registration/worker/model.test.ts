import { expect, it } from 'vitest'
import { emptyAvailability, emptyCareer, emptyWorker, validDate, validateAvailability, validateCareer, validateWorker, weekHours, normalizedWorker } from './model'
it('윤년, 존재하지 않는 날짜와 미래 날짜를 검증한다', () => {
  expect(validDate('2000-02-29','2026-09-24')).toBe(true)
  for (const d of ['1900-02-29','2001-02-29','2026-04-31','2026-13-01','0000-01-01','2026-09-25','2026-1-1']) expect(validDate(d,'2026-09-24')).toBe(false)
})
it('빈 선택과 외국인 이름을 검증한다', () => {
  expect(Object.keys(validateWorker(emptyWorker,4))).toHaveLength(6)
  expect(validateWorker({...emptyWorker,name:"Anne-Marie O'Neill",phone:'010-1234-5678',birth:'2000-02-29',gender:'여성'},1)).toEqual({})
})
it('경력 시작, 종료와 재직 중 상태를 검증한다', () => {
  const c={...emptyCareer,industry:'카페' as const,duties:'응대',start:'2024-03',end:'2025-02'}
  expect(validateCareer(c,'2000-01-01','2026-09-24')).toEqual({})
  expect(validateCareer({...c,end:'2024-02'},'2000-01-01').end).toBeTruthy()
  expect(validateCareer({...c,start:'1999-01'},'2000-01-01').start).toBeTruthy()
  expect(validateCareer({...c,current:true,end:''},'2000-01-01')).toEqual({})
})
it('30분 단위, 빈 요일, 같은 시간, 24시간 초과를 거부한다', () => {
  for (const a of [emptyAvailability,{...emptyAvailability,days:[0],start:540,end:540},{...emptyAvailability,days:[0],start:541,end:600},{...emptyAvailability,days:[0],start:540,end:600,overnight:true}]) expect(Object.keys(validateAvailability(a)).length).toBeGreaterThan(0)
})
it('인접 시간은 허용하고 일요일 자정 넘어 월요일 겹침은 거부한다', () => {
  const a={id:'a',days:[6],start:1380,end:60,overnight:true}
  expect(validateAvailability(a)).toEqual({})
  expect(validateAvailability({id:'b',days:[0],start:0,end:30,overnight:false},[a]).time).toBeTruthy()
  expect(validateAvailability({id:'b',days:[0],start:60,end:90,overnight:false},[a])).toEqual({})
  expect(weekHours([a])).toBe(2)
})
it('중복 요일을 거부하고 신입 제출 시 보존된 경력을 제외한다', () => {
  expect(validateAvailability({id:'a',days:[0,0],start:0,end:30,overnight:false}).days).toBeTruthy()
  expect(normalizedWorker({...emptyWorker,experience:'신입',careers:[emptyCareer]}).careers).toEqual([])
})
