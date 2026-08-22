# 1차 테스트 자료 5건

이 폴더는 제품 코드가 아니라, 나중에 만든 프로그램이 안전하게 동작하는지 확인할 **시험 문제와 정답표**다.

> 상태: 설계 고정 완료 / 프로그램 실행 전  
> 적용 계약: `assembly_member_partial_amendment_plenary_v1@1.0.0`  
> 세트 버전: `assembly_press_draft_p1_test_set@1.2.1`

## 아주 쉽게 보면

| ID | 자료 | 프로그램이 해야 할 일 |
|---|---|---|
| `ACTUAL-PASS-001` | 실제 공개자료: 문화예술진흥법 일부개정안 원안가결 | 근거가 맞으면 DRAFT 초안을 만든다 |
| `ACTUAL-BLOCK-002` | 실제 공개자료: 퇴직급여법 위원회 대안 | 1차 범위보다 복잡하므로 초안을 만들지 않고 멈춘다 |
| `SYN-RISK-001` | 가상자료: 표결 날짜·찬성 수 충돌 | 어느 값도 고르지 말고 사람에게 묻는다 |
| `SYN-RISK-002` | 가상자료: 표결 전 연설문·필수 자료 누락 | 상정을 통과로 바꾸지 말고 필요한 자료를 요청한다 |
| `SYN-RISK-003` | 가상자료: 대안·부칙·시행 정보 | 정상본은 초안을 만들고, 한 곳씩 변조한 후보는 Gate에서 막는다 |

## 파일을 어떻게 읽나

- `catalog.json`: 반드시 실행할 fixture와 variant 목록이다.
- `fixture.schema.json`: 다섯 manifest가 지켜야 하는 공통 모양이다.
- `mutation.schema.json`: 위험을 한 번에 하나만 넣는 파일의 공통 모양이다.
- `fact_extraction_result.schema.json`: AI가 반환하는 검증 전 사실 후보·근거 ID의 모양과 개수·문자열 상한이다. 루트 object 안의 `result.anyOf`가 정상 결과와 `FACT_SCOPE_TOO_LARGE` 결과를 나누며, 범위 초과이면 쉬운 이유·빈 배열만 허용한다. API가 지원하지 않는 schema 키워드는 쓰지 않고, ID 중복·참조 관계는 Harness가 다시 검사한다. `provenance`·원문 위치·`protected`는 여기 넣지 않고 Harness가 검증 뒤 Fact 원장에 붙인다.
- `draft_candidate.schema.json`: 고정된 초안 후보가 지켜야 하는 모양이다.
- `manifest.json`: 입력 Source, 실행 순서, 예상 상태·Issue·Finding을 담은 기계용 정답표다.
- `sources/`: Agent에게 줄 문서 내용만 있다. 정답 힌트와 Gate 지시는 넣지 않는다.
- `mutations/`: 원문·후보 초안·수정 요청을 정확히 한 곳만 바꾸는 구조화 파일이다.
- `candidates/`: 위험한 초안을 시험하기 전에 사용할 정상 raw 사실 추출 후보와 정상 초안 후보를 담는다. raw 후보의 근거 일치·참조 무결성을 Harness가 확인해 Fact 원장으로 바꾸며, manifest가 ID·schema·해시로 연결한다.
- `oracle.md`: 비전공자가 읽는 쉬운 정답 설명이며 Agent에게 전달하지 않는다.
- `CHECKSUMS.sha256`: 설계가 고정된 뒤의 파일 해시다.

## 중요한 안전 규칙

- 모든 variant는 새 Run에서 시작하고 mutation을 0개 또는 1개만 적용한다.
- `ACTUAL-PASS-001`의 원안가결 파생은 소관위·법사위·본회의가 모두 원안가결일 때만 허용한다. 각 Source 해시는 자기 manifest 항목과만 비교하며 Source끼리 해시가 같다고 요구하지 않는다.
- 조문 수를 세는 Source는 `… 일부를 다음과 같이 개정한다.`부터 독립된 `부칙` 제목까지 경계가 있고, 본칙의 개정 지시문을 모두 담아야 한다. parser가 해석하지 못한 본칙 문자가 하나라도 남으면 성공으로 처리하지 않는다.
- `SYN-RISK-003`의 raw 사실 추출 후보에는 Run 입력의 발표 주체·보도 예정일을 중복해서 넣지 않는다. Harness가 `run_input`을 검증해 `SR3-F-ANNOUNCEMENT`·`SR3-F-RELEASE-DATE` Fact를 만든 뒤, raw 후보와 합쳐 canonical FactLedger를 구성하므로 정상 초안의 해당 Fact ID 참조는 끊어진 참조가 아니다.
- 한 Source에는 문서 1개와 역할 1개만 둔다.
- 실제 자료는 정확 발췌 위치와 공식 문서 ID를 기록한다.
- 실제·합성 fixture 모두 시험 전용이며 운영 loader는 `test_sets/` 경로와 `data_class`를 보고 거부한다.
- 합성 자료는 `supplied_as_official=false`다. 테스트 loader만 `SIMULATED_OFFICIAL`로 가정한다.
- 합성 자료를 운영 Fact, 좋은 문체 사례, 실제 다운로드에 사용하지 않는다.
- 사전 Gate 차단은 초안 0건, 최초 초안 Gate 실패는 위험 후보 비공개, 수정 실패는 이전 정상본 보존이 정답이다.
- 같은 Run에서 답할 수 있는 값 충돌과 새 공식 자료·다른 지원 유형이 필요한 문제를 구분한다. 뒤의 두 문제는 새 Run으로 시작한다.
- 자료 역할이 내용과 다르면 역할 Gate에서 먼저 멈추고, 그 때문에 따라오는 자료 누락 문제를 같은 Run에 중복 기록하지 않는다.

## 지금 완료된 것과 남은 것

- 완료: 실제 정상 1건, 실제 범위초과 1건, 합성 위험 family 3건의 설계와 원문·정답 분리.
- 완료: 모든 manifest를 `sources → variants → steps → expect` 형식으로 통일.
- 완료: 필요한 variant를 catalog에 고정하고 파일·schema·해시를 검사.
- 미완료: Harness와 Agent가 아직 없으므로 자동 시험은 한 번도 실행하지 않았다.
- 미완료: 구현 뒤 실제 출력이 각 `steps[].expect`와 같은지 확인해야 한다.

`ACTUAL-PASS-001`은 원안가결이고 변경 조문이 1개라 현재 범위에 맞는다. `ACTUAL-BLOCK-002`는 원안 11건을 합친 대안이고 완전 개정문에서 코드가 센 변경 최상위 조문도 6개라 안전하게 거절해야 한다. 성공 사례뿐 아니라 **멈춰야 할 때 정확히 멈추는 사례**도 함께 둔 이유다.
