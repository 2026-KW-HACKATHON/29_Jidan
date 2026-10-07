import './dialogTestSetup'
import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {ManualPhotoSourceDialog} from './ManualPhotoSourceDialog'
afterEach(cleanup)
it.each([['카메라로 촬영','camera'],['앨범에서 선택','album'],['파일에서 선택','file']])('%s 선택을 해당 입력에 전달한다',(label,source)=>{
 const onSelect=vi.fn();render(<ManualPhotoSourceDialog open onClose={vi.fn()} onSelect={onSelect}/>);fireEvent.click(screen.getByRole('button',{name:label}));expect(onSelect).toHaveBeenCalledWith(source)
})
it('돌아가기는 선택이나 업로드를 하지 않는다',()=>{
 const onClose=vi.fn(),onSelect=vi.fn();render(<ManualPhotoSourceDialog open onClose={onClose} onSelect={onSelect}/>);fireEvent.click(screen.getByRole('button',{name:'돌아가기'}));expect(onSelect).not.toHaveBeenCalled()
})
