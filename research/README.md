# research/ — Phase 0 리서치 (참고 자료)

2026-08-15에 교수님 브리프(원안)를 기준으로 조사한 기술 스택 검토다. 후보 비교와
탈락 이유가 남아 있어 설계 근거로 참조하되, **현재 구성은 이 폴더가 아니라
`PROJECT_BRIEF.md`(v3)·`docs/architecture.md`·`docs/decisions/`가 정본**이다.
이 폴더의 문서는 조사 시점 기록이므로 고쳐 쓰지 않는다.

조사 결과와 실제 채택이 다른 곳:

| 영역 | 리서치 추천 | 실제 |
|---|---|---|
| RAG 프레임워크 | Haystack 조립 | 프레임워크 없이 직접 구현(FastAPI + SQLite) |
| 지식 그래프 | 도입 안 함 | 경량 온톨로지로 도입(ADR-0009·0010·0015) |
| 벡터 DB | Qdrant | 사전계산 임베딩 파일(npz) + SQLite. 벡터 DB 도입은 미결 |
| 서빙 장비 | DGX Spark | 젯슨 AGX 토르 2대(NVFP4), 학습은 DGX(ADR-0023) |
| 생성 모델 | MoE 우선(Qwen3.5-35B-A3B) | Qwen3.8-27B dense(2주차 미팅 확정) |
| 파서 | MinerU 1안 | MinerU + 글자층 직독 + Writer 이미지 판독(ADR-0007) |
| 임베딩·리랭커 | KURE-v1 · bge-reranker-v2-m3 | 같은 베이스의 학습본 v2·v1 운영(ADR-0020·0021) |

`99-open-questions.md`의 질문 가운데 답이 난 것(#5·#6 Writer 베이스, #1 HWP 경로,
#4 표본)은 `CLAUDE.md`의 "확정 필요" 표와 ADR에 반영돼 있다.
