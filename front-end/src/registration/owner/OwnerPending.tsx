import { Brand } from '../../brand/Brand'
import { useState } from 'react'
import { MobileLayout } from '../../ui/MobileLayout'
import { Button } from '../../ui/Button'
import { Modal } from '../../ui/Modal'
import type { OwnerReceipt } from './service'
import bell from './assets/bell.svg'
import home from './assets/home.svg'
import book from './assets/book.svg'
import brief from './assets/brief.svg'
import './OwnerStep.css'
import './OwnerPending.css'

export function OwnerPending({ receipt }: { receipt: OwnerReceipt }) {
  const [message, setMessage] = useState<'approval' | 'notifications' | 'add' | null>(null)
  return <MobileLayout className="owner-pending" header={<header className="owner-home-bar"><h1><Brand /></h1><button aria-label="알림" onClick={() => setMessage('notifications')}><img src={bell} alt="" /></button></header>}
    footer={<nav className="owner-bottom-nav" aria-label="주 메뉴"><div>{[
      ['홈', home], ['매뉴얼', book], ['공고 관리', brief],
    ].map(([label, icon], index) => <button key={label} aria-current={index === 0 ? 'page' : undefined} onClick={() => { if (index) setMessage('approval') }}><img src={icon} alt="" /><span>{label}</span></button>)}</div><span className="owner-home-indicator" aria-hidden="true" /></nav>}>
    <div className="owner-pending-content">
      <div className="owner-intro"><h2>안녕하세요, {receipt.ownerName} 점주님</h2><p>매장 등록 신청을 확인하고 있어요.</p></div>
      <section className="owner-managed" aria-label="관리 매장"><p>관리 매장</p><div className="owner-managed-title"><h3>{receipt.storeName}</h3><span>승인 대기 중</span></div><div className="owner-managed-actions"><Button intent="secondary" onClick={() => setMessage('approval')}>매장 관리</Button><Button intent="secondary" onClick={() => setMessage('approval')}>공고 등록</Button></div></section>
      <button className="owner-add-store" onClick={() => setMessage('add')}>+ 매장 추가</button>
      <p className="owner-notice">운영자가 매장 소재지와 관리 권한을 확인 중이에요. 승인되면 매장 관리·매뉴얼·공고 기능을 이용할 수 있어요.</p>
      <div className="owner-jobs-heading"><h3>모집 중 공고</h3><span>0건</span></div>
      <section className="owner-empty-jobs"><h4>승인 후 첫 공고를 등록해 보세요</h4><p>지금은 운영 승인 대기 중이에요.</p></section>
    </div>
    <Modal open={message !== null} title={message === 'approval' ? '매장 승인 후 이용할 수 있어요' : message === 'add' ? '매장 추가 화면을 준비하고 있어요' : '알림 화면을 준비하고 있어요'}
      description={message === 'approval' ? '현재 매장 등록을 확인하고 있어요.\n승인이 완료되면 매장 관리, 매뉴얼,\n공고 기능을 이용할 수 있어요.' : '매장 등록 신청은 접수된 상태로 유지돼요.'} onClose={() => setMessage(null)} />
  </MobileLayout>
}
