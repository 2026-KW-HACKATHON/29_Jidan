import './dialogTestSetup'
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {ManualInterview} from './ManualInterview'
import {createManualPreviewService} from '../dev/manualPreviewService'
import {interviewFixture} from '../dev/manualFixtures'
import {schemas} from './contract.generated'
import {matches} from './validation'
vi.mock('./ManualVoiceComposer',()=>({ManualVoiceComposer:({onRecording}:{onRecording:(recording:unknown,signal:AbortSignal)=>Promise<void>})=><button onClick={()=>void onRecording({blob:new Blob(['sound'],{type:'audio/webm'}),duration:3},new AbortController().signal)}>테스트 음성 제출</button>}))
afterEach(()=>{cleanup();vi.useRealTimers()})
async function setup(scenario='review'){const service=createManualPreviewService(true,scenario);const signal=new AbortController().signal;const initial=(await service.call('getManualInterview',{sessionId:interviewFixture.id},undefined,{signal})).data;const call=vi.spyOn(service,'call');await act(async()=>{render(<ManualInterview initial={initial} service={service} onBack={vi.fn()}/>)});return {service,call,initial}}
it('요약 확인은 다음 질문보다 먼저 나타나며 오류는 오버레이에서 복구한다',async()=>{
 const {call}=await setup('review-error');await screen.findByRole('button',{name:'요약 다시 시도'});expect(screen.queryByRole('button',{name:'테스트 음성 제출'})).toBeNull()
 expect(screen.queryByRole('button',{name:'나중에 확인하기'})).toBeNull();expect(screen.queryByRole('button',{name:'질문·답변 이력'})).toBeNull()
 fireEvent.click(screen.getByRole('button',{name:'요약 다시 시도'}));await waitFor(()=>expect(screen.getByRole('button',{name:'네, 맞아요'})).toBeEnabled())
 fireEvent.click(screen.getByRole('button',{name:'네, 맞아요'}));await screen.findByText('모두가 하는 일은 무엇인가요?');expect(call.mock.calls.some(([n])=>n==='confirmManualInterviewUnderstanding')).toBe(true)
})
it('요약 음성 정정은 선택 intent의 review revision을 쓰고 질문 답변을 덮어쓰지 않는다',async()=>{
 const {call,initial}=await setup();await screen.findByRole('button',{name:'수정할게요'});fireEvent.click(screen.getByRole('button',{name:'수정할게요'}));fireEvent.click(screen.getByRole('button',{name:'테스트 음성 제출'}));await screen.findByText(/22:00.*06:00/);expect(screen.getByText(/22:00.*06:00/)).toBeInTheDocument()
 const correction=call.mock.calls.find(([n])=>n==='correctManualInterviewUnderstanding')!;expect(correction[1]).toEqual({sessionId:initial.id,intentId:initial.intents[0].id});expect(correction[2]).toMatchObject({expectedRevision:3,input:{method:'VOICE'}});expect(call.mock.calls.some(([n])=>n==='answerManualInterviewQuestion')).toBe(false);expect(screen.queryByText('샘플 음성 답변')).not.toBeInTheDocument()
})
it('기본 질문과 추가 질문은 각 서버 questionId에 답하고 다음 흐름을 조회한다',async()=>{
 vi.useFakeTimers();const {call,initial}=await setup('');fireEvent.click(screen.getByRole('button',{name:'테스트 음성 제출'}));await act(async()=>{await Promise.resolve();await Promise.resolve()});await act(async()=>vi.advanceTimersByTimeAsync(2100));expect(screen.getByText('처음 일하는 근무자도 따라 할 수 있게 조금 더 알려주세요.')).toBeVisible();fireEvent.click(screen.getByRole('button',{name:'테스트 음성 제출'}));await act(async()=>{await Promise.resolve();await Promise.resolve()});await act(async()=>vi.advanceTimersByTimeAsync(2100));expect(screen.getByRole('heading',{name:'이렇게 이해했어요'})).toBeVisible();fireEvent.click(screen.getByRole('button',{name:'네, 맞아요'}));await act(async()=>{await Promise.resolve();await Promise.resolve()});expect(screen.getByText('모두가 하는 일은 무엇인가요?')).toBeVisible();const answers=call.mock.calls.filter(([n])=>n==='answerManualInterviewQuestion');expect(answers).toHaveLength(2);expect(answers[0][2]).toMatchObject({questionId:initial.questions[0].id});expect((answers[0][2] as {questionId:string}).questionId).not.toBe((answers[1][2] as {questionId:string}).questionId)
})
it('모든 요약을 차례로 확인해야 최종 검토로 진행한다',async()=>{
 const service=createManualPreviewService(true,'ready'),signal=new AbortController().signal;const initial=(await service.call('getManualInterview',{sessionId:interviewFixture.id},undefined,{signal})).data,onFinal=vi.fn();render(<ManualInterview initial={initial} service={service} onBack={vi.fn()} onFinal={onFinal}/>);
 for(let i=0;i<initial.intents.length;i++){await waitFor(()=>expect(screen.getByRole('button',{name:'네, 맞아요'})).toBeEnabled());fireEvent.click(screen.getByRole('button',{name:'네, 맞아요'}));await act(async()=>{await Promise.resolve()})}
 await waitFor(()=>expect(screen.getByRole('button',{name:'최종 검토로'})).toBeEnabled());fireEvent.click(screen.getByRole('button',{name:'최종 검토로'}));expect(onFinal).toHaveBeenCalledWith(initial)
})
it.each(['review','review-error','depth5','ready','review-photo'])('모의 상태 %s는 OpenAPI 세션과 검토 응답 제약을 따른다',async scenario=>{
 const service=createManualPreviewService(true,scenario),signal=new AbortController().signal;const session=(await service.call('getManualInterview',{sessionId:interviewFixture.id},undefined,{signal})).data;const reviews=(await service.call('listManualIntentReviews',{sessionId:session.id},undefined,{signal})).data
 expect(matches(schemas.ManualInterviewSession,session,schemas)).toBe(true);expect(matches(schemas.ManualIntentReviewList,reviews,schemas)).toBe(true)
})

it('다음 질문의 이전 요약 사진 추천은 이해 확인 중 보이고 생략은 요약으로 돌아온다',async()=>{
 const service=createManualPreviewService(true,'review'),original=service.call.bind(service),signal=new AbortController().signal
 const initial=(await original('getManualInterview',{sessionId:interviewFixture.id},undefined,{signal})).data
 const sectionId=crypto.randomUUID(),cardId=crypto.randomUUID(),intentId=initial.intents[0].id
 const card={id:cardId,type:'PHOTO_SUGGESTIONS' as const,title:'이 요약의 추천 사진',items:[{id:crypto.randomUUID(),label:'열쇠 위치'}],attachmentTarget:{intentId,target:'SECTION' as const,sectionId}}
 initial.questions[0].guidanceCards=[card]
 const call=vi.spyOn(service,'call').mockImplementation(async(...args)=>{
  const result=await original(...args)
  if(args[0]==='getManualInterview')return {...result,data:{...initial}} as typeof result
  if(args[0]==='listManualIntentReviews'){
   const data=result.data as import('./types').ManualIntentReviewList
   return {...result,data:{...data,items:data.items.map(review=>({...review,content:{...review.content!,sections:[{id:sectionId,category:'COMMON_TASK',shiftId:null,title:'열쇠 보관',steps:[],photos:[]}]}}))}} as typeof result
  }
  return result
 })
 render(<ManualInterview initial={initial} service={service} onBack={vi.fn()}/>)
 await screen.findByText('이 요약의 추천 사진')
 expect(screen.getByRole('heading',{name:'이렇게 이해했어요'})).toBeVisible()
 expect(screen.queryByText('모두가 하는 일은 무엇인가요?')).toBeNull()
 fireEvent.click(screen.getByRole('button',{name:'사진 첨부하기'}))
 expect(screen.getByRole('heading',{name:'이해한 내용에 사진을 더해주세요'})).toBeVisible()
 fireEvent.click(screen.getByRole('button',{name:'사진 없이 계속하기'}))
 expect(screen.getByRole('heading',{name:'이렇게 이해했어요'})).toBeVisible()
 expect(call.mock.calls.some(([name])=>['answerManualInterviewQuestion','replaceManualInterviewReviewPhotos','confirmManualInterviewUnderstanding'].includes(name))).toBe(false)
 fireEvent.click(screen.getByRole('button',{name:'네, 맞아요'}))
 await screen.findByText('모두가 하는 일은 무엇인가요?')
 expect(screen.queryByRole('heading',{name:'이해한 내용에 사진을 더해주세요'})).toBeNull()
})

it('마지막 인텐트는 질문 없이 기존 수동 사진 관리를 열고 기존 사진을 보존한다',async()=>{
 const {call,initial}=await setup('ready')
 for(let i=0;i<initial.intents.length-1;i++){
  await waitFor(()=>expect(screen.getByRole('button',{name:'네, 맞아요'})).toBeEnabled())
  fireEvent.click(screen.getByRole('button',{name:'네, 맞아요'}));await act(async()=>{await Promise.resolve()})
 }
 await screen.findByText('화장실 이용')
 expect(initial.phase).toBe('READY_TO_GENERATE');expect(initial.questions).toEqual([])
 expect(screen.queryByRole('button',{name:'테스트 음성 제출'})).toBeNull()
 fireEvent.click(screen.getByRole('button',{name:/사진 (첨부하기|관리)/}))
 await screen.findByRole('heading',{name:'업무를 사진으로 보여주세요'})
 const before=call.mock.calls.filter(([name])=>name==='replaceManualInterviewReviewPhotos').length
 fireEvent.click(screen.getByRole('button',{name:/첨부 완료|사진 없이 돌아가기/}))
 expect(screen.getByRole('heading',{name:'이렇게 이해했어요'})).toBeVisible()
 expect(call.mock.calls.filter(([name])=>name==='replaceManualInterviewReviewPhotos')).toHaveLength(before)
 expect(call.mock.calls.some(([name])=>name==='answerManualInterviewQuestion')).toBe(false)
})

it('요약을 떠난 뒤 늦게 도착한 추천은 사진 화면이나 이전 요약으로 강제 이동하지 않는다',async()=>{
 vi.useFakeTimers({shouldAdvanceTime:true})
 const service=createManualPreviewService(true,'review'),original=service.call.bind(service),signal=new AbortController().signal
 const initial=(await original('getManualInterview',{sessionId:interviewFixture.id},undefined,{signal})).data
 let late=false
 vi.spyOn(service,'call').mockImplementation(async(...args)=>{
  const result=await original(...args)
  if(args[0]==='getManualInterview'&&late){
   const data=result.data as import('./types').ManualInterviewSession
   return {...result,data:{...data,revision:data.revision+1,questions:data.questions.map(question=>({...question,guidanceCards:[{id:crypto.randomUUID(),type:'PHOTO_SUGGESTIONS',title:'늦은 추천',items:[{id:crypto.randomUUID(),label:'참고 위치'}],attachmentTarget:{intentId:initial.intents[0].id,target:'SECTION',sectionId:crypto.randomUUID()}}]}))}} as typeof result
  }
  return result
 })
 render(<ManualInterview initial={initial} service={service} onBack={vi.fn()}/>)
 fireEvent.click(await screen.findByRole('button',{name:'네, 맞아요'}))
 await screen.findByText('모두가 하는 일은 무엇인가요?')
 late=true;await act(()=>vi.advanceTimersByTimeAsync(2100))
 expect(screen.getByText('늦은 추천')).toBeVisible()
 expect(screen.queryByRole('heading',{name:'이렇게 이해했어요'})).toBeNull()
 expect(screen.queryByRole('button',{name:'사진 없이 계속하기'})).toBeNull()
})

it('사진 요청에서 돌아간 뒤 늦은 검토 조회가 사진 편집 화면을 다시 열지 않는다',async()=>{
 const service=createManualPreviewService(true,'photo-request'),original=service.call.bind(service),signal=new AbortController().signal
 const initial=(await original('getManualInterview',{sessionId:interviewFixture.id},undefined,{signal})).data
 let finish:()=>void=()=>{};const deferred=new Promise<void>(resolve=>{finish=resolve})
 const call=vi.spyOn(service,'call').mockImplementation(async(...args)=>{if(args[0]==='getManualIntentReview')await deferred;return original(...args)})
 render(<ManualInterview initial={initial} service={service} onBack={vi.fn()}/>)
 fireEvent.click(await screen.findByRole('button',{name:'사진 첨부하기'}))
 fireEvent.click(screen.getByRole('button',{name:'사진 첨부하기'}))
 await waitFor(()=>expect(call.mock.calls.some(([name])=>name==='getManualIntentReview')).toBe(true))
 fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}))
 await act(async()=>{finish();await deferred})
 expect(screen.queryByRole('heading',{name:'업무를 사진으로 보여주세요'})).toBeNull()
 expect(screen.queryByRole('button',{name:'사진 없이 계속하기'})).toBeNull()
 expect(screen.getByText(initial.questions[0].text.replace('\n',' '))).toBeVisible()
})
