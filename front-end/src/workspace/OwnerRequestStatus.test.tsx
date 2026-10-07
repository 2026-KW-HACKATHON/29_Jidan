import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,beforeEach,expect,it,vi} from 'vitest'
import {OwnerJobs} from './Jobs'
import {currentRequest} from './OwnerRequestStatus'
import data from './ownerJob.fixture.test.json'
import type {JobPosting,WorkRequest} from '../api/types.generated'
const json=(v:unknown)=>new Response(JSON.stringify(v))
beforeEach(()=>{Object.defineProperty(HTMLDialogElement.prototype,'showModal',{configurable:true,value:function(){this.setAttribute('open','')}});Object.defineProperty(HTMLDialogElement.prototype,'close',{configurable:true,value:function(){this.removeAttribute('open')}})})
afterEach(()=>{cleanup();vi.unstubAllGlobals();Reflect.deleteProperty(HTMLDialogElement.prototype,'showModal');Reflect.deleteProperty(HTMLDialogElement.prototype,'close')})
function api(confirmed=false,expired=false){const job=confirmed?data.confirmedJob:data.job,applicants=confirmed?data.confirmedApplicants:data.applicants,requests=confirmed?data.confirmedRequests:data.requests;const request=expired?{...requests.items[0],status:'EXPIRED',endedAt:requests.items[0].expiresAt}:requests.items[0];vi.stubGlobal('fetch',vi.fn(async(url:unknown)=>json(String(url).includes('/work-requests')?{...requests,items:[request]}:String(url).includes('/applications/')?applicants.items.find(a=>a.id===request.applicationId):String(url).includes('/applications?')?applicants:job)));return job}
it('서버 요청의 지원자와 자기소개를 읽고 지원서에서 되돌아온다',async()=>{const job=api();const route=vi.fn();render(<OwnerJobs view="job" id={job.id} storeId={job.store.id} storeName={job.store.name} route={route}/>);fireEvent.click(await screen.findByRole('button',{name:'지원서 보기'}));await screen.findByText(data.applicants.items.find(a=>a.id===data.requests.items[0].applicationId)!.introduction);expect(screen.queryByRole('button',{name:'근무 요청 보내기'})).toBeNull();fireEvent.click(screen.getByRole('button',{name:'닫기'}));expect(screen.getByRole('button',{name:'지원서 보기'})).toBeInTheDocument()})
it('확정 상태에서 실제 공고의 온보딩 경로를 연다',async()=>{const job=api(true),route=vi.fn();render(<OwnerJobs view="job" id={job.id} storeId={job.store.id} storeName={job.store.name} route={route}/>);fireEvent.click(await screen.findByRole('button',{name:'온보딩 보기'}));expect(route).toHaveBeenCalledWith('onboarding',job.id)})
it('미응답 알림 닫기와 다른 지원자 경로를 연결한다',async()=>{const job=api(false,true),route=vi.fn();render(<OwnerJobs view="job" id={job.id} storeId={job.store.id} storeName={job.store.name} route={route}/>);const dialog=await screen.findByRole('dialog');fireEvent(dialog,new Event('cancel',{cancelable:true}));expect(screen.getByText(/동안 미응답/)).toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'다른 지원자 보기'}));expect(route).toHaveBeenCalledWith('applicants',job.id)})
it('요청이 있어도 다른 지원자 목록을 명시적으로 열 수 있다',async()=>{const job=api();render(<OwnerJobs view="applicants" id={job.id} storeId={job.store.id} storeName={job.store.name} route={vi.fn()}/>);await screen.findByText(`지원자 ${data.applicants.items.length}명`);expect(screen.queryByText(/수락 대기/)).toBeNull()})
it('현재 요청을 우선하고 과거 취소 요청을 미응답으로 표시하지 않는다',()=>{
 const pending=data.requests.items[0] as WorkRequest,job=data.job as JobPosting,expired={...pending,id:'old',status:'EXPIRED',endedAt:pending.expiresAt} as WorkRequest
 expect(currentRequest([expired,pending],job)?.id).toBe(pending.id);expect(currentRequest([{...expired,status:'CANCELLED'}],job)).toBeUndefined();expect(currentRequest([expired],{...job,status:'CLOSED'})).toBeUndefined()
})
