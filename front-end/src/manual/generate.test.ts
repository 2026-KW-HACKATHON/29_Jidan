import {expect,it,vi} from 'vitest'
import {createDraftGeneration} from './generate'
import {createManualPreviewService} from '../dev/manualPreviewService'
import {interviewFixture} from '../dev/manualFixtures'
import type {ManualService} from './service'
const signal=new AbortController().signal
async function snapshot(){const service=createManualPreviewService(true,'ready');return {session:(await service.call('getManualInterview',{sessionId:interviewFixture.id},undefined,{signal})).data,reviews:(await service.call('listManualIntentReviews',{sessionId:interviewFixture.id},undefined,{signal})).data}}
it('confirmedAt=null인 READY 검토 전체의 독립 revision으로 생성하고 응답 유실 재시도 시 snapshot을 유지한다',async()=>{
 const {session,reviews}=await snapshot(),call=vi.fn().mockResolvedValueOnce({data:session}).mockResolvedValueOnce({data:reviews}).mockRejectedValueOnce(Error('lost')).mockResolvedValueOnce({data:session})
 const generate=createDraftGeneration({call,storeId:session.storeId} as ManualService,session.id,session.draftVersionId)
 await expect(generate(signal)).rejects.toThrow('lost');await generate(signal)
 expect(call).toHaveBeenCalledTimes(4);expect(call.mock.calls[2][2]).toEqual({expectedRevision:reviews.sessionRevision,reviewRevisions:reviews.items.map(i=>({intentId:i.intentId,revision:i.revision}))})
 expect(call.mock.calls[2][2]).toBe(call.mock.calls[3][2]);expect(call.mock.calls[2][3].key).toBe(call.mock.calls[3][3].key)
})
it('진행 중 검토·누락·중복 intent·오래된 session snapshot은 생성하지 않는다',async()=>{
 const {session,reviews}=await snapshot()
 for(const r of [{...reviews,items:reviews.items.slice(1)},{...reviews,items:[reviews.items[0],reviews.items[0],...reviews.items.slice(2)]},{...reviews,sessionRevision:reviews.sessionRevision-1},{...reviews,items:reviews.items.map((i,index)=>index===0?{...i,status:'PROCESSING'}:i)}]){
  const call=vi.fn().mockResolvedValueOnce({data:session}).mockResolvedValueOnce({data:r})
  await expect(createDraftGeneration({call,storeId:session.storeId} as ManualService,session.id,session.draftVersionId)(signal)).rejects.toThrow();expect(call).toHaveBeenCalledTimes(2)
 }
})
