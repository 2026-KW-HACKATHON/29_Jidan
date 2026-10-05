import { navigatePreview } from './navigation'
import { useState } from 'react'
import { createProfilePreviewService } from './profilePreviewService'
import { WorkerRegistration } from '../registration/worker/WorkerRegistration'
import { emptyWorker, type WorkerDraft } from '../registration/worker/model'
import type { WorkerService } from '../registration/worker/service'
/** DEV-only fixtures never set cookies or send registration requests. Normal entry starts empty. */
export default function WorkerRegistrationPreview(){
const [state]=useState(()=>{
const step=new URLSearchParams(location.search).get('step')
const fixture:WorkerDraft={name:'김지수',phone:'010-1234-5678',birth:'2001-03-14',gender:'여성',experience:step==='career'?'경력 있음':'신입',careers:step==='career'?[{id:'sample-career',industry:'카페',duties:'음료 제조 / 고객 응대',store:'',start:'2024-03',end:'2025-02',current:false}]:[],availability:[{id:'sample-time',days:[0,2,4],start:540,end:840,overnight:false}]}
const page:1|2|3|'review'|'complete'=step==='work'||step==='career'?2:step==='time'?3:step==='review'?'review':step==='complete'?'complete':1
let calls=0
const service:WorkerService={identity:async()=>({email:'member@example.com'}),submit:async()=>{if(new URLSearchParams(location.search).has('fail')&&calls++===0)throw Error('MOCK_FAILURE');return {id:'preview-worker-receipt',status:'COMPLETE'}}}
return {step,fixture,page,service}
});const {step,fixture,page,service}=state;
const [profileService]=useState(()=>createProfilePreviewService({draft:fixture,email:"member@example.com"}));return <WorkerRegistration service={service} profileService={profileService} initialDraft={step?fixture:emptyWorker} initialPage={page} onBack={()=>navigatePreview('/__auth/signup')} onExpired={()=>navigatePreview('/__auth/signup')} onHome={()=>navigatePreview('/__home/worker')} />}
