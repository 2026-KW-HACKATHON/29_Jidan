import { expect,it,vi } from 'vitest'
import { createManualHttpService } from './service'
const id='11111111-1111-4111-8111-111111111111'
it('HTTP는 쿠키·CSRF·멱등 키·signal을 전달하고 없는 대상·CSRF를 거절한다',async()=>{
 const transport=vi.fn().mockResolvedValue(new Response(null,{status:204}));const service=createManualHttpService(id,()=> 'csrf',transport)
 const signal=new AbortController().signal;await service.call('deleteUnusedManualMedia',{mediaId:id},undefined,{signal,key:'stable'})
 const [url,options]=transport.mock.calls[0];expect(url).toBe(`/api/stores/${id}/manual/media/${id}`);expect(options.credentials).toBe('include');expect(options.headers.get('X-CSRF-Token')).toBe('csrf');expect(options.headers.get('Idempotency-Key')).toBe('stable');expect(options.signal).toBe(signal)
 await expect(service.call('deleteUnusedManualMedia',{},undefined,{signal,key:'stable'})).rejects.toThrow('INVALID_TARGET')
 await expect(createManualHttpService(id,()=>null,transport).call('startManualInterview',{}, {},{signal,key:'stable'})).rejects.toThrow('CSRF_REQUIRED')
})
it('HTTP는 다른 매장·불완전한 응답을 거절하고 provider 원문을 노출하지 않는다',async()=>{
 const signal=new AbortController().signal
 for(const data of [{},{storeId:id,currentPublishedVersionId:null,draftVersionId:null,interviewSessionId:42},{storeId:'22222222-2222-4222-8222-222222222222',currentPublishedVersionId:null,draftVersionId:null,interviewSessionId:null}]){
 const service=createManualHttpService(id,()=> 'csrf',vi.fn().mockResolvedValue(new Response(JSON.stringify(data))))
 await expect(service.call('getOwnerManualState',{},undefined,{signal})).rejects.toThrow('INVALID_RESPONSE')
 }
 const service=createManualHttpService(id,()=> 'csrf',vi.fn().mockResolvedValue(new Response(JSON.stringify({code:'REVISION_CONFLICT',message:'private provider payload'}),{status:409})))
 await expect(service.call('getOwnerManualState',{},undefined,{signal})).rejects.toThrow(/^REVISION_CONFLICT$/)
})
it('이력 페이지는 0부터 시작하고 UUID 경로와 query를 분리한다',async()=>{
 const response={items:[],page:2,size:100,totalItems:0,totalPages:0,asOf:'2026-10-06T00:00:00Z'},transport=vi.fn().mockResolvedValue(new Response(JSON.stringify(response))),signal=new AbortController().signal
 await createManualHttpService(id,()=>null,transport).call('getManualInterviewTurns',{sessionId:id,page:'2'},undefined,{signal})
 expect(transport.mock.calls[0][0]).toBe(`/api/stores/${id}/manual/interviews/${id}/turns?page=2&size=100`)
})
