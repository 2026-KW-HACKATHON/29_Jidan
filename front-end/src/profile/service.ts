import type {WorkerDraft} from '../registration/worker/model'
import {clockText} from '../registration/worker/model'
import {call,mutation} from '../api/operations'
import type {WorkerProfile,Industry,Career,Availability} from '../api/types.generated'
export type WorkerProfileData={draft:WorkerDraft;email:string}
export type ProfileService={read:(signal:AbortSignal)=>Promise<WorkerProfileData>;save:(value:WorkerProfileData,signal:AbortSignal,section?:1|2|3)=>Promise<WorkerProfileData|void>}
const industries:Record<Industry,WorkerDraft['careers'][number]['industry']>={RESTAURANT:'음식점',CAFE:'카페',CONVENIENCE_STORE:'편의점',OTHER:'기타'}
const codes:Record<string,Industry>={'음식점':'RESTAURANT','카페':'CAFE','편의점':'CONVENIENCE_STORE','기타':'OTHER'}
const days:Availability['days']=['MON','TUE','WED','THU','FRI','SAT','SUN']
const minutes=(time:string)=>Number(time.slice(0,2))*60+Number(time.slice(3,5))
export function profileFromApi(value:WorkerProfile):WorkerProfileData{return {email:value.identity.email,draft:{name:value.name,phone:value.phoneNumber,birth:value.birthDate,gender:value.gender==='MALE'?'남성':'여성',experience:value.experienceLevel==='NEW'?'신입':'경력 있음',careers:value.careers.map((c,index)=>({id:`career-${index}`,industry:industries[c.industry],duties:c.duties,store:c.storeName??'',start:c.startMonth,end:c.endMonth??'',current:c.isCurrent})),availability:value.availabilities.map((a,index)=>({id:`time-${index}`,days:a.days.map(day=>days.indexOf(day)),start:minutes(a.startTime),end:minutes(a.endTime),overnight:a.endsNextDay}))}}}
export function createProfileService():ProfileService{
 const write=mutation()
 return {read:async signal=>profileFromApi(await call('getMyWorkerProfile',{signal})),async save({draft:d},signal,section=1){
  if(section===1)return profileFromApi(await write('updateMyWorkerBasicProfile',{signal,input:{name:d.name.trim(),phoneNumber:d.phone.replace(/[\s-]/g,''),birthDate:d.birth,gender:d.gender==='남성'?'MALE':'FEMALE'}}))
  if(section===2){const careers:Career[]=d.experience==='신입'?[]:d.careers.map(c=>({industry:codes[c.industry],duties:c.duties.trim(),...(c.store.trim()?{storeName:c.store.trim()}:{}),startMonth:c.start,...(c.current?{isCurrent:true as const,endMonth:null}:{isCurrent:false as const,endMonth:c.end})}));return profileFromApi(await write('replaceMyWorkerCareers',{signal,input:{experienceLevel:d.experience==='신입'?'NEW':'EXPERIENCED',careers}}))}
  return profileFromApi(await write('replaceMyWorkerAvailabilities',{signal,input:{availabilities:d.availability.map(a=>({days:a.days.map(day=>days[day]),startTime:clockText(a.start),endTime:clockText(a.end),endsNextDay:a.overnight}))}}))
 }}
}
export const profileService=createProfileService()
