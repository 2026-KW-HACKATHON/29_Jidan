import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it} from 'vitest'
import WorkerHomePreview from './WorkerHomePreview'
afterEach(cleanup)
it('프로필 저장 후 홈에서 다시 열어도 저장한 샘플 값을 유지한다',async()=>{render(<WorkerHomePreview/>);fireEvent.click(screen.getByRole('button',{name:'프로필'}));fireEvent.click(await screen.findByRole('button',{name:'기본 정보 수정'}));fireEvent.change(screen.getByLabelText('이름 *'),{target:{value:'Alex Kim'}});fireEvent.click(screen.getByRole('button',{name:'변경 사항 저장'}));await screen.findByText('프로필 수정이 완료됐어요');fireEvent.click(screen.getByRole('button',{name:'돌아가기'}));await screen.findByText('Alex Kim 님');fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}));fireEvent.click(screen.getByRole('button',{name:'프로필'}));expect(await screen.findByText('Alex Kim 님')).toBeVisible()})
