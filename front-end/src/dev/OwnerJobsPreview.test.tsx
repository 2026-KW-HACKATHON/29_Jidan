import {act,cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import PreviewApp from './PreviewApp'
beforeEach(()=>{Object.defineProperty(HTMLDialogElement.prototype,'showModal',{configurable:true,value:function(){this.setAttribute('open','')}});Object.defineProperty(HTMLDialogElement.prototype,'close',{configurable:true,value:function(){this.removeAttribute('open')}});vi.stubGlobal('fetch',vi.fn())})
afterEach(()=>{cleanup();Reflect.deleteProperty(HTMLDialogElement.prototype,'showModal');Reflect.deleteProperty(HTMLDialogElement.prototype,'close')})
it('등록 결과를 상세 화면으로 전달하고 실제 API를 호출하지 않는다',async()=>{history.replaceState(null,'','/__owner/jobs?step=3');await act(async()=>{render(<PreviewApp/>)});fireEvent.change(await screen.findByLabelText('시급 *'),{target:{value:'15000'}});fireEvent.click(screen.getByText('공고 등록하기'));await screen.findByText('공고를 등록했어요');fireEvent.click(screen.getByRole('button',{name:'공고 보기'}));expect(await screen.findByText('시급 15,000원')).toBeVisible();expect(fetch).not.toHaveBeenCalled()})
it('샘플 완료 화면에서도 공고 보기를 사용할 수 있다',async()=>{history.replaceState(null,'','/__owner/jobs?step=3&result=success');await act(async()=>{render(<PreviewApp/>)});fireEvent.click(await screen.findByRole('button',{name:'공고 보기'}));expect(await screen.findByText('시급 12,000원')).toBeVisible()})
