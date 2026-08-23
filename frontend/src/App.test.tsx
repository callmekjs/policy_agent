// 입력 화면이 네 가지 필수 항목과 안전 문구를 보여주는지 확인한다.

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from './App'
import type { Bootstrap } from './types'

const BOOTSTRAP: Bootstrap = {
  app_title: '국회 법률 개정·개선 보도자료 초안 작성 Agent',
  notice: '공개·합성 자료로 내부 검토용 초안을 만드는 도구입니다.',
  contract_id: 'assembly_member_partial_amendment_plenary_v1@1.0.0',
  procedure_stage: 'PLENARY_DECIDED',
  procedure_stage_label: '본회의 의결 결과',
  external_ai: {
    policy_version: 'external_ai_transfer_notice_v1',
    provider: 'openai',
    model: 'gpt-5.6-terra',
    sent_items: ['공개로 확인한 공식 자료 원문'],
    notice: '확인하면 전송됩니다.',
  },
  limits: {
    max_sources: 6,
    max_total_chars: 30000,
    purpose_min_chars: 10,
    purpose_max_chars: 500,
  },
  source_roles: [
    { value: 'UNKNOWN', label: '잘 모르겠음' },
    { value: 'BILL_INFORMATION', label: '의안정보' },
  ],
  model_gateway: 'fake',
}

function mockFetch(handler: (url: string, init?: RequestInit) => unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => ({
      ok: true,
      status: 200,
      json: async () => handler(url, init),
    })),
  )
}

describe('App 입력 화면', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    mockFetch(() => BOOTSTRAP)
  })

  it('DRAFT 표시를 항상 보여준다', async () => {
    render(<App />)
    expect(await screen.findByText('DRAFT / 내부 검토용')).toBeInTheDocument()
  })

  it('필수 입력 네 가지를 보여준다', async () => {
    render(<App />)
    expect(await screen.findByLabelText(/보도 목적/)).toBeInTheDocument()
    expect(screen.getByLabelText(/공개 범위/)).toBeInTheDocument()
    expect(screen.getByLabelText(/자료 확인 기준일/)).toBeInTheDocument()
    expect(screen.getByLabelText(/자료 1 본문/)).toBeInTheDocument()
  })

  it('지원 절차 단계를 읽기 전용으로 고정해 보여준다', async () => {
    render(<App />)
    const stage = (await screen.findByLabelText('지원 절차 단계')) as HTMLInputElement
    expect(stage.value).toBe('본회의 의결 결과')
    expect(stage.readOnly).toBe(true)
  })

  it('전송 확인 전에는 외부 호출이 0회라고 안내한다', async () => {
    render(<App />)
    expect(
      await screen.findByText('확인 전에는 외부 AI를 한 번도 부르지 않습니다.'),
    ).toBeInTheDocument()
  })

  it('내부·엠바고를 고르면 진행되지 않는다고 경고한다', async () => {
    const user = userEvent.setup()
    render(<App />)
    const select = await screen.findByLabelText(/공개 범위/)
    await user.selectOptions(select, 'INTERNAL')
    expect(
      screen.getByText(/이 버전은 공개 자료만 처리합니다/),
    ).toBeInTheDocument()
  })

  it('빈 입력으로 보내면 서버가 준 보완 안내를 보여준다', async () => {
    mockFetch((url) => {
      if (url === '/api/bootstrap') return BOOTSTRAP
      return {
        run_id: 'RUN-TEST',
        state: 'NEEDS_INPUT',
        status_label: '입력 보완 필요',
        contract_id: BOOTSTRAP.contract_id,
        procedure_stage: 'PLENARY_DECIDED',
        procedure_stage_label: '본회의 의결 결과',
        effect_status_label: '아직 법률 아님',
        basis_date: '2026-08-22',
        purpose: '',
        disclosure: 'PUBLIC',
        announcement_subject_input: null,
        created_at: '2026-08-22T12:00:00Z',
        updated_at: '2026-08-22T12:00:00Z',
        draft_version: 0,
        actual_model_calls: 0,
        max_model_calls: 7,
        estimated_cost_usd: 0,
        cost_limit_usd: 1.1,
        sources: [],
        role_choices: [],
        facts: [],
        rejected_evidence: [],
        issues: [
          {
            issue_id: 'ISS-001',
            code: 'REQUIRED_INPUT_MISSING',
            subject: 'PURPOSE',
            severity: 'BLOCKING',
            message: '보도 목적이 비어 있습니다.',
            question: '한두 문장으로 적어 주세요.',
            source_ids: [],
            resolution_kind: 'ANSWER_IN_SAME_RUN',
            requires_new_run: false,
          },
        ],
        failure: null,
      }
    })

    const user = userEvent.setup()
    render(<App />)
    await user.click(await screen.findByRole('button', { name: '자료 확인 시작' }))
    await waitFor(() => {
      expect(screen.getByText('보도 목적이 비어 있습니다.')).toBeInTheDocument()
    })
  })
})

/** 최종 의결문 확인 질문이 있는 상태의 화면 값. */
const NEEDS_CONFIRMATION = {
  run_id: 'RUN-TEST',
  state: 'NEEDS_INPUT',
  status_label: '확인이 필요합니다',
  contract_id: BOOTSTRAP.contract_id,
  procedure_stage: 'PLENARY_DECIDED',
  procedure_stage_label: '본회의 의결 결과',
  effect_status_label: '아직 법률 아님',
  basis_date: '2025-10-26',
  purpose: '본회의 의결 결과를 알리는 초안입니다.',
  disclosure: 'PUBLIC',
  announcement_subject_input: null,
  created_at: '2025-10-26T00:00:00Z',
  updated_at: '2025-10-26T00:00:00Z',
  draft_version: 0,
  actual_model_calls: 0,
  max_model_calls: 7,
  estimated_cost_usd: 0,
  cost_limit_usd: 1.1,
  sources: [],
  issues: [
    {
      issue_id: 'ISS-001',
      code: 'REQUIRED_INPUT_MISSING',
      subject: 'FINAL_TEXT_COMPLETENESS_CONFIRMATION_REQUIRED',
      severity: 'BLOCKING',
      message: '이 작업은 ‘발의안’를 최종 의결 내용으로 대신 씁니다.',
      question: '개정문과 부칙 끝까지 들어 있습니까?',
      source_ids: ['SRC-04'],
      resolution_kind: 'ANSWER_IN_SAME_RUN',
      requires_new_run: false,
    },
  ],
  role_choices: [],
  facts: [],
  rejected_evidence: [],
  final_text: null,
  changed_articles: [],
  supplementary_rules: [],
  draft: null,
  validation_findings: [],
  failure: null,
}

describe('최종 의결문 확인 질문', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('예를 누르면 이전 작업을 먼저 지우고 확인값을 붙여 다시 만든다', async () => {
    // 서버는 한 번에 한 건만 처리한다. 지우기 전에 새로 만들면 409로 막힌다.
    const calls: string[] = []
    let created = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET'
        calls.push(`${method} ${url}`)
        if (url.includes('/api/bootstrap')) {
          return { ok: true, status: 200, json: async () => BOOTSTRAP }
        }
        if (method === 'DELETE') {
          return { ok: true, status: 200, json: async () => ({ run_id: 'RUN-TEST', deleted: true }) }
        }
        if (method === 'POST') {
          created += 1
          const body = JSON.parse(String(init?.body))
          if (created === 1) {
            return { ok: true, status: 200, json: async () => NEEDS_CONFIRMATION }
          }
          // 두 번째 요청에는 사람이 답한 확인값이 붙어 있어야 한다.
          expect(body.final_text_completeness_confirmations).toEqual([
            { source_id: 'SRC-04', confirmed: true },
          ])
          return {
            ok: true,
            status: 200,
            json: async () => ({ ...NEEDS_CONFIRMATION, state: 'REVIEW_READY', draft_version: 1, issues: [] }),
          }
        }
        return { ok: true, status: 200, json: async () => NEEDS_CONFIRMATION }
      }),
    )

    render(<App />)
    const user = userEvent.setup()
    await screen.findByLabelText(/보도 목적/)
    await user.type(screen.getByLabelText(/보도 목적/), '본회의 의결 결과를 알리는 초안입니다.')
    await user.type(screen.getByLabelText(/자료 1 본문/), '의안번호: 2207285')
    await user.click(screen.getByLabelText(/공개 자료만 넣었음/))
    await user.click(screen.getByRole('button', { name: '자료 확인 시작' }))

    const yes = await screen.findByRole('button', { name: /예, 끝까지 들어 있습니다/ })
    await user.click(yes)

    await waitFor(() => expect(created).toBe(2))
    const deleteAt = calls.findIndex((c) => c.startsWith('DELETE'))
    const secondPostAt = calls.map((c, i) => (c.startsWith('POST /api/runs') ? i : -1)).filter((i) => i >= 0)[1]
    expect(deleteAt).toBeGreaterThanOrEqual(0)
    expect(deleteAt).toBeLessThan(secondPostAt)
  })
})
