import { afterEach, expect, it, vi } from 'vitest'
import { isWorkerReceipt, workerService } from './service'
import { emptyWorker } from './model'
const signal=()=>new AbortController().signal
const json=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status})
const context={identity:{provider:'GOOGLE',email:'member@example.com',emailVerified:true},expiresAt:'2099-01-01T00:00:00Z',allowedRoles:['WORKER','OWNER']}
const receipt={user:{id:'worker',name:'김지수',role:'WORKER',stores:[]},expiresAt:context.expiresAt,nextAction:'WORKER_HOME'}
const draft={...emptyWorker,name:' 김지수 ',phone:'010-1234-5678',birth:'2000-01-01',gender:'여성' as const,experience:'경력 있음' as const,careers:[{id:'local',industry:'카페' as const,duties:' 응대 ',store:'',start:'2024-01',end:'2024-02',current:true}],availability:[{id:'local',days:[6,0],start:1380,end:60,overnight:true}]}
afterEach(()=>vi.useRealTimers())
it('검증된 가입 이메일을 조회한다',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(json(context)));expect(await workerService.identity(signal())).toEqual({email:'member@example.com',draftScope:'member@example.com'})})
it('경력·심야 가능 시간을 명세로 변환하고 UI id와 읽기 전용 필드를 보내지 않는다',async()=>{
 const fetch=vi.fn().mockResolvedValueOnce(json({csrfToken:'csrf'})).mockResolvedValueOnce(json(receipt,201));vi.stubGlobal('fetch',fetch)
 expect(await workerService.submit(draft,'key',signal())).toEqual({id:'worker',status:'COMPLETE'})
 expect(fetch.mock.calls[1][0]).toBe('/api/auth/registrations/workers');expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({name:'김지수',phoneNumber:'01012345678',birthDate:'2000-01-01',gender:'FEMALE',experienceLevel:'EXPERIENCED',careers:[{industry:'CAFE',duties:'응대',startMonth:'2024-01',endMonth:null,isCurrent:true}],availabilities:[{days:['SUN','MON'],startTime:'23:00',endTime:'01:00',endsNextDay:true}]})
})
it('신입은 경력을 제외하고 종료 경력의 endMonth와 storeName을 전송한다',async()=>{
 const fetch=vi.fn().mockImplementation(async(url:string)=>url.endsWith('/csrf')?json({csrfToken:'csrf'}):json(receipt,201));vi.stubGlobal('fetch',fetch)
 await workerService.submit({...draft,experience:'신입'},'new',signal());expect(JSON.parse(fetch.mock.calls[1][1].body).careers).toEqual([])
 await workerService.submit({...draft,careers:[{...draft.careers[0],store:' 카페 ',current:false}]},'experienced',signal());expect(JSON.parse(fetch.mock.calls[3][1].body).careers[0]).toMatchObject({storeName:'카페',endMonth:'2024-02',isCurrent:false})
})
it.each([[401,'SESSION_EXPIRED','expired'],[403,'ACCOUNT_SUSPENDED','network'],[422,'VALIDATION_ERROR','validation'],[409,'REGISTRATION_CONFLICT','network'],[500,'INTERNAL_ERROR','network']])('HTTP %s 오류 %s를 %s로 전달한다',async(status,code,expected)=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(json({code,fieldErrors:[{field:'availabilities.0.days',code:'INVALID',message:'요일 확인'}]},Number(status))))
 await expect(workerService.submit(draft,'key',signal())).rejects.toMatchObject({code:expected,fields:{availability:'요일 확인'}})
})
it('잘못된 성공 응답을 거부한다',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(json({csrfToken:'csrf'})).mockResolvedValueOnce(json({},201)));await expect(workerService.submit(draft,'key',signal())).rejects.toHaveProperty('code','network')
})
it('지연과 취소 이후 같은 key로 재시도한다',async()=>{
 vi.useFakeTimers();const fetch=vi.fn().mockImplementation(()=>new Promise(()=>{}));vi.stubGlobal('fetch',fetch)
 const request=workerService.submit(draft,'key',signal()),assertion=expect(request).rejects.toHaveProperty('code','network');await vi.advanceTimersByTimeAsync(10000);await assertion
 const controller=new AbortController(),pending=workerService.identity(controller.signal),cancelled=expect(pending).rejects.toHaveProperty('name','AbortError');controller.abort();await cancelled
 fetch.mockResolvedValueOnce(json({csrfToken:'csrf'})).mockResolvedValueOnce(json(receipt,201));await expect(workerService.submit(draft,'key',signal())).resolves.toHaveProperty('id','worker')
})
it('등록 완료 응답의 id와 상태를 확인한다',()=>{for(const value of [null,{}, {id:'',status:'COMPLETE'},{id:'a',status:'PENDING'}])expect(isWorkerReceipt(value)).toBe(false);expect(isWorkerReceipt({id:'a',status:'COMPLETE'})).toBe(true)})
