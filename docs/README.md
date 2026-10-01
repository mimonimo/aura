# docs/

무엇이 어디 있는지만 적는다. 내용은 각 문서에 있다. 판단 기준은 저장소 루트의
`PROJECT_BRIEF.md`(v3), 작업 지침은 `CLAUDE.md`.

## 먼저 볼 것

| 문서 | 용도 |
|---|---|
| [HANDOFF.md](HANDOFF.md) | 인프라 현황·남은 일·이어받는 절차 |
| [dev-now.md](dev-now.md) | 지금 하는 일·다음 일 (화면에도 나온다) |
| [model-plan.md](model-plan.md) | 모델 4종 계획·학습 순서 |
| [llm-rerank-eval.md](llm-rerank-eval.md) | 검색·리랭커 측정 기록 |
| [quality-system.md](quality-system.md) | 품질 5계층 체계 |

## 설계·계획 문서

| 문서 | 용도 |
|---|---|
| [architecture.md](architecture.md) | 초기 설계와 현행 구현 구분(2026-10-01 점검) |
| [dataset-authoring.md](dataset-authoring.md) | 사업별 맥락 문답 생성·검수·승인 계약 |
| [document-work-flow.md](document-work-flow.md) | 실문서 반입·Google Docs 작업·산출 흐름 |
| [eval-plan.md](eval-plan.md) | 평가 계획(지표·분리 원칙) |
| [capstone-plan.md](capstone-plan.md) | 13주 실행계획 (구간별 현황) |
| [pilot-plan.md](pilot-plan.md) | 파일럿 계획 |
| [risks.md](risks.md) | 위험 관리 (§10 운영에서 드러난 위험) |
| [workflow.md](workflow.md) | 저장소·결정·보고 방식 |
| [business-plan.md](business-plan.md) | 원안 사업계획서(교수님, 참고 — 고치지 않는다) |

## 측정 기록 (날짜가 곧 유효 범위 — 최신 수치는 `data/platform/eval/` 과 실험-로그)

| 문서 | 무엇 | 비고 |
|---|---|---|
| [retrieval-baseline-mini.md](retrieval-baseline-mini.md) | 검색 미니 베이스라인 | 2026-09-04, 옛 코퍼스 |
| [retrieval-weight-sweep.md](retrieval-weight-sweep.md) | 하이브리드 가중 스윕 | 2026-09-04 |
| [rerank-baseline.md](rerank-baseline.md) | 리랭커 학습 전 베이스라인 | 2026-09-04 |
| [llm-rerank-eval.md](llm-rerank-eval.md) | 리랭커·LLM 리랭킹 평가 | 2026-09-20 |
| `data/platform/eval/retrieval-latest.json` (VM) | 검색 측정 산출물 — 실행 날짜·코퍼스·설정 확인 필수 | 과거 공개 코퍼스 성능을 현재 실문서 성능으로 쓰지 않음 |
| [embed-v0-report.md](embed-v0-report.md) | 임베딩 v0.0 리허설 | 2026-09-03, 미배포(이력) |
| [ocr-cer-bench.md](ocr-cer-bench.md) · [ocr-duel.md](ocr-duel.md) | 판독(OCR) 벤치·대결 | 2026-09-07/08 |
| [env-report.md](env-report.md) | DGX Spark 환경 점검 | 2026-09-01, Spark 는 사용 금지(이력) |
| `progress.json` | 기능 상태에서 계산한 진행률(화면용) | 수기 퍼센트 아님 |

## 폴더

| 폴더 | 무엇 |
|---|---|
| `decisions/` | ADR — 결정과 근거. 새 결정은 여기에 번호로 남긴다 |
| `model-cards/` | 학습한 모델의 카드(자료·방법·측정·한계) |
| `collaboration/` | Codex·Claude 공동 작업 기록과 규약 |
| `notes/` | 조사 메모·감사 결과·용어 정리 |
| `paper/` | 논문 원재료·실험 로그·기획서·발표 원고 (화면 '논문 자료') |
| `proposal/` | 2026-09-01 제안 시점 기획서 초안(이력) |
| `weekly/` | 주간 보고 양식(`양식.md`)과 수기 보고. 생성본은 VM `data/platform/weekly/` |
| `context/` | 누적된 작업 규율·선호 |
| `instructions/` | 지난 지시서(이력) |
| `archive/` | 기계가 뽑았던 지난 측정 보고서 |

`dev-changelog.md` 는 예전 변경 목록 사본이다 — 화면의 변경 이력은 2026-09-22부터 깃 커밋을 직접 읽는다.

## 규칙

- 측정 수치는 기계 산출물이 정본이다. 평가 스크립트는 `data/platform/eval/` 에 쓰고,
  저장소 사본은 `--report` 를 붙일 때만 갱신한다.
- 결정은 ADR로 남긴다. 문서 본문에서 결론만 바꾸고 근거를 남기지 않는 방식은 쓰지 않는다.
- 운영 접속 절차·자격 증명은 저장소에 두지 않는다.
- 기능 변경 완료 전 루트 안내·인수인계·관련 설계/실행 문서도 대조한다. 폐기 기능은
  현행 안내에서 제거하며, 과거 ADR·측정·주간 보고는 날짜가 있는 이력으로 보존한다.
  문서 수정·커밋·푸시·운영 배포는 서로 다른 상태로 기록한다.
