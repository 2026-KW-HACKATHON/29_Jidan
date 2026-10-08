import {afterEach,expect,it,vi} from 'vitest'
import {createQaService,retryDelay} from './service'
const store='11111111-1111-4111-8111-111111111111', conversation='22222222-2222-4222-8222-222222222222'
const signal=()=>new AbortController().signal
const row={id:conversation,storeId:store,createdAt:'2026-10-09T00:00:00Z',updatedAt:'2026-10-09T00:00:00Z'}
const json=(data:unknown,status=200)=>new Response(JSON.stringify(data),{status})
afterEach(()=>vi.useRealTimers())
it('생성은 쿠키·CSRF·멱등 키를 전달한다',async()=>{
 const fetcher=vi.fn().mockImplementation(async(path)=>path==='/api/auth/csrf'?json({csrfToken:'token'}):json(row,201))
 expect((await createQaService(store,fetcher).call('createQAConversation',{}, {signal:signal(),key:'stable'})).data).toEqual(row)
 const [url,options]=fetcher.mock.calls[1]
 expect(url).toBe(`/api/stores/${store}/manual/qa/conversations`)
 expect(options).toMatchObject({credentials:'include',cache:'no-store',redirect:'error',body:'{}'})
 expect(options.headers.get('X-CSRF-Token')).toBe('token');expect(options.headers.get('Idempotency-Key')).toBe('stable')
})
it('공백·초과 길이·중복 사진·VOICE 혼합·잘못된 대상은 전송하지 않는다',async()=>{
 const transport=vi.fn(),service=createQaService(store,transport),opts={signal:signal(),key:'k',params:{conversationId:conversation}}
 const input={kind:'TEXT' as const,text:'질문',transcriptionId:null,imageMediaIds:[]}
 for(const body of [{...input,text:'  '},{...input,text:'a'.repeat(2001)},{...input,imageMediaIds:[store,store]}])await expect(service.call('askManualQuestion',body,opts)).rejects.toThrow()
 await expect(service.call('getQAConversation',undefined,{signal:signal(),params:{conversationId:'../bad'}})).rejects.toThrow()
 await expect(service.call('createQAConversation',{}, {signal:signal()})).rejects.toThrow()
 expect(transport).not.toHaveBeenCalled()
})
it('조회 cursor를 전달하며 다른 매장의 응답을 거절한다',async()=>{
 const transport=vi.fn().mockImplementation(async()=>json({conversation:row,turns:[],nextBeforeSequence:null}))
 const service=createQaService(store,transport),opts={signal:signal(),params:{conversationId:conversation},query:{beforeSequence:4,size:20}}
 await service.call('getQAConversation',undefined,opts)
 expect(transport.mock.calls[0][0]).toContain('?beforeSequence=4&size=20')
 transport.mockImplementation(async()=>json({conversation:{...row,storeId:conversation},turns:[],nextBeforeSequence:null}))
 await expect(service.call('getQAConversation',undefined,opts)).rejects.toThrow()
 await expect(service.call('getQAConversation',undefined,{...opts,query:{beforeSequence:0}})).rejects.toThrow()
})
it('multipart boundary와 사진 크기·형식을 검사한다',async()=>{
 const media={id:store,storeId:store,purpose:'QUESTION_IMAGE',mimeType:'image/png',sizeBytes:1,createdAt:row.createdAt,expiresAt:row.createdAt}
 const transport=vi.fn().mockImplementation(async(path)=>path==='/api/auth/csrf'?json({csrfToken:'t'}):json(media,201)),service=createQaService(store,transport)
 const form=new FormData();form.set('purpose','QUESTION_IMAGE');form.set('file',new Blob(['x'],{type:'image/png'}),'image.png')
 await service.call('uploadQAQuestionMedia',form,{signal:signal(),key:'k'})
 expect(transport.mock.calls[1][1].body).toBe(form);expect(transport.mock.calls[1][1].headers.has('Content-Type')).toBe(false)
 for(const file of [new Blob([],{type:'image/png'}),new Blob(['x'],{type:'image/svg+xml'}),new Blob([new Uint8Array(10485761)],{type:'image/png'})]){form.set('file',file);await expect(service.call('uploadQAQuestionMedia',form,{signal:signal(),key:'k'})).rejects.toThrow()}
 expect(transport).toHaveBeenCalledTimes(2)
})
it('401은 인증 만료를 알리고 provider 원문을 버린다',async()=>{
 const expired=vi.fn();window.addEventListener('jidan-session-expired',expired)
 const service=createQaService(store,vi.fn().mockResolvedValue(json({code:'SESSION_EXPIRED',message:'secret'},401)))
 await expect(service.call('listMyQAConversations',undefined,{signal:signal()})).rejects.toMatchObject({code:'SESSION_EXPIRED'})
 expect(expired).toHaveBeenCalledOnce();window.removeEventListener('jidan-session-expired',expired)
})
it('타임아웃·취소·Retry-After 경계를 처리한다',async()=>{
 vi.useFakeTimers();const transport=vi.fn(()=>new Promise<Response>(()=>{})),controller=new AbortController()
 const request=createQaService(store,transport).call('listMyQAConversations',undefined,{signal:controller.signal}),check=expect(request).rejects.toMatchObject({code:'CLIENT_WAIT_EXCEEDED'})
 await vi.advanceTimersByTimeAsync(10000);await check;expect(controller.signal.aborted).toBe(false)
 const abort=new AbortController(),pending=createQaService(store,transport).call('listMyQAConversations',undefined,{signal:abort.signal});abort.abort();await expect(pending).rejects.toThrow()
 expect([null,'NaN','-1','0','3','999999'].map(retryDelay)).toEqual([2000,2000,2000,2000,3000,60000])
})
