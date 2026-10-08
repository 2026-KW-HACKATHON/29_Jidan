import {cleanup,fireEvent,render,screen} from '@testing-library/react'
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
