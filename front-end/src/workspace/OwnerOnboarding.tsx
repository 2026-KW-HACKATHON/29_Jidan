import {useMemo,useState} from 'react'
import {call} from '../api/operations'
import type {JobOnboarding} from '../api/types.generated'
import {ManualFrame,ManualCard} from '../manual/ManualFrame'
import {ManualPhoto} from '../manual/ManualPhoto'
import {liveManualService} from '../manual/live'
import {Button} from '../ui/Button'
import {Resource} from './Resource'
export function OwnerOnboarding({storeId,jobId,onBack}:{storeId:string;jobId:string;onBack:()=>void}){
 const load=useMemo(()=>(signal:AbortSignal)=>call('getConfirmedWorkerOnboarding',{signal,params:{storeId,jobId}}),[storeId,jobId])
 return <Resource load={load} onBack={onBack}>{onboarding=><Onboarding key={jobId} storeId={storeId} onboarding={onboarding} onBack={onBack}/>}</Resource>
}
function Onboarding({storeId,onboarding,onBack}:{storeId:string;onboarding:JobOnboarding;onBack:()=>void}){
 const [sectionId,setSectionId]=useState(''),service=useMemo(()=>liveManualService(storeId),[storeId])
 const load=useMemo(()=>async(signal:AbortSignal)=>sectionId?{detail:await call('getPublishedManualSection',{signal,params:{storeId,sectionId},query:{expectedVersionId:onboarding.manualVersionId!}})}:{list:await call('getPublishedManual',{signal,params:{storeId},query:{expectedVersionId:onboarding.manualVersionId!}})},[storeId,sectionId,onboarding.manualVersionId])
 const back=()=>sectionId?setSectionId(''):onBack()
 if(onboarding.manualStatus==='NOT_PUBLISHED')return <ManualFrame title="확정 근무 온보딩" showProgress={false} onBack={onBack}><p>아직 게시된 매뉴얼이 없어요.</p></ManualFrame>
 return <Resource load={load} onBack={back}>{value=><ManualFrame title="확정 근무 온보딩" showProgress={false} onBack={back}><div className="manual-stack"><p>{onboarding.workerName}님의 근무 안내</p>{onboarding.accessStatus==='ENDED'&&<p role="status">근무자의 매장 접근이 종료됐어요.</p>}{value.list?<>{value.list.sections.map(s=><Button key={s.id} intent="secondary" onClick={()=>setSectionId(s.id)}>{s.title}</Button>)}{!value.list.sections.length&&<p>등록된 업무가 없어요.</p>}</>:<ManualCard title={value.detail!.section.title}><ol>{value.detail!.section.steps.map(s=><li key={s.id}>{s.instruction}</li>)}</ol><div className="manual-photo-grid">{value.detail!.section.photos.map(p=><ManualPhoto key={p.mediaId} photo={p} service={service}/>)}</div></ManualCard>}</div></ManualFrame>}</Resource>
}
