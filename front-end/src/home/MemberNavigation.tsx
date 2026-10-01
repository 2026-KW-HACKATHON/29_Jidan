import home from './assets/member-home.svg'
import book from './assets/member-book.svg'
import brief from './assets/member-brief.svg'
import user from './assets/user.svg'
import './Home.css'
const items = [{key:'home',label:'홈',icon:home},{key:'manual',label:'매뉴얼',icon:book},{key:'jobs',label:'공고 찾기',icon:brief},{key:'profile',label:'프로필',icon:user}] as const
export function MemberNavigation({active,onHome,onManual,onJobs,onProfile}: {active: typeof items[number]['key'];onHome:()=>void;onManual:()=>void;onJobs:()=>void;onProfile:()=>void}) {
  const actions = {home:onHome,manual:onManual,jobs:onJobs,profile:onProfile}
  return <nav className="home-bottom-nav" aria-label="주 메뉴"><div className="home-nav-items">{items.map(({key,label,icon})=><button type="button" key={key} aria-current={key===active?'page':undefined} onClick={actions[key]}><img src={icon} alt=""/><span>{label}</span></button>)}</div><span className="home-indicator" aria-hidden="true"/></nav>
}
