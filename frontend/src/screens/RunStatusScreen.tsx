// 진행·보완·실패 화면 (README §2.8, §2.9).
// 정확한 완료율을 알 수 없으므로 가짜 퍼센트는 보여주지 않는다.

import { useState } from 'react'

import { api } from '../api/client'
import type { RunView } from '../types'

interface Props {
  run: RunView
  onConfirmFinalText: (sourceIds: string[]) => void
  onNewRun: () => void
  onDelete: () => void
  /** 5일차 동작이 끝나면 새 상태로 화면을 갈아 준다. */
  onUpdated: (run: RunView) => void
}

/** 발의안을 최종 의결 내용으로 대신 쓸 때만 나오는 질문의 대상 이름. */
const FINAL_TEXT_CONFIRMATION = 'FINAL_TEXT_COMPLETENESS_CONFIRMATION_REQUIRED'

const BUSY_STATES = new Set([
  'CREATED',
  'VALIDATING_INPUT',
  'EXTRACTING_FACTS',
  'DRAFTING',
  'CHECKING_DRAFT',
  'REVISING',
  'CHECKING_REVISION',
])

export function RunStatusScreen({
  run,
  onConfirmFinalText,
  onNewRun,
  onDelete,
  onUpdated,
}: Props) {
  const busy = BUSY_STATES.has(run.state)
  const [instruction, setInstruction] = useState('')
  const [working, setWorking] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const reviews = run.fact_reviews ?? []
  const unreviewed = run.unreviewed_fact_ids ?? []
  const protectedIds = new Set(run.protected_candidate_fact_ids ?? [])
  const attempts = run.revision_attempts ?? []
  const verdictOf = new Map(reviews.map((r) => [r.fact_id, r.verdict]))

  /** 서버를 부르는 동안 버튼을 잠근다. 두 번 눌러 두 번 처리되면 안 된다. */
  async function guarded(work: () => Promise<RunView>) {
    setWorking(true)
    setProblem(null)
    try {
      onUpdated(await work())
    } catch (error) {
      setProblem(error instanceof Error ? error.message : '요청을 처리하지 못했습니다.')
    } finally {
      setWorking(false)
    }
  }

  const reviewOne = (factId: string, verdict: 'OK' | 'WRONG') =>
    guarded(() => api.reviewFacts(run.run_id, [{ fact_id: factId, verdict }]))
  // 목록 하나가 비어 오더라도 화면 전체가 사라지면 안 된다. 사용자는 아무것도
  // 보지 못한 채 무엇이 잘못됐는지 알 수 없게 된다.
  const rules = run.supplementary_rules ?? []
  const findings = run.validation_findings ?? []
  const articles = run.changed_articles ?? []
  const facts = run.facts ?? []
  const rejected = run.rejected_evidence ?? []
  const issues = run.issues ?? []
  // 이 질문은 필요할 때만 나온다. 첫 화면의 상시 입력이 아니다 (§2.16.2).
  const confirmation = issues.find((i) => i.subject === FINAL_TEXT_CONFIRMATION)

  return (
    <div className="screen">
      <h2>{run.status_label}</h2>

      <dl className="meta">
        <div>
          <dt>실행 ID</dt>
          <dd>{run.run_id}</dd>
        </div>
        <div>
          <dt>절차 단계</dt>
          <dd>{run.procedure_stage_label}</dd>
        </div>
        <div>
          <dt>효력 상태</dt>
          <dd>{run.effect_status_label}</dd>
        </div>
        <div>
          <dt>자료 기준일</dt>
          <dd>{run.basis_date}</dd>
        </div>
        <div>
          <dt>AI 호출</dt>
          <dd>
            {run.actual_model_calls}회 / 최대 {run.max_model_calls}회
          </dd>
        </div>
        <div>
          <dt>앱 추정 비용</dt>
          <dd>
            ${run.estimated_cost_usd.toFixed(4)} / 한도 ${run.cost_limit_usd.toFixed(2)}
          </dd>
        </div>
      </dl>
      <p className="hint">
        비용은 앱이 계산한 추정치이며 세금·환율과 공급자 청구서 금액을 보증하지 않습니다.
      </p>

      {busy && <p className="hint">처리 중입니다. 완료율은 알 수 없어 표시하지 않습니다.</p>}

      {facts.length > 0 && (
        <section>
          <h3>자료에서 확인한 사실 {facts.length}건</h3>
          <p className="hint">
            모든 사실에 원문 근거가 붙어 있습니다. 근거를 찾지 못한 값은 쓰지 않습니다.
          </p>
          {run.draft && (
            <p className="hint">
              <strong>원문과 맞대어 보고 하나씩 눌러 주세요.</strong> 남은 것{' '}
              {unreviewed.length}건. 모두 확인해야 내려받을 수 있습니다.
              <br />
              <span className="badge">꼭 확인</span> 표시가 붙은 것은 틀리면 가장
              위험한 값이라, 맞다고 하시면 이후 고치기에서 지켜 드립니다.
            </p>
          )}
          <ul className="facts">
            {facts.map((fact) => (
              <li key={fact.fact_id}>
                <p className="fact-value">
                  {fact.value}
                  {fact.unit && <span className="fact-unit"> {fact.unit}</span>}
                </p>
                <p className="fact-source">
                  {fact.source_name} {fact.raw_line}행
                </p>
                <blockquote className="fact-quote">{fact.quote}</blockquote>
                <details>
                  <summary>근거 보기</summary>
                  <p className="mono">
                    {fact.kind} / {fact.subject} / {fact.provenance} /{' '}
                    {fact.raw_line}행 {fact.raw_column}칸
                  </p>
                </details>
                {run.draft && (
                  <p className="fact-review">
                    {protectedIds.has(fact.fact_id) && (
                      <span className="badge">꼭 확인</span>
                    )}
                    <button
                      type="button"
                      disabled={working}
                      aria-pressed={verdictOf.get(fact.fact_id) === 'OK'}
                      onClick={() => reviewOne(fact.fact_id, 'OK')}
                    >
                      원문과 맞습니다
                    </button>
                    <button
                      type="button"
                      disabled={working}
                      aria-pressed={verdictOf.get(fact.fact_id) === 'WRONG'}
                      onClick={() => reviewOne(fact.fact_id, 'WRONG')}
                    >
                      원문과 다릅니다
                    </button>
                    {verdictOf.get(fact.fact_id) === 'OK' && (
                      <span className="reviewed">확인함</span>
                    )}
                    {verdictOf.get(fact.fact_id) === 'WRONG' && (
                      <span className="reviewed wrong">
                        다르다고 표시함 — 이 값을 쓴 문장은 초안에 남을 수 없습니다
                      </span>
                    )}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {rejected.length > 0 && (
        <section>
          <h3>근거를 찾지 못해 쓰지 않은 것 {rejected.length}건</h3>
          <p className="hint">
            원문에서 근거를 확인하지 못해 사실로 쓰지 않았습니다. 이유는 항목마다 다릅니다.
          </p>
          <ul className="issues">
            {rejected.map((item) => (
              <li key={item}>
                <p className="mono">{item}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {run.final_text && (
        <section>
          <h3>이번 보도자료가 설명하는 최종 의결 내용</h3>
          <p className="hint">
            어느 자료를 최종 내용으로 볼지는 코드가 정합니다. AI가 고르지 않습니다.
          </p>
          <dl className="meta">
            <div>
              <dt>가져온 자료</dt>
              <dd>{run.final_text.source_name}</dd>
            </div>
            <div>
              <dt>의안번호</dt>
              <dd>{run.final_text.bill_number || '확인 필요'}</dd>
            </div>
            <div>
              <dt>바뀐 조문</dt>
              <dd>{articles.join(', ') || '없음'}</dd>
            </div>
          </dl>
          <blockquote className="fact-quote">{run.final_text.body}</blockquote>
          <details>
            <summary>근거 보기</summary>
            <p className="mono">
              {run.final_text.rule} / {run.final_text.derivation_id}
            </p>
          </details>
        </section>
      )}

      {rules.length > 0 && (
        <section>
          <h3>부칙 {rules.length}건</h3>
          <p className="hint">
            아직 공포 전이므로 제안된 내용입니다. 확정된 시행일이 아닙니다.
          </p>
          <ul className="facts">
            {rules.map((rule) => (
              <li key={rule.rule_id}>
                <p className="fact-value">{rule.applies_to}</p>
                <p className="fact-source">{rule.kind}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      {run.draft && (
        <section className="draft">
          <h3>{run.draft.draft_label}</h3>
          <p className="hint">
            검토용 초안입니다. 모든 문장에 자료 근거가 붙어 있습니다. 최종본도
            승인본도 아니며 이 프로그램은 발송·게시를 하지 않습니다.
          </p>

          <h4 className="draft-title">{run.draft.title.text}</h4>
          <p className="mono">근거 {run.draft.title.fact_ids.join(', ')}</p>

          <ul className="facts">
            {run.draft.key_points.map((point, index) => (
              <li key={`kp-${index}`}>
                <p className="fact-value">{point.text}</p>
                <p className="mono">근거 {point.fact_ids.join(', ')}</p>
              </li>
            ))}
          </ul>

          <p className="draft-lead">{run.draft.lead.text}</p>
          <p className="mono">근거 {run.draft.lead.fact_ids.join(', ')}</p>

          {run.draft.paragraphs.map((paragraph) => (
            <div key={paragraph.paragraph_id}>
              <p>{paragraph.text}</p>
              <p className="mono">
                {paragraph.paragraph_id} / 근거 {paragraph.fact_ids.join(', ') || '없음'}
                {paragraph.supplementary_rule_ids.length > 0 &&
                  ` / 부칙 ${paragraph.supplementary_rule_ids.join(', ')}`}
              </p>
            </div>
          ))}

          <p className="draft-contact">문의처: {run.draft.contact_text}</p>
          {run.draft.placeholders.length > 0 && (
            <p className="hint">사람이 채워야 하는 곳: {run.draft.placeholders.join(', ')}</p>
          )}
        </section>
      )}

      {run.draft && !busy && (
        <section className="revise">
          <h3>고치고 내려받기</h3>

          {problem && <p className="problem">{problem}</p>}

          <label htmlFor="instruction">어디를 어떻게 고칠까요?</label>
          <p className="hint">
            여기에 적은 글은 <strong>자료가 아닙니다.</strong> 없는 사실을 적어도
            초안에 넣지 않습니다. 문장을 다듬는 부탁만 처리합니다.
          </p>
          <textarea
            id="instruction"
            rows={3}
            value={instruction}
            disabled={working}
            placeholder="예: 문단 순서를 바꿔 주세요"
            onChange={(event) => setInstruction(event.target.value)}
          />
          <button
            type="button"
            disabled={working || instruction.trim().length === 0}
            onClick={() =>
              guarded(async () => {
                const next = await api.reviseDraft(
                  run.run_id,
                  `rev-${Date.now()}`,
                  instruction.trim(),
                )
                setInstruction('')
                return next
              })
            }
          >
            고쳐 주세요
          </button>

          {attempts.length > 0 && (
            <>
              <h4>고치기 기록 {attempts.length}건</h4>
              <ul className="attempts">
                {attempts.map((attempt) => (
                  <li key={attempt.attempt_id}>
                    <p>“{attempt.instruction}”</p>
                    {attempt.outcome === 'APPLIED' ? (
                      <p className="ok">
                        고쳤습니다. 새 판 {attempt.resulting_version}입니다.
                      </p>
                    ) : (
                      <div className="blocked">
                        <p>
                          고치지 않았습니다.{' '}
                          <strong>이전 초안을 그대로 두었습니다.</strong>
                        </p>
                        <ul>
                          {(attempt.blocking_messages ?? []).map((message, i) => (
                            <li key={i}>{message}</li>
                          ))}
                        </ul>
                        <details>
                          <summary>개발자용 코드 보기</summary>
                          <p className="mono">
                            {attempt.blocking_rule_ids.join(', ')}
                          </p>
                        </details>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}

          <h4>내려받기</h4>
          {run.can_download ? (
            <p>
              <a href={api.draftDownloadUrl(run.run_id)} download>
                Markdown으로 내려받기
              </a>
              <br />
              <span className="hint">
                내려받은 파일에도 <strong>DRAFT / 내부 검토용</strong> 표시가
                남습니다. 그대로 배포하지 마세요.
              </span>
            </p>
          ) : (
            <p className="hint">
              아직 확인하지 않은 사실이 {unreviewed.length}건 있습니다. 위 목록에서
              모두 확인해야 내려받을 수 있습니다.
            </p>
          )}

          {run.can_download && run.state === 'REVIEW_READY' && (
            <button
              type="button"
              disabled={working}
              onClick={() => guarded(() => api.completeRun(run.run_id))}
            >
              확인을 마쳤습니다
            </button>
          )}
        </section>
      )}

      {findings.length > 0 && (
        <section>
          <h3>안전 검사에서 막힌 것 {findings.length}건</h3>
          <p className="hint">
            아래 이유로 초안을 내주지 않았습니다. 규칙 번호와 기준 문서 위치를 함께
            적었습니다.
          </p>
          <ul className="issues">
            {findings.map((finding) => (
              <li key={finding.finding_id}>
                <p className="issue-message">
                  {finding.affected_part}: {finding.message}
                </p>
                <details>
                  <summary>근거 보기</summary>
                  <p className="mono">
                    {finding.rule_id} / {finding.rule_document} / {finding.severity}
                  </p>
                </details>
              </li>
            ))}
          </ul>
        </section>
      )}

      {confirmation && (
        <section className="confirm">
          <h3>사람이 확인해 주세요</h3>
          <p>{confirmation.message}</p>
          <p className="issue-question">{confirmation.question}</p>
          <p className="hint">
            이 질문은 사실이 맞는지 승인하는 절차가 아닙니다. 넣은 자료에 개정문과
            부칙이 처음부터 끝까지 들어 있는지만 원문을 보고 답해 주세요. 모르겠으면
            공식 최종 의결문을 넣어 새로 시작하는 편이 안전합니다.
          </p>
          <div className="actions">
            <button
              type="button"
              className="primary"
              onClick={() => onConfirmFinalText(confirmation.source_ids)}
              disabled={busy}
            >
              예, 끝까지 들어 있습니다
            </button>
            <button type="button" className="ghost" onClick={onNewRun} disabled={busy}>
              아니오, 자료를 다시 넣겠습니다
            </button>
          </div>
        </section>
      )}

      {issues.length > 0 && (
        <section>
          <h3>확인이 필요합니다</h3>
          <ul className="issues">
            {issues.map((issue) => (
              <li key={issue.issue_id}>
                <p className="issue-message">{issue.message}</p>
                {issue.question && <p className="issue-question">{issue.question}</p>}
                <details>
                  <summary>근거 보기</summary>
                  <p className="mono">
                    {issue.code} / {issue.subject} / {issue.resolution_kind}
                  </p>
                </details>
              </li>
            ))}
          </ul>
        </section>
      )}

      {run.failure && (
        <section className="failure">
          <h3>{run.failure.kind === 'QUALITY_GATE' ? '자료·품질 확인 필요' : '기술 오류'}</h3>
          <p>{run.failure.message}</p>
          {run.failure.next_action && <p className="hint">{run.failure.next_action}</p>}
          <details>
            <summary>근거 보기</summary>
            <p className="mono">
              {run.failure.kind} / {run.failure.code}
            </p>
          </details>
        </section>
      )}

      <div className="actions">
        <button type="button" className="primary" onClick={onNewRun} disabled={busy}>
          새 작업 시작
        </button>
        <button type="button" className="ghost" onClick={onDelete} disabled={busy}>
          현재 작업 삭제
        </button>
      </div>
      {busy && <p className="hint">처리 중에는 삭제와 새 작업을 할 수 없습니다.</p>}
    </div>
  )
}
