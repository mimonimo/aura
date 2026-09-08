# 한글(HWPX) 에이전트 스킬 수집 노트

수집일 2026-09-08. 출처: 사용자 제공 링크(wikidocs.net/379674 → 원문
dbhyeong.github.io/blog/hwpx-skill-form-fill-hands-on)와 관련 저장소 조사.
용도: 한글 실시간 편집 에이전트(HANDOFF §4)와 hwpx 산출물 품질 고도화의
설계 재료.

## HWPX 다루기의 원리 (실전 기록에서 배운 것)

HWPX는 OWPML 표준의 ZIP+XML 컨테이너다. 본문은 Contents/section0.xml의
hp:t 태그, 서식은 header.xml에 ID로 정의되고 charPrIDRef로 참조된다.
따라서 텍스트만 바꾸면 서식이 유지된다.

실무 핵심 세 가지.

1. 원시 ZIP 복사. 일반 재압축은 CRC·플래그가 바뀌어 한컴이 "손상"으로
   인식할 수 있다. 바뀌지 않은 엔트리는 바이트 그대로 복사해야 한다.
2. 글자 예산(max_chars). 칸마다 안전 수용 글자 수를 셀 폭×글자 크기로
   계산해 쓰기 전에 검사한다 — 넘치면 쪽수가 밀리는 사고를 사전 차단.
3. 3중 가드. validate(ZIP·XML 무결성) → page_guard(글자 예산·텍스트
   드리프트) → content_guard(placeholder 잔재 금지·필수어 포함). 우리
   품질 체계의 2계층(자기 검증)과 같은 사상이다.

작업 흐름: 양식 생성 → 슬롯 추출(slots.json, 칸별 max_chars) → 값 매핑
(values.json) → 구조 보존 편집. 함정: lxml 4.9.3 조합 버그, .hwp는 미지원
(.hwpx로 저장 변환 필요), 양식 채우기 시 page_guard는 복원 모드가 기본이라
플래그 조정 필요.

## 생태계 (2026-09 기준)

| 저장소 | 성격 |
|---|---|
| airmang/python-hwpx | 순수 파이썬 HWPX 엔진 — **우리가 이미 쓰는 것**(6.3, 산출물 내보내기) |
| airmang/hwpx-skill (hwpx-plugin) | python-hwpx 저자의 공식 에이전트 스킬. 추출·양식 채우기·생성·복구·mail merge·신구대조표. `claude plugin install hwpx-plugin@hwpx`. python-hwpx-automation(MCP 서버) 포함, 2.1.0(2026-09-07) |
| jkf87/hwpx-skill | 마크다운/텍스트/URL → HWPX 생성 스킬 |
| ai-public-peasant/hwpx-rekian | 행정문서(재기안) 자동 작성 특화 |
| devxoul/hwpilot | 문단·표·텍스트박스·이미지 읽기/검색/편집 |

## 우리 프로젝트 적용점

- 초안 hwpx 내보내기(draft_export)가 이미 python-hwpx를 쓴다 — 글자 예산과
  원시 ZIP 복사 기법은 지금 코드에도 점검해볼 가치가 있다.
- 한글 편집 에이전트(Windows) 구현 시 후보 경로 두 갈래:
  ① hwpx-plugin(공식 스킬 + MCP 서버)을 에이전트에 붙여 HWPX 직접 편집 —
  한컴오피스 없이 서버측 처리 가능. ② 한컴 자동화(COM)로 실행 중인 한글을
  조작 — 실시간 편집감은 있으나 Windows 전용·불안정.
  ①을 기본, ②는 화면 동기화가 꼭 필요할 때만.
- 공문 양식 채우기(슬롯 추출→값 매핑→3중 가드)는 우리 "공고 양식 기반
  초안 산출"과 정확히 같은 문제 — 슬롯·글자 예산 개념을 산출물 파이프라인에
  도입하면 양식 깨짐을 사전 차단할 수 있다.
