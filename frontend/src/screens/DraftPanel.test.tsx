// 내려받기 칸이 **왜 닫혔는지** 화면이 말하는지 본다 (누적 5일차 합격선 `M4`).
//
// 서버는 "틀렸다"고 표시한 사실이 초안에 남아 있으면 파일을 내주지 않는다.
// 그런데 화면이 그냥 칸을 없애 버리면, 사람은 버튼이 왜 사라졌는지 모른다.
// 막는 것과 **왜 막혔는지 말하는 것**은 따로 해야 한다.

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DraftPanel } from './DraftPanel'
import type { RunView } from '../types'

const FACT = {
  fact_id: 'F-001',
  kind: 'PLENARY_RESULT',
  value: '원안가결',
  unit: null,
  source_id: 'SRC-01',
  source_name: '본회의 표결 결과',
  raw_line: 7,
  quote: '- 회의결과: 원안가결',
  protected: false,
}

/** 사실을 다 확인했고, 그중 하나를 "다릅니다"로 표시한 상태. */
function 틀렸다고_한_작업(): RunView {
  return {
    run_id: 'RUN-TEST',
    state: 'REVIEW_READY',
    draft_version: 1,
    facts: [FACT],
    fact_reviews: [{ fact_id: 'F-001', verdict: 'WRONG', note: '' }],
    protected_candidate_fact_ids: ['F-001'],
    unreviewed_fact_ids: [],
    revision_attempts: [],
    previous_versions: [],
    // 서버가 이미 닫았다. 화면이 할 일은 **이유를 말하는 것**이다.
    can_download: false,
    wrong_fact_ids_in_use: ['F-001'],
    draft: null,
    validation_findings: [],
    failure: null,
  } as unknown as RunView
}

describe('내려받기 칸', () => {
  it('틀렸다고 한 사실이 남아 있으면 왜 못 내려받는지 말한다', () => {
    render(
      <DraftPanel
        run={틀렸다고_한_작업()}
        working={false}
        onReviewAll={() => {}}
        onReviewOne={() => {}}
        downloadUrl="/api/runs/RUN-TEST/draft.md"
      />,
    )

    expect(screen.queryByRole('link', { name: /내려받기/ })).toBeNull()
    // 사람이 읽고 **다음에 무엇을 할지** 알 수 있어야 한다.
    expect(screen.getByText(/다르다고 표시하신 사실/)).toBeInTheDocument()
    expect(screen.getByText(/고쳐 달라고/)).toBeInTheDocument()
  })
})
