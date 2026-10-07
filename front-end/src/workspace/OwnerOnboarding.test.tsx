import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {OwnerOnboarding} from './OwnerOnboarding'
import data from './ownerJob.fixture.test.json'
const json=(v:unknown,status=200)=>new Response(JSON.stringify(v),{status})
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
it('온보딩 조회에서 게시 버전을 고정하여 목록과 절차를 읽는다',async()=>{
 const fetch=vi.fn(async(url:unknown)=>json(String(url).includes('/onboarding')?data.onboarding:String(url).includes('/sections/')?data.section:data.manual));vi.stubGlobal('fetch',fetch);const back=vi.fn();render(<OwnerOnboarding storeId={data.job.store.id} jobId={data.confirmedJob.id} onBack={back}/>);
 fireEvent.click(await screen.findByRole('button',{name:data.manual.sections[0].title}));await screen.findByText(data.section.section.steps[0].instruction);expect(fetch.mock.calls.slice(1).every(([url])=>String(url).includes(`expectedVersionId=${data.onboarding.manualVersionId}`))).toBe(true)
 fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}));await screen.findByRole('button',{name:data.manual.sections[0].title});fireEvent.click(screen.getByRole('button',{name:'뒤로 가기'}));expect(back).toHaveBeenCalledOnce()
})
it('미게시 매뉴얼은 게시 조회를 호출하지 않는다',async()=>{const fetch=vi.fn(async()=>json({...data.onboarding,manualStatus:'NOT_PUBLISHED',manualVersionId:null}));vi.stubGlobal('fetch',fetch);render(<OwnerOnboarding storeId={data.job.store.id} jobId={data.confirmedJob.id} onBack={vi.fn()}/>);await screen.findByText('아직 게시된 매뉴얼이 없어요.');expect(fetch).toHaveBeenCalledTimes(1)})
it('게시 교체 충돌이나 권한 거부는 절차를 표시하지 않는다',async()=>{vi.stubGlobal('fetch',vi.fn(async(url:unknown)=>String(url).includes('/onboarding')?json({...data.onboarding,accessStatus:'ENDED'}):json({code:'MANUAL_VERSION_CONFLICT'},409)));render(<OwnerOnboarding storeId={data.job.store.id} jobId={data.confirmedJob.id} onBack={vi.fn()}/>);expect(await screen.findByRole('alert')).toHaveTextContent('정보를 불러오지 못했어요');expect(screen.queryByText(data.section.section.steps[0].instruction)).toBeNull()})
