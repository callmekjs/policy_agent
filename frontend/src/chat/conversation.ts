// 대화의 순서를 정한다 (README §0.3, §2.8, §2.9).
//
// 화면은 말풍선만 그린다. **무엇을 물을지 정하는 곳은 여기다.**
//
// 원칙 하나 — 한 번에 하나만 묻는다. 양식은 열 칸을 한꺼번에 보여 주고 사람이
// 무엇부터 해야 할지 모르게 만든다. 대화는 지금 할 일 하나만 보여 준다.
//
// 원칙 둘 — **묻지 않아도 되는 것은 묻지 않는다.** 공개 범위는 이 버전이
// `공개`만 지원하고, 자료 기준일은 오늘이며, 자료 역할은 AI가 제안한다.
// 사람에게 고르라고 할 이유가 없다.

import type { RunView } from '../types'

/** 지금 대화가 어디에 있는지. */
export type Stage =
  | 'ASK_PURPOSE'
  | 'ASK_SOURCES'
  | 'ASK_CONSENT'
  | 'WORKING'
  | 'ASK_QUESTION'
  | 'REVIEW'
  | 'DONE'
  | 'FAILED'

/** 지금 사람이 할 수 있는 일. */
export interface Prompt {
  stage: Stage
  /** 도우미가 할 말. */
  say: string
  /** 입력칸 안내 문구. */
  placeholder: string
  /** 글을 받을 수 있는지. */
  canType: boolean
  /** 누를 버튼. */
  choices?: { label: string; value: string }[]
}

export const FIRST_PROMPT: Prompt = {
  stage: 'ASK_SOURCES',
  say: [
    '안녕하세요. 국회 법률 개정·개선 보도자료 초안을 만들어 드립니다.',
    '',
    '**무엇을 알리는지 한 줄 쓰고, 공식 자료를 그 아래 붙여 넣어 주세요.**',
    '여러 문서를 한꺼번에 붙이셔도 됩니다. 제가 알아서 나눕니다.',
    '',
    '자료에 없는 값은 초안에 쓰지 않습니다. 표결 수도 시행일도 자료에서 찾아 씁니다.',
    '뉴스 기사도 괜찮습니다.',
  ].join('\n'),
  placeholder:
    '예)\n문화예술진흥법 일부개정법률안이 본회의를 통과했어요\n\n# 의안정보\n…자료를 여기 붙여 넣으세요…',
  canType: true,
}

/** 자료를 더 받을 때. 처음 물음과 달리 설명을 반복하지 않는다. */
export const ASK_SOURCES: Prompt = {
  stage: 'ASK_SOURCES',
  say: '자료를 붙여 넣어 주세요.',
  placeholder: '자료를 붙여 넣으세요…',
  canType: true,
}

export function askConsent(count: number): Prompt {
  return {
    stage: 'ASK_CONSENT',
    say: [
      `자료 ${count}건으로 읽었습니다. 초안을 만들까요?`,
      '',
      '지금은 **연습용 가짜 AI**라 인터넷으로 나가지 않고 비용도 0원입니다.',
    ].join('\n'),
    placeholder: '',
    canType: false,
    choices: [
      { label: '네, 만들어 주세요', value: 'START' },
      { label: '자료를 더 넣을게요', value: 'MORE' },
    ],
  }
}

export const WORKING: Prompt = {
  stage: 'WORKING',
  say: '',
  placeholder: '',
  canType: false,
}

/** 사람이 답해야 넘어가는 질문 하나. */
export interface Question {
  code: string
  say: string
  choices: { label: string; value: string }[]
}

/** 초안이 나온 뒤. 이제 고쳐 달라고 하거나 확인을 마칠 수 있다. */
export function reviewPrompt(run: RunView): Prompt {
  const left = run.unreviewed_fact_ids?.length ?? 0
  const say =
    left > 0
      ? [
          '초안이 나왔습니다. 아래에서 보실 수 있습니다.',
          '',
          `내려받기 전에 **사실 ${left}건이 자료와 맞는지** 봐 주셔야 합니다.`,
          '아래 목록에서 한 번에 확인하실 수 있습니다.',
          '',
          '문장을 고치고 싶으시면 여기에 적어 주세요.',
          '예: 순서를 바꿔 주세요',
        ].join('\n')
      : [
          '확인이 끝났습니다. 이제 내려받으실 수 있습니다.',
          '',
          '더 고치고 싶으시면 여기에 적어 주세요.',
        ].join('\n')
  return {
    stage: 'REVIEW',
    say,
    placeholder: '예: 순서를 바꿔 주세요',
    canType: true,
  }
}

/** 자료가 부족하거나 물어볼 것이 있을 때. */
export function issuePrompt(run: RunView): Prompt | null {
  const issues = run.issues ?? []
  if (issues.length === 0) return null

  // 최종 의결문이 완전한지 묻는 질문. 이 답이 없으면 초안을 만들지 않는다.
  const finalText = issues.find(
    (i) => i.subject === 'FINAL_TEXT_COMPLETENESS_CONFIRMATION_REQUIRED',
  )
  if (finalText !== undefined) {
    return {
      stage: 'ASK_QUESTION',
      say: [
        '하나만 확인해 주세요.',
        '',
        finalText.message,
        '',
        '이 질문은 사실이 맞는지 승인하는 것이 아닙니다.',
        '넣으신 자료에 개정문과 부칙이 **처음부터 끝까지** 들어 있는지만 봐 주세요.',
      ].join('\n'),
      placeholder: '',
      canType: false,
      choices: [
        { label: '네, 끝까지 들어 있습니다', value: 'FINAL_TEXT_YES' },
        { label: '아니요, 자료를 다시 넣을게요', value: 'RESTART' },
      ],
    }
  }

  // 자료 역할 확인. **한 자료씩 묻는다.**
  //
  // 서버는 못 알아본 자료를 한꺼번에 알려 준다. 그것을 그대로 쏟으면 글이
  // 열 줄 넘게 나오고 사람은 무엇을 해야 할지 모른다. 하나씩 묻고, AI가
  // 제안한 후보를 **버튼으로** 보여 준다. 사람은 근거를 보고 누르기만 한다.
  const roleQuestion = roleQuestionFor(run)
  if (roleQuestion !== null) return roleQuestion

  // 그 밖의 보완 안내는 그대로 옮기고 자료를 더 받는다.
  return {
    stage: 'ASK_SOURCES',
    say: [
      '이대로는 초안을 만들 수 없습니다. 이유는 이렇습니다.',
      '',
      ...issues.map((i) => `· ${i.message}`),
      '',
      '자료를 더 넣어 주시면 다시 해보겠습니다.',
    ].join('\n'),
    placeholder: '자료를 붙여 넣고 보내기…',
    canType: true,
  }
}

/** AI가 후보를 **하나만** 낸 자료는 묻지 않고 그대로 쓴다.
 *
 * 사람에게 "이게 의안정보 맞나요?"라고 묻는 것은, 답이 하나뿐일 때는
 * 확인이 아니라 **일거리**다. 그냥 쓰고 무엇으로 봤는지 알려 주면 된다.
 * 틀렸으면 사람이 그때 말한다.
 */
export function autoRoles(run: RunView): Record<string, string> {
  const chosen: Record<string, string> = {}
  for (const source of run.sources ?? []) {
    if (source.role !== 'UNKNOWN') continue
    const candidates = (run.role_choices ?? []).filter(
      (c) => c.source_id === source.source_id && c.role.length > 0,
    )
    const roles = new Set(candidates.map((c) => c.role))
    if (roles.size === 1) chosen[source.source_id] = [...roles][0]
  }
  return chosen
}

/** 후보가 여럿이라 사람이 골라야 하는 자료 **하나**를 묻는다. */
export function roleQuestionFor(run: RunView): Prompt | null {
  const auto = autoRoles(run)
  const unknown = (run.sources ?? []).filter(
    (s) => s.role === 'UNKNOWN' && auto[s.source_id] === undefined,
  )
  if (unknown.length === 0) return null

  const target = unknown[0]
  const choices = (run.role_choices ?? []).filter((c) => c.source_id === target.source_id)

  const lines = [
    `“${target.display_name}”이(가) 어떤 자료인지 알려 주세요.`,
    unknown.length > 1 ? `(모르는 자료가 ${unknown.length}건 남았습니다)` : '',
    '',
  ]
  if (choices.length > 0) {
    lines.push('제가 본 것은 이렇습니다. 근거를 보고 골라 주세요.', '')
    for (const choice of choices) {
      lines.push(`· ${choice.role_label} — “${choice.evidence_quote.slice(0, 60)}”`)
    }
  } else {
    lines.push('무엇인지 알아보지 못했습니다. 아래에서 골라 주세요.')
  }

  return {
    stage: 'ASK_QUESTION',
    say: lines.filter((l, i) => !(l === '' && lines[i - 1] === '')).join('\n'),
    placeholder: '',
    canType: false,
    choices: [
      ...choices.map((c) => ({
        label: c.role_label,
        value: `ROLE:${target.source_id}:${c.role}`,
      })),
      { label: '이 자료는 빼 주세요', value: `DROP:${target.source_id}` },
    ],
  }
}

/** 초안을 못 만들고 멈췄을 때. */
export function failurePrompt(run: RunView): Prompt {
  const failure = run.failure
  const findings = run.validation_findings ?? []
  return {
    stage: 'FAILED',
    say: [
      failure?.message ?? '초안을 만들지 못했습니다.',
      ...(findings.length > 0
        ? ['', '막힌 이유는 이렇습니다.', ...findings.slice(0, 5).map((f) => `· ${f.message}`)]
        : []),
      '',
      failure?.next_action ?? '자료를 보완해 새로 시작해 주세요.',
    ].join('\n'),
    placeholder: '',
    canType: false,
    choices: [{ label: '처음부터 다시 하기', value: 'RESTART' }],
  }
}

/** 서버 상태를 보고 지금 무엇을 할지 정한다. */
export function promptFor(run: RunView): Prompt {
  if (run.state === 'FAILED') return failurePrompt(run)
  if (run.state === 'NEEDS_INPUT') {
    return issuePrompt(run) ?? { ...ASK_SOURCES, say: '자료를 더 넣어 주세요.' }
  }
  if (run.state === 'REVIEW_READY' || run.state === 'DRAFT_READY') return reviewPrompt(run)
  return WORKING
}
