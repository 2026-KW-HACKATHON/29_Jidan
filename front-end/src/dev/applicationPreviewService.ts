import type { Application,ApplicationService } from '../application/model'
export function createApplicationPreviewService(initial:readonly Application[]=[],fail=false):ApplicationService {
  const applications=new Map(initial.map(value=>[value.job.id,value]))
  let submitFailed=false,withdrawFailed=false,sequence=initial.length
  return {
    async submit(job,introduction,signal){
      signal.throwIfAborted()
      await Promise.resolve();signal.throwIfAborted()
      if(fail && !submitFailed){submitFailed=true;throw Error('MOCK_SUBMIT_FAILURE')}
      const existing=applications.get(job.id);if(existing)return existing
      const result={id:`preview-application-${++sequence}`,job:{...job,tasks:[...job.tasks]},introduction}
      applications.set(job.id,result);return result
    },
    async withdraw(id,signal){
      signal.throwIfAborted()
      await Promise.resolve();signal.throwIfAborted()
      if(fail && !withdrawFailed){withdrawFailed=true;throw Error('MOCK_WITHDRAW_FAILURE')}
      const entry=[...applications.entries()].find(([,value])=>value.id===id)
      if(!entry)throw Error('MOCK_APPLICATION_NOT_FOUND')
      applications.delete(entry[0])
    },
  }
}
