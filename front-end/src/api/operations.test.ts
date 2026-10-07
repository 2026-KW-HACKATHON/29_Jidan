import {afterEach,expect,it,vi} from 'vitest'
import {call,mutation,pages} from './operations'
const signal=()=>new AbortController().signal
const id='7390d3c1-dc0d-4b1c-814d-b68a1df5c1b6'
const json=(data:unknown,status=200)=>new Response(JSON.stringify(data),{status})
afterEach(()=>vi.unstubAllGlobals())
it('읽기 쿼리와 응답을 배포 계약으로 검증한다',async()=>{
 const fetch=vi.fn().mockResolvedValue(json({name:'회원',pendingApplicationCount:0,favoriteStoreCount:0,regularStoreCount:0,unreadNotificationCount:0,recommendedJobs:[],asOf:'2026-10-08T00:00:00Z'}));vi.stubGlobal('fetch',fetch)
 expect((await call('getWorkerHome',{signal:signal()})).name).toBe('회원')
 fetch.mockResolvedValue(json({name:'회원'}));await expect(call('getWorkerHome',{signal:signal()})).rejects.toHaveProperty('code','INVALID_RESPONSE')
 await expect(call('getOwnerJobPosting',{signal:signal(),params:{storeId:'../auth',jobId:id}})).rejects.toHaveProperty('code','INVALID_REQUEST')
 await expect(call('listMyOwnerStores',{signal:signal(),query:{page:-1}})).rejects.toHaveProperty('code','INVALID_REQUEST');expect(fetch).toHaveBeenCalledTimes(2)
})
it('응답 유실은 같은 멱등성 키를 재사용하고 대상 변경은 새 키를 사용한다',async()=>{
 const keys:string[]=[],fetch=vi.fn().mockImplementation(async(url:string,init:RequestInit)=>{if(url.endsWith('/csrf'))return json({csrfToken:'token'});keys.push(new Headers(init.headers).get('Idempotency-Key')!);throw new TypeError('lost')});vi.stubGlobal('fetch',fetch)
 const write=mutation()
 for(const notificationId of [id,id,'51c1c743-d377-4e7a-8449-94377eecfce0'])await expect(write('markNotificationRead',{signal:signal(),params:{notificationId}})).rejects.toHaveProperty('code','NETWORK_ERROR')
 expect(keys[0]).toBe(keys[1]);expect(keys[2]).not.toBe(keys[1])
})
it('401은 인증 만료를 알리고 403은 현재 화면 오류로 남긴다',async()=>{
 const expired=vi.fn();window.addEventListener('jidan-session-expired',expired)
 try{vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(json({code:'SESSION_EXPIRED'},401)).mockResolvedValueOnce(json({code:'FORBIDDEN'},403)))
 await expect(call('getWorkerHome',{signal:signal()})).rejects.toHaveProperty('status',401)
 await expect(call('getWorkerHome',{signal:signal()})).rejects.toHaveProperty('status',403);expect(expired).toHaveBeenCalledTimes(1)
 }finally{window.removeEventListener('jidan-session-expired',expired)}
})
it('0부터 페이지를 수집하고 빈 페이지·취소·잘못된 페이지를 구분한다',async()=>{
 const read=vi.fn(async(page:number)=>({items:page===0?['a','b']:['c'],page,totalItems:3}))
 expect(await pages(read,signal())).toEqual(['a','b','c']);expect(read.mock.calls.map(([p])=>p)).toEqual([0,1])
 expect(await pages(async page=>({items:[],page,totalItems:0}),signal())).toEqual([])
 await expect(pages(async()=>({items:['a'],page:2,totalItems:1}),signal())).rejects.toHaveProperty('code','INVALID_RESPONSE')
 const controller=new AbortController();controller.abort();await expect(pages(read,controller.signal)).rejects.toHaveProperty('name','AbortError')
})
