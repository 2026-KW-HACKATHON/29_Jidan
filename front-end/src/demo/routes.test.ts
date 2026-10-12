import {describe,expect,it,vi,afterEach} from 'vitest'
import {demoRole,demoRoute,demoStart} from './routes'
afterEach(()=>vi.unstubAllEnvs())
describe('demo domain and routes',()=>{
 it('fixes each domain role and rejects hostname suffix tricks',()=>{
  expect(demoRole('jidan-ilban-demo.leehyowon14.dev')).toBe('WORKER')
  expect(demoRole('jidan-jumju-demo.leehyowon14.dev')).toBe('OWNER')
  for(const host of ['jidan.leehyowon14.dev','jidan-ilban-demo.leehyowon14.dev.evil.test','constructor'])expect(demoRole(host)).toBeNull()
 })
 it('supports only an explicit local demo role',()=>{
  vi.stubEnv('VITE_DEMO_ROLE','OWNER');expect(demoRole('localhost')).toBe('OWNER')
  vi.stubEnv('DEV',false);expect(demoRole('localhost')).toBeNull()
 })
 it.each(['OWNER','WORKER'] as const)('replaces home, auth and unrelated routes for %s',role=>{
  for(const path of ['/','/home?view=jobs','/login','/signup/owner','/status','/__preview','/worker/profile','https://evil.test/manual'])expect(demoRoute(path,role).url).toBe(demoStart(role))
 })
 it('permits only role routes and validated IDs',()=>{
  const id='00000000-0000-0000-0000-000000000001'
  expect(demoRoute(`/manual?id=${id}&store=other&view=jobs`,'WORKER').url).toBe(`/manual?id=${id}`)
  expect(demoRoute(`/questions?id=${id}`,'OWNER').url).toBe('/author')
  expect(demoRoute('/author','WORKER').url).toBe('/manual')
  expect(demoRoute('/questions?id=bad','WORKER').url).toBe('/questions')
  expect(demoRoute(`/author?id=${id}`,'OWNER').url).toBe('/author')
 })
})
