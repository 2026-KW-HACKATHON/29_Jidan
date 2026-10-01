import {useState} from 'react'
import {JobRegistration} from '../jobs/owner/JobRegistration'
import {clock,emptyJobDraft,type JobDraft,type OwnerJob,type OwnerJobService} from '../jobs/owner/model'
import {JobDetail} from '../jobs/JobDetail'
import {navigatePreview,usePreviewLocation} from './navigation'
export const previewJobDraft:JobDraft={...emptyJobDraft,title:'주말 오픈 대타',description:'주문 접수와 상품 포장, 매장 정리를 맡아요.',part:'주말 오픈',date:'2026-09-27',start:540,end:840,pay:'12000',payment:'근무 당일'}
export function draftToPreviewJob(draft:JobDraft):OwnerJob{return {id:'preview-owner-job-'+crypto.randomUUID(),industry:'식당',title:draft.title,storeName:'명랑핫도그 광운대점',address:'서울 노원구 광운로 20',date:draft.date,start:clock(draft.start),end:clock(draft.end),nextDay:draft.nextDay,hourlyPay:Number(draft.pay),headcount:1,applicants:0,publishedAt:new Date().toISOString(),experience:draft.experience,tasks:draft.description.split('\n').filter(Boolean),status:'recruiting',part:draft.part,description:draft.description,qualifications:draft.qualifications,payment:draft.payment,payNotice:draft.payNotice}}
export default function OwnerJobsPreview(){
 const params=new URLSearchParams(usePreviewLocation().split('?')[1]),view=params.get('view')
 const [created,setCreated]=useState<OwnerJob|null>(()=>params.get('result')==='success'?draftToPreviewJob(previewJobDraft):null)
 const [service]=useState<OwnerJobService>(()=>{let fail=params.has('fail');return {create:async(draft,signal)=>{if(signal.aborted)throw Error('ABORTED');if(fail){fail=false;throw Error('MOCK_OWNER_JOB_FAILURE')}return draftToPreviewJob(draft)}}})
 const back=()=>navigatePreview('/__home/owner')
 if(view==='detail'&&created)return <JobDetail job={created} onBack={back} onApply={back} applyLabel="공고 목록 보기"/>
 const step=(Number(params.get('step'))||1) as 1|2|3
 return <JobRegistration key={params.get('case')||'create'} onBack={back} onCreated={(job,destination)=>{setCreated(job);if(destination==='list'){back();return}params.set('view','detail');navigatePreview(`/__owner/jobs?${params}`)}} initialReceipt={created??undefined} service={service} initialStep={[1,2,3].includes(step)?step:1} initialDraft={step>1||params.has('picker')||params.has('result')?previewJobDraft:emptyJobDraft} initialPicker={params.get('picker') as 'part'|'experience'|'payment'|null} initialResult={params.get('result') as 'success'|'failure'|undefined}/>
}
