import './dialogTestSetup'
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {ManualDraftPhotos} from './ManualDraftPhotos'
import {draftFixture} from '../dev/manualDraftFixtures'
import {createManualPreviewService} from '../dev/manualPreviewService'
beforeEach(()=>{vi.stubGlobal('URL',Object.assign(URL,{createObjectURL:vi.fn(()=> 'blob:photo'),revokeObjectURL:vi.fn()}))})
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
it('사진 추가는 초안 version과 revision을 고정하고 다른 내용을 보존한다',async()=>{
 const service=createManualPreviewService(true,'draft'),call=vi.spyOn(service,'call'),onUpdate=vi.fn(),section=draftFixture.content.sections[0]
 render(<ManualDraftPhotos service={service} draft={draftFixture} target={{target:'SECTION',sectionId:section.id}} onUpdate={onUpdate} onClose={vi.fn()}/> )
 fireEvent.change(screen.getByLabelText('첨부할 사진'),{target:{files:[new File(['photo'],'photo.png',{type:'image/png'})]}})
 await waitFor(()=>expect(onUpdate).toHaveBeenCalledOnce())
 const request=call.mock.calls.find(c=>c[0]==='replaceManualDraftContent')![2]
 expect(request).toMatchObject({expectedVersionId:draftFixture.versionId,expectedRevision:1,content:{shifts:draftFixture.content.shifts,sections:[{...section,photos:[{mediaId:expect.any(String),title:'사진 1',caption:null}]}]}})
 expect(call.mock.calls.some(c=>c[0]==='replaceManualInterviewReviewPhotos')).toBe(false)
})
it('오래된 초안 사진 변경은 덮어쓰지 않고 충돌을 표시한다',async()=>{
 const service=createManualPreviewService(true,'draft'),onUpdate=vi.fn()
 render(<ManualDraftPhotos service={service} draft={{...draftFixture,revision:99}} target={{target:'WORK_STRUCTURE',sectionId:null}} onUpdate={onUpdate} onClose={vi.fn()}/>)
 fireEvent.change(screen.getByLabelText('첨부할 사진'),{target:{files:[new File(['photo'],'photo.png',{type:'image/png'})]}})
 await screen.findByRole('alertdialog');expect(onUpdate).not.toHaveBeenCalled();expect(screen.queryByRole('button',{name:'첨부 요청 다시 시도'})).toBeNull()
})
