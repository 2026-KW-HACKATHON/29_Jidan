import {cleanup,render,screen} from '@testing-library/react'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
beforeEach(()=>{vi.resetModules();vi.stubGlobal('fetch',vi.fn());sessionStorage.clear()})
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
it.each(['/__preview','/__auth/worker?step=complete'])('다른 미리보기 %s 진입은 점주 초안과 인증을 변경하지 않는다',async url=>{
 history.replaceState(null,'',url);const key='preview.v2.jidan.owner-draft.v1';sessionStorage.setItem(key,'owner-sentinel');sessionStorage.setItem('jidan.owner-draft.v1','real-sentinel');const before=document.cookie
 const {default:Preview}=await import('./PreviewApp');render(<Preview/>);await screen.findByRole('heading',{name:url==='/__preview'?'화면 탐색':'등록 완료'})
 expect(sessionStorage.getItem(key)).toBe('owner-sentinel');expect(sessionStorage.getItem('jidan.owner-draft.v1')).toBe('real-sentinel');expect(document.cookie).toBe(before);expect(fetch).not.toHaveBeenCalled()
})
