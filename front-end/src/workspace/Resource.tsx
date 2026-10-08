import {LoadingState} from '../ui/LoadingState'
import {useEffect,useState,type ReactNode} from 'react'
import {MobileLayout} from '../ui/MobileLayout'
import {AppBar} from '../ui/AppBar'
import {Button} from '../ui/Button'
/** A route owns its request: leaving it aborts loading and prevents stale rendering. */
export function Resource<T>({load,children,onBack}:{load:(signal:AbortSignal)=>Promise<T>;children:(value:T)=>ReactNode;onBack:()=>void}){
 const [state,setState]=useState<{value:T;source:typeof load}|{error:true;source:typeof load}|null>(null),[attempt,setAttempt]=useState(0)
 useEffect(()=>{const c=new AbortController();load(c.signal).then(value=>{if(!c.signal.aborted)setState({value,source:load})},()=>{if(!c.signal.aborted)setState({error:true,source:load})});return()=>c.abort()},[load,attempt])
 const current=state?.source===load?state:null
 if(current&&'value' in current)return children(current.value)
 return <MobileLayout header={<AppBar title="지단" onBack={onBack}/>}>{current?<p role="alert">정보를 불러오지 못했어요. 접근 권한이나 연결 상태를 확인해 주세요.</p>:<LoadingState cards/>}{current&&<Button onClick={()=>{setState(null);setAttempt(v=>v+1)}}>다시 시도</Button>}</MobileLayout>
}
