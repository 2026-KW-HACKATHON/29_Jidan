import {useMemo} from 'react'
import {call,pages} from '../api/operations'
import {workerFromApi} from '../store/api'
import {StoreManagement,WorkerList} from '../store/StoreManagement'
import {Employment} from '../store/Employment'
import {ownerJobs} from '../jobs/api'
import {Resource} from './Resource'
import type {Route} from './Jobs'
export function StorePages({view,id,storeId,storeName,route}:{view:string;id:string;storeId:string;storeName:string;route:Route}){
 const load=useMemo(()=>async(signal:AbortSignal)=>view==='worker'?{worker:await call('getStoreWorker',{signal,params:{storeId,workerId:id}})}:{workers:await pages(page=>call('listStoreWorkers',{signal,params:{storeId},query:{page,size:100}}),signal),jobs:view==='store'?await ownerJobs(storeId,signal):[]},[view,id,storeId])
 const service=useMemo(()=>({end:async(key:string,signal:AbortSignal)=>{await call('revokeStoreWorkerAccess',{signal,key,params:{storeId,workerId:id}})}}),[storeId,id])
 return <Resource load={load} onBack={()=>route('home')}>{data=>data.worker?<Employment data={workerFromApi(data.worker)} service={service} onBack={()=>route('workers')}/>:view==='workers'?<WorkerList storeName={storeName} workers={data.workers!.map(workerFromApi)} onBack={()=>route('store')} onSelect={w=>route('worker',w.id)}/>:<StoreSummary storeId={storeId} storeName={storeName} workers={data.workers!.map(workerFromApi)} jobs={data.jobs!} route={route}/>}</Resource>
}
function StoreSummary({storeId,storeName,workers,jobs,route}:{storeId:string;storeName:string;workers:Parameters<typeof StoreManagement>[0]['workers'];jobs:Awaited<ReturnType<typeof ownerJobs>>;route:Route}){
 const load=useMemo(()=>(signal:AbortSignal)=>call('getStoreManagementSummary',{signal,params:{storeId}}),[storeId])
 return <Resource load={load} onBack={()=>route('home')}>{summary=><StoreManagement storeName={storeName} statistics={{jobs:jobs.filter(j=>j.status==='RECRUITING').length,review:jobs.reduce((n,j)=>n+j.applicantCount,0),pending:summary.pendingInvitationCount}} statisticLabels={['모집 중 공고','지원자','초대 대기']} workers={workers} pendingInvitations={summary.pendingInvitationCount} onInvitations={()=>route('invitations')} onWorkers={()=>route('workers')} onHome={()=>route('home')} onBack={()=>route('home')} onJobs={()=>route('jobs')} onManual={()=>route('manual')}/>}</Resource>
}
