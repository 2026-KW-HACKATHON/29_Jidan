import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {WorkerJobs} from './Jobs'
import fixtures from './fixtures.test.json'
const json=(value:unknown)=>new Response(JSON.stringify(value))
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
it('새로고침한 공고 상세에서 서버의 지원 ID로 이동한다',async()=>{const job={...fixtures.getJobPosting,myApplicationId:'7390d3c1-dc0d-4b1c-814d-b68a1df5c1b6',canApply:false,cannotApplyReason:'ALREADY_APPLIED'};vi.stubGlobal('fetch',vi.fn(async()=>json(job)));const route=vi.fn();render(<WorkerJobs view="job" id={job.id} route={route}/>);fireEvent.click(await screen.findByRole('button',{name:'지원 확인하기'}));expect(route).toHaveBeenCalledWith('application',job.myApplicationId)})
it('마감된 공고는 지원을 차단한다',async()=>{const job={...fixtures.getJobPosting,myApplicationId:null,canApply:false,cannotApplyReason:'JOB_CLOSED',status:'CLOSED',closedAt:'2026-10-08T00:00:00Z'};vi.stubGlobal('fetch',vi.fn(async()=>json(job)));render(<WorkerJobs view="job" id={job.id} route={vi.fn()}/>);expect(await screen.findByRole('button',{name:'지원할 수 없는 공고예요'})).toBeDisabled()})
