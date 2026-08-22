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

      {run.issues.length > 0 && (
        <section>
          <h3>확인이 필요합니다</h3>
          <ul className="issues">
            {run.issues.map((issue) => (
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
