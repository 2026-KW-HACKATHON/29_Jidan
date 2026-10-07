import {useMemo,useState} from 'react'
import {call,mutation,pages} from '../api/operations'
import type {JobPosting,JobApplication,WorkerJobPosting,OwnerJobApplication} from '../api/types.generated'
import {jobFromApi,workerJobs,ownerJobs,jobCreation,jobClosure,applicantFromApi} from '../jobs/api'
import {JobBrowse} from '../jobs/JobBrowse'
import {JobDetail} from '../jobs/JobDetail'
import {OwnerJobList} from '../jobs/owner/OwnerJobList'
import {OwnerApplicants,type JobApplicant} from '../jobs/owner/OwnerApplicants'
import {JobRegistration} from '../jobs/owner/JobRegistration'
import {JobCloseFlow} from '../jobs/owner/JobCloseFlow'
import {WorkRequestFlow} from '../jobs/owner/WorkRequestFlow'
import {ApplicationDialog} from '../application/ApplicationDialog'
import {ApplicationComplete} from '../application/ApplicationComplete'
import {createApplicationService,applicationFromApi} from '../application/api'
import {Resource} from './Resource'
export type Route=(view:string,id?:string)=>void
export function WorkerJobs({view,id,route}:{view:string;id:string;route:Route}){
 const load=useMemo(()=>async(signal:AbortSignal)=>view==='application'?{application:await call('getMyJobApplication',{signal,params:{applicationId:id}})}:id?{job:await call('getJobPosting',{signal,params:{jobId:id}})}:{jobs:await workerJobs(signal)},[view,id])
 return <Resource load={load} onBack={()=>route('home')}>{data=>data.application?<ApplicationPage data={data.application} route={route}/>:data.job?<WorkerJobPage data={data.job} view={view} route={route}/>:<JobBrowse today={new Date()} jobs={data.jobs!.map(jobFromApi)} onSelect={job=>route('job',job.id)} onHome={()=>route('home')} onProfile={()=>route('profile')} onManual={()=>route('manual')}/>}</Resource>
}
function ApplicationPage({data,route}:{data:JobApplication;route:Route}){const service=useMemo(()=>createApplicationService(data),[data]);return <ApplicationComplete application={applicationFromApi(data)} service={service} onBack={()=>route('jobs')} onHome={()=>route('home')} onWithdrawn={()=>route('jobs')}/>}
function WorkerJobPage({data,view,route}:{data:WorkerJobPosting;view:string;route:Route}){
 const service=useMemo(()=>createApplicationService(),[]),job=jobFromApi(data)
 return <><JobDetail job={job} onBack={()=>route('jobs')} applyDisabled={!data.canApply&&!data.myApplicationId} applyLabel={data.myApplicationId?'지원 확인하기':data.canApply?'지원하기':'지원할 수 없는 공고예요'} onApply={()=>data.myApplicationId?route('application',data.myApplicationId):route('apply',data.id)}/>{view==='apply'&&data.canApply&&<ApplicationDialog job={job} service={service} onClose={()=>route('job',data.id)} onSuccess={a=>route('application',a.id)}/>}</>
}
export function OwnerJobs({view,id,storeId,storeName,route}:{view:string;id:string;storeId:string;storeName:string;route:Route}){
 const service=useMemo(()=>jobCreation(storeId),[storeId])
 const load=useMemo(()=>async(signal:AbortSignal)=>id?{job:await call('getOwnerJobPosting',{signal,params:{storeId,jobId:id}}),applicants:await pages(page=>call('listJobApplicants',{signal,params:{storeId,jobId:id},query:{page,size:100}}),signal)}:{jobs:await ownerJobs(storeId,signal)},[id,storeId])
 if(view==='create-job')return <JobRegistration service={service} onBack={()=>route('home')} onCreated={(job)=>route('job',job.id)}/>
 return <Resource load={load} onBack={()=>route('home')}>{data=>data.job?<OwnerJobPage data={data.job} applicants={data.applicants!} storeId={storeId} route={route}/>:<OwnerJobList jobs={data.jobs!.map(jobFromApi)} storeName={storeName} onBack={()=>route('home')} onSelect={j=>route('job',j.id)}/>}</Resource>
}
function OwnerJobPage({data,applicants,storeId,route}:{data:JobPosting;applicants:OwnerJobApplication[];storeId:string;route:Route}){
 const [closing,setClosing]=useState(false),[requesting,setRequesting]=useState<JobApplicant|null>(null)
 const closeService=useMemo(()=>jobClosure(storeId,data),[storeId,data])
 const requestService=useMemo(()=>{const write=mutation();return {request:async(jobId:string,applicationId:string,signal:AbortSignal)=>{await write('requestApplicantWork',{signal,params:{storeId,jobId,applicationId},input:{expectedJobRevision:data.revision}})}}},[storeId,data.revision])
 const job=jobFromApi(data)
 return <><OwnerApplicants job={job} applicants={applicants.map(applicantFromApi)} onBack={()=>route('jobs')} onCloseJob={()=>setClosing(true)} onRequest={setRequesting}/>{closing&&<JobCloseFlow job={job} service={closeService} onClose={()=>setClosing(false)} onUpdated={()=>{}} onCompleted={()=>route('jobs')}/>} {requesting&&<WorkRequestFlow job={job} applicant={requesting} service={requestService} onClose={()=>setRequesting(null)} onCompleted={()=>route('jobs')}/>}</>
}
