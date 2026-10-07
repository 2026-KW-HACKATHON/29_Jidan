import {afterEach,expect,it,vi} from 'vitest'
import {createProfileService,profileFromApi} from './service'
import type {WorkerProfile} from '../api/types.generated'
const profile:WorkerProfile={id:'7390d3c1-dc0d-4b1c-814d-b68a1df5c1b6',role:'WORKER',name:'회원',phoneNumber:'01012345678',birthDate:'2000-01-01',gender:'FEMALE',identity:{provider:'GOOGLE',email:'worker@example.com',emailVerified:true},experienceLevel:'NEW',careers:[],availabilities:[{days:['SUN'],startTime:'22:00',endTime:'02:00',endsNextDay:true}],updatedAt:'2026-10-08T00:00:00Z'}
afterEach(()=>vi.unstubAllGlobals())
it('요일과 자정 넘김을 변환하고 선택한 구역만 저장한다',async()=>{
 const fetch=vi.fn().mockImplementation(async(url:string)=>new Response(JSON.stringify(url.endsWith('/csrf')?{csrfToken:'token'}:profile)));vi.stubGlobal('fetch',fetch)
 const service=createProfileService(),signal=new AbortController().signal,data=await service.read(signal)
 expect(data.draft.availability[0]).toMatchObject({days:[6],start:1320,end:120,overnight:true})
 await service.save(data,signal,3)
 const [url,options]=fetch.mock.calls.at(-1)!
 expect(url).toBe('/api/users/me/profile/availabilities');expect(JSON.parse(options.body)).toEqual({availabilities:profile.availabilities})
 expect(fetch.mock.calls.some(([url])=>url.endsWith('/basic')||url.endsWith('/careers'))).toBe(false)
})
it('신입 전환은 경력 목록만 비우고 실패 후 재시도 키를 보존한다',async()=>{
 const keys:string[]=[];let fail=true
 vi.stubGlobal('fetch',vi.fn(async(url:string,options:RequestInit)=>{if(url.endsWith('/csrf'))return new Response(JSON.stringify({csrfToken:'token'}));keys.push(new Headers(options.headers).get('Idempotency-Key')!);expect(JSON.parse(options.body as string)).toEqual({experienceLevel:'NEW',careers:[]});if(fail){fail=false;throw Error('lost')}return new Response(JSON.stringify(profile))}))
 const service=createProfileService(),data=profileFromApi(profile),signal=new AbortController().signal
 await expect(service.save(data,signal,2)).rejects.toHaveProperty('code','NETWORK_ERROR');await service.save(data,signal,2);expect(keys[0]).toBe(keys[1])
})
