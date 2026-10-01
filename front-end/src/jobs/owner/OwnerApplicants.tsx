import {useState} from 'react'
import {MobileLayout} from '../../ui/MobileLayout'
import {AppBar} from '../../ui/AppBar'
import {Button} from '../../ui/Button'
import {PickerDialog} from '../../ui/PickerDialog'
import {OwnerJobSummary} from './OwnerJobList'
import type {OwnerJob} from './model'
import './OwnerJobs.css'
export type JobApplicant={id:string;name:string;experience:string;introduction:string}
export function ApplicantReview({applicant,job,readOnly=false,onClose,onRequest}:{applicant:JobApplicant;job:OwnerJob;readOnly?:boolean;onClose:()=>void;onRequest:(applicant:JobApplicant)=>void}){
 return <PickerDialog className="owner-applicant-review" title="지원서 보기" onClose={onClose}><div className="owner-applicant-review-content"><div className="owner-applicant-identity"><h3>{applicant.name}</h3><p>{applicant.experience||'등록한 경력 없음'}</p></div><OwnerJobSummary job={job}/><section className="owner-applicant-introduction"><h4>자기소개</h4><p>{applicant.introduction}</p></section><p className="owner-applicant-hint">지원자가 직접 작성한 내용이에요.</p>{!readOnly&&job.status==='recruiting'&&<Button onClick={()=>onRequest(applicant)}>근무 요청 보내기</Button>}</div></PickerDialog>
}
export function OwnerApplicants({job,applicants,onBack,onCloseJob,onRequest,initialApplicantId,initialReadOnly=false}:{job:OwnerJob;applicants:readonly JobApplicant[];onBack:()=>void;onCloseJob:()=>void;onRequest:(applicant:JobApplicant)=>void;initialApplicantId?:string;initialReadOnly?:boolean}){
 const [review,setReview]=useState(initialApplicantId),applicant=applicants.find(applicant=>applicant.id===review),readOnly=initialReadOnly||job.status!=='recruiting'
 return <><MobileLayout className="owner-jobs owner-applicants" header={<AppBar title="지원자 확인" onBack={onBack}/>} footer={<Button intent="secondary" disabled={job.status!=='recruiting'} onClick={onCloseJob}>{job.status==='recruiting'?'지원자 선정 없이 모집 마감':'모집 종료'}</Button>}><div className="owner-job-list"><h2>지원자를 확인해 주세요</h2><OwnerJobSummary job={job}/><strong className="owner-applicant-count">지원자 {applicants.length}명</strong><div className="owner-applicant-cards">{applicants.map(applicant=><article className="owner-applicant-card" key={applicant.id}><div className="owner-applicant-identity"><h3>{applicant.name}</h3><p>{applicant.experience||'등록한 경력 없음'}</p></div><div className="owner-job-actions"><Button intent="secondary" aria-label={`${applicant.name} 지원서 보기`} onClick={()=>setReview(applicant.id)}>지원서 보기</Button>{!readOnly&&<Button aria-label={`${applicant.name} 근무 요청`} onClick={()=>onRequest(applicant)}>근무 요청</Button>}</div></article>)}{!applicants.length&&<div className="owner-applicant-card" role="status"><h3>아직 지원자가 없어요</h3><p>지원자가 들어오면 알림을 보내드릴게요.</p></div>}</div></div></MobileLayout>{applicant&&<ApplicantReview applicant={applicant} job={job} readOnly={readOnly} onClose={()=>setReview(undefined)} onRequest={applicant=>{setReview(undefined);onRequest(applicant)}}/>}</>
}
