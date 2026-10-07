import type {StoreWorkerAccess} from '../api/types.generated'
import type {ManagedWorker} from './StoreManagement'
import {seoulDate} from '../home/api'
const permissionLabels={READ_MANUALS:'업무 매뉴얼',READ_CHECKLISTS:'체크리스트',USE_AI_QA:'AI 질의응답'}
export function workerFromApi(w:StoreWorkerAccess):ManagedWorker{
 const active=w.accessGrants.filter(g=>g.status==='ACTIVE'||g.status==='EXPIRING'),grants=active.length?active:w.accessGrants
 const types=[...new Set(grants.map(g=>g.type==='REGULAR'?'정기 근무':'대타 근무'))].join(' · ')
 const tasks=[...new Set(grants.map(g=>g.dutyLabel).filter(Boolean))].join(', ')
 return {id:w.workerId,name:w.name,task:tasks||types,job:tasks||types,type:types,start:grants.length?seoulDate(grants.reduce((a,b)=>a.startedAt<b.startedAt?a:b).startedAt):'기록 없음',expiry:active.some(g=>g.validUntil===null)?'별도 종료 전까지':grants.length?seoulDate(grants.reduce((a,b)=>(a.validUntil||'')>(b.validUntil||'')?a:b).validUntil||grants[0].startedAt):'접근 종료',permissions:w.permissions.map(p=>permissionLabels[p]),status:w.accessStatus==='ACTIVE'?'active':w.accessStatus==='EXPIRING'?'expiring':'ended'}
}
