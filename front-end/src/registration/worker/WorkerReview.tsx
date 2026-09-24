import type { ReactNode } from 'react'
import { Button } from '../../ui/Button'
import information from './assets/information.svg'
import { careerPeriod, daysText, rangeText, weekHours, type WorkerDraft } from './model'
export function WorkerReview({ draft, email, edit }: { draft: WorkerDraft; email:string; edit?:(step:1|2|3)=>void }) {
  function card(title:string,step:1|2|3,children:ReactNode) { return <article className="worker-card"><header><h3>{title}</h3>{edit&&<button type="button" aria-label={`${title} 수정`} className="worker-link" onClick={()=>edit(step)}>수정</button>}</header>{children}</article> }
  return <>
    {card('기본 정보',1,<><dl>{[['이름',draft.name],['전화번호',draft.phone],['생년월일',draft.birth.replaceAll('-','. ')],['성별',draft.gender]].map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl><small>Google · {email}</small></>)}
    {card('근무 정보',2,<><dl><div><dt>경력</dt><dd>{draft.experience}</dd></div></dl>{draft.experience==='경력 있음'&&draft.careers.map(c=><div key={c.id}><p>{c.industry} · {c.duties}</p><small>{careerPeriod(c)}{c.store&&` · ${c.store}`}</small></div>)}</>)}
    {card('가능한 시간',3,<>{draft.availability.map(a=><p key={a.id}>{daysText(a.days)}　{rangeText(a)}</p>)}<small>매주 반복 · 주 {weekHours(draft.availability)}시간</small></>)}
  </>
}
export function WorkerCompleteContent({ draft, onProfile }: { draft:WorkerDraft;onProfile:()=>void }) {
  return <><article className="worker-card"><h3>{draft.name} 님의 근무 프로필</h3><p className="worker-muted">{draft.experience}</p>{draft.availability.map(a=><p className="worker-muted" key={a.id}>{daysText(a.days)}　{rangeText(a)}</p>)}</article><Button intent="secondary" onClick={onProfile}>내 프로필 보기</Button></>
}
export function WorkerStatusIcon() { return <div className="worker-status"><img src={information} alt="" width="21.7" height="21.7" /></div> }
