import {beforeEach,expect,it,vi} from 'vitest'
import {ApiError,apiRequest} from '../api/client'
import {startDemoSession,resetDemoSession} from './session'
vi.mock('../api/client',async original=>({...await original<typeof import('../api/client')>(),apiRequest:vi.fn()}))
const session=()=>({role:'OWNER',storeId:'00000000-0000-0000-0000-000000000001',expiresAt:new Date(Date.now()+7200000).toISOString(),csrfToken:'token'})
beforeEach(()=>vi.mocked(apiRequest).mockReset())
it('shares StrictMode bootstrap and recognizes a new anonymous session',async()=>{
 const value=session()
 vi.mocked(apiRequest).mockRejectedValueOnce(new ApiError(401,'SESSION_EXPIRED')).mockResolvedValueOnce(value)
 const first=startDemoSession('OWNER'),second=startDemoSession('OWNER')
 expect(second).toBe(first)
 expect(await first).toEqual({...value,fresh:true})
 expect(apiRequest).toHaveBeenCalledTimes(2)
})
it('preserves a resumed expiry and allows another lookup after completion',async()=>{
 const value=session()
 vi.mocked(apiRequest).mockResolvedValueOnce({expiresAt:value.expiresAt}).mockResolvedValueOnce(value)
 expect((await startDemoSession('OWNER')).fresh).toBe(false)
 vi.mocked(apiRequest).mockResolvedValueOnce({expiresAt:value.expiresAt}).mockResolvedValueOnce(value)
 expect((await startDemoSession('OWNER')).fresh).toBe(false)
 expect(apiRequest).toHaveBeenCalledTimes(4)
})
it.each([null,{}, {role:'WORKER'}, {storeId:'-'.repeat(36)}, {expiresAt:'bad'}, {expiresAt:new Date(0).toISOString()}, {csrfToken:''}])('rejects malformed or mismatched session %j',async patch=>{
 const value=patch===null?null:{...session(),...patch}
 if(patch&&Object.keys(patch).length===0)delete (value as Record<string,unknown>).role
 vi.mocked(apiRequest).mockResolvedValueOnce(value)
 await expect(resetDemoSession('OWNER',new AbortController().signal)).rejects.toMatchObject({code:'INVALID_RESPONSE'})
})
it('does not issue bootstrap after an origin/permission/network failure',async()=>{
 vi.mocked(apiRequest).mockRejectedValueOnce(new ApiError(403,'FORBIDDEN'))
 await expect(startDemoSession('OWNER')).rejects.toMatchObject({status:403})
 expect(apiRequest).toHaveBeenCalledTimes(1)
})
