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
it.each(['review','review-error','depth5','ready'])('모의 상태 %s는 OpenAPI 세션과 검토 응답 제약을 따른다',async scenario=>{
 const service=createManualPreviewService(true,scenario),signal=new AbortController().signal;const session=(await service.call('getManualInterview',{sessionId:interviewFixture.id},undefined,{signal})).data;const reviews=(await service.call('listManualIntentReviews',{sessionId:session.id},undefined,{signal})).data
 expect(matches(schemas.ManualInterviewSession,session,schemas)).toBe(true);expect(matches(schemas.ManualIntentReviewList,reviews,schemas)).toBe(true)
})
