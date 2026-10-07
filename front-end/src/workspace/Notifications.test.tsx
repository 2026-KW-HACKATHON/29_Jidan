import {expect,it} from 'vitest'
import {notificationRoute} from './Notifications'
import type {Notification} from '../api/types.generated'
it('알림 이동은 역할별 내부 경로만 생성한다',()=>{const n={target:{type:'JOB_APPLICATION',storeId:'s',jobId:'j',applicationId:'a'}} as Notification;expect(notificationRoute(n,true)).toBe('/home?store=s&view=job&id=j');expect(notificationRoute(n,false)).toBe('/home?store=s&view=application&id=a');expect(notificationRoute({...n,target:{type:'STORE_INVITATION',invitationId:'https://other.test'}},false)).toBe('/home?view=invitation&id=https%3A%2F%2Fother.test')})
