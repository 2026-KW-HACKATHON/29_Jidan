import {act,cleanup,fireEvent,render,screen,within} from '@testing-library/react'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import PreviewApp from './PreviewApp'
beforeEach(()=>{Object.defineProperty(HTMLDialogElement.prototype,'showModal',{configurable:true,value:function(){this.setAttribute('open','')}});Object.defineProperty(HTMLDialogElement.prototype,'close',{configurable:true,value:function(){this.removeAttribute('open')}});vi.stubGlobal('fetch',vi.fn())})
afterEach(()=>{cleanup();Reflect.deleteProperty(HTMLDialogElement.prototype,'showModal');Reflect.deleteProperty(HTMLDialogElement.prototype,'close')})
it('등록 결과를 상세 화면으로 전달하고 실제 API를 호출하지 않는다',async()=>{history.replaceState(null,'','/__owner/jobs?view=create&step=3');await act(async()=>{render(<PreviewApp/>)});fireEvent.change(await screen.findByLabelText('시급 *'),{target:{value:'15000'}});fireEvent.click(screen.getByText('공고 등록하기'));await screen.findByText('공고를 등록했어요');fireEvent.click(screen.getByRole('button',{name:'공고 보기'}));expect(await screen.findByText('시급 15,000원')).toBeVisible();expect(fetch).not.toHaveBeenCalled()})
it('샘플 완료 화면에서도 공고 보기를 사용할 수 있다',async()=>{history.replaceState(null,'','/__owner/jobs?view=create&step=3&result=success');await act(async()=>{render(<PreviewApp/>)});fireEvent.click(await screen.findByRole('button',{name:'공고 보기'}));expect(await screen.findByText('시급 12,000원')).toBeVisible()})

it('모집 마감 완료를 확인한 후 마감 목록과 집계를 갱신한다',async()=>{history.replaceState(null,'','/__owner/jobs?view=detail&id=open&overlay=close');await act(async()=>{render(<PreviewApp/>)});fireEvent.click(await screen.findByText('모집 마감'));await screen.findByText('모집을 마감했어요');await act(async()=>{});fireEvent.click(within(screen.getByRole('dialog')).getByRole('button',{name:'공고 목록 보기'}));expect(await screen.findByText('모집 중 3')).toBeVisible();expect(screen.getByText('마감 3')).toBeVisible();expect(screen.getAllByText('모집 종료')).toHaveLength(2);expect(fetch).not.toHaveBeenCalled()})

it('공고 목록에서 지원자와 원문을 확인하고 요청 전송을 수행하지 않는다',async()=>{history.replaceState(null,'','/__owner/jobs');await act(async()=>{render(<PreviewApp/>)});fireEvent.click(await screen.findByRole('button',{name:/주말 오픈 대타/}));fireEvent.click(await screen.findByRole('button',{name:'박지원 지원서 보기'}));expect(await screen.findByText(/음료 제조와 고객 응대 경험/)).toBeVisible();fireEvent.click(screen.getByText('근무 요청 보내기'));expect(await screen.findByText('근무 요청은 다음 단계에서 진행해요')).toBeVisible();expect(fetch).not.toHaveBeenCalled()})

it('홈에서 시작한 등록은 홈으로 돌아가고 직접 진입한 등록은 목록으로 돌아간다',async()=>{
 history.replaceState(null,'','/__home/owner');await act(async()=>{render(<PreviewApp/>)});
 fireEvent.click(await screen.findByRole('button',{name:'공고 등록'}));await screen.findByRole('heading',{name:'공고 등록'});
 fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}));expect(await screen.findByRole('button',{name:'공고 등록'})).toBeVisible();expect(location.pathname).toBe('/__home/owner');
 cleanup();history.replaceState(null,'','/__owner/jobs?view=create');await act(async()=>{render(<PreviewApp/>)});
 fireEvent.click(await screen.findByRole('button',{name:'뒤로 가기'}));expect(await screen.findByText('등록한 공고를 확인하세요')).toBeVisible();expect(location.search).toContain('view=list');
})
