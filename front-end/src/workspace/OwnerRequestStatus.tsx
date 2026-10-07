import {useEffect,useMemo,useState} from 'react'
import {call} from '../api/operations'
import type {JobPosting,WorkRequest,OwnerJobApplication} from '../api/types.generated'
import {jobFromApi,applicantFromApi,jobClosure} from '../jobs/api'
import {OwnerApplicantPage} from '../jobs/owner/OwnerApplicantPage'
import {ApplicantReview} from '../jobs/owner/OwnerApplicants'
import {Resource} from './Resource'
import {OwnerRequestWithdrawal} from './OwnerRequestWithdrawal'
import {JobCloseFlow} from '../jobs/owner/JobCloseFlow'
import type {Route} from './Jobs'
export function OwnerRequestStatus({job,request,storeId,route,onReload}:{job:JobPosting;request:WorkRequest;storeId:string;route:Route;onReload:()=>void}){
 const load=useMemo(()=>(signal:AbortSignal)=>call('getJobApplicant',{signal,params:{storeId,jobId:job.id,applicationId:request.applicationId}}),[storeId,job.id,request.applicationId])
 return <Resource load={load} onBack={()=>route('jobs')}>{applicant=><Status key={request.id} job={job} request={request} applicant={applicant} storeId={storeId} route={route} onReload={onReload}/>}</Resource>
}
function Status({job,request,applicant,storeId,route,onReload}:{job:JobPosting;request:WorkRequest;applicant:OwnerJobApplication;storeId:string;route:Route;onReload:()=>void}){
 const [action,setAction]=useState(false),closeService=useMemo(()=>jobClosure(storeId,job),[storeId,job])
 const [review,setReview]=useState(false),[alert,setAlert]=useState(request.status==='EXPIRED'),[now,setNow]=useState(Date.now)
 useEffect(()=>{
  const clock=Date.now(),start=Date.parse(job.startAt)
  const deadline=Date.parse(request.status==='PENDING'?request.expiresAt:clock<start?job.startAt:job.endAt),delay=deadline-clock
  if(delay<=0)return
  const timer=setTimeout(()=>{setNow(Date.now());onReload()},Math.min(delay+50,2147483647))
  const minute=setInterval(()=>setNow(Date.now()),60000)
  return()=>{clearTimeout(timer);clearInterval(minute)}
 },[request.status,request.expiresAt,job.startAt,job.endAt,onReload])
 const state=request.status==='ACCEPTED'?'CONFIRMED':request.status==='EXPIRED'?'NO_RESPONSE':'WAITING'
 const minutes=Math.max(0,Math.floor((now-Date.parse(request.requestedAt))/60000))
 const windowMinutes=Math.max(1,Math.round((Date.parse(request.expiresAt)-Date.parse(request.requestedAt))/60000))
 const responseWindow=windowMinutes===60?'1시간':`${windowMinutes}분`
 const statusText=state==='WAITING'?`수락 대기 · ${minutes?`요청한 지 ${minutes}분`:'방금 요청'}`:state==='CONFIRMED'&&Date.parse(job.endAt)<=now?'근무 완료':undefined
 const model=jobFromApi(job),person=applicantFromApi(applicant)
 return <><OwnerApplicantPage state={state} job={model} applicant={person} responseWindow={responseWindow} statusText={statusText} showAlert={alert} actionDisabled={state==='CONFIRMED'&&Date.parse(job.startAt)<=now||state==='NO_RESPONSE'&&job.status!=='RECRUITING'} onAction={()=>setAction(true)} onBack={()=>route('jobs')} onViewApplication={()=>setReview(true)} onOtherApplicants={()=>route('applicants',job.id)} onOnboarding={()=>route('onboarding',job.id)} onDismissAlert={()=>setAlert(false)}/>{action&&(state==='NO_RESPONSE'?<JobCloseFlow job={model} service={closeService} onClose={()=>setAction(false)} onUpdated={()=>{}} onCompleted={()=>route('jobs')}/>:<OwnerRequestWithdrawal job={job} request={request} storeId={storeId} onClose={()=>setAction(false)} onCompleted={onReload} onReload={onReload}/>)}{review&&<ApplicantReview applicant={person} job={model} readOnly onClose={()=>setReview(false)} onRequest={()=>{}}/>}</>
}
