# 0013. 웹 한글 기안기 엔진 — rhwp (Node 사이드카)

- **상태**: 채택
- **날짜**: 2026-09-08
- **관련**: ADR-0007(OCR·산출), HWPX 스킬 노트(docs/notes/hwpx-agent-skills.md),
  한글 에이전트(tools/hwp-agent), 사용자 확정(2026-09-08)

## 맥락

행정문서(주 트랙 = 대학혁신지원사업 등 국고사업 계획서)는 대부분 한글(.hwp)
양식이다. 초안을 서버에서 무인 대량 생성하려면 한컴 설치 없이 .hwp/.hwpx를
읽고 쓰는 엔진이 필요하다. 기존 python-hwpx는 HWPX만 다루고 .hwp 바이너리를
못 읽는다.

## 검증 (2026-09-08, 실측)

rhwp(edwardkim/rhwp, Rust+WASM, MIT, npm `@rhwp/core` v0.8.6)를 맥에서
실측했다.
- 실제 관공서 .hwp 3건(이력서 양식·응시지원서·35쪽 매뉴얼) 파싱 성공:
  텍스트·페이지·표 추출 정상.
- .hwp → .hwpx 무손실 변환 확인(이력서 양식: 재파싱 텍스트가 원본과 동일).
- 편집 API 풍부: insertText/insertTextInCell/createTable/applyCharFormat/
  applyCellStyle/exportHwpx/renderPageSvg/renderPageHtml 등.
- 한계: v1.0 이전(조판 엔진 체계화 중), HML 일부만 지원. **createEmpty(빈
  문서 처음부터)는 기본 서식 ID 미등록으로 export 실패** — 양식 기반이 정답.

## 결정

웹 한글 기안기 엔진으로 **rhwp를 채택**한다. 문서 생성은 "빈 문서 짓기"가
아니라 **양식(.hwp/.hwpx)을 열어 채우기**를 기본으로 한다(사용자: "양식도
있고 주로 채워넣고 비슷하다"). 이는 createEmpty의 서식 문제를 피하고 실무
흐름과도 맞는다.

통합은 **Node 사이드카 서비스**로 한다.
- 작은 Node 프로세스가 rhwp를 담당: 양식 열기 → 필드/셀 채우기 → .hwpx/.hwp
  내보내기 → (필요 시) SVG/PDF 렌더. HTTP로 노출.
- 파이썬 플랫폼(FastAPI)은 HTTP로 호출한다. 역할 분리로 rhwp를 원본 그대로
  최신 유지, 파이썬 스택 오염 없음.
- 오프라인 설치는 npm 번들(에이전트 번들과 같은 방식).

단계:
1. (지금) 사이드카 골격 + 플랫폼 초안 내보내기를 rhwp 경로로 업그레이드
   (.hwp 양식 지원). 기존 python-hwpx는 폴백으로 유지.
2. (다음) 브라우저 WASM 편집기 임베드 — 대화창에서 초안 열어 지시로 수정
   (rhwp Action/Field API + 얇은 명령 계약 protocol.md 공유).

## 근거

- 서버측(사이드카)이 먼저인 이유: 자동화(신규 문서→자동 초안)는 무인 서버
  생성이 필수. 브라우저 방식만으론 대량·무인 생성 불가.
- rhwp는 .hwp까지 읽어 관공서 기존 문서를 포괄. MIT·WASM·무설치로 온프레미스
  적합. 미성숙(v0.8.x)은 양식 기반 채우기로 위험을 줄인다(렌더 조판보다
  텍스트·구조·셀 채우기가 안정적).

## 결과와 되돌리기 비용

- 잠그는 것: 초안 산출의 HWP 경로가 사이드카 HTTP 계약에 의존.
- 되돌리기: 사이드카는 독립 프로세스라 교체·중단이 쉽다. python-hwpx 폴백을
  남겨 rhwp 문제 시에도 HWPX 산출은 유지된다.
