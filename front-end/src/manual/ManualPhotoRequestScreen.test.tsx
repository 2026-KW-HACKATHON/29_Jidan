import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {ManualPhotoRequestScreen} from './ManualPhotoRequestScreen'
import {interviewFixture} from '../dev/manualFixtures'
import type {ManualGuidancePhotos} from './types'
afterEach(cleanup)
const card:ManualGuidancePhotos={id:'photo',type:'PHOTO_SUGGESTIONS',title:'추천 사진',items:[{id:'key',label:'열쇠 위치'}],attachmentTarget:null}
it('사진 없이 계속은 답변 제출 없이 질문으로 복귀한다',()=>{
 const onContinue=vi.fn(),onAttach=vi.fn();render(<ManualPhotoRequestScreen question={interviewFixture.questions[0]} card={card} stage={3} onAttach={onAttach} onContinue={onContinue} busy={false}/> )
 expect(screen.getByRole('button',{name:'사진 첨부하기'})).toBeDisabled();fireEvent.click(screen.getByRole('button',{name:'사진 없이 계속하기'}));expect(onContinue).toHaveBeenCalledOnce();expect(onAttach).not.toHaveBeenCalled()
})
