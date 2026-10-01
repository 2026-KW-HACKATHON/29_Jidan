import { execFileSync } from 'node:child_process'
import { readdirSync, readFileSync } from 'node:fs'
import assert from 'node:assert/strict'
const build = enabled => {
 execFileSync('npm',['run','build'],{stdio:'inherit',env:{...process.env,VITE_ENABLE_PREVIEW:String(enabled)}})
 const files=readdirSync('dist/assets')
 const js=files.filter(file=>file.endsWith('.js')).map(file=>readFileSync(`dist/assets/${file}`,'utf8')).join('\n')
 assert.equal(files.some(file=>file.startsWith('PreviewApp-')),enabled,'Preview chunk boundary')
 for(const marker of ['owner-preview','preview-worker-receipt','MOCK_FAILURE','/__store/employment','/__jobs','preview-application-','MOCK_SUBMIT_FAILURE','preview-invitation-minji','MOCK_INVITATION_FAILURE','/__store/manage'])assert.equal(js.includes(marker),enabled,`Fixture boundary: ${marker}`)
}
build(true)
build(false)
console.log('Dev previews included; production preview chunks and fixtures excluded.')
