import { useCallback,useRef,useState } from 'react'
import { MobileLayout } from '../ui/MobileLayout'
import { AppBar } from '../ui/AppBar'
import { Button } from '../ui/Button'
import { InputField } from '../ui/Field'
import { Modal } from '../ui/Modal'
import { useInvitationCommand } from './useInvitationCommand'
import { validInvitationEmail,unavailableInvitations,type Invitation,type InvitationService } from './model'
import './Invitation.css'
export function InvitationCreate({storeName,onBack,onCreated,service=unavailableInvitations}: {storeName:string;onBack:()=>void;onCreated:(invitation:Invitation)=>void;service?:InvitationService}) {
 const [email,setEmail]=useState(''),[touched,setTouched]=useState(false)
 const {busy,failed,run}=useInvitationCommand()
 const valid=validInvitationEmail(email)
 function submit(){setTouched(true);if(valid)void run(signal=>service.create(email.trim(),signal),onCreated)}
 return <MobileLayout className="invitation-screen invitation-create" header={<AppBar title="근무자 초대" onBack={onBack}/>}><form className="invitation-create-content" noValidate onSubmit={event=>{event.preventDefault();submit()}}><div className="invitation-heading"><h2>함께 일할 근무자를 초대하세요</h2><p>{storeName}</p></div><InputField label="초대 대상 이메일 *" type="email" autoComplete="email" maxLength={254} value={email} placeholder="예: jidan@email.com" onBlur={()=>setTouched(true)} onChange={event=>setEmail(event.target.value)} disabled={busy} error={touched&&!valid?'올바른 이메일을 입력해 주세요.':undefined}/><div className="invitation-create-bottom"><section className="invitation-card"><h3>초대 전 확인해 주세요</h3><p>입력한 이메일로 초대 링크를 보내요.<br/>근무자는 링크를 통해 매장 온보딩에 접근할 수 있어요.</p><small>초대 링크는 생성 후 7일 동안 유효해요.</small></section>{failed&&<p role="alert" className="invitation-error">초대를 생성하지 못했어요. 입력 내용을 유지했으니 다시 시도해 주세요.</p>}<Button type="submit" busy={busy} disabled={!valid}>초대 생성</Button></div></form></MobileLayout>
}
export function InvitationAccept({invitation,onBack,onResponded,service=unavailableInvitations}: {invitation:Invitation;onBack:()=>void;onResponded:(choice:'accept'|'decline')=>void;service?:InvitationService}) {
 const {busy,failed,run}=useInvitationCommand()
 const [result,setResult]=useState<'accept'|'decline'|null>(null),[dismissed,setDismissed]=useState(false)
 const resultHeading=useRef<HTMLHeadingElement>(null)
 const focusResultHeading=useCallback(()=>{if(resultHeading.current?.isConnected)resultHeading.current.focus()},[])
 const available=invitation.status==='PENDING'
 const response=result??(invitation.status==='ACCEPTED'?'accept':invitation.status==='DECLINED'?'decline':null)
 const notice=response==='accept'?'초대를 수락했어요. 매장 참여 상태가 변경됐어요.':response==='decline'?'초대를 거절했어요. 이 초대로 매장에 참여하지 않습니다.':available?'수락하면 매장 업무 자료를 확인하고 온보딩을 시작할 수 있어요.':'사용할 수 없는 초대예요. 매장에 새 초대를 요청해 주세요.'
 function respond(choice:'accept'|'decline'){void run(signal=>service.respond(invitation.id,choice,signal),()=>{setResult(choice);onResponded(choice)})}
 return <><MobileLayout className="invitation-screen invitation-accept" header={<AppBar title="매장 초대" onBack={onBack}/>} footer={<div className="invitation-actions"><Button intent="secondary" disabled={!available||busy||result!==null} onClick={()=>respond('decline')}>거절</Button><Button busy={busy} disabled={!available||result!==null} onClick={()=>respond('accept')}>초대 수락</Button></div>}><div className="invitation-content"><div className="invitation-heading"><h2 ref={resultHeading} tabIndex={-1}>매장에서 초대가 도착했어요</h2><p>초대 정보를 확인하고 참여 여부를 선택해 주세요.</p></div><section className="invitation-card"><h3 className="invitation-store-title">{invitation.storeName}</h3><p>신규 근무자로 초대되었어요.</p><dl><div><dt>접근 기간</dt><dd>{invitation.accessUntil}</dd></div><div><dt>시작 시점</dt><dd>수락 후 즉시</dd></div></dl></section><p className="invitation-notice">{notice}</p>{failed&&<p role="alert" className="invitation-error">처리하지 못했어요. 다시 시도해 주세요.</p>}</div></MobileLayout><Modal open={result!==null&&!dismissed} restoreFocus={false} onClosed={focusResultHeading} title={result==='accept'?'초대를 수락했어요':'초대를 거절했어요'} description={result==='accept'?'매장 참여 상태가 변경됐어요.':'이 초대로 매장에 참여하지 않습니다.'} onClose={()=>setDismissed(true)}/></>
}
