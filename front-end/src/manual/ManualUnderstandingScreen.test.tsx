import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {ManualUnderstandingScreen} from './ManualUnderstandingScreen'
import {reviewFixture} from '../dev/manualFixtures'
import {draftFixture} from '../dev/manualDraftFixtures'
import {createManualPreviewService} from '../dev/manualPreviewService'
afterEach(cleanup)
it('공통 업무에는 포함된 근무조를 노출하지 않고 해당 업무·사진을 묶는다',()=>{
 const onPhotos=vi.fn(),section={...draftFixture.content.sections[0],photos:[]};render(<ManualUnderstandingScreen content={{...reviewFixture.content!,sections:[section]}} stage="COMMON_TASKS" service={createManualPreviewService()} onPhotos={onPhotos}/> )
 expect(screen.queryByText('근무조와 시간')).toBeNull();expect(screen.queryByText('정리한 내용')).toBeNull();expect(screen.getByText(`공통 업무 · ${section.title}`)).toBeVisible()
 fireEvent.click(screen.getByRole('button',{name:'사진 첨부하기'}));expect(onPhotos).toHaveBeenCalledWith({target:'SECTION',sectionId:section.id})
})
it('근무 구조에는 해당 요약과 근무표 사진 대상만 표시한다',()=>{
 const onPhotos=vi.fn();render(<ManualUnderstandingScreen content={{...reviewFixture.content!,structurePhotos:[]}} stage="WORK_STRUCTURE" service={createManualPreviewService()} onPhotos={onPhotos}/>)
 expect(screen.getByText(/22:00/)).toBeVisible();fireEvent.click(screen.getByRole('button',{name:'사진 첨부하기'}));expect(onPhotos).toHaveBeenCalledWith({target:'WORK_STRUCTURE',sectionId:null})
})
