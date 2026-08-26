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
    await paste(user)
    // 붙여 넣기 **한 번**으로 확인 단계까지 간다. 네 번 보내지 않는다.
    expect(await screen.findByText(/자료 2건으로 읽었습니다/)).toBeInTheDocument()
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
    await paste(user)

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
    await paste(user)
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
    await paste(user)
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
})
