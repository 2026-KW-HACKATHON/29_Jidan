import { AppBar } from '../ui/AppBar'
import { MobileLayout } from '../ui/MobileLayout'
import { AuthFlow } from '../auth/AuthFlow'
import { Fragment, lazy, Suspense } from 'react'
import { handlePreviewLink, navigatePreview, usePreviewLocation } from './navigation'
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
 {title:'공통·로그인',links:[['공통 UI·오버레이','/__ui'],['Google 로그인','/__preview/login'],['가입 유형 선택','/__auth/signup']]},
 {title:'점주 가입',links:[['기본 정보','/__auth/owner'],['매장 정보·업종 선택','/__auth/owner?step=store'],['입력 확인','/__auth/owner?step=review'],['승인 대기','/__auth/owner?step=pending'],['등록 실패·재시도','/__auth/owner?step=review&fail=1']]},
 {title:'일반회원 등록',links:[['기본 정보','/__auth/worker'],['근무 경력·종료 연월','/__auth/worker?step=career'],['신입','/__auth/worker?step=work'],['가능 시간·시간 선택','/__auth/worker?step=time'],['입력 확인','/__auth/worker?step=review'],['등록 완료','/__auth/worker?step=complete'],['등록 실패·재시도','/__auth/worker?step=review&fail=1']]},
 {title:'홈·프로필·매장',links:[['점주 홈','/__home/owner'],['점주 홈 빈 상태','/__home/owner?empty=1'],['일반회원 홈','/__home/worker'],['일반회원 홈 빈 상태','/__home/worker?empty=1'],['내 프로필·섹션 수정','/__profile/worker'],['프로필 저장 실패·재시도','/__profile/worker?fail=1'],['대타 근무자 프로필','/__store/applicant'],['근무 상태·접근 종료','/__store/employment'],['접근 종료 실패·재시도','/__store/employment?fail=1']]},
] as const
function ScreenList({current}:{current:string}) {
 return <>{groups.map(group=><section key={group.title}><h3>{group.title}</h3>{group.links.map(([title,url])=><a key={url} href={url} aria-current={url===current?'page':undefined}>{title}</a>)}</section>)}</>
}
export default function PreviewApp(){const current=usePreviewLocation();const path=current.split('?')[0];let content
 if(path==='/__ui')content=<Components/>
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
 return <div className="preview-workbench" onClick={handlePreviewLink}><nav className="preview-sidebar" aria-label="미리보기 화면 목록"><h2><a href="/__preview">화면 목록</a></h2><p className="preview-note">검수 전용 · 샘플 데이터<br/>API 미연결</p><ScreenList current={current}/></nav><div className="preview-host"><aside className="preview-banner"><a href="/__preview">미리보기 · 화면 목록</a><span>샘플 데이터 / API 미연결</span></aside><Suspense fallback={<p role="status">미리보기 화면을 불러오고 있어요.</p>}><Fragment key={current}>{content}</Fragment></Suspense></div></div>
}
