import { afterEach, expect, it, vi } from 'vitest'
import { isOwnerReceipt, ownerService } from './service'
import { emptyDraft } from './model'
const signal = () => new AbortController().signal
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const context = { identity: { provider: 'GOOGLE', email: 'owner@example.com', emailVerified: true }, expiresAt: '2099-01-01T00:00:00Z', allowedRoles: ['WORKER','OWNER'] }
const receipt = { user: { id: 'owner', name: '김민수', role: 'OWNER', stores: [{storeId:'store',storeName:'매장',approvalStatus:'PENDING',permissions:['READ_STORE_STATUS']}] }, expiresAt: context.expiresAt, nextAction:'OWNER_APPROVAL_PENDING' }
const draft = {...emptyDraft,name:' 김민수 ',phone:'010-1234-5678',storeName:' 매장 ',industry:'카페' as const,postcode:'01897',address:'서울 주소',businessNumber:'123-45-67890',storePhone:'02-123-4567'}
afterEach(()=>vi.useRealTimers())
it('가입 컨텍스트의 검증된 이메일로 초안 범위를 만든다', async () => {
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(json(context)));expect(await ownerService.identity(signal())).toEqual({email:'owner@example.com',draftScope:'owner@example.com'})
})
it('점주 가입 본문을 명세로 변환하고 서버 접수 결과를 표시한다', async () => {
 const fetch=vi.fn().mockResolvedValueOnce(json({csrfToken:'csrf'})).mockResolvedValueOnce(json(receipt,201));vi.stubGlobal('fetch',fetch)
 expect(await ownerService.submit(draft,'key',signal())).toEqual({id:'store',ownerName:'김민수',storeName:'매장',status:'PENDING'})
 const [url,init]=fetch.mock.calls[1];expect(url).toBe('/api/auth/registrations/owners');expect(init.headers.get('Idempotency-Key')).toBe('key')
 expect(JSON.parse(init.body)).toEqual({name:'김민수',phoneNumber:'01012345678',store:{name:'매장',industry:'CAFE',postalCode:'01897',address:'서울 주소',detailAddress:'',businessRegistrationNumber:'1234567890',phoneNumber:'021234567'}})
})
it.each([[401,'SESSION_EXPIRED','expired'],[403,'ACCOUNT_SUSPENDED','network'],[409,'STORE_ALREADY_REGISTERED','duplicate'],[422,'VALIDATION_ERROR','validation'],[500,'INTERNAL_ERROR','network']])('HTTP %s %s를 가입 오류 %s로 전달한다',async(status,code,expected)=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(json({code,fieldErrors:[{field:'store.businessRegistrationNumber',code:'INVALID',message:'번호 확인'}]},Number(status))))
 await expect(ownerService.submit(draft,'key',signal())).rejects.toMatchObject({code:expected,fields:{businessNumber:'번호 확인'}})
})
it('잘못된 접수 응답은 가입 성공으로 만들지 않는다',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(json({csrfToken:'csrf'})).mockResolvedValueOnce(json({...receipt,nextAction:'OWNER_HOME'},201)))
 await expect(ownerService.submit(draft,'key',signal())).rejects.toHaveProperty('code','network')
})
it('지연과 취소를 처리하고 같은 key 재시도를 허용한다',async()=>{
 vi.useFakeTimers();const fetch=vi.fn().mockImplementation(()=>new Promise(()=>{}));vi.stubGlobal('fetch',fetch)
 const request=ownerService.submit(draft,'key',signal()),assertion=expect(request).rejects.toHaveProperty('code','network');await vi.advanceTimersByTimeAsync(10000);await assertion
 const controller=new AbortController(),pending=ownerService.identity(controller.signal),cancelled=expect(pending).rejects.toHaveProperty('name','AbortError');controller.abort();await cancelled
 fetch.mockResolvedValueOnce(json({csrfToken:'csrf'})).mockResolvedValueOnce(json(receipt,201));await expect(ownerService.submit(draft,'key',signal())).resolves.toHaveProperty('id','store')
})
it('서버의 승인 대기 접수 결과만 수락한다', () => {
 expect(isOwnerReceipt({ id: 'id', ownerName: '김민수', storeName: '매장', status: 'PENDING' })).toBe(true)
 for (const value of [null, {}, { id: 'id', ownerName: '김민수', storeName: '매장', status: 'VERIFIED' }, { id: '', ownerName: '김민수', storeName: '매장', status: 'PENDING' }]) expect(isOwnerReceipt(value)).toBe(false)
})
