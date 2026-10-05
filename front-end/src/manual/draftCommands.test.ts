import {expect,it,vi} from 'vitest'
import {createDraftVoiceCorrection,publicationInput} from './draftCommands'
import {draftFixture} from '../dev/manualDraftFixtures'
import {ManualError,type ManualService} from './service'
const recording={blob:new Blob(['voice'],{type:'audio/webm'}),duration:3}
it('음성 정정은 녹음 시작의 version·revision·대상을 보존하고 접수 응답 유실에도 같은 요청을 재현한다',async()=>{
 const draft=structuredClone(draftFixture),mediaId=crypto.randomUUID(),transcriptionId=crypto.randomUUID(),target={kind:'SHIFT' as const,targetId:draft.content!.shifts[0].id}
 const job={id:crypto.randomUUID(),versionId:draft.versionId,baseRevision:draft.revision,target,status:'RUNNING'}
 const call=vi.fn().mockResolvedValueOnce({data:{id:mediaId}}).mockResolvedValueOnce({data:{id:transcriptionId}}).mockResolvedValueOnce({data:{id:transcriptionId,mediaId,status:'READY',text:'확인 화면 없음'}}).mockRejectedValueOnce(Error('lost')).mockResolvedValueOnce({data:job})
 const correct=createDraftVoiceCorrection({storeId:draft.storeId,call} as ManualService,draft,target,recording),signal=new AbortController().signal
 await expect(correct(signal)).rejects.toThrow('lost');draft.revision++;await correct(signal)
 expect(call.mock.calls[3][2]).toEqual({expectedVersionId:draft.versionId,expectedRevision:draft.revision-1,target,input:{method:'VOICE',transcriptionId}})
 expect(call.mock.calls[3][2]).toEqual(call.mock.calls[4][2]);expect(call.mock.calls[3][3].key).toBe(call.mock.calls[4][3].key)
})
it('삭제된 대상·처리 중 정정·미확인 부족 항목은 정정 또는 게시를 차단한다',()=>{
 const service={} as ManualService
 expect(()=>createDraftVoiceCorrection(service,draftFixture,{kind:'SECTION',targetId:crypto.randomUUID()},recording)).toThrow('MANUAL_REFERENCE_CONFLICT')
 const running={...draftFixture,latestCorrection:{id:crypto.randomUUID(),versionId:draftFixture.versionId,baseRevision:draftFixture.revision,target:{kind:'MANUAL' as const,targetId:null},status:'RUNNING' as const,attempt:1,resultRevision:null,error:null,createdAt:new Date().toISOString(),completedAt:null}}
 expect(()=>publicationInput(running)).toThrow('MANUAL_CORRECTION_IN_PROGRESS')
 const issue={id:crypto.randomUUID(),intentId:null,description:'시간 확인 필요',status:'OPEN' as const,ownerNote:null,acknowledgedAt:null}
 expect(()=>publicationInput({...draftFixture,issues:[issue]})).toThrow('MANUAL_ISSUES_NOT_ACKNOWLEDGED')
 expect(publicationInput({...draftFixture,issues:[{...issue,status:'ACKNOWLEDGED',acknowledgedAt:new Date().toISOString()}]})).toMatchObject({acknowledgedIssueIds:[issue.id],confirmed:true,expectedRevision:draftFixture.revision})
 expect(new ManualError('REVISION_CONFLICT').code).toBe('REVISION_CONFLICT')
})
