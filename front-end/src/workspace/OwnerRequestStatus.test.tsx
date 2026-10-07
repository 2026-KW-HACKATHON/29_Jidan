import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {OwnerJobs} from './Jobs'
import {OwnerRequestStatus} from './OwnerRequestStatus'
import {currentRequest} from './currentRequest'
import data from './ownerJob.fixture.test.json'
import type {JobPosting,WorkRequest} from '../api/types.generated'
const json=(v:unknown)=>new Response(JSON.stringify(v))
beforeEach(()=>{vi.useFakeTimers({toFake:['Date']});vi.setSystemTime(new Date(data.requests.asOf));Object.defineProperty(HTMLDialogElement.prototype,'showModal',{configurable:true,value:function(){this.setAttribute('open','')}});Object.defineProperty(HTMLDialogElement.prototype,'close',{configurable:true,value:function(){this.removeAttribute('open')}})})
afterEach(()=>{cleanup();vi.useRealTimers();vi.unstubAllGlobals();Reflect.deleteProperty(HTMLDialogElement.prototype,'showModal');Reflect.deleteProperty(HTMLDialogElement.prototype,'close')})
function api(confirmed=false,expired=false){const job=confirmed?data.confirmedJob:data.job,applicants=confirmed?data.confirmedApplicants:data.applicants,requests=confirmed?data.confirmedRequests:data.requests;const request=expired?{...requests.items[0],status:'EXPIRED',endedAt:requests.items[0].expiresAt}:requests.items[0];vi.stubGlobal('fetch',vi.fn(async(url:unknown)=>json(String(url).includes('/work-requests')?{...requests,items:[request]}:String(url).includes('/applications/')?applicants.items.find(a=>a.id===request.applicationId):String(url).includes('/applications?')?applicants:job)));return job}
it('서버 요청의 지원자와 자기소개를 읽고 지원서에서 되돌아온다',async()=>{const job=api();const route=vi.fn();render(<OwnerJobs view="job" id={job.id} storeId={job.store.id} storeName={job.store.name} route={route}/>);fireEvent.click(await screen.findByRole('button',{name:'지원서 보기'}));await screen.findByText(data.applicants.items.find(a=>a.id===data.requests.items[0].applicationId)!.introduction);expect(screen.queryByRole('button',{name:'근무 요청 보내기'})).toBeNull();fireEvent.click(screen.getByRole('button',{name:'닫기'}));expect(screen.getByRole('button',{name:'지원서 보기'})).toBeInTheDocument()})
it('확정 상태에서 실제 공고의 온보딩 경로를 연다',async()=>{const job=api(true),route=vi.fn();render(<OwnerJobs view="job" id={job.id} storeId={job.store.id} storeName={job.store.name} route={route}/>);fireEvent.click(await screen.findByRole('button',{name:'온보딩 보기'}));expect(route).toHaveBeenCalledWith('onboarding',job.id)})
it('미응답 알림 닫기와 다른 지원자 경로를 연결한다',async()=>{const job=api(false,true),route=vi.fn();render(<OwnerJobs view="job" id={job.id} storeId={job.store.id} storeName={job.store.name} route={route}/>);const dialog=await screen.findByRole('dialog');fireEvent(dialog,new Event('cancel',{cancelable:true}));expect(screen.getByText(/동안 미응답/)).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'다른 지원자 보기'}));expect(route).toHaveBeenCalledWith('applicants',job.id)})
it('요청이 있어도 다른 지원자 목록을 명시적으로 열 수 있다',async()=>{const job=api();render(<OwnerJobs view="applicants" id={job.id} storeId={job.store.id} storeName={job.store.name} route={vi.fn()}/>);await screen.findByText(`지원자 ${data.applicants.items.length}명`);expect(screen.queryByText(/수락 대기/)).toBeNull()})
it('현재 요청을 우선하고 과거 취소 요청을 미응답으로 표시하지 않는다',()=>{
 const pending=data.requests.items[0] as WorkRequest,job=data.job as JobPosting,expired={...pending,id:'old',status:'EXPIRED',endedAt:pending.expiresAt} as WorkRequest
 expect(currentRequest([expired,pending],job)?.id).toBe(pending.id);expect(currentRequest([{...expired,status:'CANCELLED',respondedAt:null,endedAt:expired.expiresAt,accessGrant:null}],job)).toBeUndefined();expect(currentRequest([expired],{...job,status:'CLOSED',closedAt:'2026-10-08T00:00:00Z'})).toBeUndefined()
})
it.each([false,true])('상태 화면의 철회 버튼 %s가 해당 확인 대화상자를 연다',async confirmed=>{const job=api(confirmed);render(<OwnerJobs view="job" id={job.id} storeId={job.store.id} storeName={job.store.name} route={vi.fn()}/>);fireEvent.click(await screen.findByRole('button',{name:confirmed?'확정 철회하기':'요청 철회하기'}));expect(await screen.findByRole('alertdialog')).toHaveTextContent(confirmed?'근무 확정을 철회할까요?':'근무 요청을 철회할까요?')})
it('미응답 상태의 마감 버튼에서 모집 마감 확인을 연다',async()=>{const job=api(false,true);render(<OwnerJobs view="job" id={job.id} storeId={job.store.id} storeName={job.store.name} route={vi.fn()}/>);fireEvent(await screen.findByRole('dialog'),new Event('cancel',{cancelable:true}));fireEvent.click(screen.getByRole('button',{name:'지원자 선정 없이 모집 마감'}));expect(await screen.findByRole('alertdialog')).toHaveTextContent('모집을 마감할까요?')})

it('요청 만료 시 서버를 다시 읽고 이탈 후 타이머를 해제한다',async()=>{
 vi.useFakeTimers();const now=Date.now(),request={...data.requests.items[0],requestedAt:new Date(now-60000).toISOString(),expiresAt:new Date(now+500).toISOString()} as WorkRequest
 vi.stubGlobal('fetch',vi.fn(async()=>json(data.applicants.items.find(a=>a.id===request.applicationId))));const reload=vi.fn()
 const {unmount}=render(<OwnerRequestStatus job={data.job as JobPosting} request={request} storeId={data.job.store.id} route={vi.fn()} onReload={reload}/>);
 await act(async()=>{await vi.advanceTimersByTimeAsync(0)});expect(screen.getByText('수락 대기 · 요청한 지 1분')).toBeInTheDocument();await act(async()=>{await vi.advanceTimersByTimeAsync(551)});expect(reload).toHaveBeenCalledOnce();unmount();await act(async()=>{await vi.advanceTimersByTimeAsync(120000)});expect(reload).toHaveBeenCalledOnce()
})

it('다른 지원자에게 요청 완료 후 최신 지원자 상태 경로로 이동한다',async()=>{
 const job=data.job,a=data.applicants.items.find(a=>a.status==='APPLIED')!,request={...data.requests.items[0],applicationId:a.id,workerId:a.applicant.workerId,workerName:a.applicant.name}
 vi.stubGlobal('fetch',vi.fn(async(url:unknown,init?:RequestInit)=>json(String(url).includes('/csrf')?{csrfToken:'token'}:init?.method==='POST'?request:String(url).includes('/work-requests')?{...data.requests,items:[]}:String(url).includes('/applications?')?data.applicants:job)))
 const route=vi.fn();render(<OwnerJobs view="applicants" id={job.id} storeId={job.store.id} storeName={job.store.name} route={route}/>);fireEvent.click(await screen.findByRole('button',{name:`${a.applicant.name} 근무 요청`}));fireEvent.click(screen.getByRole('button',{name:'요청하기'}));const done=await screen.findByRole('button',{name:'지원자 확인'});await waitFor(()=>expect(done).toBeEnabled());fireEvent.click(done);await waitFor(()=>expect(route).toHaveBeenCalledWith('job',job.id))
})

it('지원자 조회 중 이미 요청 기한이 지난 경우 즉시 최신 상태를 읽는다',async()=>{
 vi.useFakeTimers();const now=Date.now(),request={...data.requests.items[0],requestedAt:new Date(now-60000).toISOString(),expiresAt:new Date(now-1).toISOString()} as WorkRequest
 vi.stubGlobal('fetch',vi.fn(async()=>json(data.applicants.items.find(a=>a.id===request.applicationId))));const reload=vi.fn();render(<OwnerRequestStatus job={data.job as JobPosting} request={request} storeId={data.job.store.id} route={vi.fn()} onReload={reload}/>);await act(async()=>{await vi.advanceTimersByTimeAsync(0)});await act(async()=>{await vi.advanceTimersByTimeAsync(1)});expect(reload).toHaveBeenCalledOnce()
})
