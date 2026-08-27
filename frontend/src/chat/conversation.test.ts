import { describe, expect, it } from 'vitest'

import { askConsent } from './conversation'

/**
 * 초안을 만들지 묻는 자리는 **자료를 바깥으로 보내도 되냐고 묻는 자리**다.
 *
 * 예전에는 이 글이 어떤 경우에도 "연습용 가짜 AI"라고 적혀 있었다. 서버를
 * 진짜 AI로 켜도 화면은 그대로였다. 사람은 자료가 인터넷으로 나가는 줄
 * 모른 채 "네, 만들어 주세요"를 눌렀을 것이다.
 *
 * 아래 시험은 **글이 서버 값을 따라가는지**만 본다. 글자를 예쁘게 쓰는지가
 * 아니라, 진짜일 때 "가짜"라고 적지 않는지를 잰다.
 */
describe('초안 만들기 동의', () => {
  it('진짜 AI일 때 가짜라고 적지 않는다', () => {
    const 물음 = askConsent(2, 'live')
    expect(물음.say).not.toContain('가짜')
    expect(물음.say).toContain('진짜')
  })

  it('진짜 AI일 때 나가고 돈이 든다고 알린다', () => {
    const 물음 = askConsent(2, 'live')
    expect(물음.say).toContain('인터넷으로 나가고')
    expect(물음.say).toContain('비용')
  })

  it('가짜 AI일 때는 0원이라고 알린다', () => {
    const 물음 = askConsent(2, 'fake')
    expect(물음.say).toContain('가짜')
    expect(물음.say).toContain('0원')
  })

  it('모르면 모른다고 한다', () => {
    // 서버에 아직 못 물어봤는데 "가짜"라고 단정하면, 진짜일 때 거짓말이 된다.
    const 물음 = askConsent(2, null)
    expect(물음.say).not.toContain('0원')
    expect(물음.say).toContain('확인하지 못했습니다')
  })

  it('자료 건수는 그대로 적는다', () => {
    expect(askConsent(3, 'fake').say).toContain('자료 3건')
  })
})
