import { useEffect,useId,useRef,useState } from 'react'
import { AppBar } from '../../ui/AppBar'
import { Button } from '../../ui/Button'
import { InputField,SelectField,TextareaField } from '../../ui/Field'
import { MobileLayout } from '../../ui/MobileLayout'
import { RegistrationProgress } from '../../ui/RegistrationProgress'
import { SingleSelectDialog } from '../../ui/SingleSelectDialog'
import { PickerDialog } from '../../ui/PickerDialog'
import { Modal } from '../../ui/Modal'
import { ValuePicker } from '../../registration/worker/ValuePicker'
import { useCommand } from '../../async/useCommand'
import { clock,emptyJobDraft,experienceOptions,paymentOptions,validateJobDraft,workParts,unavailableOwnerJobs,type JobDraft,type OwnerJob,type OwnerJobService } from './model'
import './OwnerJobs.css'
type Picker='part'|'experience'|'payment'|'date'|'start'|'end'
function DatePicker({value,onClose,onSave}:{value:string;onClose:()=>void;onSave:(date:string)=>void}){
 const [date,setDate]=useState(value)
 return <PickerDialog title="근무 날짜" onClose={onClose}><div className="owner-job-picker-content"><InputField type="date" label="근무 날짜" value={date} onChange={e=>setDate(e.target.value)}/><Button disabled={!date} onClick={()=>onSave(date)}>선택 완료</Button></div></PickerDialog>
}
export function JobRegistration({onBack,onCreated,service=unavailableOwnerJobs,initialStep=1,initialDraft=emptyJobDraft,initialPicker=null,initialResult}: {onBack:()=>void;onCreated:(job:OwnerJob)=>void;service?:OwnerJobService;initialStep?:1|2|3;initialDraft?:JobDraft;initialPicker?:Picker|null;initialResult?:'success'|'failure'}){
 const [step,setStep]=useState(initialStep),[draft,setDraft]=useState(initialDraft),[errors,setErrors]=useState<Record<string,string>>({}),[picker,setPicker]=useState<Picker|null>(initialPicker),[created,setCreated]=useState<OwnerJob|null>(null),[success,setSuccess]=useState(initialResult==='success'),[failure,setFailure]=useState(initialResult==='failure')
 const {busy,run,cancel}=useCommand(),formId=useId(),heading=useRef<HTMLHeadingElement>(null),previousStep=useRef(step),generation=useRef(0)
 useEffect(()=>{if(previousStep.current!==step){heading.current?.focus();heading.current?.closest('main')?.scrollTo?.(0,0);previousStep.current=step}},[step])
 useEffect(()=>()=>{generation.current+=1},[])
 const change=(patch:Partial<JobDraft>)=>{setDraft(d=>({...d,...patch}));setErrors({})}
 const closePicker=()=>setPicker(null)
 async function register(){const e=validateJobDraft(draft);setErrors(e);if(Object.keys(e).length){setStep(e.title||e.description||e.part||e.date||e.time?1:e.experience||e.qualifications?2:3);return}setFailure(false);const current=++generation.current;const ok=await run(signal=>service.create({...draft,title:draft.title.trim(),description:draft.description.trim(),qualifications:draft.qualifications.trim(),payNotice:draft.payNotice.trim()},signal),job=>{setCreated(job);setSuccess(true)});if(!ok&&current===generation.current)setFailure(true)}
 function next(){if(busy||success)return;if(step===3){void register();return}const e=validateJobDraft(draft,step);setErrors(e);if(!Object.keys(e).length)setStep(step===1?2:3)}
 const back=()=>{generation.current+=1;cancel();if(step===1)onBack();else setStep(step===3?2:1)}
 const closeResult=()=>{cancel();setFailure(false);setSuccess(false);if(created)onCreated(created)}
 return <><MobileLayout className="owner-jobs owner-job-registration" header={<AppBar title="공고 등록" onBack={back}/>} footer={<div className="owner-job-actions">{step!==1&&<Button intent="secondary" disabled={busy||success} onClick={back}>이전</Button>}<Button type="submit" form={formId} busy={busy} disabled={success}>{step===3?'공고 등록하기':'다음'}</Button></div>}>
 <form id={formId} className="owner-job-form" noValidate onSubmit={event=>{event.preventDefault();next()}}>
 <RegistrationProgress step={step} labels={['업무 정보','경험 조건','급여 조건']} label="공고 등록 진행 단계"/>
 <h2 ref={heading} tabIndex={-1}>{step===1?'업무 정보를 알려주세요':step===2?'경험 조건을 알려주세요':'급여 조건을 알려주세요'}</h2>
 {step===1?<><InputField label="담당 업무명 *" placeholder="예: 주말 오픈 · 주문 접수 및 포장" value={draft.title} maxLength={100} onChange={e=>change({title:e.target.value})} error={errors.title}/><TextareaField label="업무 상세 설명 *" placeholder="예: 주문 접수, 상품 포장, 매장 정리를 맡아요." value={draft.description} maxLength={2000} onChange={e=>change({description:e.target.value})} error={errors.description}/><div className="owner-job-schedule"><SelectField label="근무 파트 *" onClick={()=>setPicker('part')} error={errors.part}>{draft.part||'근무 파트 선택'}</SelectField><SelectField label="근무 날짜 *" onClick={()=>setPicker('date')} error={errors.date}>{draft.date||'날짜 선택'}</SelectField><div className="owner-job-actions">{(['start','end'] as const).map(key=><SelectField key={key} label={key==='start'?'시작 시간 *':'종료 시간 *'} onClick={()=>setPicker(key)}>{draft[key]<0?'시간 선택':`${key==='end'&&draft.nextDay?'다음 날 ':''}${clock(draft[key])}`}</SelectField>)}</div>{errors.time&&<p role="alert" className="owner-job-error">{errors.time}</p>}</div></>:
 step===2?<><SelectField label="최소 경력 조건" onClick={()=>setPicker('experience')} error={errors.experience}>{draft.experience}</SelectField><p className="owner-job-notice">{draft.experience==='무관'?'경력이 없어도 지원할 수 있어요.':`${draft.experience}의 경력이 필요해요.`}</p><TextareaField label="추가 경험 · 자격 요건 (선택)" placeholder="예: 식품 매장 근무 경험, 학력 조건, 나이" value={draft.qualifications} maxLength={2000} onChange={e=>change({qualifications:e.target.value})} error={errors.qualifications}/></>:
 <><InputField label="시급 *" placeholder="예: 12,000원" inputMode="numeric" value={draft.pay?Number(draft.pay).toLocaleString('ko-KR'):''} maxLength={11} disabled={busy} onChange={e=>change({pay:e.target.value.replace(/\D/g,'').slice(0,9)})} error={errors.pay}/><SelectField label="지급 시점 *" disabled={busy} onClick={()=>setPicker('payment')} error={errors.payment}>{draft.payment||'지급 시점 선택'}</SelectField><TextareaField label="기타 보수 안내 (선택)" disabled={busy} placeholder="예: 식사 제공 여부와 추가 지급 조건을 알려주세요." value={draft.payNotice} maxLength={2000} onChange={e=>change({payNotice:e.target.value})} error={errors.payNotice}/><p className="owner-job-notice">등록 후 공고 내용을 확인할 수 있어요.</p></>}
 </form></MobileLayout>
 {picker==='part'&&<SingleSelectDialog title="근무 파트" description="모집할 근무 파트를 선택해 주세요." options={workParts} value={draft.part} onClose={closePicker} onConfirm={part=>{change({part});closePicker()}}/>}
 {picker==='experience'&&<SingleSelectDialog title="최소 경력 조건" description="지원에 필요한 최소 경력을 선택해 주세요." options={experienceOptions} value={draft.experience} onClose={closePicker} onConfirm={experience=>{change({experience});closePicker()}}/>}
 {picker==='payment'&&<SingleSelectDialog title="지급 시점" description="근무자에게 보수를 지급할 시점을 선택해 주세요." options={paymentOptions} value={draft.payment} onClose={closePicker} onConfirm={payment=>{change({payment});closePicker()}}/>}
 {picker==='date'&&<DatePicker value={draft.date} onClose={closePicker} onSave={date=>{change({date});closePicker()}}/>}
 {(picker==='start'||picker==='end')&&<ValuePicker title={picker==='start'?'시작 시간':'종료 시간'} type="time" value={draft[picker]<0?'':String(draft[picker])} overnight={picker==='end'?draft.nextDay:undefined} onClose={closePicker} onSave={(value,nextDay)=>change({[picker]:Number(value),...(picker==='end'?{nextDay:nextDay??false}:{})})}/>}
 <Modal open={failure} state="error" title="공고를 등록하지 못했어요" description={"입력한 내용은 그대로 남아 있어요.\n연결 상태를 확인한 뒤 다시 시도해 주세요."} cancelLabel="닫기" confirmLabel="다시 시도" busy={busy} onClose={()=>{generation.current+=1;cancel();setFailure(false)}} onConfirm={register}/>
 <Modal open={success} title="공고를 등록했어요" description={"지원자가 들어오면 알림으로 알려드려요.\n등록한 공고에서 지원 현황을 확인하세요."} showCancel cancelLabel="닫기" confirmLabel="공고 보기" onClose={closeResult} onConfirm={()=>{if(!created)onBack()}}/>
 </>
}
