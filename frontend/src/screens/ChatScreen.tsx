// 대화로 초안을 만드는 화면 (README §0.3, §2.8).
//
// 전에는 양식이었다. 자료 칸을 네 번 만들고, 역할을 13개 목록에서 고르고,
// 날짜를 고르고, 사실 10개를 하나씩 누르게 했다. 스무 번 넘게 눌러야 했다.
//
// README는 원래 이렇게 하라고 적어 두었다.
//
// > 비전공자에게 12가지 자료 역할을 **먼저 고르라고 하지 않는다.** 기본값은
// > `잘 모르겠음`이고, AI가 근거와 함께 쉬운 후보를 제안하면 사람이 고른다.
//
// 그래서 대화로 바꾼다. 사람은 **하고 싶은 말을 쓰고, 자료를 붙여 넣고,
// 물어보면 답한다.** 나머지는 프로그램이 한다.
//
// 안전은 하나도 낮추지 않는다. 뒤에서 도는 검사기는 그대로다. 바뀌는 것은
// **묻는 방법**뿐이다.

import { useEffect, useRef, useState } from 'react'

import type { RunView } from '../types'

/** `**이렇게**` 감싼 곳을 굵게 만든다.
 *
 * 사람에게 별표를 그대로 보여 주면 안 된다. 강조하려고 쓴 표시가 오히려
 * 글을 지저분하게 만든다. 화면에서 직접 걷어낸다.
 */
function bold(line: string) {
  const pieces = line.split(/\*\*(.+?)\*\*/g)
  return pieces.map((piece, i) => (i % 2 === 1 ? <strong key={i}>{piece}</strong> : piece))
}

/** 대화 한 줄. */
export interface Turn {
  id: string
  who: 'agent' | 'user'
  text: string
  /** 이 줄에 붙는 버튼. 누르면 다음으로 넘어간다. */
  choices?: { label: string; value: string }[]
}

interface Props {
  turns: Turn[]
  /** 사람이 지금 무엇을 하면 되는지. 입력칸의 안내 문구가 된다. */
  placeholder: string
  /** 지금 글을 받을 수 있는지. 처리 중에는 잠근다. */
  canType: boolean
  busy: boolean
  /** 입력칸에 미리 채워 줄 글. PDF에서 뽑은 글이 여기로 온다. */
  draftText: string
  onSend: (text: string) => void
  onChoose: (value: string) => void
  /** PDF를 올렸을 때. 뽑은 글을 입력칸에 넣어 준다. */
  onPickFile: (file: File) => void
  /** 초안이 나왔으면 함께 보여 준다. */
  run: RunView | null
  onNewRun: () => void
}

export function ChatScreen({
  turns,
  placeholder,
  canType,
  busy,
  draftText,
  onSend,
  onChoose,
  onPickFile,
  run,
  onNewRun,
}: Props) {
  const [text, setText] = useState('')
  const endRef = useRef<HTMLDivElement | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)

  // 밖에서 넣어 준 글(PDF에서 뽑은 것)을 입력칸에 올린다. **보내지는
  // 않는다.** 사람이 보고 고쳐야 한다.
  useEffect(() => {
    if (draftText.length > 0) setText(draftText)
  }, [draftText])

  // 새 말이 올라오면 아래로 따라간다. 사람이 스스로 내려야 하면 놓친다.
  //
  // 따라가기는 **있으면 좋은 것**이지 꼭 있어야 하는 것이 아니다. 브라우저가
  // 이 기능을 안 갖고 있어도 대화는 그대로 돌아가야 한다. 감싸지 않으면
  // 화면 전체가 죽는다.
  useEffect(() => {
    try {
      endRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' })
    } catch {
      // 따라가지 못해도 대화는 계속된다.
    }
  }, [turns.length, busy])

  function send() {
    const value = text.trim()
    if (value.length === 0 || !canType) return
    setText('')
    onSend(value)
  }

  const last = turns[turns.length - 1]
  const choices = last?.who === 'agent' ? last.choices : undefined

  return (
    <div className="chat">
      <ol className="turns">
        {turns.map((turn) => (
          <li key={turn.id} className={`turn ${turn.who}`}>
            <p className="who">{turn.who === 'agent' ? '보도자료 도우미' : '나'}</p>
            <div className="bubble">
              {turn.text
                .split('\n')
                .filter((line, i, all) => !(line === '' && all[i - 1] === ''))
                .map((line, i) => (line === '' ? <br key={i} /> : <p key={i}>{bold(line)}</p>))}
            </div>
          </li>
        ))}
        {busy && (
          <li className="turn agent">
            <p className="who">보도자료 도우미</p>
            <div className="bubble working">
              <p>보는 중입니다…</p>
            </div>
          </li>
        )}
        <div ref={endRef} />
      </ol>

      {choices !== undefined && !busy && (
        <div className="choices">
          {choices.map((choice) => (
            <button key={choice.value} type="button" onClick={() => onChoose(choice.value)}>
              {choice.label}
            </button>
          ))}
        </div>
      )}

      <div className="composer">
        <label className="visually-hidden" htmlFor="chat-input">
          할 말 적기
        </label>
        <textarea
          id="chat-input"
          rows={3}
          value={text}
          disabled={!canType || busy}
          placeholder={placeholder}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            // 줄바꿈은 Shift+Enter. 자료를 붙여 넣는 칸이라 Enter로 바로
            // 보내면 여러 줄짜리 자료를 넣다가 실수로 보낸다.
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
              event.preventDefault()
              send()
            }
          }}
        />
        <div className="composer-row">
          <p className="hint">
            Ctrl+Enter로도 보낼 수 있습니다.
            <br />
            <button
              type="button"
              className="link"
              disabled={!canType || busy}
              onClick={() => fileRef.current?.click()}
            >
              PDF에서 글자 가져오기
            </button>
          </p>
          <input
            ref={fileRef}
            type="file"
            accept="application/pdf,.pdf"
            className="visually-hidden"
            onChange={(event) => {
              const picked = event.target.files?.[0]
              // 같은 파일을 다시 골라도 동작하도록 값을 비운다.
              event.target.value = ''
              if (picked) onPickFile(picked)
            }}
          />
          <button type="button" disabled={!canType || busy || text.trim().length === 0} onClick={send}>
            보내기
          </button>
        </div>
      </div>

      {run !== null && (
        <p className="chat-foot">
          <button type="button" className="link" onClick={onNewRun}>
            처음부터 다시 하기
          </button>
        </p>
      )}
    </div>
  )
}
