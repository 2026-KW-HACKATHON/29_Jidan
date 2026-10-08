import {act,renderHook,waitFor} from '@testing-library/react'
import {expect,it,vi} from 'vitest'
import {ApiError} from '../api/client'
import {useConversation} from './useConversation'
import type {QaService} from './service'
const id='11111111-1111-4111-8111-111111111111'
const input={kind:'TEXT' as const,text:'마감 방법',transcriptionId:null,imageMediaIds:[]}
const ready={id,conversationId:id,sequence:1,manualVersionId:id,status:'READY',text:'마감 방법',imageMediaIds:[],createdAt:'2026-10-09T00:00:00Z',completedAt:'2026-10-09T00:00:01Z',answer:{outcome:'NEEDS_OWNER',text:'점주님께 확인해 주세요.',citations:[]},error:null}
const result=(data:unknown)=>({data,retryAfterMs:2000})
it('응답 유실은 같은 질문 키로 재전송하며 대화를 중복 생성하지 않는다',async()=>{
 let attempts=0
 const call=vi.fn(async(name)=>{if(name==='createQAConversation')return result({id});if(++attempts===1)throw new ApiError(0,'NETWORK_ERROR');return result(ready)})
 const service={storeId:id,call} as QaService
 const {result:r}=renderHook(()=>useConversation(service,''))
 act(()=>{r.current.send(input);r.current.send(input)})
 await waitFor(()=>expect(r.current.error).not.toBe(''));expect(r.current.pending).toBe(true)
 act(()=>r.current.retry());await waitFor(()=>expect(r.current.sent).toBe(1))
 const writes=call.mock.calls.filter(c=>c[0]==='askManualQuestion') as unknown as [string,unknown,{key:string}][]
 expect(writes).toHaveLength(2);expect(writes[0][2].key).toBe(writes[1][2].key)
 expect(call.mock.calls.filter(c=>c[0]==='createQAConversation')).toHaveLength(1)
})
it('대화 복원 후 처리 중 답변을 조회하고 이전 기록을 중복 없이 합친다',async()=>{
 const call=vi.fn(async(name,_input,options)=>{
  if(name==='getManualQuestionResult')return result(ready)
  if(options.query.beforeSequence)return result({turns:[{...ready,id:'old',sequence:0}],nextBeforeSequence:null})
  return result({turns:[{...ready,status:'RUNNING',answer:null,completedAt:null}],nextBeforeSequence:1})
 })
 const service={storeId:id,call} as QaService
 const {result:r}=renderHook(()=>useConversation(service,id))
 await waitFor(()=>expect(r.current.turns[0]?.status).toBe('READY'))
 act(()=>r.current.older());await waitFor(()=>expect(r.current.turns).toHaveLength(2))
 expect(r.current.turns.map(t=>t.sequence)).toEqual([0,1])
})
it('권한 종료 시 표시된 대화와 입력 가능 상태를 제거한다',async()=>{
 const call=vi.fn().mockResolvedValueOnce(result({turns:[ready],nextBeforeSequence:null})).mockRejectedValue(new ApiError(404,'RESOURCE_NOT_FOUND'))
 const service={storeId:id,call} as QaService
 const {result:r}=renderHook(()=>useConversation(service,id))
 await waitFor(()=>expect(r.current.turns).toHaveLength(1))
 act(()=>{void r.current.reload()});await waitFor(()=>expect(r.current.blocked).toBe(true));expect(r.current.turns).toEqual([])
})
it('이탈한 화면의 늦은 생성 응답은 질문 제출로 이어지지 않는다',async()=>{
 let resolve!:(v:unknown)=>void
 const call=vi.fn(()=>new Promise(r=>{resolve=r})),service={storeId:id,call} as QaService
 const {result:r,unmount}=renderHook(()=>useConversation(service,''))
 act(()=>r.current.send(input));unmount();await act(async()=>resolve(result({id})))
 expect(call).toHaveBeenCalledOnce()
})
it('기존 대화를 불러오지 못하면 입력을 열지 않고 재조회로 복구한다',async()=>{
 const call=vi.fn().mockRejectedValueOnce(new ApiError(0,'NETWORK_ERROR')).mockResolvedValue(result({turns:[],nextBeforeSequence:null}))
 const service={storeId:id,call} as QaService
 const {result:r}=renderHook(()=>useConversation(service,id))
 await waitFor(()=>expect(r.current.error).not.toBe(''));expect(r.current.loading).toBe(true)
 act(()=>r.current.retry());await waitFor(()=>expect(r.current.loading).toBe(false))
})
