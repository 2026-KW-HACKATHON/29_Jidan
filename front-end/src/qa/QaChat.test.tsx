import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import '../manual/dialogTestSetup'
import {QaChat} from './QaChat'
import type {QaService} from './service'
import {ApiError} from '../api/client'
afterEach(cleanup)
const store='11111111-1111-4111-8111-111111111111'
const turn={id:store,conversationId:store,sequence:1,manualVersionId:store,status:'READY',text:'퇴근할 때 음식물도 제가 버려야 하나요?',imageMediaIds:[],createdAt:'2026-10-09T00:00:00Z',completedAt:'2026-10-09T00:00:01Z',answer:{outcome:'NEEDS_OWNER',text:'점주님께 확인해 주세요.',citations:[]},error:null}
const response=(data:unknown)=>({data,retryAfterMs:2000})
it('공백 전송을 차단하고 질문을 보내면 실제 결과와 점주 확인 안내를 표시한다',async()=>{
 const call=vi.fn(async(name)=>response(name==='createQAConversation'?{id:store}:turn))
 render(<QaChat service={{storeId:store,call} as QaService} storeName="광운대점" onBack={()=>{}} onLatest={()=>{}}/>)
 expect(screen.getByRole('button',{name:'보내기'})).toBeDisabled()
 fireEvent.change(screen.getByRole('textbox'),{target:{value:'  '}});expect(screen.getByRole('button',{name:'보내기'})).toBeDisabled()
 fireEvent.change(screen.getByRole('textbox'),{target:{value:turn.text}});fireEvent.click(screen.getByRole('button',{name:'보내기'}))
 await screen.findByText(turn.answer.text);await waitFor(()=>expect(screen.getByRole('textbox')).toHaveValue(''));expect(screen.getByText(turn.text)).toBeVisible()
 expect(screen.getByText('매뉴얼만으로 확인하기 어려워요. 점주님께 확인해 주세요.')).toBeVisible()
})
it('실패한 답변을 재시도하고 처리 중에는 새 질문 전송을 막는다',async()=>{
 let finish!:(v:unknown)=>void
 const call=vi.fn(async(name)=>{
  if(name==='getQAConversation')return response({turns:[{...turn,status:'ERROR',answer:null,error:{code:'AI_PROCESSING_FAILED'}}],nextBeforeSequence:null})
  if(name==='retryManualQuestion')return response({...turn,status:'RUNNING',answer:null,completedAt:null})
  return new Promise(r=>{finish=r})
 })
 render(<QaChat service={{storeId:store,call} as QaService} storeName="광운대점" conversationId={store} onBack={()=>{}} onLatest={()=>{}}/>)
 fireEvent.click(await screen.findByRole('button',{name:'답변 다시 시도'}))
 await screen.findByText('매뉴얼에서 답변을 찾고 있어요.');expect(screen.getByRole('textbox')).toBeDisabled()
 finish(response(turn));await screen.findByText(turn.answer.text)
})
it('접근 종료는 대화 내용을 제거하고 입력창을 숨긴다',async()=>{
 const call=vi.fn().mockRejectedValue(new ApiError(404,'RESOURCE_NOT_FOUND'))
 render(<QaChat service={{storeId:store,call} as QaService} storeName="광운대점" conversationId={store} onBack={()=>{}} onLatest={()=>{}}/>)
 await waitFor(()=>expect(screen.queryByRole('textbox')).not.toBeInTheDocument())
 expect(screen.getByRole('button',{name:'매장으로 돌아가기'})).toBeVisible()
})

const citation={versionId:store,sectionId:store,sectionTitle:'음식물 처리 근거',excerpt:'점주 담당'}
const answered={...turn,answer:{outcome:'ANSWERED',text:'음식물은 점주님이 처리해요.',citations:[citation]}}
const detail={versionId:store,storeId:store,ownerConfirmed:true,publishedAt:'2026-10-09T00:00:00Z',section:{id:store,category:'RULE',shiftId:null,title:citation.sectionTitle,steps:[{id:store,instruction:'점주가 음식물을 처리합니다.',checklistItem:false}],photos:[{mediaId:store,title:'보관 장소',caption:null}]}}
it.each([[401,'UNAUTHENTICATED'],[403,'FORBIDDEN'],[404,'RESOURCE_NOT_FOUND']] as const)('근거 상세 후 사진 %s 접근 종료는 대화와 작성 입력을 제거한다',async(status,code)=>{
 let finishPhoto!:(response:Response)=>void
 vi.stubGlobal('fetch',vi.fn(async(url)=>String(url).includes('/content')?new Promise<Response>(resolve=>{finishPhoto=resolve}):new Response(JSON.stringify(detail),{status:200})))
 const call=vi.fn(async()=>response({turns:[answered],nextBeforeSequence:null}))
 render(<QaChat service={{storeId:store,call} as QaService} storeName="광운대점" conversationId={store} onBack={()=>{}} onLatest={()=>{}}/>)
 await screen.findByText(answered.answer.text)
 fireEvent.change(screen.getByRole('textbox'),{target:{value:'작성 중인 후속 질문'}})
 fireEvent.click(screen.getByRole('button',{name:citation.sectionTitle}))
 await screen.findByText(detail.section.steps[0].instruction)
 await waitFor(()=>expect(finishPhoto).toBeTypeOf('function'))
 finishPhoto(new Response(JSON.stringify({code}),{status}))
 await screen.findByRole('button',{name:'매장으로 돌아가기'})
 expect(screen.queryByText(turn.text)).not.toBeInTheDocument()
 expect(screen.queryByText(answered.answer.text)).not.toBeInTheDocument()
 expect(screen.queryByText(detail.section.steps[0].instruction)).not.toBeInTheDocument()
 expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
 expect(screen.queryByDisplayValue('작성 중인 후속 질문')).not.toBeInTheDocument()
})
it('근거 사진의 일시적인 서버 오류는 대화와 작성 입력을 유지한다',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(url)=>String(url).includes('/content')?new Response(JSON.stringify({code:'INTERNAL_ERROR'}),{status:500}):new Response(JSON.stringify(detail),{status:200})))
 const call=vi.fn(async()=>response({turns:[answered],nextBeforeSequence:null}))
 render(<QaChat service={{storeId:store,call} as QaService} storeName="광운대점" conversationId={store} onBack={()=>{}} onLatest={()=>{}}/>)
 await screen.findByText(answered.answer.text)
 fireEvent.change(screen.getByRole('textbox'),{target:{value:'작성 중인 후속 질문'}})
 fireEvent.click(screen.getByRole('button',{name:citation.sectionTitle}))
 await screen.findByRole('button',{name:'사진 다시 불러오기'})
 expect(screen.queryByRole('button',{name:'매장으로 돌아가기'})).not.toBeInTheDocument()
 expect(screen.getByText(turn.text)).toBeInTheDocument()
 expect(screen.getByDisplayValue('작성 중인 후속 질문')).toBeInTheDocument()
})
