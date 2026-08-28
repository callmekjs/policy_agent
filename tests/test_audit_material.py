"""재료를 제목과 부제로 가르는 일.

실제 보도자료는 **한 줄이 화면 폭에서 갈린다.** gold가 그렇다 — 제목이 두
줄이고, 부제 하나도 두 줄로 갈려 있다.

    태양광 3년간 상암축구장          ← 제목 1
    6천 개 규모 산림 사라져           ← 제목 2

    -지역별로 전남(1,025ha), 경북(790ha),   ← 부제 시작
    전북(684ha)순으로 많이 훼손             ← 같은 부제의 이어진 줄

`-`로 시작하는 줄만 부제로 보면, 이어진 줄이 제목으로 붙고 부제는 잘린다.
처음 돌렸을 때 실제로 그렇게 나왔다.
"""

from __future__ import annotations

from app.audit.material import split_material

GOLD_MATERIAL = """태양광 3년간 상암축구장
6천 개 규모 산림 사라져

-3년간 베어진 나무만 233만 그루로 4,407ha의 산림 훼손

-지역별로 전남(1,025ha), 경북(790ha),
전북(684ha)순으로 많이 훼손

-윤 의원, “매해 산림훼손 기하급수적으로 증가 중, 미세먼지 필터인 산림 훼손 중단하고 즉각 복원하라”"""


def test_제목은_첫_부제_앞까지다() -> None:
    headline, _ = split_material(GOLD_MATERIAL)
    assert headline == "태양광 3년간 상암축구장\n6천 개 규모 산림 사라져"


def test_두_줄로_갈린_부제를_한_덩이로_붙인다() -> None:
    """처음 돌렸을 때 여기서 틀렸다."""
    _, subheads = split_material(GOLD_MATERIAL)

    assert len(subheads) == 3, subheads
    assert subheads[1] == "지역별로 전남(1,025ha), 경북(790ha), 전북(684ha)순으로 많이 훼손"


def test_이어진_줄이_제목으로_새지_않는다() -> None:
    """대조군. 위 시험이 통과해도 제목이 오염되면 결과물이 망가진다."""
    headline, _ = split_material(GOLD_MATERIAL)
    assert "684ha" not in headline, headline


def test_부제가_없으면_전부_제목이다() -> None:
    headline, subheads = split_material("제목 한 줄")
    assert headline == "제목 한 줄"
    assert subheads == []
