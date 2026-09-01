# 「짜이미」 ZZAIMY — 캡스톤디자인 구현

> **공고를 날실로, 우리의 실적을 씨실로.**

영남이공대학교 국고사업 계획서 작성 지원 시스템의 캡스톤 구현 저장소.
기준 문서는 [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md)이며, 모든 판단은 여기서 출발한다.
Phase 0 리서치 원본은 [mrgrit/zzaimy](https://github.com/mrgrit/zzaimy)에서 가져왔다.

## ⚠️ 커밋 전 반드시 확인

이 저장소는 **개인정보와 기관 내부정보가 포함된 문서**를 다루는 시스템의 코드를 담는다.

- **저장소는 Private으로 유지한다.**
- 실제 문서·파싱 결과·실적 카드는 `data/` 아래에만 두며 `.gitignore`로 차단돼 있다.
- 한 번 커밋된 파일은 이후 삭제해도 git 이력에 남는다. `git add` 전에 `git status`를 확인할 것.
- 발표·시연에는 비식별화를 통과한 `data/demo/` 세트만 사용한다.

## 현재 상태

| 단계 | 내용 | 상태 |
|---|---|---|
| P0 | 기술 리서치 17건 | ✅ 완료 (upstream) |
| P1 | 아키텍처 설계 5종 | 🔨 작성 중 |
| P2~ | 구현 | ⏸ 대기 |

일정은 [`docs/capstone-plan.md`](docs/capstone-plan.md) — 13주 압축안(2026-09-01 ~ 11-30).

## 문서

| 문서 | 내용 |
|---|---|
| [PROJECT_BRIEF.md](PROJECT_BRIEF.md) | **기준 문서** — 목표·제약·설계 원칙·금지사항 |
| [docs/capstone-plan.md](docs/capstone-plan.md) | 캡스톤 13주 실행계획, 삭감 범위, 블로커 |
| [docs/business-plan.md](docs/business-plan.md) | 사업기획서 (원안 10개월 계획) |
| [research/00-summary.md](research/00-summary.md) | 기술 스택 추천 — 여기부터 읽을 것 |
| [research/02-system-overview.md](research/02-system-overview.md) | 시스템 조감도·유즈케이스 |

P1 산출물(작성 예정): `architecture.md` · `model-plan.md` · `eval-plan.md` · `pilot-plan.md` · `risks.md`

## 디렉터리 구조

```
src/zzaimy/
  ingest/     파싱·OCR → PII 마스킹 → 계열 분류·메타데이터
  index/      청킹 → 이중 벡터화(dense + Kiwi sparse) → Qdrant 적재
  extract/    실적 카드 구조화 추출 (xgrammar + 원문 대조)
  retrieve/   하이브리드 검색(RRF) + 리랭킹
  generate/   공고 스키마 추출 + 섹션별 생성 루프
  verify/     검증기 3종 — 수치 대조 · 배점 커버리지 · 예산 계산
  eval/       평가 하네스 (베이스라인 대비 개선폭)

configs/      모델·파이프라인 설정 (학습 YAML = 모델 카드 재료)
scripts/      배치 실행 진입점
models/cards/ 모델 카드 (가중치는 커밋하지 않음)
data/         전부 gitignore — 실제 문서는 여기서 나가지 않는다
```

## 모델 산출물 (축 B)

| 모델 | 베이스(1안) | 역할 |
|---|---|---|
| `ZZAIMY-Embed` | KURE-v1 | 도메인 특화 임베딩 |
| `ZZAIMY-Rerank` | bge-reranker-v2-m3 | 리랭킹 |
| `ZZAIMY-Writer` | Qwen3.5-35B-A3B (MoE) | 섹션 생성 (QLoRA SFT) |
| `ZZAIMY-Extract` | Qwen3-4B | 실적 카드 추출 |

**절대 규칙**: 지식 주입 금지(능력 학습만) · P3 베이스라인 측정 후에만 학습 시작.

## 대외 표현 지침 (브리프 7.6)

- 사용 가능: "국고사업 문서 도메인 특화 모델 개발", "오픈웨이트 기반 도메인 적응", "검색 정확도 N%p 개선"
- 사용 금지: "자체 개발 LLM", "독자 파운데이션 모델", "from scratch 학습"

발표 자료와 논문 문구에도 동일하게 적용한다.

## 명명 규칙

「짜임」 + 「이」 → [짜이미]. Y는 영남이공대학교의 이니셜을 겸한다.
`ZZAIMY` / `zzaimy` 외의 표기는 사용하지 않는다.
