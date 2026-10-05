import { AppBar } from '../ui/AppBar'
import { MobileLayout } from '../ui/MobileLayout'
import { AuthFlow } from '../auth/AuthFlow'
import { Fragment, lazy, Suspense } from 'react'
import { handlePreviewLink, navigatePreview, usePreviewLocation } from './navigation'
const Invitations = lazy(() => import('./InvitationsPreview'))
const StoreManagement = lazy(() => import('./StoreManagementPreview'))
const OwnerJobs = lazy(() => import('./OwnerJobsPreview'))
const Jobs = lazy(() => import('./JobsPreview'))
const Components = lazy(() => import('./ComponentPreview'))
const Roles = lazy(() => import('./RoleSelectionPreview'))
const Owner = lazy(() => import('./OwnerRegistrationPreview'))
const Worker = lazy(() => import('./WorkerRegistrationPreview'))
const OwnerHome = lazy(() => import('./OwnerHomePreview'))
const WorkerHome = lazy(() => import('./WorkerHomePreview'))
const Applicant = lazy(() => import('./ApplicantProfilePreview'))
const Employment = lazy(() => import('./EmploymentPreview'))
const ProfilePreview = lazy(() => import('./WorkerProfilePreview'))
import './PreviewApp.css'
const groups=[
 {title:'점주 지원자 확인',links:[['지원자 목록','/__owner/jobs?view=applicants&id=open'],['지원자 없음','/__owner/jobs?view=applicants&id=open&noApplicants=1'],['박지원 지원서','/__owner/jobs?view=applicants&id=open&review=park'],['김래원 지원서·열람 전용','/__owner/jobs?view=applicants&id=open&review=kim&readonly=1'],['근무 요청 확인','/__owner/jobs?view=applicants&id=open&overlay=request&applicant=park'],['근무 요청 완료','/__owner/jobs?view=applicants&id=open&overlay=request-complete&applicant=park'],['근무 요청 실패·재시도','/__owner/jobs?view=applicants&id=open&overlay=request&applicant=park&requestFail=1']]},
 {title:'점주 공고 관리',links:[['모집 중 공고','/__owner/jobs'],['마감 공고','/__owner/jobs?tab=closed'],['공고 없는 상태','/__owner/jobs?empty=1'],['모집 마감 확인','/__owner/jobs?view=detail&id=open&overlay=close'],['모집 마감 완료','/__owner/jobs?view=detail&id=open&overlay=closed'],['모집 마감 실패','/__owner/jobs?view=detail&id=open&overlay=close&fail=1']]},
 {title:'점주 공고 등록',links:[['1. 업무 조건','/__owner/jobs?view=create'],['2. 경험 조건','/__owner/jobs?view=create&step=2'],['3. 보수 조건','/__owner/jobs?view=create&step=3'],['근무 파트 선택','/__owner/jobs?view=create&picker=part'],['최소 경력 선택','/__owner/jobs?view=create&step=2&picker=experience'],['지급 시점 선택','/__owner/jobs?view=create&step=3&picker=payment'],['공고 등록 완료','/__owner/jobs?view=create&step=3&result=success'],['공고 등록 실패','/__owner/jobs?view=create&step=3&result=failure&fail=1']]},
 {title:'근무자 초대',links:[['근무자 초대 관리','/__invitations'],['지난 초대','/__invitations?view=past'],['활성 초대 없음','/__invitations?empty=1'],['초대 취소 확인','/__invitations?overlay=cancel'],['초대 재전송 안내','/__invitations?overlay=resent'],['초대 취소 실패','/__invitations?overlay=cancel&fail=1'],['근무자 초대 생성','/__invitations?view=create'],['초대 수락','/__invitations?view=accept'],['초대 생성 실패','/__invitations?view=create&fail=1']]},
 {title:'매장·근무자 관리',links:[['매장 관리 홈','/__store/manage'],['근무자 목록','/__store/manage?view=workers'],['김지수 상세','/__store/manage?view=worker&id=jisu'],['최유진 상세·만료 예정','/__store/manage?view=worker&id=yujin'],['근무자 목록 빈 상태','/__store/manage?view=workers&empty=1'],['접근 종료 실패','/__store/manage?view=worker&id=jisu&fail=1']]},
 {title:'대타 공고',links:[['공고 탐색','/__jobs'],['검색 결과 없음','/__jobs?empty=1'],['공고 필터','/__jobs?filter=1'],['공고 상세','/__jobs?view=detail&id=cafe'],['다음 날 종료 공고','/__jobs?view=detail&id=night'],['지원 자기소개 작성','/__jobs?view=apply&id=cafe&case=empty'],['지원 작성 완료','/__jobs?view=apply&id=cafe&case=written'],['지원 완료','/__jobs?view=complete&id=cafe&case=complete'],['지원 철회 확인','/__jobs?view=complete&id=cafe&case=withdraw'],['지원 실패·재시도','/__jobs?view=apply&id=cafe&case=submit-fail&fail=1'],['철회 실패·재시도','/__jobs?view=complete&id=cafe&case=withdraw-fail&fail=1']]},
 {title:'공통·로그인',links:[['공통 UI·오버레이','/__ui'],['Google 로그인','/__preview/login'],['가입 유형 선택','/__auth/signup']]},
 {title:'점주 가입',links:[['기본 정보','/__auth/owner'],['매장 정보·업종 선택','/__auth/owner?step=store'],['입력 확인','/__auth/owner?step=review'],['승인 대기','/__auth/owner?step=pending'],['등록 실패·재시도','/__auth/owner?step=review&fail=1']]},
 {title:'일반회원 등록',links:[['기본 정보','/__auth/worker'],['근무 경력·종료 연월','/__auth/worker?step=career'],['신입','/__auth/worker?step=work'],['가능 시간·시간 선택','/__auth/worker?step=time'],['입력 확인','/__auth/worker?step=review'],['등록 완료','/__auth/worker?step=complete'],['등록 실패·재시도','/__auth/worker?step=review&fail=1']]},
 {title:'홈·프로필·매장',links:[['점주 홈','/__home/owner'],['점주 홈 빈 상태','/__home/owner?empty=1'],['일반회원 홈','/__home/worker'],['일반회원 홈 빈 상태','/__home/worker?empty=1'],['내 프로필·섹션 수정','/__profile/worker'],['프로필 저장 실패·재시도','/__profile/worker?fail=1'],['대타 근무자 프로필','/__store/applicant'],['근무 상태·접근 종료','/__store/employment'],['접근 종료 실패·재시도','/__store/employment?fail=1']]},
] as const
function ScreenList({current}:{current:string}) {
 return <>{groups.map(group=><section key={group.title}><h3>{group.title}</h3>{group.links.map(([title,url])=><a key={url} href={url} aria-current={url===current?'page':undefined}>{title}</a>)}</section>)}</>
}
export default function PreviewApp(){const current=usePreviewLocation();const path=current.split('?')[0];let content
 if(path==='/__owner/jobs')content=<OwnerJobs/>
 else if(path==='/__invitations')content=<Invitations/>
 else if(path==='/__store/manage')content=<StoreManagement/>
 else if(path==='/__jobs')content=<Jobs/>
 else if(path==='/__ui')content=<Components/>
 else if(path==='/__preview/login')content=<AuthFlow path="/login" search="" navigate={()=>navigatePreview('/__auth/signup')} service={{read:async()=>({kind:'unavailable'}),startGoogle:async()=>{throw Error('MOCK')}}} renderHome={()=>null} renderRegistration={()=>null}/>
 else if(path==='/__auth/signup')content=<Roles/>
 else if(path==='/__auth/owner')content=<Owner/>
 else if(path==='/__auth/worker')content=<Worker/>
 else if(path==='/__home/owner')content=<OwnerHome/>
 else if(path==='/__home/worker')content=<WorkerHome/>
 else if(path==='/__profile/worker')content=<ProfilePreview/>
 else if(path==='/__store/applicant')content=<Applicant/>
 else if(path==='/__store/employment')content=<Employment/>
 else content=<MobileLayout header={<AppBar title="화면 탐색" onBack={()=>navigatePreview("/__preview")}/>}><div className="preview-dashboard"><p>샘플 데이터로 구현된 화면을 확인합니다. 실제 로그인·등록·권한 변경은 수행하지 않습니다.</p><ScreenList current={current}/></div></MobileLayout>
 return <div className="preview-workbench" onClick={handlePreviewLink}><nav className="preview-sidebar" aria-label="미리보기 화면 목록"><header className="preview-intro"><a className="preview-wordmark" href="/__preview">지단</a><p className="preview-eyebrow">INTERACTIVE PROTOTYPE</p><h2>매장의 시작을,<br/>더 단단하게.</h2><p className="preview-description">점주와 근무자의 경험을<br/>직접 이어서 확인해 보세요.</p></header><div className="preview-role-links"><a href="/__home/owner">점주 체험</a><a href="/__home/worker">근무자 체험</a></div><div className="preview-screen-list"><h2>화면 목록</h2><ScreenList current={current}/></div><footer className="preview-footer"><p>검수 전용 · 샘플 데이터<br/>실제 API는 연결되지 않았습니다.</p><a href="https://www.figma.com/design/ZaFHresnBXJ1h98Xl1AUDj/KW-HACKATHON-2026" target="_blank" rel="noreferrer">Figma Design <span aria-hidden="true">↗</span></a></footer></nav><div className="preview-host"><aside className="preview-banner"><a href="/__preview">미리보기 · 화면 목록</a><span>샘플 데이터 / API 미연결</span></aside><Suspense fallback={<p role="status">미리보기 화면을 불러오고 있어요.</p>}><Fragment key={path==='/__owner/jobs' ? `${path}:${['step','picker','result','fail','case','empty','overlay','review','readonly','noApplicants','applicant','requestFail'].map(key=>new URLSearchParams(current.split('?')[1]).get(key)).join(':')}` : path==='/__jobs' ? `${path}:${new URLSearchParams(current.split('?')[1]).get('empty')}:${new URLSearchParams(current.split('?')[1]).get('filter')}:${new URLSearchParams(current.split('?')[1]).get('case')}` : path==='/__invitations' ? `${path}:${new URLSearchParams(current.split('?')[1]).has('fail')}:${new URLSearchParams(current.split('?')[1]).has('empty')}:${new URLSearchParams(current.split('?')[1]).get('overlay')}` : path==='/__store/manage' ? `${path}:${new URLSearchParams(current.split('?')[1]).has('empty')}` : current}>{content}</Fragment></Suspense></div></div>
}
