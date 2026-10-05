import { afterEach,expect,it,vi } from 'vitest'
import { createManualHttpService } from './service'
const id='11111111-1111-4111-8111-111111111111'
it('HTTP는 쿠키·CSRF·멱등 키·signal을 전달하고 없는 대상·CSRF를 거절한다',async()=>{
 const transport=vi.fn().mockResolvedValue(new Response(null,{status:204}));const service=createManualHttpService(id,()=> 'csrf',transport)
 const signal=new AbortController().signal;await service.call('deleteUnusedManualMedia',{mediaId:id},undefined,{signal,key:'stable'})
 const [url,options]=transport.mock.calls[0];expect(url).toBe(`/api/stores/${id}/manual/media/${id}`);expect(options.credentials).toBe('include');expect(options.headers.get('X-CSRF-Token')).toBe('csrf');expect(options.headers.get('Idempotency-Key')).toBe('stable');expect(options.signal.aborted).toBe(false)
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

afterEach(()=>vi.useRealTimers())
it('타임아웃은 transport와 응답 body 읽기를 끝내고 부모 signal과 재시도 키를 보존한다',async()=>{
 vi.useFakeTimers();const parent=new AbortController(),transport=vi.fn().mockImplementation(()=>new Promise(()=>{})),service=createManualHttpService(id,()=> 'csrf',transport)
 const key=crypto.randomUUID();const promise=service.call('startManualInterview',{}, {},{signal:parent.signal,key});const rejected=expect(promise).rejects.toThrow('REQUEST_TIMEOUT')
 await vi.advanceTimersByTimeAsync(10000);await rejected;expect(transport.mock.calls[0][1].signal.aborted).toBe(true);expect(parent.signal.aborted).toBe(false);expect(transport.mock.calls[0][1].headers.get('Idempotency-Key')).toBe(key)
 transport.mockImplementation(()=>Promise.resolve({ok:true,headers:new Headers(),json:()=>new Promise(()=>{})}));const body=service.call('getOwnerManualState',{},undefined,{signal:parent.signal});const bodyRejected=expect(body).rejects.toThrow('REQUEST_TIMEOUT');await vi.advanceTimersByTimeAsync(10000);await bodyRejected
})
it('화면 이탈은 진행 중 HTTP를 중단하고 잘못된 조건부 body는 보내지 않는다',async()=>{
 const controller=new AbortController(),transport=vi.fn().mockImplementation(()=>new Promise(()=>{})),service=createManualHttpService(id,()=> 'csrf',transport)
 const promise=service.call('getOwnerManualState',{},undefined,{signal:controller.signal});controller.abort();await expect(promise).rejects.toThrow();expect(transport.mock.calls[0][1].signal.aborted).toBe(true)
 await expect(service.call('publishManualDraft',{}, {expectedVersionId:id,expectedRevision:0,confirmed:true,acknowledgedIssueIds:[]},{signal:new AbortController().signal,key:crypto.randomUUID()})).rejects.toThrow('INVALID_REQUEST');expect(transport).toHaveBeenCalledOnce()
})
