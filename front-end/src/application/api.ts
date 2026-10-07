import {mutation} from '../api/operations'
import type {JobApplication} from '../api/types.generated'
import {jobFromApi} from '../jobs/api'
import type {Application,ApplicationService} from './model'
export const statuses={APPLIED:'지원 완료',REQUESTED:'근무 요청 도착',CONFIRMED:'근무 확정',WITHDRAWN:'지원 철회',NOT_SELECTED:'미선정',COMPLETED:'근무 완료'} as const
export function applicationFromApi(a:JobApplication):Application{return {id:a.id,job:jobFromApi(a.job),introduction:a.introduction,statusLabel:statuses[a.status],statusMessage:a.status==='APPLIED'?'점주님이 근무를 요청하면 알림으로 알려드려요.':a.status==='REQUESTED'?'근무 요청 알림에서 수락 여부를 선택해 주세요.':'최신 지원 상태를 확인해 주세요.',canWithdraw:a.status==='APPLIED'||a.status==='REQUESTED'}}
export function createApplicationService(current?:JobApplication):ApplicationService{
 const write=mutation()
 return {submit:async(job,introduction,signal)=>applicationFromApi(await write('applyForJobPosting',{signal,params:{jobId:job.id},input:{introduction}})),withdraw:async(id,signal)=>{if(!current||current.id!==id)throw Error('INVALID_APPLICATION');await write('withdrawJobApplication',{signal,params:{applicationId:id},input:{expectedRevision:current.revision}})}}
}
