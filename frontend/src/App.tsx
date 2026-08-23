// 화면 전환 (README §2.8, §3.8).
// React는 화면과 입력만 담당한다. AI 호출·사실 검사·DRAFT 강제는 모두 FastAPI 뒤에 있다.

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiCallError, api } from './api/client'
import { NewRunScreen } from './screens/NewRunScreen'
import { RunStatusScreen } from './screens/RunStatusScreen'
import type { Bootstrap, RunView, SourceDraft } from './types'

const POLL_INTERVAL_MS = 1500

/** 첫 화면이 보내는 입력 한 벌. */
export interface SubmitInput {
  purpose: string
  disclosure: string
  basisDate: string
  announcementSubject: string
  sources: SourceDraft[]
  transferConfirmed: boolean
}

/** 최종 의결문이 완전한지에 대한 사람의 답. */
export interface FinalTextAnswer {
  source_id: string
  confirmed: boolean
}

const BUSY_STATES = new Set([
  'CREATED',
  'VALIDATING_INPUT',
  'EXTRACTING_FACTS',
  'DRAFTING',
  'CHECKING_DRAFT',
  'REVISING',
  'CHECKING_REVISION',
])

export function App() {
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null)
  const [bootstrapError, setBootstrapError] = useState<string | null>(null)
  const [run, setRun] = useState<RunView | null>(null)
  const [submitting, setSubmitting] = useState(false)
  // 마지막으로 보낸 입력. 최종 의결문 확인 질문에 답할 때 그대로 다시 쓴다.
  const [lastInput, setLastInput] = useState<SubmitInput | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const requestIdRef = useRef(0)

  useEffect(() => {
    api
      .bootstrap()
      .then(setBootstrap)
      .catch((error: unknown) => {
        const detail = error instanceof ApiCallError ? error.detail.message : '서버에 연결하지 못했습니다.'
        setBootstrapError(detail)
      })
  }, [])

  // 상태 조회. 이 조회는 서버의 2시간 만료 시간을 늘리지 않는다.
  useEffect(() => {
    if (run === null || !BUSY_STATES.has(run.state)) return
    const runId = run.run_id
    const timer = window.setInterval(() => {
      api
        .getRun(runId)
        .then(setRun)
        .catch((error: unknown) => {
          if (error instanceof ApiCallError) setErrorMessage(error.detail.message)
        })
    }, POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [run])

  const handleSubmit = useCallback(
    async (input: SubmitInput, confirmations?: FinalTextAnswer[]) => {
      if (bootstrap === null) return
      setLastInput(input)
      setSubmitting(true)
      setErrorMessage(null)
      requestIdRef.current += 1
      try {
        const created = await api.createRun({
          // 멱등 키. 같은 키로 두 번 눌러도 작업은 한 번만 만들어진다.
          client_request_id: `req-${requestIdRef.current}-${Date.now()}`,
          purpose: input.purpose,
          disclosure: input.disclosure,
          basis_date: input.basisDate,
          sources: input.sources
            .filter((s) => s.text.trim().length > 0)
            .map((s) => ({ display_name: s.display_name, text: s.text, role: s.role })),
          announcement_subject: input.announcementSubject.trim() || null,
          external_ai_policy_version: bootstrap.external_ai.policy_version,
          external_ai_transfer_confirmed: input.transferConfirmed,
          ...(confirmations ? { final_text_completeness_confirmations: confirmations } : {}),
        })
        setRun(created)
      } catch (error: unknown) {
        setErrorMessage(
          error instanceof ApiCallError
            ? `${error.detail.message} ${error.detail.next_action}`
            : '요청을 처리하지 못했습니다.',
        )
      } finally {
        setSubmitting(false)
      }
    },
    [bootstrap],
  )

  // 최종 의결문 확인 질문에 "예"로 답한다. 같은 입력에 확인만 붙여 다시 만든다.
  // 상태를 되돌리는 것이 아니라 새 작업이므로, 무엇을 확인했는지 기록이 남는다.
  const handleConfirmFinalText = useCallback(
    async (sourceIds: string[]) => {
      if (lastInput === null) return
      // 서버는 한 번에 한 건만 처리한다. 그래서 **먼저 지우고** 새로 만든다.
      // 넣은 내용은 `lastInput`에 그대로 있으므로 새로 만들지 못해도 잃지 않는다.
      if (run !== null) {
        try {
          await api.deleteRun(run.run_id)
        } catch {
          // 이미 지워졌거나 만료됐으면 그대로 진행한다.
        }
      }
      await handleSubmit(
        lastInput,
        sourceIds.map((source_id) => ({ source_id, confirmed: true })),
      )
    },
    [handleSubmit, lastInput, run],
  )

  const handleNewRun = useCallback(async () => {
    if (run !== null) {
      try {
        await api.deleteRun(run.run_id)
      } catch {
        // 이미 삭제·만료된 경우는 그대로 새 작업으로 넘어간다.
      }
    }
    setRun(null)
    setErrorMessage(null)
  }, [run])

  const handleDelete = useCallback(async () => {
    if (run === null) return
    try {
      await api.deleteRun(run.run_id)
      setRun(null)
    } catch (error: unknown) {
      if (error instanceof ApiCallError) setErrorMessage(error.detail.message)
    }
  }, [run])

  return (
    <div className="app">
      <header className="app-header">
        <p className="draft-mark">DRAFT / 내부 검토용</p>
        <h1>{bootstrap?.app_title ?? '국회 법률 개정·개선 보도자료 초안 작성 Agent'}</h1>
        <p className="notice">
          {bootstrap?.notice ?? '공개·합성 자료로 내부 검토용 초안을 만드는 도구입니다.'}
        </p>
        {bootstrap?.model_gateway === 'fake' && (
          <p className="dev-badge">개발 모드: 가짜 AI를 사용합니다. 외부 호출 0회 · 비용 0달러</p>
        )}
      </header>

      <main>
        {bootstrapError !== null && (
          <p className="error" role="alert">
            {bootstrapError}
          </p>
        )}
        {bootstrap === null && bootstrapError === null && <p>불러오는 중…</p>}
        {bootstrap !== null && run === null && (
          <NewRunScreen
            bootstrap={bootstrap}
            submitting={submitting}
            errorMessage={errorMessage}
            onSubmit={handleSubmit}
          />
        )}
        {bootstrap !== null && run !== null && (
          <RunStatusScreen
            run={run}
            onConfirmFinalText={handleConfirmFinalText}
            onNewRun={handleNewRun}
            onDelete={handleDelete}
          />
        )}
      </main>

      <footer className="app-footer">
        <p>
          산출물은 언제나 DRAFT / 내부 검토용입니다. 승인·게시·배포 기능은 없습니다.
        </p>
      </footer>
    </div>
  )
}
