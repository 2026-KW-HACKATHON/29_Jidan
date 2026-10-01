import { Button } from '../ui/Button'
import { MobileLayout } from '../ui/MobileLayout'
import check from './assets/check-onboarding.svg'
import brand from './assets/store-brand.svg'
import './LoginScreen.css'

export function LoginScreen({ onStart, busy = false }: { onStart: () => void; busy?: boolean }) {
  return <MobileLayout className="auth-login">
    <div className="auth-login-content">
      <div className="auth-brand"><span className="auth-brand-symbol"><img src={brand} alt="" /></span><span>지단</span></div>
      <div className="auth-introduction">
        <h1>매장의 시작을,<br />더 단단하게.</h1>
        <p>첫 출근부터 매장 운영까지<br />함께하는 AI 온보딩</p>
      </div>
      <section className="auth-onboarding" aria-labelledby="onboarding-title">
        <span className="auth-onboarding-badge">AI 온보딩</span>
        <h2 id="onboarding-title">오늘도 준비된 우리 매장</h2>
        {['업무 매뉴얼, 한곳에서 확인', '궁금한 업무는 AI에게 질문'].map(text => <div className="auth-onboarding-row" key={text}><img src={check} alt="" /><p>{text}</p></div>)}
      </section>
      <div className="auth-sign-in">
        <Button onClick={onStart} busy={busy} aria-describedby="sign-in-description">Google 계정으로 시작하기</Button>
        <p id="sign-in-description">기존 계정으로 로그인하거나 새로 가입할 수 있어요.</p>
      </div>
      <p className="auth-login-footer">매장 AI 온보딩 서비스 · 지단</p>
    </div>
  </MobileLayout>
}
