// 한 덩이로 붙여 넣은 글을 자료 여러 개로 나눈다.
//
// 전에는 자료를 하나씩 보내게 했다. 네 개면 네 번 보내야 했다.
// 사람은 보통 **여기저기서 복사한 것을 한꺼번에 붙인다.** 그 방식에 맞춘다.
//
// 나누는 기준은 사람이 실제로 쓰는 모양이다.
//
// - `# 제목`처럼 머리글로 시작하는 줄
// - 빈 줄 두 개 이상으로 확실히 끊은 자리
//
// 잘못 나눠도 큰일이 나지 않는다. 자료가 몇 덩이든 검사는 똑같이 돈다.
// 다만 너무 잘게 나누면 근거를 찾는 데 불리하므로 짧은 조각은 앞에 붙인다.

/** 이보다 짧은 조각은 앞 자료에 붙인다. 혼자서는 자료 구실을 못 한다. */
const MIN_CHARS = 60

export function splitSources(text: string): string[] {
  const body = text.replace(/\r\n/g, '\n').trim()
  if (body.length === 0) return []

  // 머리글로 나눈다. 사람이 문서마다 제목을 달아 붙이는 가장 흔한 모양이다.
  //
  // **가장 얕은 단계만** 자른다. `#`로 문서를 나누고 `##`로 그 안을 나눈
  // 글에서 둘 다 자르면 한 문서가 여러 조각으로 흩어진다. 자료가 잘게
  // 쪼개지면 근거를 찾을 때 불리하다.
  let pieces: string[] | null = null
  for (const level of [1, 2, 3]) {
    const mark = '#'.repeat(level)
    const found = body.match(new RegExp('^' + mark + ' .+$', 'gm')) ?? []
    if (found.length >= 2) {
      pieces = body.split(new RegExp('\\n(?=' + mark + ' )', 'g'))
      break
    }
  }
  // 머리글이 없으면 빈 줄 두 개 이상으로 나눈다.
  pieces ??= body.split(/\n{3,}/g)

  const out: string[] = []
  for (const piece of pieces.map((p) => p.trim()).filter((p) => p.length > 0)) {
    if (piece.length < MIN_CHARS && out.length > 0) {
      out[out.length - 1] = `${out[out.length - 1]}\n\n${piece}`
    } else {
      out.push(piece)
    }
  }
  return out.length > 0 ? out : [body]
}
