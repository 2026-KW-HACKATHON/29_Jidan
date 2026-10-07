import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {Notifications} from './Notifications'
const id='aa1827b4-720f-4251-a451-2b2a2811c2f3',store='51c1c743-d377-4e7a-8449-94377eecfce0',job='8215b01a-ed7b-4c4a-950f-c6e1480bd563'
const item={id,type:'NEW_APPLICATION',title:'새 지원자',body:'지원서를 확인하세요.\n실제 서버 본문',createdAt:'2026-10-08T00:00:00Z',readAt:null,target:{type:'JOB_APPLICATION',applicationId:id,storeId:store,jobId:job}}
const json=(v:unknown,status=200)=>new Response(JSON.stringify(v),{status})
const list=(items:unknown[],page=0,totalItems=items.length)=>({items,page,size:100,totalItems,asOf:'2026-10-08T00:00:00Z',unreadCount:1})
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
it('페이지를 모두 읽고 읽음 POST 성공 후 실제 점주 대상 경로로 이동한다',async()=>{
 const fetch=vi.fn(async(url:unknown)=>String(url).includes('csrf')?json({csrfToken:'token'}):String(url).endsWith('/read')?json({...item,readAt:'2026-10-08T01:00:00Z'}):json(list(String(url).includes('page=1')?[{...item,id:job,title:'읽은 알림',readAt:'2026-10-08T00:00:00Z'}]:[item],String(url).includes('page=1')?1:0,2)))
 vi.stubGlobal('fetch',fetch);const navigate=vi.fn();render(<Notifications owner navigate={navigate} onBack={vi.fn()}/>);
 await screen.findByRole('button',{name:'읽은 알림'});fireEvent.click(screen.getByRole('button',{name:'안읽은 알림만 보기'}));expect(screen.getByText('안 읽은 알림 1개')).toBeInTheDocument();expect(screen.queryByRole('button',{name:'읽은 알림'})).toBeNull();
 fireEvent.click(screen.getByRole('button',{name:'새 지원자'}));fireEvent.click(screen.getByRole('button',{name:'새 지원자'}));await waitFor(()=>expect(navigate).toHaveBeenCalledWith(`/home?store=${store}&view=job&id=${job}`));
 expect(fetch.mock.calls.filter(([url])=>String(url).endsWith('/read'))).toHaveLength(1)
})
it('읽음 응답 손실은 이동하지 않고 같은 멱등 키로 재시도한다',async()=>{
 let fail=true;const keys:(string|null)[]=[];const fetch=vi.fn(async(url:unknown,init?:RequestInit)=>{if(String(url).includes('csrf'))return json({csrfToken:'token'});if(String(url).endsWith('/read')){keys.push(new Headers(init?.headers).get('Idempotency-Key'));if(fail){fail=false;throw Error('offline')}return json({...item,readAt:'2026-10-08T01:00:00Z'})}return json(list([item]))});vi.stubGlobal('fetch',fetch);const navigate=vi.fn();render(<Notifications owner={false} navigate={navigate} onBack={vi.fn()}/>);
 fireEvent.click(await screen.findByRole('button',{name:'새 지원자'}));expect(await screen.findByRole('alert')).toHaveTextContent('알림을 열지 못했어요');expect(navigate).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'새 지원자'}));await waitFor(()=>expect(navigate).toHaveBeenCalledWith(`/home?store=${store}&view=application&id=${id}`));expect(keys[0]).toBeTruthy();expect(keys[1]).toBe(keys[0])
})
it('이미 읽은 알림은 중복 읽음 요청 없이 연다',async()=>{const fetch=vi.fn(async()=>json(list([{...item,readAt:'2026-10-08T00:00:00Z'}])));vi.stubGlobal('fetch',fetch);const navigate=vi.fn();render(<Notifications owner navigate={navigate} onBack={vi.fn()}/>);fireEvent.click(await screen.findByRole('button',{name:'새 지원자'}));expect(screen.getByText('모두 읽었어요')).toBeInTheDocument();expect(fetch).toHaveBeenCalledTimes(1);expect(navigate).toHaveBeenCalledOnce()})
it('조회 오류를 재시도하고 빈 목록을 표시한다',async()=>{let fail=true;vi.stubGlobal('fetch',vi.fn(async()=>{if(fail){fail=false;return json({code:'FORBIDDEN'},403)}return json(list([]))}));render(<Notifications owner navigate={vi.fn()} onBack={vi.fn()}/>);fireEvent.click(await screen.findByRole('button',{name:'다시 시도'}));expect(await screen.findByText('도착한 알림이 없어요.')).toBeInTheDocument()})
