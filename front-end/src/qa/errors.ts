import {ApiError} from '../api/client'
import {ManualError,errorMessage as recordingError} from '../manual/service'
export const accessLost=(error:unknown)=>error instanceof ApiError&&(error.status===401||error.status===403||error.status===404&&error.code==='RESOURCE_NOT_FOUND')
export function qaError(error:unknown):string {
 if(error instanceof ManualError)return recordingError(error)
 const code=error instanceof ApiError?error.code:''
 const messages:Record<string,string>={
  MANUAL_NOT_PUBLISHED:'아직 게시된 매뉴얼이 없어요. 점주님께 확인해 주세요.',
  RESOURCE_NOT_FOUND:'매장 접근이 종료되었거나 대화를 찾을 수 없어요.',
  STORE_APPROVAL_REQUIRED:'매장 승인 상태를 확인해 주세요.',
  FORBIDDEN:'이 매장의 AI 질문을 사용할 권한이 없어요.',
  QA_BUSY:'이 대화에서 답변을 만들고 있어요. 대화를 다시 불러와 주세요.',
  QA_NOT_RETRYABLE:'이미 처리 중이거나 완료된 질문이에요. 대화를 다시 불러와 주세요.',
  QA_INPUT_EXPIRED:'질문에 첨부한 자료가 만료됐어요. 새 질문에 다시 첨부해 주세요.',
  QA_PHOTO_INVALID:'JPG·PNG·WebP 사진을 10 MiB 이하로 첨부해 주세요.',
  QA_PHOTO_LIMIT:'사진은 질문 하나에 최대 3장까지 첨부할 수 있어요.',
  MANUAL_VERSION_CHANGED:'답변 이후 매뉴얼이 바뀌었어요. 최신 매뉴얼을 확인해 주세요.',
  TRANSCRIPTION_FAILED:'음성을 알아듣지 못했어요. 다시 시도하거나 직접 입력해 주세요.',
  INVALID_RESPONSE:'응답을 확인하지 못했어요. 다시 불러와 주세요.',
 }
 return messages[code]??(error instanceof ApiError?error.message:'처리하지 못했어요. 연결을 확인하고 다시 시도해 주세요.')
}
