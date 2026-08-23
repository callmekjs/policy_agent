// 진행·보완·실패 화면 (README §2.8, §2.9).
// 정확한 완료율을 알 수 없으므로 가짜 퍼센트는 보여주지 않는다.

import type { RunView } from '../types'

interface Props {
  run: RunView
  onNewRun: () => void
  onDelete: () => void
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

export function RunStatusScreen({ run, onNewRun, onDelete }: Props) {
  const busy = BUSY_STATES.has(run.state)
  // 목록 하나가 비어 오더라도 화면 전체가 사라지면 안 된다. 사용자는 아무것도
  // 보지 못한 채 무엇이 잘못됐는지 알 수 없게 된다.
  const rules = run.supplementary_rules ?? []
  const findings = run.validation_findings ?? []
  const articles = run.changed_articles ?? []
  const facts = run.facts ?? []
  const rejected = run.rejected_evidence ?? []
  const issues = run.issues ?? []

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
