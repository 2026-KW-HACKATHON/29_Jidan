import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {ManualWorkerPreview} from './ManualWorkerPreview'
import {draftFixture} from '../dev/manualDraftFixtures'
import {createManualPreviewService} from '../dev/manualPreviewService'
afterEach(cleanup)
it('서버 미리보기의 section 개수와 순서로 이동하고 변경 API를 호출하지 않는다',()=>{
 const service=createManualPreviewService(true,'draft'),call=vi.spyOn(service,'call'),onClose=vi.fn(),section=draftFixture.content.sections[0]
 render(<ManualWorkerPreview service={service} preview={{preview:true,versionId:draftFixture.versionId,revision:1,content:{...draftFixture.content,sections:[section,{...section,id:crypto.randomUUID(),title:'매장 청소'}]}}} onClose={onClose}/>)
 expect(screen.getByText('1 / 2')).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'다음'}));expect(screen.getByRole('heading',{name:'매장 청소'})).toBeInTheDocument();expect(screen.getByText('2 / 2')).toBeInTheDocument();expect(screen.queryByRole('button',{name:'다음'})).not.toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'이전'}));expect(screen.getByRole('heading',{name:'재고 정리'})).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'검토로 돌아가기'}));expect(onClose).toHaveBeenCalled();expect(call).not.toHaveBeenCalled()
})
it('미확정 업무 배열도 페이지를 만들어내지 않고 돌아갈 수 있다',()=>{
 render(<ManualWorkerPreview service={createManualPreviewService()} preview={{preview:true,versionId:draftFixture.versionId,revision:1,content:{shifts:[],sections:[]}}} onClose={vi.fn()}/> )
 expect(screen.getByText('등록된 업무가 없어요.')).toBeInTheDocument();expect(screen.queryByRole('button',{name:'다음'})).not.toBeInTheDocument()
})
it('중간 페이지는 이전·다음, 마지막은 이전·검토 완료만 제공한다',()=>{
 const onClose=vi.fn(),section=draftFixture.content.sections[0],service=createManualPreviewService(),call=vi.spyOn(service,'call')
 render(<ManualWorkerPreview service={service} preview={{preview:true,versionId:draftFixture.versionId,revision:1,content:{shifts:[],sections:[0,1,2].map(i=>({...section,id:String(i)}))}}} onClose={onClose}/>)
 fireEvent.click(screen.getByRole('button',{name:'다음'}));expect(screen.queryByRole('button',{name:'검토로 돌아가기'})).toBeNull();expect(screen.getByRole('button',{name:'이전'})).toBeVisible()
 fireEvent.click(screen.getByRole('button',{name:'다음'}));expect(screen.queryByRole('button',{name:'다음'})).toBeNull();fireEvent.click(screen.getByRole('button',{name:'검토 완료'}));expect(onClose).toHaveBeenCalledTimes(1);expect(call).not.toHaveBeenCalled()
})
it('업무가 하나면 검토 완료로 돌아가고 게시·부족 확인을 수행하지 않는다',()=>{
 const service=createManualPreviewService(),call=vi.spyOn(service,'call'),onClose=vi.fn()
 render(<ManualWorkerPreview service={service} preview={{preview:true,versionId:draftFixture.versionId,revision:1,content:draftFixture.content}} onClose={onClose}/> )
 expect(screen.queryByRole('button',{name:'이전'})).toBeNull();expect(screen.queryByRole('button',{name:'다음'})).toBeNull();fireEvent.click(screen.getByRole('button',{name:'검토 완료'}));expect(onClose).toHaveBeenCalledOnce();expect(call).not.toHaveBeenCalled()
})
