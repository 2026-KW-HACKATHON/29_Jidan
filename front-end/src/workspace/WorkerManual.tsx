import {useMemo} from 'react'
import {call,pages} from '../api/operations'
import {ManualFrame,ManualCard} from '../manual/ManualFrame'
import {ManualPhoto} from '../manual/ManualPhoto'
import {liveManualService} from '../manual/live'
import {Button} from '../ui/Button'
import {Resource} from './Resource'
export function WorkerManual({storeId,id,navigate,onBack}:{storeId:string;id:string;navigate:(url:string)=>void;onBack:()=>void}){
 const load=useMemo(()=>(signal:AbortSignal)=>pages(page=>call('listMyAccessibleStores',{signal,query:{page,size:100}}),signal),[])
 return <Resource load={load} onBack={onBack}>{stores=>{const store=stores.find(s=>s.store.id===storeId);return store?<Published storeId={storeId} id={id} published={store.publishedVersionId!==null} canAsk={store.access.permissions.includes('USE_AI_QA')} navigate={navigate} onBack={onBack}/>:<ManualFrame showProgress={false} title="매장 매뉴얼" onBack={onBack}><div className="manual-stack">{stores.length?stores.map(s=><Button key={s.store.id} intent="secondary" onClick={()=>navigate(`/home?view=manual&store=${s.store.id}`)}>{s.store.name}</Button>):<p>접근할 수 있는 매장이 없어요. 초대 또는 근무 확정을 확인해 주세요.</p>}</div></ManualFrame>}}</Resource>
}
function Published({storeId,id,published,canAsk,navigate,onBack}:{storeId:string;id:string;published:boolean;canAsk:boolean;navigate:(url:string)=>void;onBack:()=>void}){
 const load=useMemo(()=>async(signal:AbortSignal)=>id?{detail:await call('getPublishedManualSection',{signal,params:{storeId,sectionId:id}})}:{list:await call('getPublishedManual',{signal,params:{storeId}})},[storeId,id]),service=useMemo(()=>liveManualService(storeId),[storeId])
 if(!published)return <ManualFrame showProgress={false} title="매장 매뉴얼" onBack={onBack}><p>아직 게시된 매뉴얼이 없어요.</p></ManualFrame>
 const back=()=>id?navigate(`/home?view=manual&store=${storeId}`):onBack()
 return <Resource load={load} onBack={back}>{value=><ManualFrame showProgress={false} title="매장 매뉴얼" onBack={back} footer={canAsk?<Button onClick={()=>navigate(`/home?view=qa&store=${storeId}`)}>AI에게 업무 질문하기</Button>:undefined}><div className="manual-stack">{value.list?<>{value.list.sections.map(s=><Button key={s.id} intent="secondary" onClick={()=>navigate(`/home?view=manual&store=${storeId}&id=${s.id}`)}>{s.title}</Button>)}{!value.list.sections.length&&<p>등록된 업무가 없어요.</p>}</>:<ManualCard title={value.detail!.section.title}><ol>{value.detail!.section.steps.map(s=><li key={s.id}>{s.instruction}</li>)}</ol><div className="manual-photo-grid">{value.detail!.section.photos.map(p=><ManualPhoto key={p.mediaId} photo={p} service={service}/>)}</div></ManualCard>}</div></ManualFrame>}</Resource>
}
