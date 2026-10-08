import {useEffect,useId,useRef,useState} from 'react'
import {PickerDialog} from '../ui/PickerDialog'
import {TextareaField} from '../ui/Field'
import {Button} from '../ui/Button'
import {DeadlineExceeded,withDeadline} from '../async/deadline'
import {jobTime,shortJobDate,type Job} from '../jobs/model'
import {applicationService,introductionCharacters,limitIntroduction,validIntroduction,type Application,type ApplicationService} from './model'
import './Application.css'
export function ApplicationDialog({job,onClose,onSuccess,service=applicationService,initialIntroduction=''}: {
  job:Job;onClose:()=>void;onSuccess:(application:Application)=>void;service?:ApplicationService;initialIntroduction?:string
}) {
  const [introduction,setIntroduction]=useState(()=>limitIntroduction(initialIntroduction)),[busy,setBusy]=useState(false),[failed,setFailed]=useState(false),[composing,setComposing]=useState(false)
  const request=useRef<AbortController|null>(null),locked=useRef(false),id=useId()
  useEffect(()=>()=>request.current?.abort(),[])
  async function submit(value: string) {
    const answer=limitIntroduction(value).trim()
    if(locked.current || !validIntroduction(answer))return
    setIntroduction(answer);setComposing(false)
    locked.current=true;setBusy(true);setFailed(false)
    const controller=new AbortController();request.current=controller
    try {
      const result=await withDeadline(signal=>service.submit(job,answer,signal),controller)
      if(!controller.signal.aborted)onSuccess(result)
    } catch(error) {
      if(!controller.signal.aborted || error instanceof DeadlineExceeded)setFailed(true)
    } finally {
      if(request.current===controller && (!controller.signal.aborted || controller.signal.reason instanceof DeadlineExceeded)){locked.current=false;setBusy(false)}
    }
  }
  return <PickerDialog title="대타 공고 지원" onClose={onClose} className="application-dialog">
    <form className="application-content" onSubmit={event=>{event.preventDefault();if(composing && !(event.nativeEvent as SubmitEvent).submitter)return;void submit(event.currentTarget.querySelector('textarea')?.value ?? introduction)}}>
      <div className="application-job-summary"><strong>{job.title}</strong><p>{job.storeName}</p><p>{shortJobDate(job.date)} ㅣ {jobTime(job)}</p></div>
      <div className="application-introduction"><TextareaField label="지원자 자기소개 *" placeholder={'관련 경력과 지원 이유를 알려주세요.\n이 공고에서 잘할 수 있는 일을 적어주세요.'} value={introduction} disabled={busy} aria-describedby={`${id}-counter ${id}-helper`} onCompositionStart={()=>setComposing(true)} onCompositionEnd={event=>{setComposing(false);setIntroduction(limitIntroduction(event.currentTarget.value))}} onChange={event=>setIntroduction(composing?event.target.value:limitIntroduction(event.target.value))}/><p className="application-counter" id={`${id}-counter`}>{introductionCharacters(introduction).length} / 500</p></div>
      <p className="application-helper" id={`${id}-helper`}>작성한 소개서와 등록한 경력이 점주에게 전달돼요.</p>
      {failed && <p className="application-error" role="alert">지원하지 못했어요. 작성한 내용을 유지했으니 다시 시도해 주세요.</p>}
      <Button type="submit" busy={busy} disabled={!validIntroduction(limitIntroduction(introduction))}>{busy?'지원 중이에요':'지원 완료하기'}</Button>
    </form>
  </PickerDialog>
}
