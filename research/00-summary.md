# Phase 0 리서치 요약 — 「짜이미」 기술 스택 추천

> 조사 기간: 2026-08-15 · 상세 근거는 각 `01-{영역}.md`, 모델 트랙은 `50-model-track.md`, 사용자 확인 사항은 `99-open-questions.md`.
> 원칙: aarch64(DGX Spark) 1차 필터 · 한국어 성능 기준 · 라이선스 위험 보고 · 검증 불가 정보는 "미확인" 표기.

---

## 한눈에 보는 추천 스택

| 영역 | 추천 1안 | 대안 1안 | 결정 시점 |
|---|---|---|---|
| A. 문서 파싱/OCR | **MinerU 3.x** (VLM+파이프라인 이중 백엔드) | **Docling** (MIT, TableFormer) | Phase 2 초입 실물 bake-off로 확정 |
| B. RAG 프레임워크 | **Haystack 2.x** 얇은 조립 | LlamaIndex | Phase 1 |
| C. 지식 그래프 | **도입 안 함** (정형 DB로 충분) | (재검토 시 LightRAG) | Phase 6 조건부 |
| D. 임베딩 베이스 | **KURE-v1** (BGE-M3 한국어 파인튜닝) | BGE-M3 직접 파인튜닝 | Phase 3 베이스라인에서 A/B |
| D. 리랭커 베이스 | **bge-reranker-v2-m3** | Qwen3-Reranker-0.6B/4B | Phase 3 |
| E. 임베딩 학습 | **FlagEmbedding** (hn_mine 내장) | sentence-transformers | Phase 2 스모크 테스트 |
| F. LLM 파인튜닝 | **LLaMA-Factory** (NVIDIA 플레이북 등재) | Unsloth (Spark 공식 지원) | Phase 2 스모크 테스트 |
| G. 형태소/BM25 | **Kiwi** + Qdrant sparse 주입 | bm25s 인프로세스 | Phase 1 |
| H. 벡터 DB | **Qdrant** (필터 인식 HNSW, 하이브리드) | pgvector (1-스토어 단순화) | Phase 1 |
| I. 구조화 추출 | **vLLM xgrammar 강제 + LangExtract식 그라운딩** | Instructor | Phase 2 |
| J. LLM 서빙 | **vLLM** (DGX Spark 공식 지원) | SGLang | 확정 (검증 태그 고정) |
| K. Writer 베이스 | **Qwen3.5-35B-A3B** (MoE, Apache 2.0) | Qwen3.5-122B-A10B 승격 경로 | Phase 3 3파전 비교 |
| K. Extract 베이스 | **Qwen3-4B** | Mi:dm-2.0-Mini / A.X-4.0-Light | Phase 5 |
| L. 평가 | **자체 IR 하네스(pytrec_eval) + DeepEval(로컬 저지)** | promptfoo 보조 | Phase 1 eval-plan |
| M. PII | **Presidio + 한국 커스텀 recognizer 6종** | 순수 자체 구현 | Phase 2 |

---

## 영역별 요지와 핵심 탈락 사유

### A. 문서 파싱/OCR (상세: 01-A)
- **MinerU 3.x 1안**: 77.7k★ 최대 생태계, 2026-04 AGPL→Apache 2.0 기반 라이선스 전환(교내 사용 실질 제약 없음), 한국어 명시 지원(PP-OCRv6, `lang=korean`), DGX Spark 동작 사례 확인(vLLM 이미지 경유).
- **Docling 대안**: MIT로 법적 최청정, arm64 공식 명시, TableFormer 표 구조 인식이 "결과보고서 실적 표"라는 핵심 요건에 직결.
- **탈락**: marker·Surya(가중치 상용 제한 — 매출 $5M 초과 조직 유료, 공공기관 해석 위험), olmOCR(학습 데이터에서 비영어 필터링 — 한국어 구조적 부적합), unstructured(자체 모델 없음), PyMuPDF4LLM(AGPL 의존), PaddleOCR 풀파이프라인(paddlepaddle-gpu aarch64 공식 휠 부재).
- ⚠ **어떤 후보도 한국어 행정 문서 공개 벤치마크가 없다** — 실물 30~50페이지 bake-off 없이 확정 금지.

### B·C. RAG 프레임워크 / 지식 그래프 (01-B, 01-C)
- 시스템의 차별 로직(공고 스키마, 섹션 루프, 수치 검증, 배점 대조, 예산 계산)은 어떤 프레임워크도 제공하지 않음 → **얇은 조립** 원칙. Haystack의 명시적 파이프라인이 감사가능성 요구에 부합.
- 지식 그래프는 **기본 미도입**: 필요 관계(사업↔프로그램↔지표↔예산↔장비)는 실적 카드 DB의 SQL로 표현됨. GraphRAG류는 비정형→LLM 그래프 추출 비용+관계 환각만 추가. Phase 6에서 A/B 입증 시에만.
- 탈락: Dify·RAGFlow(플랫폼 결합이 커스텀 모델 4종 투입과 충돌), R2R(2025-11 이후 정체), txtai(단독 메인테이너).

### D·E. 임베딩/리랭커와 학습 (01-D, 01-E)
- 한국어 검색 리더보드(MTEB-ko-retrieval) 1위 **KURE-v1**(MIT)이 베이스 최적 — 이미 한국어 적응이 끝난 지점에서 도메인 적응 시작. 구조가 BGE-M3와 동일해 FlagEmbedding 학습 체계(공식 스크립트+하드 네거티브 채굴)를 그대로 사용.
- 공개 벤치마크 간 격차(0.50~0.53)보다 도메인 파인튜닝 이득이 클 것 — **베이스 고민보다 데이터 품질에 에너지 집중**이 결론.
- 탈락: KoE5(512 토큰), jina(비상업 가중치), API 임베딩(온프레미스 위반). Qwen3-Embedding은 관찰(한국어 개별 근거 미확인, 무거움).

### F. LLM 파인튜닝 (01-F)
- aarch64+Blackwell 1차 필터 통과: LLaMA-Factory·Unsloth·TRL/PEFT·NeMo AutoModel(전부 NVIDIA 플레이북/공식 지원 근거 있음). **Axolotl·SWIFT은 sm_121/aarch64 검증 사례 미발견 — 미확인 후순위.**
- 운용안: 반복 실험은 Unsloth(속도), 최종 산출 학습은 LLaMA-Factory(YAML 재현성 = 모델 카드 재료).

### G·H. 검색 인프라 (01-G, 01-H)
- 한국어 형태소는 **Kiwi가 사실상 유일한 활성 선택지**(2026-08 push, 사용자 사전·오타 교정). mecab-ko 세대 교체. 국고사업 용어 사용자 사전 구축이 실질 과제.
- Qdrant 1안: dense+sparse 하이브리드 네이티브 + **필터 인식 HNSW**(연도·사업유형·열람등급 강필터 질의가 지배적인 본 프로젝트에 구조적 강점) + 단일 바이너리 운영.
- pgvector 대안의 논리도 강함(실적 카드 DB와 PostgreSQL 일원화 — 출처 참조 무결성) — Phase 1에서 2-스토어 vs 1-스토어 최종 결정.
- 탈락: Milvus(운영 과잉), Weaviate(한국어 BM25 커스텀 불가 구조), OpenSearch(JVM 운영 부담 — 단 운영 경험자 있으면 재평가), LanceDB(서버 모드 미성숙).

### I·J. 구조화 추출 / 서빙 (01-I, 01-J)
- **vLLM으로 수렴**: DGX Spark 공식 지원(2026-06 공식 블로그), LoRA 동적 로딩(Writer v0.x 교체 운용), xgrammar 구조화 강제(Extract), 임베딩 서빙, A 영역 VLM 백엔드까지 전부 vLLM 위에서 만남 — 운영 표면적 최소화.
- 추출은 "형식 보장(xgrammar)"과 "내용 보장(소스 그라운딩+원문 대조)"을 분리 — 수치 환각의 기계적 차단선(5.3 구현).
- LangExtract(구글, 38.4k★): 추출값→원문 오프셋 매핑 내장 — 채택 또는 설계 차용.

### K. 생성 모델 (01-K)
- **명명 자유도가 강한 필터로 작동**: EXAONE(연구 한정 라이선스), Solar Open('Solar' 접두사 의무), Kanana-2('Kanana' 접두사 의무), Llama('Llama' 포함 의무) 전부 ZZAIMY-* 불가. gpt-oss는 한국어 약세 문서화로 탈락.
- 남는 유력지: **Qwen3.5/3.6-35B-A3B**(Apache 2.0, MoE A3B — 대역폭 이론상 ~180 tok/s급 vs dense 72B ~7 tok/s급). 한국어 특화 vs 명명 제약의 트레이드오프(Kanana-2)는 Phase 3 실측 후 사용자 결정 사항.
- 국산 대안: Mi:dm 2.0(MIT)·A.X-4.0-Light(Apache 2.0)는 Extract 후보 겸 Writer 비교군.

### L·M. 평가 / PII (01-L, 01-M)
- 축 B 4개 모델 중 3개는 결정론적 지표로 평가 — **평가셋 구축이 실작업의 90%.** LLM 저지(Writer 문체)는 DeepEval+로컬 모델, 사람 블라인드 평가 병행.
- Ragas는 2026-02 이후 커밋 정체로 탈락(지표 개념만 차용).
- Presidio(MIT, 활발)에 **한국 주민번호 인식기 내장 확인**. 커스텀 개발 범위: 정규식·체크섬 6종(각 1~2일) + 인명 NER(1~2주) — Phase 2 내 소화 가능.

### N. 선행 사례 (01-N)
- 전체 범위를 커버하는 오픈소스 부재 — 자체 구축 판단 타당. 섹션 단위 생성·커버리지 추적·사람 검수 전제는 상용/학술이 독립 검증. **실적 카드 DB 기반 생성이 본 프로젝트의 실질 차별점.**

---

## 최우선 위험 5가지 (전 영역 종합)

1. **한국어 파싱 품질 미검증** — 전 영역의 상류. Phase 2 초입 bake-off가 1순위 행동.
2. **Spark(aarch64+sm_121) 특유의 빌드/휠 이슈** — vLLM·학습 프레임워크 모두 "검증 이미지 태그 고정" 규율 필요. PaddlePaddle GPU 계열은 구조적 회피.
3. **합성 데이터 품질**(질의 생성·retro-fill)이 축 B 성과의 상한 — 프롬프트 버전 관리 + 표본 사람 검수 의무화.
4. **수치 환각** — 파이프라인 구조(인출·대조·빈칸 규약)와 학습 데이터 정제(출력 수치 ⊆ 입력 근거)의 이중 방어. 50-model-track의 정제 규칙이 절대 규칙.
5. **문서 실물 의존 미지수** — HWP 비율, 스캔 품질, 계획서 양식 일관성, 열람 등급 체계(99-open-questions 1~14) — Phase 1 설계 전 사용자 답변 필요.

## 다음 단계

사용자 검토·승인 후 Phase 1 착수: `docs/architecture.md` · `model-plan.md` · `eval-plan.md` · `pilot-plan.md` · `risks.md` (브리프 9장). 승인 전 99-open-questions의 답변(특히 1·2·7·12·13번)을 받으면 설계 정밀도가 크게 오른다.
