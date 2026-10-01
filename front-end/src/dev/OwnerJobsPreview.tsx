import {useRef,useState} from 'react'
import {JobRegistration} from '../jobs/owner/JobRegistration'
import {clock,emptyJobDraft,type JobDraft,type OwnerJob,type OwnerJobService} from '../jobs/owner/model'
import {OwnerJobList} from '../jobs/owner/OwnerJobList'
import {JobCloseFlow} from '../jobs/owner/JobCloseFlow'
import {ownerJobFixtures} from './ownerJobFixtures'
import {JobDetail} from '../jobs/JobDetail'
import {navigatePreview,usePreviewLocation} from './navigation'
const previewJobDraft:JobDraft={...emptyJobDraft,title:'주말 오픈 대타',description:'주문 접수와 상품 포장, 매장 정리를 맡아요.',part:'주말 오픈',date:'2026-09-27',start:540,end:840,pay:'12000',payment:'근무 당일'}
function draftToPreviewJob(draft:JobDraft):OwnerJob{return {id:'preview-owner-job-'+crypto.randomUUID(),industry:'식당',title:draft.title,storeName:'명랑핫도그 광운대점',address:'서울 노원구 광운로 20',date:draft.date,start:clock(draft.start),end:clock(draft.end),nextDay:draft.nextDay,hourlyPay:Number(draft.pay),headcount:1,applicants:0,publishedAt:new Date().toISOString(),experience:draft.experience,tasks:draft.description.split('\n').filter(Boolean),status:'recruiting',part:draft.part,description:draft.description,qualifications:draft.qualifications,payment:draft.payment,payNotice:draft.payNotice}}
export default function OwnerJobsPreview(){
 const params=new URLSearchParams(usePreviewLocation().split('?')[1]),view=params.get('view')||'list'
 const [jobs,setJobs]=useState(()=>params.has('empty')?[]:ownerJobFixtures)
 const [created,setCreated]=useState<OwnerJob|null>(()=>params.get('result')==='success'?draftToPreviewJob(previewJobDraft):null)
 const [closing,setClosing]=useState(()=>params.get('overlay')==='close'||params.get('overlay')==='closed')
 const failureState=useRef({create:params.has('fail'),close:params.has('fail')})
 const service:OwnerJobService={create:async(draft,signal)=>{if(signal.aborted)throw Error('ABORTED');if(failureState.current.create){failureState.current.create=false;throw Error('MOCK_OWNER_JOB_FAILURE')}return draftToPreviewJob(draft)}}
 const closeService={close:async(id:string,signal:AbortSignal)=>{if(signal.aborted)throw Error('ABORTED');if(failureState.current.close){failureState.current.close=false;throw Error('MOCK_OWNER_JOB_FAILURE')}const job=jobs.find(job=>job.id===id);if(!job||job.status!=='recruiting')throw Error('JOB_NOT_RECRUITING');return {...job,status:'closed' as const}}}
 const route=(view:string,id?:string)=>{params.set('view',view);if(id)params.set('id',id);navigatePreview(`/__owner/jobs?${params}`)}
 const back=()=>navigatePreview('/__home/owner')
 const list=()=>route('list')
 const job=jobs.find(job=>job.id===params.get('id'))||created
 if(view==='create'){
  const step=(Number(params.get('step'))||1) as 1|2|3
  return <JobRegistration onBack={list} onCreated={(job,destination)=>{setCreated(job);setJobs(previous=>[job,...previous]);route(destination,job.id)}} initialReceipt={created??undefined} service={service} initialStep={[1,2,3].includes(step)?step:1} initialDraft={step>1||params.has('picker')||params.has('result')?previewJobDraft:emptyJobDraft} initialPicker={params.get('picker') as 'part'|'experience'|'payment'|null} initialResult={params.get('result') as 'success'|'failure'|undefined}/>
 }
 return <>{view==='detail'&&job?<JobDetail job={job} onBack={list} onApply={()=>{if(job.status==='recruiting')setClosing(true);else list()}} applyLabel={job.status==='recruiting'?'지원자 선정 없이 모집 마감':'공고 목록 보기'}/>:<OwnerJobList key={params.get('tab')} jobs={jobs} storeName="명랑핫도그 광운대점" onBack={back} onSelect={job=>route('detail',job.id)} initialTab={params.get('tab')==='closed'?'closed':'recruiting'}/>}
 {closing&&job&&<JobCloseFlow job={job} service={closeService} initialComplete={params.get('overlay')==='closed'} onClose={()=>setClosing(false)} onUpdated={updated=>setJobs(previous=>previous.map(job=>job.id===updated.id?updated:job))} onCompleted={()=>{setClosing(false);params.set('tab','closed');list()}}/>}</>
}
