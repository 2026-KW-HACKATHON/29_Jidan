import {useEffect,useRef,useState} from 'react'
import {MobileLayout} from '../ui/MobileLayout'
import {AppBar} from '../ui/AppBar'
import {Button} from '../ui/Button'
import {LoadingState} from '../ui/LoadingState'
import {Modal} from '../ui/Modal'
import {QaCitation} from './QaCitation'
import {useConversation} from './useConversation'
import type {QaService} from './service'
import type {QACitation} from './types.generated'
import './Qa.css'
export function QaChat({service,storeName,conversationId='',onBack,onLatest}:{service:QaService;storeName:string;conversationId?:string;onBack:()=>void;onLatest:()=>void}){
 const chat=useConversation(service,conversationId),[text,setText]=useState(''),[citation,setCitation]=useState<QACitation|null>(null)
 const lastStatus=chat.turns.at(-1)?.status
 const end=useRef<HTMLDivElement>(null),lastSent=useRef(chat.sent)
 useEffect(()=>{end.current?.scrollIntoView?.({block:'end'})},[chat.turns.length,lastStatus])
 useEffect(()=>{if(chat.sent!==lastSent.current){lastSent.current=chat.sent;setText('')}},[chat.sent])
 const disabled=chat.busy||chat.loading||chat.pending||chat.running||chat.blocked
 return <MobileLayout className="qa-screen" header={<AppBar title="AI 업무 질문" onBack={onBack}/>} footer={!chat.blocked&&<form className="qa-composer" onSubmit={e=>{e.preventDefault();if(!disabled&&text.trim())chat.send({kind:'TEXT',text:text.trim(),transcriptionId:null,imageMediaIds:[]})}}>
  <textarea aria-label="궁금한 업무" placeholder="궁금한 업무를 입력해 주세요" value={text} maxLength={2000} disabled={disabled} onChange={e=>setText(e.target.value)} rows={2}/>
  <div className="qa-tools"><span className="qa-muted">{text.length}/2,000</span><Button type="submit" disabled={disabled||!text.trim()}>보내기</Button></div>
 </form>}>
  <div className="qa-messages"><p className="qa-muted">{storeName} · 점주가 확인한 매뉴얼</p>
   {chat.loading?<LoadingState message="대화를 불러오고 있어요."/>:chat.blocked?<p role="alert">이 대화를 사용할 수 없어요. 매장 접근 상태를 확인해 주세요.</p>:<>
    {chat.cursor!==null&&<Button intent="secondary" disabled={chat.busy||chat.pending} onClick={chat.older}>이전 질문 더 보기</Button>}
    {!chat.turns.length&&<section className="qa-answer"><h2>지단 AI</h2><p>궁금한 업무를 물어보세요.<br/>점주님이 확인한 매뉴얼을 바탕으로 답해 드려요.</p></section>}
    {chat.turns.map(turn=><article className="qa-turn" key={turn.id}>
     <div className="qa-user"><p>{turn.text}</p>{turn.imageMediaIds.length>0&&<span>첨부 사진 {turn.imageMediaIds.length}장</span>}</div>
     <section className="qa-answer" aria-label="AI 답변"><h2>지단 AI</h2>
      {turn.status==='RUNNING'?<LoadingState message="매뉴얼에서 답변을 찾고 있어요."/>:turn.status==='ERROR'?<><p>답변을 만들지 못했어요.</p><Button intent="secondary" disabled={disabled} onClick={()=>chat.retryQuestion(turn.id)}>답변 다시 시도</Button></>:<>
       <p className="qa-text">{turn.answer.text}</p>
       {turn.answer.outcome==='NEEDS_OWNER'?<p className="qa-muted">매뉴얼만으로 확인하기 어려워요. 점주님께 확인해 주세요.</p>:<div className="qa-citations"><h3>매뉴얼 근거</h3>{turn.answer.citations.map(c=><button type="button" key={`${c.sectionId}-${c.versionId}`} onClick={()=>setCitation(c)}>{c.sectionTitle}</button>)}</div>}
      </>}
     </section>
    </article>)}
    <p className="qa-muted">매뉴얼에 없는 내용은 점주님께 확인해 주세요.</p>
    {chat.pending&&!chat.busy&&<Button intent="secondary" onClick={chat.retry}>보낸 질문 확인 및 재시도</Button>}
   </>}
   <div ref={end}/>
  </div>
  <Modal open={!!chat.error} state="error" title="질문 상태를 확인해 주세요" description={chat.error} onClose={chat.dismiss} confirmLabel={chat.blocked?'매장으로 돌아가기':'다시 시도'} closeOnConfirm={false} onConfirm={chat.blocked?onBack:chat.retry} busy={chat.busy}/>
  {citation&&!chat.blocked&&<QaCitation citation={citation} storeId={service.storeId} onClose={()=>setCitation(null)} onLatest={onLatest}/>}
 </MobileLayout>
}
