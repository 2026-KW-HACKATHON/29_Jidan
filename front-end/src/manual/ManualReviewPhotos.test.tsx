import './dialogTestSetup'
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {ManualError} from './service'
import {ManualReviewPhotos} from './ManualReviewPhotos'
import {ManualPhoto} from './ManualPhoto'
import {createManualPreviewService} from '../dev/manualPreviewService'
import {interviewFixture,reviewFixture} from '../dev/manualFixtures'
beforeEach(()=>{vi.stubGlobal('URL',Object.assign(URL,{createObjectURL:vi.fn(()=> 'blob:photo'),revokeObjectURL:vi.fn()}))})
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
it('삭제는 확인 후 연결만 갱신하고 취소하면 요청하지 않는다',async()=>{
 const service=createManualPreviewService(true,'review'),call=vi.spyOn(service,'call'),onUpdate=vi.fn(),photo=reviewFixture.content!.structurePhotos![0]
 render(<ManualReviewPhotos service={service} sessionId={interviewFixture.id} review={reviewFixture} target={{target:'WORK_STRUCTURE',sectionId:null}} onUpdate={onUpdate} onClose={vi.fn()}/>)
 fireEvent.click(screen.getByRole('button',{name:`${photo.title} 삭제`}));expect(call.mock.calls.some(c=>c[0]==='replaceManualInterviewReviewPhotos')).toBe(false)
 fireEvent.click(screen.getByRole('button',{name:'취소'}));expect(onUpdate).not.toHaveBeenCalled()
 fireEvent.click(screen.getByRole('button',{name:`${photo.title} 삭제`}));fireEvent.click(screen.getByRole('button',{name:'사진 삭제'}));await waitFor(()=>expect(onUpdate).toHaveBeenCalledTimes(1))
 expect(call.mock.calls.find(c=>c[0]==='replaceManualInterviewReviewPhotos')![2]).toMatchObject({photos:[],expectedRevision:reviewFixture.revision,sectionId:null})
 expect(call.mock.calls.some(c=>c[0]==='deleteUnusedManualMedia')).toBe(false);expect(screen.queryByText('앞으로')).toBeNull()
})
it('삭제 실패는 기존 사진을 유지하고 같은 요청으로 재시도한다',async()=>{
 const service=createManualPreviewService(true,'review'),original=service.call.bind(service),onUpdate=vi.fn();let fail=true
 const call=vi.spyOn(service,'call').mockImplementation(async(...args)=>{if(args[0]==='replaceManualInterviewReviewPhotos'&&fail){fail=false;throw Error('lost')}return original(...args)})
 render(<ManualReviewPhotos service={service} sessionId={interviewFixture.id} review={reviewFixture} target={{target:'WORK_STRUCTURE',sectionId:null}} onUpdate={onUpdate} onClose={vi.fn()}/>)
 fireEvent.click(screen.getByRole('button',{name:'업무 참고 사진 삭제'}));fireEvent.click(screen.getByRole('button',{name:'사진 삭제'}));await screen.findByRole('button',{name:'첨부 요청 다시 시도'});expect(onUpdate).not.toHaveBeenCalled();expect(screen.getByText('업무 참고 사진')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'첨부 요청 다시 시도'}));await waitFor(()=>expect(onUpdate).toHaveBeenCalledTimes(1));const calls=call.mock.calls.filter(c=>c[0]==='replaceManualInterviewReviewPhotos');expect(calls[0][3].key).toBe(calls[1][3].key)
})
it('사진은 보호된 읽기 API를 사용하고 URL·읽기 요청을 해제한다',async()=>{
 const service=createManualPreviewService(true,'review'),call=vi.spyOn(service,'call'),photo=reviewFixture.content!.structurePhotos![0]
 const {unmount}=render(<ManualPhoto service={service} photo={photo}/>);await screen.findByRole('img',{name:photo.title})
 expect(call.mock.calls[0][0]).toBe('readManualPhoto');expect(call.mock.calls[0][1]).toEqual({mediaId:photo.mediaId})
 unmount();expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:photo');expect(call.mock.calls[0][3].signal.aborted).toBe(true)
})
it('삭제된 업무 대상에는 사진 선택을 허용하지 않는다',()=>{
 render(<ManualReviewPhotos service={createManualPreviewService()} sessionId={interviewFixture.id} review={reviewFixture} target={{target:'SECTION',sectionId:crypto.randomUUID()}} onUpdate={vi.fn()} onClose={vi.fn()}/> )
 expect(screen.getByRole('alertdialog')).toHaveTextContent('연결할 업무가 바뀌었어요');expect(screen.queryByRole('button',{name:'＋사진 추가'})).not.toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'사진 없이 돌아가기'}))
})
it('연결 실패 전까지 로컬 사진을 표시하고 첨부 취소·이탈 시 URL을 해제한다',async()=>{
 const service=createManualPreviewService(true,'review'),original=service.call.bind(service),call=vi.spyOn(service,'call').mockImplementation(async(...args)=>{if(args[0]==='replaceManualInterviewReviewPhotos')throw Error('lost');return original(...args)})
 const {unmount}=render(<ManualReviewPhotos service={service} sessionId={interviewFixture.id} review={reviewFixture} target={{target:'WORK_STRUCTURE',sectionId:null}} onUpdate={vi.fn()} onClose={vi.fn()}/> )
 fireEvent.change(screen.getByLabelText('첨부할 사진'),{target:{files:[]}});expect(call.mock.calls.some(c=>c[0]==='uploadManualMedia')).toBe(false)
 fireEvent.change(screen.getByLabelText('첨부할 사진'),{target:{files:[new File(['p'],'p.png',{type:'image/png'})]}})
 await screen.findByRole('button',{name:'이 첨부 취소'});expect(screen.getByRole('img',{name:'사진 1 연결 전 미리보기'})).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'이 첨부 취소'}));expect(screen.queryByRole('img',{name:'사진 1 연결 전 미리보기'})).not.toBeInTheDocument();expect(URL.revokeObjectURL).toHaveBeenCalled()
 unmount();expect(call.mock.calls.some(c=>c[0]==='deleteUnusedManualMedia')).toBe(false)
})

it('revision 충돌은 첨부 재시도 버튼으로 최신 요약을 읽고 같은 업로드를 연결한다',async()=>{
 const service=createManualPreviewService(true,'review'),original=service.call.bind(service),onUpdate=vi.fn();let conflict=true
 const call=vi.spyOn(service,'call').mockImplementation(async(...args)=>{if(args[0]==='replaceManualInterviewReviewPhotos'&&conflict){conflict=false;throw new ManualError('REVISION_CONFLICT')}return original(...args)})
 render(<ManualReviewPhotos service={service} sessionId={interviewFixture.id} review={reviewFixture} target={{target:'WORK_STRUCTURE',sectionId:null}} onUpdate={onUpdate} onClose={vi.fn()}/>)
 fireEvent.change(screen.getByLabelText('첨부할 사진'),{target:{files:[new File(['p'],'p.png',{type:'image/png'})]}})
 fireEvent.click(await screen.findByRole('button',{name:'첨부 요청 다시 시도'}))
 await waitFor(()=>expect(onUpdate).toHaveBeenCalledOnce())
 expect(call.mock.calls.filter(([name])=>name==='uploadManualMedia')).toHaveLength(1)
 expect(call.mock.calls.filter(([name])=>name==='getManualIntentReview')).toHaveLength(1)
 const links=call.mock.calls.filter(([name])=>name==='replaceManualInterviewReviewPhotos')
 expect(links).toHaveLength(2);expect(links[0][3].key).not.toBe(links[1][3].key)
})

it('추천에서 연 사진 선택 취소는 추천 복귀 콜백만 실행하고 업로드하지 않는다',()=>{
 const service=createManualPreviewService(true,'review'),call=vi.spyOn(service,'call'),cancel=vi.fn()
 render(<ManualReviewPhotos service={service} sessionId={interviewFixture.id} review={reviewFixture} target={{target:'WORK_STRUCTURE',sectionId:null}} onUpdate={vi.fn()} onClose={vi.fn()} onSelectionCancel={cancel}/>)
 fireEvent.change(screen.getByLabelText('첨부할 사진'),{target:{files:[]}})
 expect(cancel).toHaveBeenCalledOnce()
 fireEvent(screen.getByLabelText('첨부할 사진'),new Event('cancel'))
 expect(cancel).toHaveBeenCalledTimes(2)
 expect(call.mock.calls.some(([name])=>name==='uploadManualMedia'||name==='replaceManualInterviewReviewPhotos')).toBe(false)
})
