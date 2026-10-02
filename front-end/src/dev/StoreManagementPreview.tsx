import { useMemo,useState } from 'react'
import { StoreManagement,WorkerList,type ManagedWorker } from '../store/StoreManagement'
import { Employment,type AccessService } from '../store/Employment'
import { navigatePreview,usePreviewLocation } from './navigation'
const storeWorkers:ManagedWorker[]=[
 {id:'jisu',name:'김지수',task:'홀 서빙',job:'홀 서빙 · 정기 근무',type:'정기 근무',start:'2025. 07. 01',expiry:'별도 종료 전까지',permissions:['업무 매뉴얼','체크리스트','AI 질의응답'],status:'active'},
 {id:'minjun',name:'이민준',task:'주방 보조',job:'주방 보조 · 정기 근무',type:'정기 근무',start:'2025. 07. 01',expiry:'별도 종료 전까지',permissions:['업무 매뉴얼','체크리스트','AI 질의응답'],status:'active'},
 {id:'seoyeon',name:'박서연',task:'카운터, 홀 서빙',job:'카운터, 홀 서빙 · 정기 근무',type:'정기 근무',start:'2025. 07. 01',expiry:'별도 종료 전까지',permissions:['업무 매뉴얼','체크리스트','AI 질의응답'],status:'active'},
 {id:'yujin',name:'최유진',task:'홀 서빙',job:'홀 서빙 · 대타 근무',type:'대타 근무',start:'2025. 07. 01',expiry:'2025. 07. 26',permissions:['업무 매뉴얼','체크리스트','AI 질의응답'],status:'expiring'},
]
export default function StoreManagementPreview() {
 const current=usePreviewLocation(),params=new URLSearchParams(current.split('?')[1]),view=params.get('view'),id=params.get('id'),fail=params.has('fail')
 const [workers,setWorkers]=useState(()=>params.has('empty')?[]:storeWorkers)
 const service=useMemo<AccessService>(()=>{let calls=0;return {end:async()=>{if(fail && calls++===0)throw Error('MOCK_STORE_ACCESS_FAILURE')}}},[fail])
 const worker=workers.find(item=>item.id===id)
 const list=()=>navigatePreview('/__store/manage?view=workers')
 if(view==='worker' && worker)return <Employment key={worker.id} data={worker} service={service} onBack={list} onEnded={()=>setWorkers(items=>items.map(item=>item.id===worker.id?{...item,status:'ended'}:item))}/>
 if(view==='workers'||view==='worker')return <WorkerList storeName="명랑핫도그 광운대점" workers={workers} onBack={()=>navigatePreview('/__store/manage')} onSelect={item=>navigatePreview(`/__store/manage?view=worker&id=${item.id}`)}/>
 return <StoreManagement onJobs={()=>navigatePreview('/__owner/jobs')} storeName="명랑핫도그 광운대점" statistics={{jobs:3,review:5,pending:2}} workers={workers} pendingInvitations={1} onInvitations={()=>navigatePreview('/__invitations')} onWorkers={list} onHome={()=>navigatePreview('/__home/owner')} onBack={()=>navigatePreview('/__home/owner')}/>
}
