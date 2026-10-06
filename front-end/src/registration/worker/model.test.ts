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
  expect(validateCareer({...c,start:'0000-01'},'2000-01-01').start).toBeTruthy()
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
it('그리드 편집은 심야 슬롯을 보존하고 동일한 일간 시간대를 묶는다', async () => {
  const { availabilityFromSlots, slots } = await import('./model')
  const values=[{id:'n',days:[6],start:1380,end:60,overnight:true},{id:'d',days:[0,2,4],start:540,end:840,overnight:false}]
  const selected=new Set(values.flatMap(slots))
  expect(new Set(availabilityFromSlots(selected).flatMap(slots))).toEqual(selected)
  expect(weekHours(availabilityFromSlots(selected))).toBe(17)
})

it('정확히 24시간은 허용하고 초과·0시간·역순은 구분한다', () => {
  const a = { id: 'day', days: [0], start: 540, end: 540, overnight: true }
  expect(validateAvailability(a)).toEqual({})
  expect(validateAvailability({ ...a, end: 570 }).time).toBe('근무 가능 시간은 하루 24시간을 초과할 수 없어요.')
  expect(validateAvailability({ ...a, overnight: false }).time).toBe('종료 시간과 다음 날 종료 여부를 확인해 주세요.')
  expect(validateAvailability({ ...a, end: 510, overnight: false }).time).toBe('종료 시간과 다음 날 종료 여부를 확인해 주세요.')
})

it('명세의 휴대전화와 담당 업무 길이 및 배열 상한을 적용한다',()=>{
 for(const phone of ['01112345678','0101234567'])expect(validateWorker({...emptyWorker,phone},1).phone).toBeTruthy()
 const career={...emptyCareer,industry:'카페' as const,duties:'a'.repeat(300),start:'2024-01',current:true}
 expect(validateCareer(career,'2000-01-01').duties).toBeUndefined();expect(validateCareer({...career,duties:'a'.repeat(301)},'2000-01-01').duties).toBeTruthy()
 expect(validateWorker({...emptyWorker,experience:'경력 있음',careers:Array(21).fill(career)},2).careers).toBeTruthy()
 expect(validateWorker({...emptyWorker,availability:Array(101).fill({id:'a',days:[0],start:0,end:30,overnight:false})},3).availability).toBeTruthy()
})

it('신입 제출에서는 보존된 임시 경력의 상한을 검사하지 않는다', () => {
 const draft={...emptyWorker,experience:'신입' as const,careers:Array(21).fill(emptyCareer)}
 expect(validateWorker(draft,2)).toEqual({})
 expect(validateWorker(draft,4).careers).toBeUndefined()
 expect(normalizedWorker(draft).careers).toEqual([])
})
