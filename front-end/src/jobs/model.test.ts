import { describe, expect, it } from 'vitest'
import { defaultFilters, filterJobs, jobHours, jobTime, type Job } from './model'
const base: Job = { id:'1', industry:'카페', title:'오픈 대타', storeName:'광운 커피', address:'서울', date:'2026-12-31', start:'09:00', end:'14:00', nextDay:false, hourlyPay:12000, headcount:1, applicants:3, publishedAt:'2026-12-20T00:00:00Z', experience:'신입 가능', tasks:['음료 제조'] }
const find = (jobs: Job[], date: typeof defaultFilters.date, today: Date) => filterJobs(jobs, '', {...defaultFilters,date},today)
describe('승인된 공고 필터 기준', () => {
  it('지역 날짜로 오늘·내일을 연말 경계에서 판별한다', () => {
    const jobs = [base, {...base,id:'2',date:'2027-01-01'}]
    expect(find(jobs,'오늘',new Date(2026,11,31,23,59)).map(x=>x.id)).toEqual(['1'])
    expect(find(jobs,'내일',new Date(2026,11,31)).map(x=>x.id)).toEqual(['2'])
  })
  it('7일 범위에서 과거와 8일째를 제외한다', () => {
    const jobs = ['2026-12-30','2026-12-31','2027-01-06','2027-01-07'].map((date,i)=>({...base,id:String(i),date}))
    expect(find(jobs,'일주일 이내',new Date(2026,11,31)).map(x=>x.date)).toEqual(['2026-12-31','2027-01-06'])
  })
  it.each([[2026,'2026-02-27','2026-02-28'],[2028,'2028-02-28','2028-02-29']] as const)('다음 달 말일을 종료 경계로 보정한다 (%i)',(year,inside,outside)=>{
    expect(find([{...base,date:inside},{...base,id:'2',date:outside}],'한달 이내',new Date(year,0,31))).toHaveLength(1)
  })
  it.each([['05:59','야간'],['06:00','오전'],['11:59','오전'],['12:00','오후'],['17:59','오후'],['18:00','야간'],['23:59','야간']] as const)('시작 시각 %s는 %s', (start,time)=>{
    expect(filterJobs([{...base,start,nextDay:true}], '', {...defaultFilters,time},new Date())).toHaveLength(1)
    expect(filterJobs([{...base,start}], '', {...defaultFilters,time:time==='야간'?'오전':'야간'},new Date())).toHaveLength(0)
  })
  it('검색 단어와 업종·날짜·시간을 동시에 적용하고 원본을 변형하지 않는다',()=>{
    const jobs=[base,{...base,id:'2',industry:'식당' as const,publishedAt:'2026-12-21T00:00:00Z'}]
    expect(filterJobs(jobs,' 광운 음료 ',{industry:'카페',date:'오늘',time:'오전'},new Date(2026,11,31))).toEqual([base])
    expect(filterJobs(jobs,'',defaultFilters,new Date()).map(x=>x.id)).toEqual(['2','1'])
    expect(jobs[0]).toBe(base)
    expect(filterJobs(jobs,'없는 업무',defaultFilters,new Date())).toEqual([])
  })
  it('다음 날 종료의 근무 시간을 계산한다',()=>{
    const job={...base,start:'22:00',end:'02:30',nextDay:true}
    expect(jobHours(job)).toBe(4.5)
    expect(jobTime(job)).toBe('22:00–다음 날 02:30')
  })
})
