import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {WorkerApplications} from './WorkerApplications'
import fixtures from './fixtures.test.json'
const json=(v:unknown,status=200)=>new Response(JSON.stringify(v),{status})
const id='aa1827b4-720f-4251-a451-2b2a2811c2f3'
const {myApplicationId: _applicationId,canApply: _canApply,cannotApplyReason: _reason,...job}=fixtures.getJobPosting
const application={id,job:{...job,endsNextDay:true,startTime:'22:00',endTime:'02:00'},status:'APPLIED',introduction:'안녕하세요',submittedAt:'2026-10-08T00:00:00Z',withdrawnAt:null,revision:1}
const list=(items:unknown[],page=0,totalItems=items.length)=>({items,page,size:100,totalItems,asOf:'2026-10-08T00:00:00Z',counts:{pending:101,confirmed:1,ended:2}})
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
it('서버 탭과 집계·익일 시간을 사용하고 실제 지원 ID로 이동한다',async()=>{
 const fetch=vi.fn(async(url:unknown)=>json(String(url).includes('tab=CONFIRMED')?list([{...application,status:'CONFIRMED'}]):list([application])));vi.stubGlobal('fetch',fetch);const route=vi.fn();render(<WorkerApplications route={route}/>);
 fireEvent.click(await screen.findByRole('button',{name:application.job.title}));expect(route).toHaveBeenCalledWith('application',id);expect(screen.getByText(/22:00 – 다음 날 02:00/)).toBeInTheDocument();expect(screen.getByRole('tab',{name:'신청 중 101'})).toBeInTheDocument();
 fireEvent.click(screen.getByRole('tab',{name:'확정 1'}));await screen.findByRole('button',{name:application.job.title});expect(fetch.mock.calls.some(([url])=>String(url).includes('tab=CONFIRMED'))).toBe(true)
})
it('빠른 탭 전환에서 이전 응답을 무시하고 빈 목록을 표시한다',async()=>{
 let resolve!:(value:Response)=>void;const pending=new Promise<Response>(r=>resolve=r);const fetch=vi.fn(async(url:unknown)=>String(url).includes('tab=PENDING')?pending:json(list([])));vi.stubGlobal('fetch',fetch);render(<WorkerApplications route={vi.fn()}/>);
 fireEvent.click(screen.getByRole('tab',{name:'종료 0'}));await screen.findByText('이 상태의 신청 내역이 없어요.');await act(async()=>resolve(json(list([application]))));expect(screen.queryByText(application.job.title)).toBeNull();expect(screen.getByRole('tab',{name:'종료 2'})).toHaveAttribute('aria-selected','true')
})
it('페이지를 모두 읽고 실패 후 재시도한다',async()=>{
 let fail=true;const fetch=vi.fn(async(url:unknown)=>{if(fail){fail=false;return json({code:'FORBIDDEN'},403)}return json(String(url).includes('page=1')?list([{...application,id:fixtures.getJobPosting.id,job:{...application.job,title:'두 번째 공고'}}],1,2):list([application],0,2))});vi.stubGlobal('fetch',fetch);render(<WorkerApplications route={vi.fn()}/>);fireEvent.click(await screen.findByRole('button',{name:'다시 시도'}));await screen.findByRole('button',{name:'두 번째 공고'});await waitFor(()=>expect(screen.getByRole('button',{name:application.job.title})).toBeInTheDocument());expect(fetch.mock.calls.some(([url])=>String(url).includes('page=1'))).toBe(true)
})
