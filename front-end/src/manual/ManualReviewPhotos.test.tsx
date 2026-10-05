import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {ManualReviewPhotos} from './ManualReviewPhotos'
import {ManualPhoto} from './ManualPhoto'
import {createManualPreviewService} from '../dev/manualPreviewService'
import {interviewFixture,reviewFixture} from '../dev/manualFixtures'
import type {ManualIntentReview} from './types'
beforeEach(()=>{vi.stubGlobal('URL',Object.assign(URL,{createObjectURL:vi.fn(()=> 'blob:photo'),revokeObjectURL:vi.fn()}))})
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
it('삭제·순서 변경은 연결만 갱신하며 저장된 이름을 유지한다',async()=>{
 const service=createManualPreviewService(true,'review'),call=vi.spyOn(service,'call'),onUpdate=vi.fn()
 const photos=[{mediaId:crypto.randomUUID(),title:'사진 1',caption:null},{mediaId:crypto.randomUUID(),title:'사진 2',caption:null}],review:ManualIntentReview={...reviewFixture,content:{...reviewFixture.content!,structurePhotos:photos}}
 const {rerender}=render(<ManualReviewPhotos service={service} sessionId={interviewFixture.id} review={review} target={{target:'WORK_STRUCTURE',sectionId:null}} onUpdate={onUpdate} onClose={vi.fn()}/>)
 fireEvent.click(screen.getByRole('button',{name:'사진 2 앞으로'}));await waitFor(()=>expect(onUpdate).toHaveBeenCalled())
 const request=call.mock.calls.find(c=>c[0]==='replaceManualInterviewReviewPhotos')![2]
 expect(request).toMatchObject({photos:[photos[1],photos[0]],expectedRevision:review.revision,sectionId:null})
 rerender(<ManualReviewPhotos service={service} sessionId={interviewFixture.id} review={onUpdate.mock.calls[0][0]} target={{target:'WORK_STRUCTURE',sectionId:null}} onUpdate={onUpdate} onClose={vi.fn()}/>);await waitFor(()=>expect(screen.getByRole('button',{name:'사진 1 삭제'})).toBeEnabled())
 fireEvent.click(screen.getByRole('button',{name:'사진 1 삭제'}));await waitFor(()=>expect(onUpdate).toHaveBeenCalledTimes(2))
 expect(call.mock.calls.some(c=>c[0]==='deleteUnusedManualMedia')).toBe(false)
 expect(onUpdate.mock.calls[1][0].content.structurePhotos).toEqual([photos[1]])
 expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
})
it('사진은 보호된 읽기 API를 사용하고 URL·읽기 요청을 해제한다',async()=>{
 const service=createManualPreviewService(true,'review'),call=vi.spyOn(service,'call'),photo=reviewFixture.content!.structurePhotos![0]
 const {unmount}=render(<ManualPhoto service={service} photo={photo}/>);await screen.findByRole('img',{name:photo.title})
 expect(call.mock.calls[0][0]).toBe('readManualPhoto');expect(call.mock.calls[0][1]).toEqual({mediaId:photo.mediaId})
 unmount();expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:photo');expect(call.mock.calls[0][3].signal.aborted).toBe(true)
})
it('삭제된 업무 대상에는 사진 선택을 허용하지 않는다',()=>{
 render(<ManualReviewPhotos service={createManualPreviewService()} sessionId={interviewFixture.id} review={reviewFixture} target={{target:'SECTION',sectionId:crypto.randomUUID()}} onUpdate={vi.fn()} onClose={vi.fn()}/> )
 expect(screen.getByRole('alert')).toHaveTextContent('연결할 업무가 바뀌었어요');expect(screen.queryByRole('button',{name:'＋사진 추가'})).not.toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'첨부 완료'}))
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
