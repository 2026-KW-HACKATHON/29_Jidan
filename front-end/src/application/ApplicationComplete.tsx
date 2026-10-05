import {useEffect,useRef,useState} from 'react'
import {MobileLayout} from '../ui/MobileLayout'
import {AppBar} from '../ui/AppBar'
import {Button} from '../ui/Button'
import {Modal} from '../ui/Modal'
import {withDeadline} from '../async/deadline'
import {jobDate,jobTime,won} from '../jobs/model'
import {applicationService,type Application,type ApplicationService} from './model'
import '../jobs/Jobs.css'
import './Application.css'
export function ApplicationComplete({application,onHome,onBack,onWithdrawn,service=applicationService,initialWithdrawOpen=false}: {
  application:Application;onHome:()=>void;onBack:()=>void;onWithdrawn:()=>void;service?:ApplicationService;initialWithdrawOpen?:boolean
}) {
  const [withdrawOpen,setWithdrawOpen]=useState(initialWithdrawOpen),request=useRef<AbortController|null>(null)
  useEffect(()=>()=>request.current?.abort(),[])
  function close(){request.current?.abort();setWithdrawOpen(false)}
  async function withdraw() {
    const controller=new AbortController();request.current=controller
    try {await withDeadline(signal=>service.withdraw(application.id,signal),controller);if(!controller.signal.aborted)onWithdrawn()}
    finally {if(request.current===controller)request.current=null}
  }
  const job=application.job
  return <><MobileLayout className="jobs-screen application-complete" header={<AppBar title="대타 지원 확인" onBack={onBack}/>} footer={<div className="application-footer"><Button className="application-withdraw" intent="secondary" onClick={()=>setWithdrawOpen(true)}>신청 철회하기</Button><Button onClick={onHome}>홈으로</Button></div>}>
    <div className="jobs-content"><div className="application-heading"><h2>지원이 완료됐어요</h2><p className="jobs-secondary">점주님이 수락하면 알림을 보내드려요</p></div>
      <section className="jobs-card application-complete-card"><div className="application-store"><h3>{job.storeName}</h3><span className="application-pending">승인 대기 중</span></div><dl className="application-details"><div><dt>근무 일</dt><dd>{jobDate(job.date).replaceAll('.', '. ')}</dd></div><div><dt>근무 시간</dt><dd>{jobTime(job).replace(/^0/u,'').replace('–','-')}</dd></div><div><dt>시급</dt><dd>{won(job.hourlyPay)}</dd></div></dl></section>
    </div>
  </MobileLayout><Modal open={withdrawOpen} state="warning" title="지원을 취소할까요?" description="철회 즉시 신청 목록에서 삭제되며, 다시 복구할 수 없습니다." cancelLabel="돌아가기" confirmLabel="지원 철회" onClose={close} onConfirm={withdraw}/></>
}
