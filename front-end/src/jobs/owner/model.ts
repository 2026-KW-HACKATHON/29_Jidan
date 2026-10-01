import type { Job } from '../model'
export const workParts=['평일 오픈','평일 마감','주말 오픈','주말 마감','기타'] as const
export const experienceOptions=['무관','3개월 이상','6개월 이상','1년 이상'] as const
export const paymentOptions=['근무 당일','근무 다음 날','별도 협의'] as const
export type JobDraft={title:string;description:string;part:typeof workParts[number]|'';date:string;start:number;end:number;nextDay:boolean;experience:typeof experienceOptions[number];qualifications:string;pay:string;payment:typeof paymentOptions[number]|'';payNotice:string}
export const emptyJobDraft:JobDraft={title:'',description:'',part:'',date:'',start:-1,end:-1,nextDay:false,experience:'무관',qualifications:'',pay:'',payment:'',payNotice:''}
export type OwnerJob=Job & {status:'recruiting'|'closed'|'selected';part:string;description:string;qualifications:string;payment:string;payNotice:string}
export type OwnerJobService={create:(draft:JobDraft,signal:AbortSignal)=>Promise<OwnerJob>}
export const unavailableOwnerJobs:OwnerJobService={create:async()=>{throw Error('OWNER_JOBS_NOT_CONFIGURED')}}
export function validJobDate(value:string){if(!/^\d{4}-\d{2}-\d{2}$/.test(value)||value<'0001-01-01')return false;const d=new Date(`${value}T00:00:00Z`);return Number.isFinite(d.getTime())&&d.toISOString().slice(0,10)===value}
export function clock(minutes:number){return `${String(Math.floor(minutes/60)).padStart(2,'0')}:${String(minutes%60).padStart(2,'0')}`}
export function validateJobDraft(d:JobDraft,step?:1|2|3){
 const errors:Record<string,string>={}
 if(!step||step===1){
  if(!d.title.trim()||d.title.trim().length>100)errors.title='담당 업무명을 1~100자로 입력해 주세요.'
  if(!d.description.trim()||d.description.trim().length>2000)errors.description='업무 상세 설명을 1~2,000자로 입력해 주세요.'
  if(!workParts.includes(d.part as typeof workParts[number]))errors.part='근무 파트를 선택해 주세요.'
  if(!validJobDate(d.date))errors.date='올바른 근무 날짜를 선택해 주세요.'
  const duration=d.end+(d.nextDay?1440:0)-d.start
  if(![d.start,d.end].every(v=>Number.isInteger(v)&&v>=0&&v<1440&&v%30===0)||duration<=0||duration>1440)errors.time='시작 이후의 종료 시간을 30분 단위로 선택해 주세요.'
 }
 if(!step||step===2){if(!experienceOptions.includes(d.experience))errors.experience='최소 경력 조건을 선택해 주세요.';if(d.qualifications.length>2000)errors.qualifications='추가 요건은 2,000자 이내로 입력해 주세요.'}
 if(!step||step===3){if(!/^\d{1,9}$/.test(d.pay)||Number(d.pay)<=0)errors.pay='시급을 올바른 양의 정수로 입력해 주세요.';if(!paymentOptions.includes(d.payment as typeof paymentOptions[number]))errors.payment='지급 시점을 선택해 주세요.';if(d.payNotice.length>2000)errors.payNotice='기타 보수 안내는 2,000자 이내로 입력해 주세요.'}
 return errors
}
