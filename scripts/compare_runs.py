"""여러 번 돌린 결과를 gold와 함께 한 표에 놓는다.

모델·설정을 바꿔 가며 견줄 때 쓴다. 표를 매번 즉석에서 짜면 **세는 방법이
조금씩 달라져** 결과를 신뢰할 수 없다. 실제로 한 번 그랬다 — 못 채운 칸의
안내문("자료가 없어 못 씀")을 글자 수에 넣어 세는 바람에, 아무것도 못 쓴
설정이 40%를 받았다.

그래서 규칙을 여기 한 곳에 못 박는다.

- **채운 칸(`○`)만 센다.** 못 채운 칸(`⚠`)의 안내문은 글이 아니다.
- 숫자는 검사기와 **같은 자**(`gate._numbers_in`)로 센다.
- **문장을 반드시 함께 싣는다.** 숫자만 보면 틀린 결정을 한다 — sol/max가
  숫자로는 gold의 123%였지만 읽어 보면 목록 나열에 문장이 잘려 있었다.

    python scripts/compare_runs.py tmp/cmp-terra-low.md tmp/cmp-sol-max.md
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.audit.compare import _cell, split_gold_body  # noqa: E402
from app.audit.contracts import SLOT_LABELS  # noqa: E402
from app.audit.gate import _numbers_in  # noqa: E402
from app.audit.slots import SLOT_ORDER  # noqa: E402

GOLD = ROOT / "references" / "보도자료예시" / "01_역피라미드_태양광_산림훼손.txt"

#: 채운 칸만 읽는다. `⚠`(못 채움)은 일부러 뺀다.
_FILLED = re.compile(r"^○\s*\((?P<label>[^)]+)\)\s*(?P<body>.*)$")


def read_run(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        matched = _FILLED.match(line)
        if matched:
            out[matched.group("label")] = matched.group("body").strip()
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="+", help="견줄 결과물 파일들")
    parser.add_argument("--out", default=str(ROOT / "tmp" / "모델비교.md"))
    args = parser.parse_args()

    order = [SLOT_LABELS[slot] for slot in SLOT_ORDER]
    raw = GOLD.read_text(encoding="utf-8")
    columns: list[tuple[str, dict[str, str]]] = [
        ("gold (사람)", split_gold_body(raw[raw.index("○") :], order))
    ]
    for run in args.runs:
        path = Path(run)
        columns.append((path.stem.replace("cmp-", ""), read_run(path)))

    lines = [
        "# 사람이 쓴 것과 설정별 초안",
        "",
        "## 문장 비교",
        "",
        "| 칸 | " + " | ".join(name for name, _ in columns) + " |",
        "|---|" + "---|" * len(columns),
    ]
    totals = {name: [0, 0] for name, _ in columns}
    for label in order:
        cells = []
        for name, run in columns:
            text = run.get(label, "")
            totals[name][0] += len(text)
            totals[name][1] += len(_numbers_in(text))
            cells.append(_cell(text) or "⚠ 못 씀")
        lines.append(f"| **{label}** | " + " | ".join(cells) + " |")

    base = totals[columns[0][0]][1] or 1
    lines += [
        "",
        "## 채움 정도",
        "",
        "| 설정 | 분량 | 숫자 | gold 대비 |",
        "|---|---:|---:|---:|",
    ]
    for name, value in totals.items():
        if value[0] == 0:
            # **0자와 "실패"는 다르다.** 한 칸도 못 썼다면 글이 짧은 것이
            # 아니라 아예 결과가 없는 것이다. 0%로만 적으면 "많이 썼는데
            # 내용이 없다"로 읽힌다.
            lines.append(f"| {name} | — | — | **출력 실패 (한 칸도 못 씀)** |")
            continue
        lines.append(
            f"| {name} | {value[0]}자 | {value[1]}개 | {round(value[1] / base * 100)}% |"
        )

    lines += [
        "",
        "> **숫자만 보고 고르지 말 것.** 위 문장 표를 반드시 함께 읽는다.",
        "> 숫자로 gold의 123%를 받고도 목록 나열에 문장이 잘린 설정이 있었다.",
        "",
    ]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"비교표: {out}")
    for name, value in totals.items():
        print(f"  {name:16} {value[0]:5}자 · 숫자 {value[1]:3}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
