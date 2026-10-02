import {useRef,useState} from 'react'
import {JobRegistration} from '../jobs/owner/JobRegistration'
import {clock,emptyJobDraft,type JobDraft,type OwnerJob,type OwnerJobService} from '../jobs/owner/model'
import {OwnerApplicants,type JobApplicant} from '../jobs/owner/OwnerApplicants'
import {Modal} from '../ui/Modal'
import {OwnerJobList} from '../jobs/owner/OwnerJobList'
import {JobCloseFlow} from '../jobs/owner/JobCloseFlow'
import {ownerJobFixtures} from './ownerJobFixtures'
import {JobDetail} from '../jobs/JobDetail'
import {navigatePreview,usePreviewLocation} from './navigation'
const previewJobDraft:JobDraft={...emptyJobDraft,title:'주말 오픈 대타',description:'주문 접수와 상품 포장, 매장 정리를 맡아요.',part:'주말 오픈',date:'2026-09-27',start:540,end:840,pay:'12000',payment:'근무 당일'}
function draftToPreviewJob(draft:JobDraft):OwnerJob{return {id:'preview-owner-job-'+crypto.randomUUID(),industry:'식당',title:draft.title,storeName:'명랑핫도그 광운대점',address:'서울 노원구 광운로 20',date:draft.date,start:clock(draft.start),end:clock(draft.end),nextDay:draft.nextDay,hourlyPay:Number(draft.pay),headcount:1,applicants:0,publishedAt:new Date().toISOString(),experience:draft.experience,tasks:draft.description.split('\n').filter(Boolean),status:'recruiting',part:draft.part,description:draft.description,qualifications:draft.qualifications,payment:draft.payment,payNotice:draft.payNotice}}
export default function OwnerJobsPreview(){
 const params=new URLSearchParams(usePreviewLocation().split('?')[1]),view=params.get('view')||'list'
 const [requesting,setRequesting]=useState<JobApplicant|null>(null)
 const [jobs,setJobs]=useState(()=>params.has('empty')?[]:ownerJobFixtures.map(job=>params.get('overlay')==='closed'&&job.id===(params.get('id')||'open')?{...job,status:'closed' as const}:job))
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
  return <JobRegistration onBack={params.get('from')==='home'?back:list} onCreated={(job,destination)=>{setCreated(job);setJobs(previous=>[job,...previous]);route(destination,job.id)}} initialReceipt={created??undefined} service={service} initialStep={[1,2,3].includes(step)?step:1} initialDraft={step>1||params.has('picker')||params.has('result')?previewJobDraft:emptyJobDraft} initialPicker={params.get('picker') as 'part'|'experience'|'payment'|null} initialResult={params.get('result') as 'success'|'failure'|undefined}/>
 }
 const sampleApplicants:JobApplicant[]=[{id:'kim',name:'김래원',experience:'식당 근무 · 2년',introduction:'식당에서 주문 접수와 홀 응대를 담당했어요.\n바쁜 시간에도 차분하게 손님을 응대하겠습니다.'},{id:'lee',name:'이민하',experience:'',introduction:'안내받은 업무를 성실하게 배우겠습니다.'},{id:'park',name:'박지원',experience:'카페 근무 · 6개월',introduction:'음료 제조와 고객 응대 경험이 있어요.\n안내받은 순서대로 꼼꼼하게 일하겠습니다.'}]
 const applicants=params.has('noApplicants')?[]:sampleApplicants.slice(0,job?.applicants||0)
 return <>{view==='applicants'&&job?<OwnerApplicants key={job.id} job={job} applicants={applicants} onBack={list} onCloseJob={()=>setClosing(true)} onRequest={setRequesting} initialApplicantId={params.get('review')||undefined} initialReadOnly={params.has('readonly')}/>:view==='detail'&&job?<JobDetail job={job} onBack={list} onApply={()=>{if(job.status==='recruiting')setClosing(true);else list()}} applyLabel={job.status==='recruiting'?'지원자 선정 없이 모집 마감':'공고 목록 보기'}/>:<OwnerJobList key={params.get('tab')} jobs={jobs} storeName="명랑핫도그 광운대점" onBack={back} onSelect={job=>route('applicants',job.id)} initialTab={params.get('tab')==='closed'?'closed':'recruiting'}/>}
 <Modal open={requesting!==null} title="근무 요청은 다음 단계에서 진행해요" description={`${requesting?.name||''} 님의 지원서는 확인했어요. 근무 요청 확인·전송 흐름은 후속 작업에서 연결합니다.`} onClose={()=>setRequesting(null)}/>
 {closing&&job&&<JobCloseFlow job={job} service={closeService} initialComplete={params.get('overlay')==='closed'} onClose={()=>setClosing(false)} onUpdated={updated=>setJobs(previous=>previous.map(job=>job.id===updated.id?updated:job))} onCompleted={()=>{setClosing(false);params.set('tab','closed');list()}}/>}</>
}
