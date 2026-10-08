import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import '../manual/dialogTestSetup'
import {QaCitation} from './QaCitation'
afterEach(cleanup)
const store='11111111-1111-4111-8111-111111111111'
it('이전 버전 근거 조회 충돌은 최신 매뉴얼 이동을 제공한다',async()=>{
 const latest=vi.fn(),fetcher=vi.fn().mockResolvedValue(new Response(JSON.stringify({code:'MANUAL_VERSION_CHANGED'}),{status:409}));vi.stubGlobal('fetch',fetcher)
 render(<QaCitation storeId={store} citation={{versionId:store,sectionId:store,sectionTitle:'음식물 처리',excerpt:'점주 담당'}} onClose={()=>{}} onLatest={latest}/>)
 fireEvent.click(await screen.findByRole('button',{name:'최신 매뉴얼 보기'}));expect(latest).toHaveBeenCalledOnce()
 expect(fetcher.mock.calls[0][0]).toContain(`expectedVersionId=${store}`)
})

it('근거를 닫은 뒤 도착한 사진 접근 오류는 상위 대화로 전달하지 않는다',async()=>{
 let finishPhoto!:(response:Response)=>void
 const lost=vi.fn(),detail={versionId:store,storeId:store,ownerConfirmed:true,publishedAt:'2026-10-09T00:00:00Z',section:{id:store,category:'RULE',shiftId:null,title:'음식물 처리',steps:[{id:store,instruction:'점주가 음식물을 처리합니다.',checklistItem:false}],photos:[{mediaId:store,title:'보관 장소',caption:null}]}}
 vi.stubGlobal('fetch',vi.fn(async(url)=>String(url).includes('/content')?new Promise<Response>(resolve=>{finishPhoto=resolve}):new Response(JSON.stringify(detail),{status:200})))
 const {unmount}=render(<QaCitation storeId={store} citation={{versionId:store,sectionId:store,sectionTitle:'음식물 처리',excerpt:'점주 담당'}} onClose={()=>{}} onLatest={()=>{}} onAccessLost={lost}/>)
 await waitFor(()=>expect(finishPhoto).toBeTypeOf('function'))
 unmount()
 await act(async()=>finishPhoto(new Response(JSON.stringify({code:'FORBIDDEN'}),{status:403})))
 expect(lost).not.toHaveBeenCalled()
})
