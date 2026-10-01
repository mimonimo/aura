# ZZAIMY (짜이미)

영남이공대학교 행정문서 처리 플랫폼. 국고사업 계획서 초안, 채용·입학 서류 검토,
문서 추출(OCR)을 하나의 파이프라인에서 다룬다. 산출물은 초안이며 최종 판단은 담당자가 한다.

문서 분석·생성은 교내 로컬 모델로 처리한다. 학교 Google Workspace는 계정 권한 안에서
문서 협업에 사용한다. 운영 VM은 웹·PostgreSQL·검색, 토르 2대는 실시간 모델,
DGX는 실문서 원본 보관·일괄 처리·학습을 맡는다. 배경과 설계 원칙은
[PROJECT_BRIEF.md](PROJECT_BRIEF.md), 지금 구성과 접속은 [docs/HANDOFF.md](docs/HANDOFF.md).

## 처리 흐름

```
공고 → 파싱 → 요구사항·배점 추출
                    ↓
문서 → 파싱 → 권한·사업·문서 맥락 분류 → 색인(어휘+조밀)
                    ↓
        섹션별: 근거 검색 → 리랭킹 → 생성 → 수치 검증
                    ↓
        배점 누락 검사 → 예산 검산 → 출처 달린 초안
```

검색은 Kiwi 어휘 순위와 임베딩 순위를 RRF로 합쳐 후보 20개를 만들고, 교차인코더로 재정렬한다.
생성된 수치는 모두 인출된 근거에 있어야 하며, 없으면 초안에 넣지 않는다.
내부 문서의 일괄 PII 마스킹은 필수 단계가 아니다. 내부 마스킹은 기본 꺼짐이며
설정에 따른다. 접근 권한과 학습 사본의 개인정보 검수·승인 관문은 별도로 유지한다.
실적 카드·사업 그래프는 확장 중이며 원문 전체 분석이나 Writer 학습 완료를 뜻하지 않는다.

## 구조

```
src/zzaimy/
  ingest/     파싱 · 처리 정책 · 문서 분류
  index/      청킹 · 벡터화
  extract/    실적 카드 추출
  retrieve/   하이브리드 검색
  generate/   공고 스키마 추출 · 섹션 생성
  verify/     수치 대조 · 배점 커버리지 · 예산 계산
  eval/       평가 하네스
  app/        FastAPI 플랫폼(화면·DB·파이프라인·개발자 도구)
  graph/      지식 그래프
configs/      학습 설정 · 서빙 구성 기록
scripts/      실행 진입점(번호가 순서) — 서빙·학습본 이관·측정·반입·점검·배포
tools/        이전 도구 코드(Windows COM 에이전트·installer는 폐기, ADR-0034)
docs/         설계·측정·결정(decisions/)·논문(paper/)·주간 보고·모델 카드
research/     Phase 0 기술 조사(참고 기록)
data/         산출물·문서 (저장소에 포함하지 않음)
```

## 모델

오픈웨이트 모델의 도메인 적응이다. 네 가지를 쓴다.

| 이름 | 베이스 | 역할 | 현재 |
|---|---|---|---|
| ZZAIMY-Embed | KURE-v1 | 조밀 검색 | v2 적용 (ADR-0021) |
| ZZAIMY-Rerank | bge-reranker-v2-m3 | 후보 재정렬 | v1 적용 (ADR-0020) |
| ZZAIMY-Writer | Qwen3.8-27B | 섹션 생성 · 이미지 판독 · 검토 · 대화 | 베이스 서빙(NVFP4, ADR-0023), 학습은 실물 문서 뒤 |
| ZZAIMY-Extract | Qwen3.8-27B (Writer 공용) | 실적 카드 추출 | 예정 — 소형 모델은 처리량이 문제될 때만(ADR-0026) |

학습은 베이스라인을 먼저 재고 시작한다. 계획은 [docs/model-plan.md](docs/model-plan.md),
검색 품질 측정은 [docs/llm-rerank-eval.md](docs/llm-rerank-eval.md).

## 기능

- 업무 영역별 프로젝트 관리(지침·기준 연결·마감일)
- 문서 접수 → 검토 → 판정, 국고사업 계획서 초안·재작성
- 문서 추출: 스캔·이미지는 비전 모델 판독, 대형 PDF는 MinerU + 오타 교정.
  표는 병합 셀 유지, 그림은 제자리 배치(ADR-0007·0016)
- 기준 문서 저장소와 하이브리드 검색, 근거 인용 채팅
- Google Docs 작업본 공동 편집과 원본 서식 기반 HWPX 산출(ADR-0034·0035)

## 데이터·학습 상태 (2026-10-01)

실문서 수령·처리가 진행 중이다. 각 사업은 독립적으로 분석하고 근거가 있는 관계만
연결한다. 여러 사업의 데이터를 함께 SFT하더라도 질문·앞 대화·입력 근거·답변에
사업·연차·문서 맥락이 유지되어야 한다. 생성 후보와 승인 학습본은 구분하며,
Writer 학습은 독립 검수·평가셋 분리·베이스라인 확인 뒤 진행한다.
생성·검수 계약은 [dataset-authoring.md](docs/dataset-authoring.md), 구현·배포 여부는
[공동 작업 기록](docs/collaboration/codex.md)과 [현재 작업](docs/dev-now.md)을 확인한다.

## 실행

```
pip install -e .
python -m zzaimy.app.main
pytest -q
```

검색·추출은 색인과 파서 모델이, 검토·초안은 LLM 연결(개발자 > 연결)이 있어야 동작한다.
없으면 해당 기능만 꺼진 채 뜬다. 환경변수 예시는 `.env.example`.

## 일정

13주(2026-09-01 ~ 11-30). 계획은 [docs/capstone-plan.md](docs/capstone-plan.md),
결정은 [docs/decisions/](docs/decisions/).
