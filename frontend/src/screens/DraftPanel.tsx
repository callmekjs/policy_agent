// 초안과 사실 목록 (README §2.8, §3.7 누적 5일차).
//
// 전 화면은 사실 10건을 **하나씩** 누르게 했다. 스무 번 넘게 눌러야 했다.
// 여기서는 **한 번에 확인**할 수 있게 하고, 다른 것이 있으면 그것만 따로
// 표시하게 한다. 사람은 보통 자료를 쭉 훑어보고 "다 맞네" 하거나 "이거 하나
// 이상한데" 한다. 그 방식에 맞춘다.
//
// 확인 자체는 없애지 않는다. 확인하지 않은 초안은 내려받을 수 없다는 규칙은
// 그대로다(`M1`). 바꾸는 것은 **누르는 횟수**뿐이다.

import type { RunView } from '../types'

interface Props {
  run: RunView
  working: boolean
  onReviewAll: () => void
  onReviewOne: (factId: string, verdict: 'OK' | 'WRONG') => void
  downloadUrl: string
}

export function DraftPanel({ run, working, onReviewAll, onReviewOne, downloadUrl }: Props) {
  const facts = run.facts ?? []
  const reviews = run.fact_reviews ?? []
  const unreviewed = run.unreviewed_fact_ids ?? []
  const protectedIds = new Set(run.protected_candidate_fact_ids ?? [])
  const verdictOf = new Map(reviews.map((r) => [r.fact_id, r.verdict]))
  const attempts = run.revision_attempts ?? []
  const draft = run.draft
  // 사람이 "다릅니다"를 눌렀는데 초안이 아직 쓰고 있는 사실. 서버가 센 값을
  // 그대로 쓴다. 화면이 따로 세면 두 벌이 어긋난다.
  const wrongInUse = run.wrong_fact_ids_in_use ?? []

  return (
    <aside className="panel">
      {draft !== null && (
        <section className="panel-block">
          <p className="draft-mark small">{draft.draft_label}</p>
          <h3>{draft.title.text}</h3>
          <ul className="points">
            {draft.key_points.map((point, i) => (
              <li key={i}>{point.text}</li>
            ))}
          </ul>
          <p className="lead">{draft.lead.text}</p>
          {/* 순서는 서버가 이미 양식대로 맞춰 보낸다. 여기서 다시 정하지 않는다. */}
          {draft.paragraphs.map((paragraph) => (
            <p key={paragraph.paragraph_id} className="para">
              {paragraph.text}
            </p>
          ))}
        </section>
      )}

      {facts.length > 0 && (
        <section className="panel-block">
          <h4>
            자료에서 찾은 사실 {facts.length}건
            {unreviewed.length > 0 && <span className="left"> · 확인 안 한 것 {unreviewed.length}건</span>}
          </h4>

          {unreviewed.length > 0 && (
            <div className="review-all">
              <p className="hint">
                자료와 맞대어 보시고, 다 맞으면 아래 버튼 하나로 끝내실 수 있습니다.
                <br />
                <strong>다른 것이 하나라도 있으면</strong> 그 항목의 “이건 다릅니다”를 눌러 주세요.
              </p>
              <button type="button" disabled={working} onClick={onReviewAll}>
                {unreviewed.length}건 모두 자료와 맞습니다
              </button>
            </div>
          )}

          <ul className="fact-list">
            {facts.map((fact) => {
              const verdict = verdictOf.get(fact.fact_id)
              return (
                <li key={fact.fact_id} className={verdict === 'WRONG' ? 'wrong' : undefined}>
                  <p className="fact-value">
                    {fact.value}
                    {fact.unit && <span className="fact-unit"> {fact.unit}</span>}
                    {protectedIds.has(fact.fact_id) && <span className="badge">꼭 확인</span>}
                  </p>
                  <p className="fact-source">
                    {fact.source_name} {fact.raw_line}행
                  </p>
                  <blockquote className="fact-quote">{fact.quote}</blockquote>
                  <p className="fact-review">
                    {verdict === 'OK' && <span className="reviewed">확인함</span>}
                    {verdict === 'WRONG' && (
                      <span className="reviewed wrong">다르다고 표시함 — 이 값은 초안에 못 씁니다</span>
                    )}
                    <button
                      type="button"
                      disabled={working}
                      onClick={() => onReviewOne(fact.fact_id, verdict === 'WRONG' ? 'OK' : 'WRONG')}
                    >
                      {verdict === 'WRONG' ? '역시 맞습니다' : '이건 다릅니다'}
                    </button>
                  </p>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {attempts.length > 0 && (
        <section className="panel-block">
          <h4>고치기 기록 {attempts.length}건</h4>
          <ul className="attempts">
            {attempts.map((attempt) => (
              <li key={attempt.attempt_id}>
                <p className="asked">“{attempt.instruction}”</p>
                {attempt.outcome === 'APPLIED' ? (
                  <p className="ok">고쳤습니다. 새 판 {attempt.resulting_version}입니다.</p>
                ) : (
                  <div className="blocked">
                    <p>
                      고치지 않았습니다. <strong>이전 초안을 그대로 두었습니다.</strong>
                    </p>
                    <ul>
                      {(attempt.blocking_messages ?? []).map((message, i) => (
                        <li key={i}>{message}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {run.can_download && (
        <section className="panel-block">
          <h4>내려받기</h4>
          <p>
            <a href={downloadUrl} download>
              Markdown으로 내려받기
            </a>
          </p>
          <p className="hint">
            내려받은 파일에도 <strong>DRAFT / 내부 검토용</strong> 표시가 남습니다. 그대로 배포하지 마세요.
          </p>
        </section>
      )}

      {/*
        칸이 그냥 사라지면 사람은 버튼을 왜 잃었는지 모른다. 막는 것과
        **왜 막혔는지 말하는 것**은 따로 해야 한다 (`M4`).
      */}
      {!run.can_download && wrongInUse.length > 0 && (
        <section className="panel-block">
          <h4>내려받기</h4>
          <div className="blocked">
            <p>
              <strong>다르다고 표시하신 사실 {wrongInUse.length}건</strong>을 초안이 아직 쓰고
              있어서 내려받을 수 없습니다.
            </p>
            <p className="hint">
              위 대화창에 <strong>고쳐 달라고</strong> 적어 주세요. 그 값이 초안에서 빠지면
              내려받을 수 있습니다.
            </p>
          </div>
        </section>
      )}
    </aside>
  )
}
