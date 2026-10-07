import {mutation} from '../api/operations'
import type {JobApplication} from '../api/types.generated'
import {jobFromApi} from '../jobs/api'
import type {Application,ApplicationService} from './model'
export const statuses={APPLIED:'지원 완료',REQUESTED:'근무 요청 도착',CONFIRMED:'근무 확정',WITHDRAWN:'지원 철회',NOT_SELECTED:'미선정',COMPLETED:'근무 완료'} as const
export function applicationFromApi(a:JobApplication):Application{return {id:a.id,job:jobFromApi(a.job),introduction:a.introduction,statusLabel:statuses[a.status],canWithdraw:a.status==='APPLIED'||a.status==='REQUESTED'}}
export function createApplicationService(current?:JobApplication):ApplicationService{
 const write=mutation()
 return {submit:async(job,introduction,signal)=>applicationFromApi(await write('applyForJobPosting',{signal,params:{jobId:job.id},input:{introduction}})),withdraw:async(id,signal)=>{if(!current||current.id!==id)throw Error('INVALID_APPLICATION');await write('withdrawJobApplication',{signal,params:{applicationId:id},input:{expectedRevision:current.revision}})}}
}
