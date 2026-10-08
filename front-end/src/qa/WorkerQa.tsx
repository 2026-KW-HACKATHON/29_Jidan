import {useMemo,useState} from 'react'
import {call} from '../api/operations'
import {Resource} from '../workspace/Resource'
import {MobileLayout} from '../ui/MobileLayout'
import {AppBar} from '../ui/AppBar'
import {Button} from '../ui/Button'
import {createQaService,type QaService} from './service'
import {QaChat} from './QaChat'
import './Qa.css'
export function WorkerQa({storeId,id,newChat,navigate}:{storeId:string;id:string;newChat:boolean;navigate:(url:string)=>void}){
 const back=()=>navigate(`/home?view=manual&store=${storeId}`)
 const load=useMemo(()=>(signal:AbortSignal)=>call('getMyStoreAccess',{signal,params:{storeId}}),[storeId])
 const service=useMemo(()=>createQaService(storeId),[storeId])
 return <Resource load={load} onBack={back}>{access=>{
  if(!access.access.permissions.includes('USE_AI_QA')||!access.publishedVersionId)return <MobileLayout className="qa-screen" header={<AppBar title="AI 업무 질문" onBack={back}/>}><p>{!access.publishedVersionId?'아직 게시된 매뉴얼이 없어요. 점주님께 확인해 주세요.':'이 매장의 AI 질문을 이용할 수 없어요.'}</p></MobileLayout>
  if(id||newChat)return <QaChat key={`${storeId}:${id}`} service={service} storeName={access.store.name} conversationId={id} onBack={()=>navigate(`/home?view=qa&store=${storeId}`)} onLatest={back}/>
  return <Conversations service={service} storeName={access.store.name} onBack={back} onOpen={id=>navigate(`/home?view=qa&store=${storeId}&${id?`id=${id}`:'new=1'}`)}/>
 }}</Resource>
}
function Conversations({service,storeName,onBack,onOpen}:{service:QaService;storeName:string;onBack:()=>void;onOpen:(id:string)=>void}){
 const [page,setPage]=useState(0)
 const load=useMemo(()=>async(signal:AbortSignal)=>(await service.call('listMyQAConversations',undefined,{signal,query:{page,size:20}})).data,[service,page])
 return <Resource load={load} onBack={onBack}>{data=><MobileLayout className="qa-screen" header={<AppBar title="AI 업무 질문" onBack={onBack}/>} footer={<Button onClick={()=>onOpen('')}>새 질문 시작하기</Button>}><div className="qa-list"><p>{storeName} · 내 대화</p>{!data.items.length&&<p>아직 질문한 대화가 없어요.</p>}{data.items.map(c=><Button intent="secondary" key={c.id} onClick={()=>onOpen(c.id)}>{new Date(c.updatedAt).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})} 대화</Button>)}<div className="qa-tools">{page>0&&<Button intent="secondary" onClick={()=>setPage(p=>p-1)}>이전 대화 목록</Button>}{(page+1)*data.size<data.totalItems&&<Button intent="secondary" onClick={()=>setPage(p=>p+1)}>다음 대화 목록</Button>}</div></div></MobileLayout>}</Resource>
}
