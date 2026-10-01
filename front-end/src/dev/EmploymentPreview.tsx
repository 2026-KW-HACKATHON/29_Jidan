import { useMemo } from 'react'
import { Employment, type AccessService } from '../store/Employment'
export default function EmploymentPreview(){const service=useMemo<AccessService>(()=>{let calls=0;return {end:async()=>{await new Promise(r=>setTimeout(r,400));if(new URLSearchParams(location.search).has('fail')&&calls++===0)throw Error('MOCK_FAILURE')}}},[]);return <Employment data={{name:'김지수',job:'홀 서빙 · 정기 근무자',type:'정기 근무',start:'2025. 07. 01',expiry:'별도 종료 전까지',permissions:['업무 매뉴얼','체크리스트','AI 질의응답']}} service={service} onBack={()=>location.assign('/__home/owner')}/>}
