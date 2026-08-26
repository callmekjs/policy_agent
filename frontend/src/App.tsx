// 화면 전환 (README §0.3, §2.8, §3.8).
// React는 화면과 입력만 담당한다. AI 호출·사실 검사·DRAFT 강제는 모두 FastAPI 뒤에 있다.
//
// 대화로 만든다. 전에는 양식이었다 — 자료 칸을 네 번 만들고, 역할을 13개
// 목록에서 고르고, 날짜를 고르고, 사실 10건을 하나씩 눌러야 했다.
// 스무 번 넘게 누르는 화면이었다.
//
// README §0.3이 원래 이렇게 하라고 적어 두었다.
//
// > 비전공자에게 12가지 자료 역할을 **먼저 고르라고 하지 않는다.**
//
// **안전은 하나도 낮추지 않는다.** 자료 없이 초안을 만들지 않고, 확인하지
// 않은 초안은 내려받을 수 없다. 바뀌는 것은 **묻는 방법**뿐이다.

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiCallError, api } from './api/client'
import {
  ASK_SOURCES,
  FIRST_PROMPT,
  askConsent,
  autoRoles,
  promptFor,
  type Prompt,
} from './chat/conversation'
import { splitSources } from './chat/split'
import { ChatScreen, type Turn } from './screens/ChatScreen'
import { DraftPanel } from './screens/DraftPanel'
import type { Bootstrap, RunView } from './types'

const POLL_INTERVAL_MS = 1500

const BUSY_STATES = new Set([
  'CREATED',
  'VALIDATING_INPUT',
  'EXTRACTING_FACTS',
  'DRAFTING',
  'CHECKING_DRAFT',
  'REVISING',
  'CHECKING_REVISION',
])

/** "다 넣었어요"처럼 자료가 아니라 **끝났다는 신호**인지 본다.
 *
 * 정확한 문장을 외우게 하지 않는다. 짧고 끝났다는 뜻이면 받아들인다.
 * 자료는 보통 길기 때문에 길이로도 갈린다.
 */
function looksLikeDone(text: string): boolean {
  if (text.length > 40) return false
  return /(다\s*넣었|다\s*됐|끝|완료|없어요|없습니다|시작|그만|이제)/.test(text)
}

let turnSeq = 0
function turn(who: 'agent' | 'user', text: string, choices?: Turn['choices']): Turn {
  turnSeq += 1
  return { id: `t${turnSeq}`, who, text, choices }
}

export function App() {
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null)
  const [bootstrapError, setBootstrapError] = useState<string | null>(null)
  const [run, setRun] = useState<RunView | null>(null)
  const [turns, setTurns] = useState<Turn[]>([turn('agent', FIRST_PROMPT.say, FIRST_PROMPT.choices)])
  const [prompt, setPrompt] = useState<Prompt>(FIRST_PROMPT)
  const [purpose, setPurpose] = useState('')
  const [sources, setSources] = useState<string[]>([])
  // 사람이 고른 자료 역할. 서버는 한 번에 한 건만 처리하므로, 고를 때마다
  // 지금까지 고른 것을 모두 붙여 다시 만든다.
  const [roles, setRoles] = useState<Record<string, string>>({})
  const [working, setWorking] = useState(false)
  const requestIdRef = useRef(0)
  // 서버가 물어본 것에 이미 답했는지. 같은 질문을 두 번 띄우지 않는다.
  const answeredRef = useRef<string>('')
  // 서버에 남아 있어 새 작업을 막는 이전 작업 번호. 지우고 다시 시작한다.
  const stuckRef = useRef<string | null>(null)

  useEffect(() => {
    api
      .bootstrap()
      .then(setBootstrap)
      .catch((error: unknown) => {
        const detail =
          error instanceof ApiCallError ? error.detail.message : '서버에 연결하지 못했습니다.'
        setBootstrapError(detail)
      })
  }, [])

  const say = useCallback((next: Prompt) => {
    setPrompt(next)
    if (next.say.length > 0) setTurns((old) => [...old, turn('agent', next.say, next.choices)])
  }, [])

  // 처리 중이면 상태를 따라간다. 이 조회는 서버의 2시간 만료를 늘리지 않는다.
  useEffect(() => {
    if (run === null || !BUSY_STATES.has(run.state)) return
    const runId = run.run_id
    const timer = window.setInterval(() => {
      api.getRun(runId).then(setRun).catch(() => undefined)
    }, POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [run])

  // 서버 상태가 바뀌면 도우미가 할 말도 바뀐다.
  useEffect(() => {
    if (run === null || BUSY_STATES.has(run.state)) return
    const next = promptFor(run)
    const key = `${run.state}:${run.draft_version}:${next.stage}`
    if (answeredRef.current === key) return
    answeredRef.current = key
    say(next)
  }, [run, say])



  const createRun = useCallback(
    async (
      texts: string[],
      confirmations?: { source_id: string; confirmed: boolean }[],
      roles?: Record<string, string>,
    ) => {
      if (bootstrap === null) return
      setWorking(true)
      requestIdRef.current += 1
      try {
        const created = await api.createRun({
          client_request_id: `req-${requestIdRef.current}-${Date.now()}`,
          purpose,
          // 이 버전은 공개 자료만 다룬다. 그래서 고르라고 하지 않는다.
          disclosure: 'PUBLIC',
          basis_date: new Date().toISOString().slice(0, 10),
          // **역할을 고르라고 하지 않는다.** AI가 근거와 함께 제안한다 (§0.3).
          sources: texts.map((text, i) => ({
            display_name: `자료 ${i + 1}`,
            text,
            // 사람이 고른 역할이 있으면 그것을 쓴다. 없으면 `잘 모르겠음`으로
            // 보내고 AI가 근거와 함께 제안하게 한다 (§0.3).
            role: roles?.[`SRC-${String(i + 1).padStart(2, '0')}`] ?? 'UNKNOWN',
            // 서버는 넣은 순서대로 SRC-01, SRC-02… 로 이름을 붙인다.
          })),
          announcement_subject: null,
          external_ai_policy_version: bootstrap.external_ai.policy_version,
          external_ai_transfer_confirmed: true,
          ...(confirmations ? { final_text_completeness_confirmations: confirmations } : {}),
        })
        answeredRef.current = ''
        setRun(created)
      } catch (error: unknown) {
        // **막다른 길을 만들지 않는다.** 오류만 보여 주고 버튼이 없으면
        // 사람은 화면을 새로 고치는 수밖에 없고, 넣은 자료를 다 잃는다.
        // 무엇이 잘못됐는지와 **다음에 누를 것**을 함께 준다.
        const detail =
          error instanceof ApiCallError
            ? `${error.detail.message} ${error.detail.next_action}`
            : '요청을 처리하지 못했습니다.'
        const stuckRunId = error instanceof ApiCallError ? error.detail.run_id : null
        const retry: Prompt = {
          stage: 'ASK_CONSENT',
          say: detail,
          placeholder: '',
          canType: false,
          choices: [
            { label: '이전 작업을 지우고 다시 하기', value: 'CLEAR_AND_RETRY' },
            { label: '처음부터 다시 하기', value: 'RESTART' },
          ],
        }
        stuckRef.current = stuckRunId
        say(retry)
      } finally {
        setWorking(false)
      }
    },
    [bootstrap, purpose],
  )

  // AI가 자료 역할을 하나로 짚었으면 **묻지 않고 그대로 쓴다.**
  // 답이 하나뿐인 물음은 확인이 아니라 일거리다.
  useEffect(() => {
    if (run === null || run.state !== 'NEEDS_INPUT') return
    const auto = autoRoles(run)
    const found = Object.keys(auto)
    if (found.length === 0) return
    if (found.every((id) => roles[id] === auto[id])) return
    const merged = { ...roles, ...auto }
    setRoles(merged)
    const named = (run.sources ?? [])
      .filter((s) => auto[s.source_id] !== undefined)
      .map((s) => `· ${s.display_name} → ${s.role_label ?? auto[s.source_id]}`)
    void (async () => {
      setTurns((old) => [
        ...old,
        turn(
          'agent',
          ['자료가 무엇인지 알아냈습니다. 틀린 것이 있으면 말씀해 주세요.', '', ...named].join('\n'),
        ),
      ])
      try {
        await api.deleteRun(run.run_id)
      } catch {
        // 이미 지워졌으면 그대로 진행한다.
      }
      await createRun(sources, undefined, merged)
    })()
  }, [createRun, roles, run, sources])

  const handleSend = useCallback(
    async (text: string) => {
      setTurns((old) => [...old, turn('user', text)])

      if (prompt.stage === 'ASK_SOURCES') {
        if (looksLikeDone(text)) {
          if (sources.length === 0) {
            say({
              ...ASK_SOURCES,
              say: '아직 자료를 하나도 못 받았습니다. 자료가 없으면 초안을 만들 수 없습니다.',
            })
            return
          }
          say(askConsent(sources.length))
          return
        }

        // 첫 줄이 짧으면 **무엇을 알리는지**로 본다. 자료는 보통 길고
        // 머리글이나 여러 줄로 되어 있다.
        const lines = text.split('\n')
        let body = text
        if (purpose.length === 0 && lines.length > 1 && lines[0].trim().length <= 80) {
          setPurpose(lines[0].trim())
          body = lines.slice(1).join('\n')
        } else if (purpose.length === 0 && lines.length === 1) {
          setPurpose(text)
          say(ASK_SOURCES)
          return
        }

        // 한 덩이로 붙여도 알아서 나눈다. 사람이 네 번 보내지 않아도 된다.
        const pieces = splitSources(body)
        if (pieces.length === 0) {
          say(ASK_SOURCES)
          return
        }
        const next = [...sources, ...pieces]
        setSources(next)
        say(askConsent(next.length))
        return
      }

      if (prompt.stage === 'REVIEW' && run !== null) {
        setWorking(true)
        try {
          const next = await api.reviseDraft(run.run_id, `rev-${Date.now()}`, text)
          answeredRef.current = ''
          setRun(next)
        } catch (error: unknown) {
          const detail =
            error instanceof ApiCallError ? error.detail.message : '고치지 못했습니다.'
          setTurns((old) => [...old, turn('agent', detail)])
        } finally {
          setWorking(false)
        }
      }
    },
    [prompt.stage, run, say, sources],
  )

  const handleChoose = useCallback(
    async (value: string) => {
      const chosen = prompt.choices?.find((c) => c.value === value)
      if (chosen !== undefined) setTurns((old) => [...old, turn('user', chosen.label)])

      if (value === 'START') {
        say({
          stage: 'WORKING',
          say: '자료를 보고 있습니다. 잠시만요.',
          placeholder: '',
          canType: false,
        })
        await createRun(sources)
        return
      }
      if (value === 'MORE') {
        say({ ...ASK_SOURCES, say: '네, 더 붙여 넣어 주세요.' })
        return
      }
      if (value === 'FINAL_TEXT_YES' && run !== null) {
        // 서버는 한 번에 한 건만 처리한다. 먼저 지우고 확인값을 붙여 다시 만든다.
        const target = run.issues?.find(
          (i) => i.subject === 'FINAL_TEXT_COMPLETENESS_CONFIRMATION_REQUIRED',
        )
        const sourceId = target?.source_ids?.[0]
        try {
          await api.deleteRun(run.run_id)
        } catch {
          // 이미 지워졌으면 그대로 진행한다.
        }
        say({
          stage: 'WORKING',
          say: '확인 감사합니다. 초안을 만들고 있습니다.',
          placeholder: '',
          canType: false,
        })
        await createRun(sources, sourceId ? [{ source_id: sourceId, confirmed: true }] : undefined)
        return
      }
      if (value.startsWith('ROLE:') || value.startsWith('DROP:')) {
        const [kind, sourceId, role] = value.split(':')
        const nextRoles =
          kind === 'ROLE' ? { ...roles, [sourceId]: role } : { ...roles, [sourceId]: 'DROP' }
        setRoles(nextRoles)
        const keep = sources.filter(
          (_, i) => nextRoles[`SRC-${String(i + 1).padStart(2, '0')}`] !== 'DROP',
        )
        if (keep.length === 0) {
          say({ ...ASK_SOURCES, say: '자료가 하나도 남지 않았습니다. 다시 넣어 주세요.' })
          setSources([])
          setRoles({})
          return
        }
        if (run !== null) {
          try {
            await api.deleteRun(run.run_id)
          } catch {
            // 이미 지워졌으면 그대로 진행한다.
          }
        }
        say({ stage: 'WORKING', say: '고맙습니다. 다시 보고 있습니다.', placeholder: '', canType: false })
        await createRun(sources, undefined, nextRoles)
        return
      }
      if (value === 'CLEAR_AND_RETRY') {
        const stuck = stuckRef.current
        if (stuck !== null) {
          try {
            await api.deleteRun(stuck)
          } catch {
            // 이미 지워졌으면 그대로 다시 시도한다.
          }
          stuckRef.current = null
        }
        say({ stage: 'WORKING', say: '다시 해보겠습니다.', placeholder: '', canType: false })
        await createRun(sources, undefined, roles)
        return
      }
      if (value === 'RESTART') {
        if (run !== null) {
          try {
            await api.deleteRun(run.run_id)
          } catch {
            // 이미 지워졌으면 그대로 넘어간다.
          }
        }
        setRun(null)
        setSources([])
        setRoles({})
        setPurpose('')
        answeredRef.current = ''
        turnSeq = 0
        setTurns([turn('agent', FIRST_PROMPT.say)])
        setPrompt(FIRST_PROMPT)
      }
    },
    [createRun, prompt.choices, roles, run, say, sources],
  )

  const reviewFacts = useCallback(
    async (reviews: { fact_id: string; verdict: string }[]) => {
      if (run === null) return
      setWorking(true)
      try {
        const next = await api.reviewFacts(run.run_id, reviews)
        answeredRef.current = ''
        setRun(next)
      } catch (error: unknown) {
        const detail =
          error instanceof ApiCallError ? error.detail.message : '확인을 저장하지 못했습니다.'
        setTurns((old) => [...old, turn('agent', detail)])
      } finally {
        setWorking(false)
      }
    },
    [run],
  )

  return (
    <div className="app">
      <header className="app-header">
        <p className="draft-mark">DRAFT / 내부 검토용</p>
        <h1>{bootstrap?.app_title ?? '국회 법률 개정·개선 보도자료 초안 작성 Agent'}</h1>
        <p className="notice">
          {bootstrap?.notice ?? '공개·합성 자료로 내부 검토용 초안을 만드는 도구입니다.'}
        </p>
        <p className="notice">
          이 버전은 <strong>공개 자료</strong>만 다룹니다. 내부·엠바고 자료는 넣지 마세요.
        </p>
        {bootstrap?.model_gateway === 'fake' && (
          <p className="dev-badge">개발 모드: 가짜 AI를 사용합니다. 외부 호출 0회 · 비용 0달러</p>
        )}
      </header>

      <main className="layout">
        {bootstrapError !== null && (
          <p className="error" role="alert">
            {bootstrapError}
          </p>
        )}
        {bootstrap === null && bootstrapError === null && <p>불러오는 중…</p>}
        {bootstrap !== null && (
          <>
            <ChatScreen
              turns={turns}
              placeholder={prompt.placeholder}
              canType={prompt.canType}
              busy={working || (run !== null && BUSY_STATES.has(run.state))}
              onSend={handleSend}
              onChoose={handleChoose}
              run={run}
              onNewRun={() => handleChoose('RESTART')}
            />
            {run !== null && (run.draft !== null || (run.facts ?? []).length > 0) && (
              <DraftPanel
                run={run}
                working={working}
                downloadUrl={api.draftDownloadUrl(run.run_id)}
                onReviewAll={() =>
                  reviewFacts(
                    (run.unreviewed_fact_ids ?? []).map((fact_id) => ({
                      fact_id,
                      verdict: 'OK',
                    })),
                  )
                }
                onReviewOne={(fact_id, verdict) => reviewFacts([{ fact_id, verdict }])}
              />
            )}
          </>
        )}
      </main>

      <footer className="app-footer">
        <p>산출물은 언제나 DRAFT / 내부 검토용입니다. 승인·게시·배포 기능은 없습니다.</p>
      </footer>
    </div>
  )
}
