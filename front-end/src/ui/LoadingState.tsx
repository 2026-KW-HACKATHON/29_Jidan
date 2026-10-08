import './LoadingState.css'

/** A passive loading indicator; data and error transitions remain owned by the caller. */
export function LoadingState({message='정보를 불러오고 있어요.',cards=false}:{message?:string;cards?:boolean}) {
 return <div className={`ds-loading ${cards?'ds-loading-with-cards':''}`}>
  <div className="ds-loading-message" role="status">
   <span className="ds-loading-spinner" aria-hidden="true"/>
   <p>{message}</p>
  </div>
  {cards&&<div className="ds-loading-cards" aria-hidden="true">{[0,1,2].map(i=><div className="ds-loading-card" key={i}>
   <span className="ds-loading-bar ds-loading-bar-title"/>
   <span className="ds-loading-bar"/>
   <span className="ds-loading-bar ds-loading-bar-short"/>
  </div>)}</div>}
 </div>
}
