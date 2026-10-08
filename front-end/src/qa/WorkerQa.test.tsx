import {fireEvent,render,screen} from '@testing-library/react'
import {beforeEach,expect,it,vi} from 'vitest'
import {WorkerQa} from './WorkerQa'
import {WorkerManual} from '../workspace/WorkerManual'
import {call,pages} from '../api/operations'
vi.mock('../api/operations',()=>({call:vi.fn(),pages:vi.fn()}))
const store='11111111-1111-4111-8111-111111111111'
const access={store:{id:store,name:'광운대점'},access:{permissions:['USE_AI_QA']},publishedVersionId:store}
beforeEach(()=>{vi.mocked(call).mockReset();vi.mocked(pages).mockReset()})
it('게시 매뉴얼에서 Q&A 목록으로 진입한다',async()=>{
 vi.mocked(pages).mockResolvedValue([access]);vi.mocked(call).mockResolvedValue({sections:[]} as never)
 const navigate=vi.fn();render(<WorkerManual storeId={store} id="" navigate={navigate} onBack={()=>{}}/>)
 fireEvent.click(await screen.findByRole('button',{name:'AI에게 업무 질문하기'}))
 expect(navigate).toHaveBeenCalledWith(`/home?view=qa&store=${store}`)
})
it('미게시 매장은 질문 진입을 제공하지 않는다',async()=>{
 vi.mocked(call).mockResolvedValue({...access,publishedVersionId:null} as never)
 render(<WorkerQa storeId={store} id="" newChat navigate={()=>{}}/>)
 await screen.findByText('아직 게시된 매뉴얼이 없어요. 점주님께 확인해 주세요.')
 expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
})
it('권한이 없는 매장은 질문 진입을 제공하지 않는다',async()=>{
 vi.mocked(call).mockResolvedValue({...access,access:{permissions:[]}} as never)
 render(<WorkerQa storeId={store} id="" newChat navigate={()=>{}}/>)
 await screen.findByText('이 매장의 AI 질문을 이용할 수 없어요.')
 expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
})
it('빈 대화 목록에서 새 대화를 시작한다',async()=>{
 vi.mocked(call).mockResolvedValue(access as never)
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({items:[],page:0,size:20,totalItems:0,asOf:'2026-10-09T00:00:00Z'}))))
 const navigate=vi.fn();render(<WorkerQa storeId={store} id="" newChat={false} navigate={navigate}/>)
 fireEvent.click(await screen.findByRole('button',{name:'새 질문 시작하기'}))
 expect(navigate).toHaveBeenCalledWith(`/home?view=qa&store=${store}&new=1`)
})
