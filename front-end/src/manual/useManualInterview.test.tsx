import {act,cleanup,renderHook,waitFor} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {useManualInterview} from './useManualInterview'
import {createManualPreviewService} from '../dev/manualPreviewService'
import {interviewFixture} from '../dev/manualFixtures'
afterEach(()=>{cleanup();vi.restoreAllMocks();vi.useRealTimers()})
it('답변 이후 늦은 세션 응답이 질문과 revision을 되돌리지 않는다',async()=>{
 vi.useFakeTimers({shouldAdvanceTime:true})
 const service=createManualPreviewService(true),original=service.call.bind(service),signal=new AbortController().signal
 const initial=(await original('getManualInterview',{sessionId:interviewFixture.id},undefined,{signal})).data
 vi.spyOn(service,'call').mockImplementation(async(...args)=>args[0]==='getManualInterview'?{data:initial,retryAfterMs:2000}:original(...args))
 const {result}=renderHook(()=>useManualInterview({service,initial}));await waitFor(()=>expect(result.current.reviews).not.toBeNull())
 await act(()=>result.current.submit({blob:new Blob(['voice'],{type:'audio/webm'}),duration:3},signal));const revision=result.current.session.revision
 await act(()=>vi.advanceTimersByTimeAsync(2100))
 expect(result.current.session.phase).toBe('PROCESSING');expect(result.current.session.revision).toBe(revision);expect(result.current.question?.id).toBe(initial.questions[0].id);expect(result.current.question?.guidanceCards).toEqual(initial.questions[0].guidanceCards)
})
it('처리 중 새로고침은 서버 질문 스냅샷의 제목과 카드를 복원한다',async()=>{
 const service=createManualPreviewService(true,'processing'),signal=new AbortController().signal
 const initial=(await service.call('getManualInterview',{sessionId:interviewFixture.id},undefined,{signal})).data
 const {result}=renderHook(()=>useManualInterview({service,initial}));expect(result.current.question).toEqual(initial.lastAnsweredQuestion);expect(result.current.session.questions).toHaveLength(0)
})
it('화면 이탈은 폴링 요청을 취소하며 늦은 응답을 적용하지 않는다',async()=>{
 const service=createManualPreviewService(true),original=service.call.bind(service),signal=new AbortController().signal,initial=(await original('getManualInterview',{sessionId:interviewFixture.id},undefined,{signal})).data
 let captured:AbortSignal|undefined
 vi.spyOn(service,'call').mockImplementation(async(...args)=>{captured=args[3].signal;return original(...args)})
 const {unmount}=renderHook(()=>useManualInterview({service,initial}));await act(async()=>{await Promise.resolve()});unmount();expect(captured?.aborted).toBe(true)
})
