import {render,screen,waitFor} from '@testing-library/react'
import {expect,it,vi} from 'vitest'
import {ApiError} from '../api/client'
import type {QaService} from './service'
import {QaImage} from './QaImage'
it('사진 보관 만료는 재시도 루프 대신 만료 안내를 표시한다',async()=>{
 const call=vi.fn().mockRejectedValue(new ApiError(410,'QA_MEDIA_EXPIRED'))
 render(<QaImage service={{storeId:'store',call} as QaService} mediaId="photo" index={0} onAccessLost={()=>{}}/>)
 await screen.findByText('사진 보관 기간이 지났어요.');expect(screen.queryByRole('button')).not.toBeInTheDocument()
})
it('사진 조회 중 접근이 종료되면 상위 대화에 전달한다',async()=>{
 const error=new ApiError(404,'RESOURCE_NOT_FOUND'),call=vi.fn().mockRejectedValue(error),lost=vi.fn()
 render(<QaImage service={{storeId:'store',call} as QaService} mediaId="photo" index={0} onAccessLost={lost}/>)
 await waitFor(()=>expect(lost).toHaveBeenCalledWith(error))
})
it('화면 이탈 시 보호 사진의 object URL을 해제한다',async()=>{
 const revoke=vi.fn();vi.stubGlobal('URL',class extends URL{static createObjectURL=()=> 'blob:test';static revokeObjectURL=revoke})
 const call=vi.fn().mockResolvedValue({data:new Blob(['photo']),retryAfterMs:2000})
 const {unmount}=render(<QaImage service={{storeId:'store',call} as QaService} mediaId="photo" index={0} onAccessLost={()=>{}}/>)
 await screen.findByRole('img');unmount();expect(revoke).toHaveBeenCalledWith('blob:test')
})
