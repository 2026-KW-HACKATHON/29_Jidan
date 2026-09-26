import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { WorkerRegistration } from './WorkerRegistration'
import { WorkerFailure, type WorkerService } from './service'
import { emptyWorker, type WorkerDraft } from './model'
const valid:WorkerDraft={...emptyWorker,name:'김지수',phone:'010-1234-5678',birth:'2001-03-14',gender:'여성',experience:'신입',availability:[{id:'a',days:[0,2,4],start:540,end:840,overnight:false}]}
const original=Object.getOwnPropertyDescriptors(HTMLDialogElement.prototype)
beforeEach(()=>{
  Object.defineProperty(HTMLDialogElement.prototype,'showModal',{configurable:true,value(this:HTMLDialogElement){this.setAttribute('open','')}})
  Object.defineProperty(HTMLDialogElement.prototype,'close',{configurable:true,value(this:HTMLDialogElement){this.removeAttribute('open')}})
})
afterEach(()=>{cleanup();vi.useRealTimers();for(const key of ['showModal','close']){if(original[key])Object.defineProperty(HTMLDialogElement.prototype,key,original[key]);else Reflect.deleteProperty(HTMLDialogElement.prototype,key)}})
function setup(initialDraft=valid,initialPage:1|2|3|'review'='review',submit:WorkerService['submit']=vi.fn().mockResolvedValue({id:'receipt',status:'COMPLETE'})) {
 const service:WorkerService={identity:vi.fn().mockResolvedValue({email:'member@example.com'}),submit}
 return {service,...render(<WorkerRegistration service={service} initialDraft={initialDraft} initialPage={initialPage} onBack={vi.fn()} onExpired={vi.fn()} onHome={vi.fn()} />)}
}
it('최초 진입은 빈 입력이며 잘못된 날짜와 빈 선택을 막는다',async()=>{
 setup(emptyWorker,1);await screen.findByLabelText('이름 *')
 expect(screen.getByLabelText('이름 *')).toHaveValue('')
 fireEvent.click(screen.getByRole('button',{name:'다음'}))
 expect(screen.getByText('오늘 이전의 올바른 생년월일을 입력해 주세요.')).toBeInTheDocument()
 expect(screen.getByText('성별을 선택해 주세요.')).toBeInTheDocument()
})
it('기본 정보를 수정한 뒤 확인 화면으로 복귀한다',async()=>{
 setup();fireEvent.click(await screen.findByRole('button',{name:'기본 정보 수정'}))
 fireEvent.change(screen.getByLabelText('이름 *'),{target:{value:'Alex Kim'}})
 fireEvent.click(screen.getByRole('button',{name:'수정 완료'}))
 expect(screen.getByText('프로필을 확인해 주세요')).toBeInTheDocument()
 expect(screen.getByText('Alex Kim')).toBeInTheDocument()
})
it('업종 확인, 기간 입력으로 경력을 추가하고 수정 값을 보존한다',async()=>{
 setup({...valid,experience:'경력 있음'},2)
 fireEvent.click(await screen.findByRole('button',{name:'+ 경력 추가'}))
 fireEvent.click(screen.getByRole('button',{name:'업종 *'}));fireEvent.click(screen.getByRole('radio',{name:'카페'}));fireEvent.click(screen.getByRole('button',{name:'선택 완료'}))
 fireEvent.change(screen.getByLabelText('담당 업무 *'),{target:{value:'음료 제조'}})
 fireEvent.click(screen.getByRole('button',{name:'시작 연월 *'}));fireEvent.keyDown(screen.getByRole('spinbutton',{name:'연도'}),{key:'End'});fireEvent.keyDown(screen.getByRole('spinbutton',{name:'월'}),{key:'Home'});fireEvent.click(screen.getByRole('button',{name:'선택 완료'}))
 fireEvent.click(screen.getByRole('button',{name:'종료 연월 *'}));fireEvent.click(screen.getByRole('checkbox',{name:'현재 근무 중'}));fireEvent.click(screen.getByRole('button',{name:'선택 완료'}));fireEvent.click(screen.getByRole('button',{name:'경력 저장'}))
 expect(screen.getByText('카페 · 음료 제조')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'경력 수정'}));expect(screen.getByLabelText('담당 업무 *')).toHaveValue('음료 제조')
 fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}));expect(screen.getByText('카페 · 음료 제조')).toBeInTheDocument()
})
it('시간 추가에서 겹친 구간을 막고 취소하면 원래 시간을 유지한다',async()=>{
 setup(valid,3);fireEvent.click(await screen.findByRole('button',{name:'+ 시간 추가'}))
 fireEvent.click(screen.getByLabelText('월'))
 for(const [label,value] of [['시작 시간','540'],['종료 시간','600']]){fireEvent.click(screen.getByRole('button',{name:`${label} *`}));fireEvent.keyDown(screen.getByRole('spinbutton',{name:'시 · 24시간제'}),{key:'Home'});for(let h=0;h<Number(value)/60;h++)fireEvent.keyDown(screen.getByRole('spinbutton',{name:'시 · 24시간제'}),{key:'ArrowDown'});fireEvent.click(screen.getByRole('button',{name:'선택 완료'}))}
 fireEvent.click(screen.getByRole('button',{name:'시간 추가'}));expect(screen.getByRole('alert')).toHaveTextContent('겹쳐요')
 fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}));expect(screen.getByText('선택한 시간 · 주 15시간')).toBeInTheDocument()
})
it('중복 제출을 막고 성공 응답 후에만 완료 화면을 표시한다',async()=>{
 let resolve!:(value:{id:string;status:'COMPLETE'})=>void
 const submit=vi.fn(()=>new Promise<{id:string;status:'COMPLETE'}>(r=>{resolve=r}))
 setup(valid,'review',submit);const button=await screen.findByRole('button',{name:'프로필 등록 완료'})
 fireEvent.click(button);fireEvent.click(button);expect(submit).toHaveBeenCalledTimes(1)
 expect(screen.queryByText('프로필 등록이 완료됐어요')).not.toBeInTheDocument()
 await act(async()=>resolve({id:'receipt',status:'COMPLETE'}));expect(screen.getByText('프로필 등록이 완료됐어요')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'내 프로필 보기'}));expect(screen.getByText('김지수')).toBeInTheDocument()
})
it('실패 재시도는 같은 키를 쓰고 수정하면 새 키를 쓴다',async()=>{
 const submit=vi.fn().mockRejectedValue(new WorkerFailure('network'));setup(valid,'review',submit)
 fireEvent.click(await screen.findByRole('button',{name:'프로필 등록 완료'}));await screen.findByRole('alertdialog');fireEvent.click(await screen.findByRole('button',{name:'닫기'}));fireEvent.click(screen.getByRole('button',{name:'프로필 등록 완료'}));await waitFor(()=>expect(submit).toHaveBeenCalledTimes(2));expect(submit.mock.calls[0][1]).toBe(submit.mock.calls[1][1])
 fireEvent.click(await screen.findByRole('button',{name:'닫기'}));fireEvent.click(screen.getByRole('button',{name:'기본 정보 수정'}));fireEvent.change(screen.getByLabelText('이름 *'),{target:{value:'김하늘'}});fireEvent.click(screen.getByRole('button',{name:'수정 완료'}));fireEvent.click(screen.getByRole('button',{name:'프로필 등록 완료'}));await waitFor(()=>expect(submit).toHaveBeenCalledTimes(3));expect(submit.mock.calls[2][1]).not.toBe(submit.mock.calls[1][1])
})
it('등록 시간 제한 후 늦은 성공을 무시한다',async()=>{
 let resolve!:(value:{id:string;status:'COMPLETE'})=>void
 setup(valid,'review',()=>new Promise(r=>{resolve=r}))
 const button=await screen.findByRole('button',{name:'프로필 등록 완료'})
 vi.useFakeTimers();fireEvent.click(button)
 await act(async()=>{vi.advanceTimersByTime(15000)})
 expect(screen.getByText('등록 응답이 지연되고 있어요. 같은 내용으로 다시 시도해 주세요.')).toBeInTheDocument()
 await act(async()=>resolve({id:'late',status:'COMPLETE'}))
 expect(screen.queryByText('프로필 등록이 완료됐어요')).not.toBeInTheDocument()
})
it('빈 가입 폼에서 신입과 시간을 입력해 확인 및 완료까지 진행한다',async()=>{
 const {service}=setup(emptyWorker,1)
 await screen.findByLabelText('이름 *')
 fireEvent.change(screen.getByLabelText('이름 *'),{target:{value:'Alex Kim'}})
 fireEvent.change(screen.getByLabelText('전화번호 *'),{target:{value:'010123456789999'}})
 expect(screen.getByLabelText('전화번호 *')).toHaveValue('010-1234-5678')
 fireEvent.change(screen.getByLabelText('생년월일 *'),{target:{value:'200003019999'}})
 expect(screen.getByLabelText('생년월일 *')).toHaveValue('2000. 03. 01')
 fireEvent.click(screen.getByLabelText('남성'));fireEvent.click(screen.getByRole('button',{name:'다음'}))
 fireEvent.click(screen.getByLabelText('신입'));fireEvent.click(screen.getByRole('button',{name:'다음'}))
 fireEvent.click(screen.getByRole('button',{name:'월 09:00–09:30'}))
 expect(screen.getByText('선택한 시간 · 주 0.5시간')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button',{name:'입력 내용 확인'}));fireEvent.click(screen.getByRole('button',{name:'프로필 등록 완료'}))
 await screen.findByText('프로필 등록이 완료됐어요')
 expect(service.submit).toHaveBeenCalledWith(expect.objectContaining({name:'Alex Kim',birth:'2000-03-01',phone:'01012345678',experience:'신입'}),expect.any(String),expect.any(AbortSignal))
})

it.each(['identity', 'submit'] as const)('%s 만료 안내를 닫아도 재인증 없이 가입을 계속할 수 없다', async stage => {
  const onExpired = vi.fn()
  const identity = vi.fn().mockImplementation(async () => {
    if (stage === 'identity') throw new WorkerFailure('expired')
    return { email: 'member@example.com' }
  })
  const submit = vi.fn().mockRejectedValue(new WorkerFailure('expired'))
  render(<WorkerRegistration service={{ identity, submit }} initialDraft={valid} initialPage="review" onBack={vi.fn()} onExpired={onExpired} onHome={vi.fn()} />)
  if (stage === 'submit') fireEvent.click(await screen.findByRole('button', { name: '프로필 등록 완료' }))
  await screen.findByRole('alertdialog')
  fireEvent.click(screen.getByRole('button', { name: '닫기' }))
  expect(screen.queryByRole('button', { name: '기본 정보 수정' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '프로필 등록 완료' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '다시 시도' })).not.toBeInTheDocument()
  expect(screen.queryByText('member@example.com', { exact: false })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '다시 로그인' }))
  expect(onExpired).toHaveBeenCalledOnce()
  expect(identity).toHaveBeenCalledTimes(1)
  expect(submit).toHaveBeenCalledTimes(stage === 'submit' ? 1 : 0)
})
