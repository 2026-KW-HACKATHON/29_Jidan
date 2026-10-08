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
