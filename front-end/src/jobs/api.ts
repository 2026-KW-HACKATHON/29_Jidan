import {call,mutation,pages} from '../api/operations'
import type {JobPosting,JobPostingCreate,OwnerJobApplication} from '../api/types.generated'
import type {OwnerJob,JobDraft} from './owner/model'
import {clock} from './owner/model'
import type {JobApplicant} from './owner/OwnerApplicants'
const industries={RESTAURANT:'식당',CAFE:'카페',CONVENIENCE_STORE:'편의점',OTHER:'기타'} as const
const parts={WEEKDAY_OPEN:'평일 오픈',WEEKDAY_CLOSE:'평일 마감',WEEKEND_OPEN:'주말 오픈',WEEKEND_CLOSE:'주말 마감',OTHER:'기타'} as const
const experience={ANY:'무관',MONTHS_3:'3개월 이상',MONTHS_6:'6개월 이상',YEAR_1:'1년 이상'} as const
const payments={WORK_DAY:'근무 당일',NEXT_DAY:'근무 다음 날',NEGOTIABLE:'별도 협의'} as const
export function jobFromApi(j:JobPosting):OwnerJob{return {id:j.id,industry:industries[j.store.industry],title:j.title,storeName:j.store.name,address:j.store.address,district:j.store.neighborhood,date:j.workDate,start:j.startTime,end:j.endTime,nextDay:j.endsNextDay,hourlyPay:j.hourlyPay,headcount:j.recruitmentCount,applicants:j.applicantCount,publishedAt:j.createdAt,experience:experience[j.minimumExperience],tasks:j.description.split('\n'),status:j.status==='RECRUITING'?'recruiting':'closed',part:parts[j.workPart],description:j.description,qualifications:j.experienceNotes,payment:payments[j.paymentTiming],payNotice:j.paymentNotes}}
const code=<T extends Record<string,string>>(map:T,value:string)=>Object.keys(map).find(key=>map[key]===value) as keyof T
export function jobInput(d:JobDraft):JobPostingCreate{return {title:d.title.trim(),description:d.description.trim(),workPart:code(parts,d.part),workDate:d.date,startTime:clock(d.start),endTime:clock(d.end),endsNextDay:d.nextDay,minimumExperience:code(experience,d.experience),experienceNotes:d.qualifications.trim(),hourlyPay:Number(d.pay),paymentTiming:code(payments,d.payment),paymentNotes:d.payNotice.trim()}}
export const ownerJobs=(storeId:string,signal:AbortSignal)=>pages(page=>call('listOwnerJobPostings',{signal,params:{storeId},query:{page,size:100}}),signal)
export const workerJobs=(signal:AbortSignal)=>pages(page=>call('searchJobPostings',{signal,query:{page,size:100}}),signal)
export function jobCreation(storeId:string){const write=mutation();return {create:async(d:JobDraft,signal:AbortSignal)=>jobFromApi(await write('createJobPosting',{signal,params:{storeId},input:jobInput(d)}))}}
export function jobClosure(storeId:string,job:JobPosting){const write=mutation();return {close:async(_id:string,signal:AbortSignal)=>jobFromApi(await write('closeJobPosting',{signal,params:{storeId,jobId:job.id},input:{expectedRevision:job.revision}}))}}
export function applicantFromApi(a:OwnerJobApplication):JobApplicant{return {id:a.id,name:a.applicant.name,experience:a.applicant.careers.map(c=>`${industries[c.industry]} · ${c.startMonth}–${c.isCurrent?'현재':c.endMonth}`).join(', '),introduction:a.introduction,canRequest:a.status==='APPLIED'}}
