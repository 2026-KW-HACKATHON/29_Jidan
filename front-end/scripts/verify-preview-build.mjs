import { execFileSync } from 'node:child_process'
import { readdirSync, readFileSync } from 'node:fs'
import assert from 'node:assert/strict'
const build = enabled => {
 execFileSync('npm',['run','build'],{stdio:'inherit',env:{...process.env,VITE_ENABLE_PREVIEW:String(enabled)}})
 const files=readdirSync('dist/assets')
 const js=files.filter(file=>file.endsWith('.js')).map(file=>readFileSync(`dist/assets/${file}`,'utf8')).join('\n')
 assert.equal(files.some(file=>file.startsWith('PreviewApp-')),enabled,'Preview chunk boundary')
 assert.equal(files.some(file=>file.startsWith('OwnerApplicantPreview-')),enabled,'Owner applicant preview boundary')
 assert.equal(files.some(file=>file.startsWith('UserApplyStatusPreview-')),enabled,'User application preview boundary')
 assert.equal(files.some(file=>file.startsWith('UserNotificationPreview-')),enabled,'User notification preview boundary')
 assert.equal(files.some(file=>file.startsWith('OwnerNotificationPreview-')),enabled,'Owner notification preview boundary')
 for(const marker of ['owner-preview','preview-worker-receipt','MOCK_FAILURE','/__store/employment','/__jobs','preview-application-','MOCK_SUBMIT_FAILURE','preview-invitation-minji','MOCK_INVITATION_FAILURE','/__store/manage','/__owner/jobs','MOCK_OWNER_JOB_FAILURE','오전 매장 정리','주말 오픈 대타 공고에 지원자가 신청했어요.','명랑핫도그 광운대점의 업무 안내가 업데이트됐어요.'])assert.equal(js.includes(marker),enabled,`Fixture boundary: ${marker}`)
}
build(true)
build(false)
console.log('Dev previews included; production preview chunks and fixtures excluded.')
