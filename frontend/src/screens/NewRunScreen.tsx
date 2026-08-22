// 새 작업 화면 (README §2.9, §3.2).
// 사용자는 네 가지만 입력한다: 보도 목적 · 공식 자료 · 공개 범위 · 자료 기준일.
// 지원 절차 단계는 서버가 고정하며 읽기 전용으로 보여준다.

import { useState } from 'react'
import type { Bootstrap, SourceDraft } from '../types'

interface Props {
  bootstrap: Bootstrap
  submitting: boolean
  errorMessage: string | null
  onSubmit: (input: {
    purpose: string
    disclosure: string
    basisDate: string
    announcementSubject: string
    sources: SourceDraft[]
    transferConfirmed: boolean
  }) => void
}

function newSource(index: number): SourceDraft {
  return { key: `src-${index}-${Date.now()}`, display_name: '', text: '', role: 'UNKNOWN' }
}

function today(): string {
  return new Date().toISOString().slice(0, 10)
}

export function NewRunScreen({ bootstrap, submitting, errorMessage, onSubmit }: Props) {
  const [purpose, setPurpose] = useState('')
  const [disclosure, setDisclosure] = useState('PUBLIC')
  const [basisDate, setBasisDate] = useState(today())
  const [announcementSubject, setAnnouncementSubject] = useState('')
  const [sources, setSources] = useState<SourceDraft[]>([newSource(1)])
  const [transferConfirmed, setTransferConfirmed] = useState(false)

  const totalChars = sources.reduce((sum, s) => sum + s.text.length, 0)
  const overLimit = totalChars > bootstrap.limits.max_total_chars

  function updateSource(key: string, patch: Partial<SourceDraft>) {
    setSources((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)))
  }

  function addSource() {
    setSources((prev) =>
      prev.length >= bootstrap.limits.max_sources ? prev : [...prev, newSource(prev.length + 1)],
    )
  }

  function removeSource(key: string) {
    setSources((prev) => (prev.length <= 1 ? prev : prev.filter((s) => s.key !== key)))
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    onSubmit({ purpose, disclosure, basisDate, announcementSubject, sources, transferConfirmed })
  }

  return (
    <form className="screen" onSubmit={handleSubmit} noValidate>
      <h2>새 보도자료 초안 만들기</h2>

      <section className="field">
        <label htmlFor="purpose">
          보도 목적 <span className="required">필수</span>
        </label>
        <p className="hint">
          독자가 무엇을 알아야 하는지 한두 문장으로 적어 주세요. 공식 자료에 없는 새 사실을
          적는 칸이 아닙니다.
        </p>
        <textarea
          id="purpose"
          rows={3}
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
          placeholder="예: 「문화예술진흥법」 일부개정법률안이 본회의를 통과한 결과를 알리려고 합니다."
        />
        <p className="counter">
          {purpose.length}자 (권장 {bootstrap.limits.purpose_min_chars}~
          {bootstrap.limits.purpose_max_chars}자)
        </p>
      </section>

      <section className="field">
        <label htmlFor="disclosure">
          공개 범위 <span className="required">필수</span>
        </label>
        <select
          id="disclosure"
          value={disclosure}
          onChange={(e) => setDisclosure(e.target.value)}
        >
          <option value="PUBLIC">공개</option>
          <option value="INTERNAL">내부</option>
          <option value="EMBARGO">엠바고</option>
        </select>
        {disclosure !== 'PUBLIC' && (
          <p className="warn">
            이 버전은 공개 자료만 처리합니다. 내부·엠바고를 고르면 진행되지 않습니다.
          </p>
        )}
      </section>

      <section className="field">
        <label htmlFor="basis-date">
          자료 확인 기준일 <span className="required">필수</span>
        </label>
        <p className="hint">제공한 자료의 상태를 직접 확인한 날짜입니다.</p>
        <input
          id="basis-date"
          type="date"
          value={basisDate}
          onChange={(e) => setBasisDate(e.target.value)}
        />
      </section>

      <section className="field">
        <label htmlFor="stage">지원 절차 단계</label>
        <input id="stage" type="text" value={bootstrap.procedure_stage_label} readOnly />
        <p className="hint">
          이 버전은 이 단계만 지원합니다. 다른 단계 자료는 초안을 만들지 않고 멈춥니다.
        </p>
      </section>

      <section className="field">
        <label htmlFor="announcer">발표 주체 (선택)</label>
        <p className="hint">
          공식 자료에 없을 때만 직접 적어 주세요. 사용자 확인 값으로 표시됩니다.
        </p>
        <input
          id="announcer"
          type="text"
          value={announcementSubject}
          onChange={(e) => setAnnouncementSubject(e.target.value)}
          placeholder="예: ○○○ 의원실"
        />
      </section>

      <section className="field">
        <div className="sources-head">
          <label>
            공식 자료 <span className="required">필수</span>
          </label>
          <span className={overLimit ? 'counter over' : 'counter'}>
            합계 {totalChars.toLocaleString()}자 / 최대{' '}
            {bootstrap.limits.max_total_chars.toLocaleString()}자
          </span>
        </div>
        <p className="hint">
          붙여 넣기와 UTF-8 TXT·Markdown만 지원합니다. PDF·DOCX·HWP는 본문을 복사해 붙여 넣어
          주세요.
        </p>

        {sources.map((source, index) => (
          <div className="source-card" key={source.key}>
            <div className="source-row">
              <input
                type="text"
                aria-label={`자료 ${index + 1} 이름`}
                value={source.display_name}
                onChange={(e) => updateSource(source.key, { display_name: e.target.value })}
                placeholder={`붙여넣기 자료 ${index + 1}`}
              />
              <select
                aria-label={`자료 ${index + 1} 역할`}
                value={source.role}
                onChange={(e) => updateSource(source.key, { role: e.target.value })}
              >
                {bootstrap.source_roles.map((role) => (
                  <option key={role.value} value={role.value}>
                    {role.label}
                  </option>
                ))}
              </select>
              {sources.length > 1 && (
                <button type="button" className="ghost" onClick={() => removeSource(source.key)}>
                  빼기
                </button>
              )}
            </div>
            <textarea
              rows={5}
              aria-label={`자료 ${index + 1} 본문`}
              value={source.text}
              onChange={(e) => updateSource(source.key, { text: e.target.value })}
              placeholder="공식 자료 본문을 붙여 넣으세요."
            />
            <p className="counter">{source.text.length.toLocaleString()}자</p>
          </div>
        ))}

        {sources.length < bootstrap.limits.max_sources && (
          <button type="button" className="ghost" onClick={addSource}>
            자료 추가 ({sources.length}/{bootstrap.limits.max_sources})
          </button>
        )}
      </section>

      <section className="field transfer">
        <h3>외부 AI 전송 안내</h3>
        <p>
          확인하면 아래 자료가 <strong>{bootstrap.external_ai.provider}</strong>의{' '}
          <strong>{bootstrap.external_ai.model}</strong> 모델로 전송됩니다.
        </p>
        <ul>
          {bootstrap.external_ai.sent_items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <p className="hint">{bootstrap.external_ai.notice}</p>
        <label className="check">
          <input
            type="checkbox"
            checked={transferConfirmed}
            onChange={(e) => setTransferConfirmed(e.target.checked)}
          />
          위 내용을 읽었고, 공개 자료만 넣었음을 확인합니다.
        </label>
        {!transferConfirmed && (
          <p className="hint">확인 전에는 외부 AI를 한 번도 부르지 않습니다.</p>
        )}
      </section>

      {errorMessage && <p className="error" role="alert">{errorMessage}</p>}

      <button type="submit" className="primary" disabled={submitting}>
        {submitting ? '자료 확인 중…' : '자료 확인 시작'}
      </button>
    </form>
  )
}
