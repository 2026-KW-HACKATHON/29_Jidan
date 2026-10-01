import type { Ref } from 'react'
import character from '../brand/assets/character.png'
import check from './assets/check.svg'
import type { EmploymentData } from './Employment'
export function WorkerInformation({data,ended,summaryRef}: {data:EmploymentData;ended:boolean;summaryRef?:Ref<HTMLHeadingElement>}) {
 return <article className="employment-card"><div className="employment-worker"><img className="employment-character" src={character} alt="" width="60" height="60"/><div className="employment-worker-text"><div className="employment-worker-heading"><h2 tabIndex={-1} ref={summaryRef}>{data.name}</h2><strong className={`employment-badge${ended?' employment-badge-ended':''}`}>{ended?'접근 종료':data.status==='expiring'?'만료 예정':'재직 중'}</strong></div><p>{data.job}</p></div></div><dl>{[['접근 유형',data.type],['접근 시작일',data.start],['접근 만료일',ended?'접근 종료':data.expiry]].map(([label,value])=><div key={label}><dt>{label}</dt><dd className={label==='접근 만료일' && data.status==='expiring' && !ended?'employment-expiry':undefined}>{value}</dd></div>)}</dl></article>
}
export function PermissionCard({permissions,ended}: {permissions:readonly string[];ended:boolean}) {
 return <article className="employment-card"><h3>매장 접근 권한</h3>{permissions.map(p=><div className="employment-permission" key={p}><span>{p}</span><strong className={ended?'employment-permission-ended':undefined}>{!ended&&<img src={check} alt=""/>}{ended?'접근 종료':'접근 허용'}</strong></div>)}</article>
}
