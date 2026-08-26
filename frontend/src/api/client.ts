// FastAPI 호출만 담당한다. 화면 로직과 AI 공급자는 여기 들어오지 않는다.
// 브라우저는 외부 AI를 직접 부르지 않는다.

import type { ApiError, Bootstrap, RunView } from '../types'

export class ApiCallError extends Error {
  readonly detail: ApiError

  constructor(detail: ApiError) {
    super(detail.message)
    this.name = 'ApiCallError'
    this.detail = detail
  }
}

const FALLBACK_ERROR: ApiError = {
  error_code: 'NETWORK_ERROR',
  message: '서버에 연결하지 못했습니다.',
  next_action: '서버가 켜져 있는지 확인한 뒤 다시 시도해 주세요.',
  run_id: null,
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    throw new ApiCallError(FALLBACK_ERROR)
  }

  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    body = null
  }

  if (!response.ok) {
    const detail = body as Partial<ApiError> | null
    throw new ApiCallError({
      error_code: detail?.error_code ?? `HTTP_${response.status}`,
      message: detail?.message ?? '요청을 처리하지 못했습니다.',
      next_action: detail?.next_action ?? '잠시 뒤 다시 시도해 주세요.',
      run_id: detail?.run_id ?? null,
    })
  }
  return body as T
}

export interface CreateRunPayload {
  client_request_id: string
  purpose: string
  disclosure: string
  basis_date: string
  sources: {
    display_name: string
    text: string
    role: string
  }[]
  announcement_subject: string | null
  external_ai_policy_version: string
  external_ai_transfer_confirmed: boolean
  /** 발의안을 최종 의결 내용으로 대신 쓸 때만 묻는다. 첫 화면의 상시 입력이 아니다. */
  final_text_completeness_confirmations?: { source_id: string; confirmed: boolean }[]
}

export const api = {
  bootstrap: () => request<Bootstrap>('/api/bootstrap'),
  createRun: (payload: CreateRunPayload) =>
    request<RunView>('/api/runs', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getRun: (runId: string) => request<RunView>(`/api/runs/${runId}`),
  deleteRun: (runId: string) =>
    request<{ run_id: string; deleted: boolean }>(`/api/runs/${runId}`, {
      method: 'DELETE',
    }),
  /** 사람이 확인한 결과를 보낸다. 확인이 곧 보호다. */
  reviewFacts: (runId: string, reviews: { fact_id: string; verdict: string }[]) =>
    request<RunView>(`/api/runs/${runId}/fact-review`, {
      method: 'POST',
      body: JSON.stringify({ reviews }),
    }),
  /** 고쳐 달라고 부탁한다. 실패해도 이전 초안은 그대로 남는다. */
  reviseDraft: (runId: string, clientRequestId: string, instruction: string) =>
    request<RunView>(`/api/runs/${runId}/revisions`, {
      method: 'POST',
      body: JSON.stringify({ client_request_id: clientRequestId, instruction }),
    }),
  /** 확인을 마친 초안을 완료로 옮긴다. */
  completeRun: (runId: string) =>
    request<RunView>(`/api/runs/${runId}/complete`, { method: 'POST' }),
  /** 내려받기 주소. 확인을 안 마쳤으면 서버가 거부한다. */
  draftDownloadUrl: (runId: string) => `/api/runs/${runId}/draft.md`,
}
