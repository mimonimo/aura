# E. 임베딩·리랭커 학습 프레임워크

> 조사일: 2026-08-15
> 요구: 대조학습(contrastive), **하드 네거티브 마이닝 지원**, BGE-M3/KURE 계열 호환, aarch64 동작.

---

## 결론 요약

| 구분 | 선정 | 근거 |
|---|---|---|
| **추천 1안** | **FlagEmbedding** (BAAI) | BGE-M3·bge-reranker-v2-m3의 **공식 학습 스크립트** 보유 — D 영역 1안과 직계. **hn_mine 하드 네거티브 마이닝 도구 내장**(브리프 7.1 요구와 정확히 일치). 임베딩+리랭커를 한 프레임워크에서 학습 → 7.1·7.2 데이터 자산 재활용 구조와 정합. MIT |
| **대안 1안** | **sentence-transformers** (HF) | KURE-v1의 실제 학습 레시피(CachedGISTEmbedLoss)를 재현 가능. `mine_hard_negatives` 유틸 제공. HF 생태계 표준이라 문서·커뮤니티 최대. Apache 2.0. CrossEncoder 학습으로 리랭커도 커버 |
| **관찰** | SWIFT (ModelScope) | 임베딩·리랭커·LLM 학습 통합, Qwen3-Embedding 계열 1순위 지원. 단 aarch64 검증 사례 미확인, 중국 생태계 중심 문서화 |

**실행 방침**: 두 후보 모두 순수 PyTorch 위 라이브러리라 aarch64 이식성 위험이 낮다(커스텀 CUDA 커널 없음). NVIDIA PyTorch 컨테이너(nvcr.io/nvidia/pytorch, Blackwell 최적화 빌드) 위에 pip 설치로 동작 예상 — **Phase 2 진입 시 Spark 1대에서 스모크 테스트로 확정**(공식 aarch64 지원 명시는 양쪽 다 없음 → "미확인"으로 분류하되 위험도 낮음).

---

## 상세

### FlagEmbedding — 추천 1안
- **저장소**: FlagOpen/FlagEmbedding · MIT
- BGE-M3 finetune 예제: (질의, 정답, 네거티브) jsonl → 통합 스크립트. dense+sparse+colbert 3중 목적함수(unified fine-tuning) 지원 — BGE-M3/KURE의 sparse 출력을 유지한 채 도메인 적응 가능(G 영역 하이브리드 검색과 연동).
- **hn_mine**: 자체 임베딩으로 상위 후보를 뽑아 정답을 제외한 구간(range_for_sampling)에서 네거티브 샘플링 — "그럴듯하지만 오답" 하드 네거티브를 자동 채굴. 여기에 도메인 규칙(같은 사업의 인접 연도 청크, 유사 프로그램명)을 추가하는 커스텀 채굴을 결합할 것(50-model-track 상세).
- 리랭커 학습: bge-reranker 계열 finetune 스크립트 공식 제공.
- 위험: 문서화가 예제 중심으로 다소 산만. 프로젝트 활동이 BGE 신모델 위주라 구버전 스크립트 유지보수 편차.

### sentence-transformers — 대안 1안
- v3+ 학습 API 전면 개편: 손실 함수 라이브러리(MultipleNegativesRankingLoss, **CachedGISTEmbedLoss** 등), 대배치 그래디언트 캐싱 — KURE-v1이 배치 4096을 쓴 바로 그 방법. 128GB 통합 메모리와 궁합 좋음.
- `mine_hard_negatives` 유틸로 하드 네거티브 채굴 지원.
- BGE-M3 로드 시 sparse/colbert 헤드 학습은 미지원(dense 전용) — **sparse 출력을 유지하려면 FlagEmbedding 필요.** 이것이 1안/대안을 가르는 실질 차이.

### 평가 하네스
- 두 프레임워크 모두 학습 중 IR 평가(Recall@k, MRR, nDCG) 콜백 지원. 자체 평가셋(Phase 2 구축)을 dev셋으로 물려 학습 조기 종료·체크포인트 선택에 사용.

## 위험
1. aarch64 공식 명시 부재(양쪽 공통) — 스모크 테스트로 해소. 실패 시 대안: NVIDIA PyTorch 컨테이너 내 소스 설치.
2. BGE-M3 unified fine-tuning(3중 목적)은 dense 단독 대비 하이퍼파라미터 민감 — 1차는 dense 단독, sparse 유지가 필요하면 2차에 unified로.
