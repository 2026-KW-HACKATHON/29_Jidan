import { expect,it } from 'vitest'
import { emptyJobDraft,validateJobDraft,validJobDate,type JobDraft } from './model'
const valid:JobDraft={...emptyJobDraft,title:'오픈',description:'포장',part:'주말 오픈',date:'2026-10-03',start:540,end:840,pay:'12000',payment:'근무 당일'}
it.each(['2026-02-30','0000-01-01','2026-13-01','abc'])('유효하지 않은 날짜 %s를 거부한다',value=>expect(validJobDate(value)).toBe(false))
it('윤년 날짜와 자정을 지나 종료하는 근무를 허용한다',()=>{expect(validJobDate('2028-02-29')).toBe(true);expect(validateJobDraft({...valid,start:1320,end:120,nextDay:true})).toEqual({})})
it.each([{start:540,end:540},{start:540,end:480},{start:540,end:-1},{start:541,end:840},{start:NaN,end:840},{start:60,end:120,nextDay:true}])('올바르지 않은 시간 범위를 거부한다',patch=>expect(validateJobDraft({...valid,...patch}).time).toBeDefined())
it.each(['0','-1','1.5','Infinity','1000000000',''])('잘못된 시급 %s를 거부한다',pay=>expect(validateJobDraft({...valid,pay}).pay).toBeDefined())
it('빈 값과 공백 입력 및 긴 문구를 거부하고 선택 입력은 생략할 수 있다',()=>{expect(Object.keys(validateJobDraft(emptyJobDraft))).toContain('title');expect(validateJobDraft({...valid,title:' ',description:' '.repeat(5)})).toMatchObject({title:expect.any(String),description:expect.any(String)});expect(validateJobDraft({...valid,title:'가'.repeat(101)}).title).toBeDefined();expect(validateJobDraft(valid)).toEqual({});expect(validateJobDraft(emptyJobDraft,2)).toEqual({})})
