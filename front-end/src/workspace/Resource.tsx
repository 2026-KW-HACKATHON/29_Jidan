import {useEffect,useState,type ReactNode} from 'react'
import {MobileLayout} from '../ui/MobileLayout'
import {AppBar} from '../ui/AppBar'
import {Button} from '../ui/Button'
/** A route owns its request: leaving it aborts loading and prevents stale rendering. */
export function Resource<T>({load,children,onBack}:{load:(signal:AbortSignal)=>Promise<T>;children:(value:T)=>ReactNode;onBack:()=>void}){
 const [state,setState]=useState<{value:T}|{error:true}|null>(null),[attempt,setAttempt]=useState(0)
 useEffect(()=>{const c=new AbortController();load(c.signal).then(value=>{if(!c.signal.aborted)setState({value})},()=>{if(!c.signal.aborted)setState({error:true})});return()=>c.abort()},[load,attempt])
 if(state&&'value' in state)return children(state.value)
 return <MobileLayout header={<AppBar title="지단" onBack={onBack}/>}><p role={state?'alert':'status'}>{state?'정보를 불러오지 못했어요. 접근 권한이나 연결 상태를 확인해 주세요.':'정보를 불러오고 있어요.'}</p>{state&&<Button onClick={()=>{setState(null);setAttempt(v=>v+1)}}>다시 시도</Button>}</MobileLayout>
}
