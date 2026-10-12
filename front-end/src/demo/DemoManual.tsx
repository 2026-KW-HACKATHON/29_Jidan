import {useMemo} from 'react'
import {call} from '../api/operations'
import {ApiError} from '../api/client'
import {ManualFrame,ManualCard} from '../manual/ManualFrame'
import {ManualPhoto} from '../manual/ManualPhoto'
import {liveManualService} from '../manual/live'
import {Button} from '../ui/Button'
import {Resource} from '../workspace/Resource'
import type {DemoRole} from './routes'

export function DemoManual({storeId,id,role,navigate}:{storeId:string;id:string;role:DemoRole;navigate:(url:string)=>void}){
 const service=useMemo(()=>liveManualService(storeId),[storeId])
 const load=useMemo(()=>async(signal:AbortSignal)=>{
  try{
   if(id)return {kind:'section' as const,data:await call('getPublishedManualSection',{signal,params:{storeId,sectionId:id}})}
   return {kind:'list' as const,data:await call('getPublishedManual',{signal,params:{storeId}})}
  }catch(error){if(role==='OWNER'&&error instanceof ApiError&&error.code==='MANUAL_NOT_PUBLISHED')return {kind:'empty' as const};throw error}
 },[storeId,id,role])
 const back=()=>navigate(role==='OWNER'?'/author':'/manual')
 return <Resource load={load} onBack={back}>{value=><ManualFrame showProgress={false} title={role==='OWNER'?'완성된 매뉴얼 미리보기':'매장 매뉴얼'} onBack={()=>id?navigate('/manual'):back()}
  footer={<Button onClick={()=>navigate(role==='OWNER'?'/author':'/questions')}>{role==='OWNER'?'매뉴얼 작성 계속하기':'AI에게 업무 질문하기'}</Button>}>
  <div className="manual-stack">{value.kind==='empty'?<ManualCard title="매뉴얼을 함께 완성해요"><p>작성 중인 내용은 이어서 확인할 수 있어요.<br/>완성하고 게시하면 이곳에서 근무자 화면으로 확인해요.</p></ManualCard>:value.kind==='list'?<>
   <p className="manual-muted">지단 카페 체험 매장 · 업무 안내</p>
   {value.data.sections.map(section=><Button intent="secondary" key={section.id} onClick={()=>navigate(`/manual?id=${section.id}`)}>{section.title}</Button>)}
   {!value.data.sections.length&&<p>등록된 업무가 없어요.</p>}
   {role==='WORKER'&&<p className="manual-muted">예시 질문: 포스 마감은 어떻게 하나요?</p>}
  </>:<ManualCard title={value.data.section.title}><ol>{value.data.section.steps.map(step=><li key={step.id}>{step.instruction}</li>)}</ol>
   {!value.data.section.steps.length&&<p>자세한 절차는 점주님께 확인해 주세요.</p>}
   <div className="manual-photo-grid">{value.data.section.photos.map(photo=><ManualPhoto key={photo.mediaId} photo={photo} service={service}/>)}</div>
  </ManualCard>}</div>
 </ManualFrame>}</Resource>
}
