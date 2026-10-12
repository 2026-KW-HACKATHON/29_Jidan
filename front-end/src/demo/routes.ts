export type DemoRole = 'OWNER' | 'WORKER'
export type DemoRoute = {path:'/author'|'/manual'|'/questions';id:string;url:string}
const hosts:Record<string,DemoRole> = {
  'jidan-ilban-demo.leehyowon14.dev':'WORKER',
  'jidan-jumju-demo.leehyowon14.dev':'OWNER',
}
export function demoRole(host=window.location.hostname):DemoRole|null {
  if(Object.hasOwn(hosts,host))return hosts[host]
  const local=import.meta.env.DEV?import.meta.env.VITE_DEMO_ROLE:undefined
  return local==='OWNER'||local==='WORKER'?local:null
}
export const demoStart=(role:DemoRole)=>role==='OWNER'?'/author':'/manual'
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
export const isDemoId=(value:unknown):value is string=>typeof value==='string'&&uuid.test(value)
export function demoRoute(input:string,role:DemoRole):DemoRoute {
  const url=new URL(input,window.location.origin)
  const allowed=role==='OWNER'?['/author','/manual']:['/manual','/questions']
  const path=(url.origin===window.location.origin&&allowed.includes(url.pathname)?url.pathname:demoStart(role)) as DemoRoute['path']
  const candidate=url.searchParams.get('id')||''
  const id=path==='/author'?'':isDemoId(candidate)?candidate:''
  return {path,id,url:path+(id?`?id=${id}`:'')}
}
