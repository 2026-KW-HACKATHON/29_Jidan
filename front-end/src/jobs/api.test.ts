import {expect,it} from 'vitest'
import {jobInput,applicantFromApi} from './api'
import {emptyJobDraft} from './owner/model'
it('공고 입력의 근무 파트·야간·지급 조건을 서버 enum으로 변환한다',()=>{
 expect(jobInput({...emptyJobDraft,title:' 야간 ',description:' 정리 ',date:'2026-10-10',start:1320,end:120,nextDay:true,pay:'12000',part:'주말 마감',experience:'1년 이상',payment:'별도 협의'})).toMatchObject({title:'야간',description:'정리',workPart:'WEEKEND_CLOSE',startTime:'22:00',endTime:'02:00',endsNextDay:true,hourlyPay:12000,minimumExperience:'YEAR_1',paymentTiming:'NEGOTIABLE'})
})
it('요청·철회·확정된 지원자에게 다시 근무 요청을 노출하지 않는다',()=>{
 for(const status of ['APPLIED','REQUESTED','WITHDRAWN','CONFIRMED','NOT_SELECTED','COMPLETED'] as const){const value=applicantFromApi({id:'a',jobId:'j',status,introduction:'소개',submittedAt:'2026-10-08T00:00:00Z',revision:3,applicant:{workerId:'w',name:'회원',ageAtSubmission:20,experienceLevel:'NEW',careers:[]}});expect(value.canRequest).toBe(status==='APPLIED');expect(value.experience).toBe('')}
})
