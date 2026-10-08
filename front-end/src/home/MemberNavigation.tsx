import './Home.css'
const items = [{key:'home',label:'홈'},{key:'manual',label:'매뉴얼'},{key:'jobs',label:'공고 찾기'},{key:'profile',label:'프로필'}] as const
export function MemberNavigation({active,onHome,onManual,onJobs,onProfile}: {active: typeof items[number]['key'];onHome:()=>void;onManual:()=>void;onJobs:()=>void;onProfile:()=>void}) {
  const actions = {home:onHome,manual:onManual,jobs:onJobs,profile:onProfile}
  return <nav className="home-bottom-nav" aria-label="주 메뉴"><div className="home-nav-items">{items.map(({key,label})=><button type="button" key={key} aria-current={key===active?'page':undefined} onClick={actions[key]}><span aria-hidden="true" className={`member-nav-icon member-nav-${key}`}/><span>{label}</span></button>)}</div></nav>
}
