import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {ManualDraftReview} from './ManualDraftReview'
import {ManualAuthoring} from './ManualAuthoring'
import {createManualPreviewService} from '../dev/manualPreviewService'
import {draftFixture} from '../dev/manualDraftFixtures'
import {ManualError} from './service'
vi.mock('./ManualVoiceComposer',()=>({ManualVoiceComposer:({onRecording,disabled}:{onRecording:(recording:unknown,signal:AbortSignal)=>Promise<void>;disabled?:boolean})=><button disabled={disabled} onClick={()=>void onRecording({blob:new Blob(['voice'],{type:'audio/webm'}),duration:3},new AbortController().signal)}>테스트 음성 정정</button>}))
beforeEach(()=>{vi.stubGlobal('URL',Object.assign(URL,{createObjectURL:vi.fn(()=> 'blob:photo'),revokeObjectURL:vi.fn()}))})
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();vi.useRealTimers()})
function setup(scenario='draft') {const service=createManualPreviewService(true,scenario),call=vi.spyOn(service,'call'),onReload=vi.fn();render(<ManualDraftReview service={service} versionId={draftFixture.versionId} onBack={vi.fn()} onReload={onReload}/>);return {service,call,onReload}}
it('최종 검토 진입에서 confirmedAt 없이 생성하고 서버 초안을 다시 읽는다',async()=>{
 const service=createManualPreviewService(true,'ready'),call=vi.spyOn(service,'call');render(<ManualAuthoring service={service} onBack={vi.fn()}/> )
 const button=await screen.findByRole('button',{name:'최종 검토로'});await waitFor(()=>expect(button).toBeEnabled());fireEvent.click(button)
 await screen.findByRole('button',{name:'확인하고 게시'});expect(await screen.findAllByText('먼저 들어온 제품을 앞쪽에 진열하세요.')).toHaveLength(3)
 expect(call.mock.calls.some(c=>c[0]==='generateManualDraft')).toBe(true);expect(call.mock.calls.some(c=>c[0]==='confirmManualInterviewUnderstanding')).toBe(false);expect(call.mock.calls.some(c=>c[0]==='getManualDraft')).toBe(true)
})
it('처리 중에도 기존 내용을 표시하고 게시·추가 정정을 막는다',async()=>{
 setup('correction-running');await screen.findByText('먼저 들어온 제품을 앞쪽에 진열하세요.')
 expect(screen.getByRole('button',{name:'확인하고 게시'})).toBeDisabled();expect(screen.getByRole('button',{name:'수정할게요'})).toBeDisabled();expect(screen.getByRole('status')).toHaveTextContent('수정한 내용을 정리')
})
it('정정 실패는 이전 내용을 보존하며 명시적으로 재확인한 뒤 게시할 수 있다',async()=>{
 const {call}=setup('correction-clarify');await screen.findByRole('button',{name:'이전 내용을 확인했어요'})
 expect(screen.getByText('먼저 들어온 제품을 앞쪽에 진열하세요.')).toBeInTheDocument();expect(screen.getByRole('button',{name:'확인하고 게시'})).toBeDisabled();expect(screen.queryByRole('button',{name:'수정 처리 다시 시도'})).not.toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'이전 내용을 확인했어요'}));fireEvent.click(screen.getByRole('button',{name:'확인하고 게시'}));await screen.findByRole('heading',{name:'매뉴얼을 게시했어요'})
 const request=call.mock.calls.find(c=>c[0]==='publishManualDraft')![2];expect(request).toMatchObject({expectedVersionId:draftFixture.versionId,expectedRevision:1,confirmed:true})
})
it('부족 항목 확인은 서버에 저장한 최신 revision으로 게시하며 미리보기로 확인을 대체하지 않는다',async()=>{
 const {call}=setup('missing');await screen.findByRole('checkbox',{name:'야간조 종료 시간 확인 필요'})
 expect(screen.getByRole('button',{name:'확인하고 게시'})).toBeDisabled();fireEvent.click(screen.getByRole('checkbox'));fireEvent.click(screen.getByRole('button',{name:'부족한 내용을 확인했어요'}))
 await waitFor(()=>expect(screen.getByRole('button',{name:'확인하고 게시'})).toBeEnabled());fireEvent.click(screen.getByRole('button',{name:'확인하고 게시'}));await screen.findByRole('heading',{name:'매뉴얼을 게시했어요'})
 expect(call.mock.calls.find(c=>c[0]==='publishManualDraft')![2]).toMatchObject({expectedRevision:2,acknowledgedIssueIds:['eae0fb79-c6cd-40b1-a127-f3e3e93c5f87']})
})
it('발행 응답 유실은 자동 게시 성공 표시 없이 같은 키·본문으로 재시도한다',async()=>{
 const service=createManualPreviewService(true,'draft'),original=service.call.bind(service);let lost=true
 const call=vi.spyOn(service,'call').mockImplementation(async(...args)=>{const result=await original(...args);if(args[0]==='publishManualDraft'&&lost){lost=false;throw Error('lost')}return result})
 render(<ManualDraftReview service={service} versionId={draftFixture.versionId} onBack={vi.fn()} onReload={vi.fn()}/>);await screen.findByText('먼저 들어온 제품을 앞쪽에 진열하세요.');fireEvent.click(screen.getByRole('button',{name:'확인하고 게시'}));await screen.findByRole('button',{name:'요청 다시 시도'})
 expect(screen.queryByRole('heading',{name:'매뉴얼을 게시했어요'})).not.toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'요청 다시 시도'}));await screen.findByRole('heading',{name:'매뉴얼을 게시했어요'})
 const requests=call.mock.calls.filter(c=>c[0]==='publishManualDraft');expect(requests).toHaveLength(2);expect(requests[0][2]).toEqual(requests[1][2]);expect(requests[0][3].key).toBe(requests[1][3].key)
})
it('음성 정정 후 작업 조회와 초안 재조회로 변경 내용을 표시한다',async()=>{
 vi.useFakeTimers({shouldAdvanceTime:true});const {call}=setup();await screen.findByText('먼저 들어온 제품을 앞쪽에 진열하세요.');fireEvent.click(screen.getByRole('button',{name:'야간조 수정할게요'}));fireEvent.click(screen.getByRole('button',{name:'테스트 음성 정정'}))
 await waitFor(()=>expect(call.mock.calls.some(c=>c[0]==='createManualDraftCorrection')).toBe(true));await act(()=>vi.advanceTimersByTimeAsync(2100))
 await waitFor(()=>expect(screen.getByText(/06:00/)).toBeInTheDocument());expect(call.mock.calls.some(c=>c[0]==='getManualDraftCorrection')).toBe(true);expect(screen.getByRole('button',{name:'확인하고 게시'})).toBeEnabled()
})
it('정정 무변경 성공은 revision과 부족 항목 확인을 유지한다',async()=>{
 const service=createManualPreviewService(true,'correction-noop'),call=vi.spyOn(service,'call'),signal=new AbortController().signal
 const job=await service.call('createManualDraftCorrection',{}, {expectedVersionId:draftFixture.versionId,expectedRevision:1,target:{kind:'MANUAL',targetId:null},input:{method:'VOICE',transcriptionId:crypto.randomUUID()}},{signal,key:crypto.randomUUID()})
 const result=await service.call('getManualDraftCorrection',{correctionId:job.data.id},undefined,{signal});expect(result.data.resultRevision).toBe(1)
 render(<ManualDraftReview service={service} versionId={draftFixture.versionId} onBack={vi.fn()} onReload={vi.fn()}/>);await screen.findByText('먼저 들어온 제품을 앞쪽에 진열하세요.');fireEvent.click(screen.getByRole('button',{name:'확인하고 게시'}));await screen.findByRole('heading',{name:'매뉴얼을 게시했어요'});expect(call.mock.calls.find(c=>c[0]==='publishManualDraft')![2]).toMatchObject({expectedRevision:1})
})
it('이전 초안에 도착한 결과와 version 교체는 현재 초안에 적용하지 않는다',async()=>{
 const service=createManualPreviewService(true,'draft'),original=service.call.bind(service)
 vi.spyOn(service,'call').mockImplementation(async(...args)=>{if(args[0]==='getManualDraft')return {data:{...draftFixture,versionId:crypto.randomUUID()},retryAfterMs:2000} as Awaited<ReturnType<typeof original>>;return original(...args)})
 render(<ManualDraftReview service={service} versionId={draftFixture.versionId} onBack={vi.fn()} onReload={vi.fn()}/>);await screen.findByRole('alert');expect(screen.getByRole('button',{name:'확인하고 게시'})).toBeDisabled();expect(screen.queryByText('먼저 들어온 제품을 앞쪽에 진열하세요.')).not.toBeInTheDocument()
})
it('재시도 불가 conflict는 최신 초안 조회를 요구하고 게시 성공으로 표시하지 않는다',async()=>{
 const service=createManualPreviewService(true,'draft'),original=service.call.bind(service),onReload=vi.fn()
 vi.spyOn(service,'call').mockImplementation(async(...args)=>{if(args[0]==='publishManualDraft')throw new ManualError('REVISION_CONFLICT');return original(...args)})
 render(<ManualDraftReview service={service} versionId={draftFixture.versionId} onBack={vi.fn()} onReload={onReload}/>);await screen.findByText('먼저 들어온 제품을 앞쪽에 진열하세요.');fireEvent.click(screen.getByRole('button',{name:'확인하고 게시'}));await screen.findByRole('alert');fireEvent.click(screen.getByRole('button',{name:'최신 작성 상태 다시 불러오기'}));expect(onReload).toHaveBeenCalled();expect(screen.queryByRole('heading',{name:'매뉴얼을 게시했어요'})).not.toBeInTheDocument()
})
it('실제 내용 변경 성공은 서버가 초기화한 부족 항목을 다시 확인하게 하고 무변경은 확인을 유지한다',async()=>{
 for(const scenario of ['missing','noop-missing']){
  const {call}=setup(scenario);await screen.findByRole('checkbox',{name:'야간조 종료 시간 확인 필요'});fireEvent.click(screen.getByRole('checkbox'));fireEvent.click(screen.getByRole('button',{name:'부족한 내용을 확인했어요'}));await waitFor(()=>expect(screen.getByRole('button',{name:'확인하고 게시'})).toBeEnabled())
  fireEvent.click(screen.getByRole('button',{name:'재고 정리 수정할게요'}));fireEvent.click(screen.getByRole('button',{name:'테스트 음성 정정'}));await waitFor(()=>expect(call.mock.calls.some(c=>c[0]==='getManualDraftCorrection')).toBe(true));await waitFor(()=>expect(screen.getByRole('button',{name:'수정할게요'})).toBeEnabled())
  if(scenario==='missing'){expect(screen.getByRole('checkbox')).not.toBeChecked();expect(screen.getByRole('button',{name:'확인하고 게시'})).toBeDisabled();expect(screen.getByText('물품의 유통기한을 먼저 확인하세요.')).toBeInTheDocument()}
  else{expect(screen.getByRole('checkbox')).toBeChecked();expect(screen.getByRole('button',{name:'확인하고 게시'})).toBeEnabled()}
  cleanup()
 }
})
it('생성 실패를 같은 session revision으로 재시도하여 저장된 초안 검토로 복구한다',async()=>{
 const {call}=setup('generation-error');fireEvent.click(await screen.findByRole('button',{name:'매뉴얼 생성 다시 시도'}));await screen.findAllByText('먼저 들어온 제품을 앞쪽에 진열하세요.')
 const request=call.mock.calls.find(c=>c[0]==='retryManualInterviewProcessing');expect(request?.[1]).toEqual({sessionId:draftFixture.interviewSessionId});expect(request?.[2]).toMatchObject({expectedRevision:expect.any(Number)})
})

it('숨겨진 화면의 대기 중 이탈은 요청 없이 취소되며 처리되지 않은 오류를 만들지 않는다',async()=>{
 vi.spyOn(document,'visibilityState','get').mockReturnValue('hidden');const service=createManualPreviewService(true,'draft'),call=vi.spyOn(service,'call')
 const {unmount}=render(<ManualDraftReview service={service} versionId={draftFixture.versionId} onBack={vi.fn()} onReload={vi.fn()}/>)
 unmount();await act(async()=>{await Promise.resolve()});expect(call).not.toHaveBeenCalled()
})
