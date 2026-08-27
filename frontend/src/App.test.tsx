// 대화 화면이 안전 문구를 지키고 순서대로 묻는지 확인한다.
//
// 화면이 양식에서 대화로 바뀌었다. **지키던 성질은 그대로 옮긴다.**
// 화면 모양이 바뀌었다고 안전 문구가 사라지면 안 된다.

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

/** 목적 한 줄과 자료를 **한 번에** 붙여 넣는다. 이것이 새 흐름의 핵심이다. */
const PASTE = [
  '문화예술진흥법 일부개정법률안이 본회의를 통과했어요',
  '',
  '# 의안정보',
  '의안번호 2207285, 조계원 의원 등 16인이 발의한 문화예술진흥법 일부개정법률안입니다.',
  '',
  '# 본회의 표결 결과',
  '- 대상 의안번호: 2207285',
  '- 의결일: 2025. 10. 26.',
  '- 회의결과: 원안가결',
].join('\n')

async function paste(user: ReturnType<typeof userEvent.setup>, text = PASTE) {
  const input = await screen.findByLabelText('할 말 적기')
  await user.click(input)
  await user.paste(text)
  await user.click(screen.getByRole('button', { name: '보내기' }))
  return input
}

/** 자료를 넣고 **공개 범위·자료 기준일**까지 답해 초안 만들기 직전까지 간다. */
async function toConsent(user: ReturnType<typeof userEvent.setup>) {
  await paste(user)
  await user.click(await screen.findByRole('button', { name: '네, 공개 자료입니다' }))
  const sameDay = await screen.findByRole('button', { name: /네, 오늘/ })
  await user.click(sameDay)
  // 발표 주체는 **사람이 말한 값만** 쓴다. 묻지 않으면 초안이 막힌다.
  await paste(user, '조계원 의원실')
}

describe('대화 화면', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    mockFetch(() => BOOTSTRAP)
  })

  it('DRAFT 표시를 항상 보여준다', async () => {
    render(<App />)
    expect(await screen.findAllByText('DRAFT / 내부 검토용')).not.toHaveLength(0)
  })

  it('공개 자료만 다룬다고 미리 알린다', async () => {
    render(<App />)
    expect(
      await screen.findByText(/내부·엠바고 자료는 넣지 마세요/),
    ).toBeInTheDocument()
  })

  it('전송 확인 전에는 외부 호출이 0회라고 안내한다', async () => {
    render(<App />)
    expect(await screen.findByText(/외부 호출 0회 · 비용 0달러/)).toBeInTheDocument()
  })

  it('처음부터 목적과 자료를 한 번에 받는다', async () => {
    render(<App />)
    expect(await screen.findByText(/한 줄 쓰고, 공식 자료를 그 아래 붙여 넣어 주세요/)).toBeInTheDocument()
    // 양식이 아니므로 역할·날짜·공개 범위를 미리 고르라고 하지 않는다 (§0.3).
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.queryByLabelText(/자료 확인 기준일/)).toBeNull()
  })

  it('한 덩이로 붙여 넣으면 자료를 알아서 나눈다', async () => {
    const user = userEvent.setup()
    render(<App />)
    await toConsent(user)
    // 붙여 넣기 **한 번**으로 확인 단계까지 간다. 네 번 보내지 않는다.
    expect(await screen.findByText(/자료 2건으로 읽었습니다/)).toBeInTheDocument()
  })

  it('공개 자료인지 반드시 묻는다', async () => {
    const user = userEvent.setup()
    render(<App />)
    await paste(user)
    // 묻지 않고 공개로 넣으면 내부 문서를 붙였을 때 아무도 못 막는다.
    // 고를 수 있는 **버튼**이 있어야 막을 수 있다.
    expect(
      await screen.findByRole('button', { name: '네, 공개 자료입니다' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '내부·엠바고 자료예요' })).toBeInTheDocument()
  })

  it('내부·엠바고를 고르면 진행되지 않는다', async () => {
    const user = userEvent.setup()
    const calls: string[] = []
    mockFetch((url) => {
      calls.push(url)
      return BOOTSTRAP
    })
    render(<App />)
    await paste(user)
    await user.click(await screen.findByRole('button', { name: '내부·엠바고 자료예요' }))

    expect(await screen.findByText(/이 버전이 다루지 않습니다/)).toBeInTheDocument()
    expect(calls.filter((u) => u === '/api/runs')).toHaveLength(0)
  })

  it('자료 기준일을 오늘로 넣어 버리지 않고 묻는다', async () => {
    const user = userEvent.setup()
    render(<App />)
    await paste(user)
    await user.click(await screen.findByRole('button', { name: '네, 공개 자료입니다' }))
    // 이 날짜는 초안에 그대로 실린다. 사람이 하지 않은 주장이 되면 안 된다.
    expect(await screen.findByText(/이 날짜는 초안에 그대로 실립니다/)).toBeInTheDocument()
  })

  it('사람이 적은 자료 기준일을 그대로 보낸다', async () => {
    const user = userEvent.setup()
    let sent: Record<string, unknown> | null = null
    mockFetch((url, init) => {
      if (url === '/api/runs' && init?.body) {
        sent = JSON.parse(String(init.body))
        return { run_id: 'RUN-1', state: 'EXTRACTING_FACTS', status_label: 'AI 처리 중' }
      }
      return BOOTSTRAP
    })
    render(<App />)
    await paste(user)
    await user.click(await screen.findByRole('button', { name: '네, 공개 자료입니다' }))
    const input = await screen.findByLabelText('할 말 적기')
    await user.click(input)
    await user.paste('2025-10-26')
    await user.click(screen.getByRole('button', { name: '보내기' }))
    // 발표 주체까지 답해야 초안 만들기 물음이 나온다.
    await paste(user, '조계원 의원실')
    await user.click(await screen.findByRole('button', { name: '네, 만들어 주세요' }))

    await waitFor(() => expect(sent).not.toBeNull())
    const 보낸 = sent as unknown as { basis_date: string; announcement_subject: string }
    expect(보낸.basis_date).toBe('2025-10-26')
    // 발표 주체도 **사람이 말한 그대로** 간다. 비면 초안이 막힌다.
    expect(보낸.announcement_subject).toBe('조계원 의원실')
  })

  it('자료 없이 끝내겠다고 하면 초안을 만들지 않는다', async () => {
    const user = userEvent.setup()
    render(<App />)
    await paste(user, '문화예술진흥법이 통과했어요')
    const input = await screen.findByLabelText('할 말 적기')
    await user.click(input)
    await user.paste('다 넣었어요')
    await user.click(screen.getByRole('button', { name: '보내기' }))
    expect(
      await screen.findByText(/자료가 없으면 초안을 만들 수 없습니다/),
    ).toBeInTheDocument()
  })

  it('시작을 누르기 전에는 서버에 작업을 만들지 않는다', async () => {
    const user = userEvent.setup()
    const calls: string[] = []
    mockFetch((url) => {
      calls.push(url)
      return BOOTSTRAP
    })
    render(<App />)
    await toConsent(user)

    await screen.findByText(/초안을 만들까요/)
    expect(calls.filter((u) => u === '/api/runs')).toHaveLength(0)
  })

  it('오류가 나도 막다른 길로 두지 않는다', async () => {
    const user = userEvent.setup()
    // 이전 작업이 남아 새 작업을 못 만드는 상황.
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/api/runs') {
          return {
            ok: false,
            status: 409,
            json: async () => ({
              error_code: 'RUN_ALREADY_ACTIVE',
              message: '이미 진행 중인 작업이 있습니다.',
              next_action: '현재 작업을 삭제해 주세요.',
              run_id: 'RUN-OLD',
            }),
          }
        }
        return { ok: true, status: 200, json: async () => BOOTSTRAP }
      }),
    )
    render(<App />)
    await toConsent(user)
    await user.click(await screen.findByRole('button', { name: '네, 만들어 주세요' }))

    // 오류만 보여 주고 버튼이 없으면 사람은 화면을 새로 고치는 수밖에 없고,
    // 넣은 자료를 다 잃는다.
    expect(await screen.findByText(/이미 진행 중인 작업이 있습니다/)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: '이전 작업을 지우고 다시 하기' }),
    ).toBeInTheDocument()
  })

  it('역할을 고르라고 하지 않고 잘 모르겠음으로 보낸다', async () => {
    const user = userEvent.setup()
    let sent: Record<string, unknown> | null = null
    mockFetch((url, init) => {
      if (url === '/api/runs' && init?.body) {
        sent = JSON.parse(String(init.body))
        return { run_id: 'RUN-1', state: 'EXTRACTING_FACTS', status_label: 'AI 처리 중' }
      }
      return BOOTSTRAP
    })
    render(<App />)
    await toConsent(user)
    await user.click(await screen.findByRole('button', { name: '네, 만들어 주세요' }))

    await waitFor(() => expect(sent).not.toBeNull())
    const body = sent as unknown as {
      sources: { role: string; text: string }[]
      disclosure: string
      purpose: string
    }
    expect(body.sources.every((s) => s.role === 'UNKNOWN')).toBe(true)
    expect(body.disclosure).toBe('PUBLIC')
    // 첫 줄은 자료가 아니라 **무엇을 알리는지**로 쓴다.
    expect(body.purpose).toContain('본회의를 통과')
    expect(body.sources.some((s) => s.text.includes('본회의를 통과'))).toBe(false)
  })

  it('사람 확인을 눌러도 방금 고른 역할을 잃지 않는다', async () => {
    // 역할을 고른 뒤 확인 버튼을 누르면 서버에 **새 작업**을 만든다. 그때
    // 역할을 함께 보내지 않으면 새 작업이 역할을 모른 채 시작해, 방금 고른
    // 것을 처음부터 다시 묻는다. 화면에서는 초안까지 갈 수 없었다.
    const user = userEvent.setup()
    const 보낸_것: { sources: { role: string }[]; final_text_completeness_confirmations?: unknown }[] = []
    let 단계 = 0
    mockFetch((url, init) => {
      if (url === '/api/runs' && init?.body) {
        보낸_것.push(JSON.parse(String(init.body)))
        단계 += 1
        return { run_id: `RUN-${단계}`, state: 'EXTRACTING_FACTS', status_label: 'AI 처리 중' }
      }
      if (url.startsWith('/api/runs/RUN-')) {
        const 자료 = [
          { source_id: 'SRC-01', display_name: '자료 1', role: 'UNKNOWN' },
          { source_id: 'SRC-02', display_name: '자료 2', role: 'INTRODUCED_TEXT' },
        ]
        if (단계 === 1) {
          // 1단계: 자료 1의 역할을 사람에게 묻는다. 후보를 둘 준다.
          return {
            run_id: 'RUN-1',
            state: 'NEEDS_INPUT',
            status_label: '확인이 필요합니다',
            sources: 자료,
            role_choices: [
              {
                source_id: 'SRC-01',
                role: 'BILL_INFORMATION',
                role_label: '의안정보',
                evidence_quote: '의안번호 2207285',
              },
              {
                source_id: 'SRC-01',
                role: 'OFFICIAL_REASON',
                role_label: '공식 제안·개정이유',
                evidence_quote: '제안이유 및 주요내용',
              },
            ],
            issues: [
              {
                issue_id: 'ISS-001',
                code: 'SOURCE_ROLE_CONTENT_MISMATCH',
                subject: 'SOURCE_ROLE:SRC-01',
                message: '자료 1이 어떤 자료인지 확인해 주세요.',
                source_ids: ['SRC-01'],
              },
            ],
          }
        }
        // 2단계: 역할이 정해졌고 이제 사람 확인만 남았다.
        return {
          run_id: 'RUN-2',
          state: 'NEEDS_INPUT',
          status_label: '확인이 필요합니다',
          sources: [{ ...자료[0], role: 'BILL_INFORMATION' }, 자료[1]],
          issues: [
            {
              issue_id: 'ISS-002',
              code: 'REQUIRED_INPUT_MISSING',
              subject: 'FINAL_TEXT_COMPLETENESS_CONFIRMATION_REQUIRED',
              message: '개정문과 부칙이 끝까지 들어 있는지 봐 주세요.',
              source_ids: ['SRC-02'],
            },
          ],
        }
      }
      return BOOTSTRAP
    })
    render(<App />)
    await toConsent(user)
    await user.click(await screen.findByRole('button', { name: '네, 만들어 주세요' }))

    // 서버 상태는 1.5초마다 따라간다. 기본 대기(1초)로는 못 본다.
    const 역할 = await screen.findByRole('button', { name: '의안정보' }, { timeout: 5000 })
    await user.click(역할)

    const 확인 = await screen.findByRole(
      'button',
      { name: '네, 끝까지 들어 있습니다' },
      { timeout: 5000 },
    )
    await user.click(확인)

    await waitFor(() => expect(보낸_것.length).toBe(3))
    const 마지막 = 보낸_것[2]
    // 확인값은 그대로 가야 한다.
    expect(마지막.final_text_completeness_confirmations).toEqual([
      { source_id: 'SRC-02', confirmed: true },
    ])
    // 그리고 **방금 고른 역할도 함께** 가야 한다.
    expect(마지막.sources[0].role).toBe('BILL_INFORMATION')
  })
})
