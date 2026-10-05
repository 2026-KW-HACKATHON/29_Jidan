import { useState } from 'react'
import { act,fireEvent,render,screen,waitFor } from '@testing-library/react'
import { expect,it,vi } from 'vitest'
import { mockDialog } from '../jobs/dialogTestSupport'
import { InvitationCreate,InvitationAccept } from './InvitationForms'
import { validInvitationEmail,type Invitation } from './model'
mockDialog()
const invitation:Invitation={id:'one',email:'one@example.com',storeName:'매장',sentAt:'9월 25일',expiresAt:'10월 2일까지',accessUntil:'2026. 09. 26까지',status:'PENDING'}
it.each(['','  ','a','a@','a@@b.com','a b@c.com','a@b','a'.repeat(255)+'@b.com'])('잘못된 이메일 %s을 거부한다',value=>expect(validInvitationEmail(value)).toBe(false))
it('실패 후 입력을 유지하고 정규화된 이메일로 재시도한다',async()=>{const create=vi.fn().mockRejectedValueOnce(Error()).mockResolvedValueOnce(invitation),done=vi.fn();render(<InvitationCreate storeName="매장" service={{create,respond:vi.fn()}} onBack={()=>{}} onCreated={done}/>);fireEvent.change(screen.getByLabelText('초대 대상 이메일 *'),{target:{value:' one@example.com '}});fireEvent.click(screen.getByRole('button',{name:'초대 생성'}));await screen.findByRole('alert');expect(screen.getByLabelText('초대 대상 이메일 *')).toHaveValue('one@example.com');fireEvent.click(screen.getByRole('button',{name:'초대 생성'}));await waitFor(()=>expect(done).toHaveBeenCalledWith(invitation));expect(create.mock.calls[0][0]).toBe('one@example.com')})
it.each(['accept','decline'] as const)('응답 %s 중 중복 실행을 막고 결과를 전달한다',async choice=>{let resolve!:()=>void;const respond=vi.fn(()=>new Promise<void>(r=>{resolve=r})),done=vi.fn();render(<InvitationAccept invitation={invitation} service={{create:vi.fn(),respond}} onBack={()=>{}} onResponded={done}/>);const button=screen.getByRole('button',{name:choice==='accept'?'초대 수락':'거절'});fireEvent.click(button);fireEvent.click(button);expect(respond).toHaveBeenCalledOnce();await act(async()=>resolve());expect(done).toHaveBeenCalledWith(choice)})
it.each(['ACCEPTED','DECLINED','EXPIRED','CANCELLED'] as const)('이미 %s인 초대는 응답할 수 없다',status=>{render(<InvitationAccept invitation={{...invitation,status}} onBack={()=>{}} onResponded={()=>{}}/>);expect(screen.getByRole('button',{name:'초대 수락'})).toBeDisabled();expect(screen.getByRole('button',{name:'거절'})).toBeDisabled()})
it('화면을 벗어난 뒤 늦은 결과는 적용하지 않는다',async()=>{let resolve!:(v:Invitation)=>void;const done=vi.fn(),create=vi.fn((_email:string,_signal:AbortSignal)=>new Promise<Invitation>(r=>{resolve=r}));const {unmount}=render(<InvitationCreate storeName="매장" service={{create,respond:vi.fn()}} onBack={()=>{}} onCreated={done}/>);fireEvent.change(screen.getByLabelText('초대 대상 이메일 *'),{target:{value:'one@example.com'}});fireEvent.click(screen.getByRole('button',{name:'초대 생성'}));unmount();await act(async()=>resolve(invitation));expect(done).not.toHaveBeenCalled();expect(create.mock.calls[0][1].aborted).toBe(true)})

it.each(['accept','decline'] as const)('응답 %s 성공 후 상태 안내와 포커스를 유지한다',async choice=>{
 const service={create:vi.fn(),respond:vi.fn().mockResolvedValue(undefined)}
 function Sample(){const [data,setData]=useState(invitation);return <InvitationAccept invitation={data} service={service} onBack={()=>{}} onResponded={result=>setData({...data,status:result==='accept'?'ACCEPTED':'DECLINED'})}/>}
 render(<Sample/>);const trigger=screen.getByRole('button',{name:choice==='accept'?'초대 수락':'거절'});trigger.focus();fireEvent.click(trigger)
 const dialog=await screen.findByRole('dialog');fireEvent.click(dialog.querySelector('button')!)
 await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
 expect(screen.queryByText(/사용할 수 없는 초대/)).not.toBeInTheDocument()
 expect(screen.getByText(choice==='accept'?'초대를 수락했어요. 매장 참여 상태가 변경됐어요.':'초대를 거절했어요. 이 초대로 매장에 참여하지 않습니다.')).toBeVisible()
 expect(screen.getByRole('heading',{name:'매장에서 초대가 도착했어요'})).toHaveFocus()
})
