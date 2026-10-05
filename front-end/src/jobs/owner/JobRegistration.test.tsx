import {act,cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {JobRegistration} from './JobRegistration'
import {emptyJobDraft,type JobDraft,type OwnerJob} from './model'
const draft:JobDraft={...emptyJobDraft,title:'오픈',description:'포장',part:'주말 오픈',date:'2026-10-03',start:540,end:840,pay:'12000',payment:'근무 당일'}
const job={id:'created',title:'오픈'} as OwnerJob
beforeEach(()=>{Object.defineProperty(HTMLDialogElement.prototype,'showModal',{configurable:true,value:function(){this.setAttribute('open','')}});Object.defineProperty(HTMLDialogElement.prototype,'close',{configurable:true,value:function(){this.removeAttribute('open')}})})
afterEach(()=>{cleanup();Reflect.deleteProperty(HTMLDialogElement.prototype,'showModal');Reflect.deleteProperty(HTMLDialogElement.prototype,'close')})
it('단계 이동에도 입력을 유지하고 완료 콜백은 한 번만 호출한다',async()=>{
 const create=vi.fn().mockResolvedValue(job),onCreated=vi.fn()
 render(<JobRegistration initialDraft={draft} service={{create}} onBack={vi.fn()} onCreated={onCreated}/>)
 fireEvent.change(screen.getByLabelText('담당 업무명 *'),{target:{value:'변경한 제목'}})
 fireEvent.click(screen.getByText('다음'))
 fireEvent.click(screen.getByText('이전'))
 expect(screen.getByLabelText('담당 업무명 *')).toHaveValue('변경한 제목')
 fireEvent.click(screen.getByText('다음'))
 fireEvent.click(screen.getByText('다음'))
 await act(async()=>{fireEvent.click(screen.getByText('공고 등록하기'))})
 expect(screen.getByRole('dialog',{name:'공고를 등록했어요'})).toBeVisible()
 const view=screen.getByRole('button',{name:'공고 보기'})
 // Modal.confirm은 await 이후 닫히므로 React의 비동기 처리를 완료한 뒤 검증한다.
 await act(async()=>{fireEvent.click(view);fireEvent.click(view)})
 expect(onCreated).toHaveBeenCalledExactlyOnceWith(job,'detail')
 expect(screen.queryByRole('dialog',{name:'공고를 등록했어요'})).not.toBeInTheDocument()
 expect(create).toHaveBeenCalledTimes(1)
 expect(create.mock.calls[0][0].title).toBe('변경한 제목')
})
it('선택창에서 닫기를 누르면 변경을 취소한다',()=>{render(<JobRegistration initialDraft={draft} onBack={vi.fn()} onCreated={vi.fn()}/>);fireEvent.click(screen.getByText('주말 오픈'));fireEvent.click(screen.getByText('평일 마감'));fireEvent.click(screen.getByText('닫기'));expect(screen.getByText('주말 오픈')).toBeVisible()})
it('실패 후 같은 입력으로 재시도한다',async()=>{const create=vi.fn().mockRejectedValueOnce(Error('offline')).mockResolvedValue(job);render(<JobRegistration initialStep={3} initialDraft={draft} service={{create}} onBack={vi.fn()} onCreated={vi.fn()}/>);fireEvent.click(screen.getByText('공고 등록하기'));await screen.findByText('다시 시도');fireEvent.click(screen.getByText('다시 시도'));await screen.findByText('공고를 등록했어요');expect(create).toHaveBeenCalledTimes(2);expect(create.mock.calls[1][0]).toEqual(draft)})
it('중복 등록을 막고 뒤로 이동한 뒤 늦게 도착한 결과를 무시한다',async()=>{let resolve!:(job:OwnerJob)=>void;let signal!:AbortSignal;const create=vi.fn((_draft,received:AbortSignal)=>{signal=received;return new Promise<OwnerJob>(done=>{resolve=done})});render(<JobRegistration initialStep={3} initialDraft={draft} service={{create}} onBack={vi.fn()} onCreated={vi.fn()}/>);fireEvent.click(screen.getByText('공고 등록하기'));fireEvent.click(screen.getByText('공고 등록하기'));expect(create).toHaveBeenCalledTimes(1);fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}));expect(signal.aborted).toBe(true);await act(async()=>resolve(job));expect(screen.queryByText('공고를 등록했어요')).not.toBeInTheDocument();expect(screen.queryByText('공고를 등록하지 못했어요')).not.toBeInTheDocument()})
it('시급에서 문자와 과도한 자릿수를 제거하고 0원은 거부한다',()=>{render(<JobRegistration initialStep={3} initialDraft={draft} onBack={vi.fn()} onCreated={vi.fn()}/>);const input=screen.getByLabelText('시급 *');fireEvent.change(input,{target:{value:'abc123456789012'}});expect(input).toHaveValue('123,456,789');fireEvent.change(input,{target:{value:'0'}});fireEvent.click(screen.getByText('공고 등록하기'));expect(screen.getByText('시급을 올바른 양의 정수로 입력해 주세요.')).toBeVisible()})

it('완료 안내의 닫기는 목록 목적지로 한 번만 전달한다',()=>{const created=vi.fn();render(<JobRegistration initialDraft={draft} initialResult="success" initialReceipt={job} onBack={vi.fn()} onCreated={created}/>);fireEvent.click(screen.getByText('닫기'));expect(created).toHaveBeenCalledExactlyOnceWith(job,'list')})
