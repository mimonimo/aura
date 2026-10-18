"""개발 현황과 모델 학습 화면의 공통 계획. 실행 상태는 별도 실시간 조회(학습셋은 export.bundle.trainsets — DGX 목록)."""
UPDATED = "2026-10-18"
TRACKS = (
    {"key": "embed", "name": "ZZAIMY-Embed", "base": "KURE-v1",
     "role": "규정·사업 문서 근거 검색용 임베딩", "method": "대조학습",
     "status": "v2 운영 채택 · 사업 문서 조각 약 158만 개 색인(pgvector)",
     "next": "실사용 질문으로 만든 평가 세트로 다시 측정"},
    {"key": "rerank", "name": "ZZAIMY-Rerank", "base": "bge-reranker-v2-m3",
     "role": "검색 후보 재정렬", "method": "목록 단위 순위 학습",
     "status": "v1 운영 채택",
     "next": "현재 문서 기준 재측정·근거 하한 검증"},
    {"key": "answer", "name": "ZZAIMY-Writer", "base": "Qwen3.8-27B (NVFP4 · MTP)",
     "role": "답·문서 작성 에이전트·검토·서류 갈래와 사업 판정·이미지 판독",
     "method": "QLoRA SFT → DPO",
     "status": "베이스 모델로 운영 — 라이브러리 문서 약 3.5만 건의 검색(RAG)과 지식그래프(GraphRAG)를 근거로 답한다. "
               "SFT 학습셋 writer-v1(학습 4,434 · 검증 346 · 시험 220)은 10-18 최종 점검에서 답 결함이 나와 보류(품질 검토 대기), 학습 전 측정 30건 중 25 통과",
     "next": "고친 학습셋 재확인 → DGX 에서 SFT → 같은 시험 세트·모델 교체 기준표(195)로 학습 전후 비교 → 서빙 장비로 옮김"},
    {"key": "extract", "name": "ZZAIMY-Extract", "base": "Qwen3.8-27B",
     "role": "Writer 공용 베이스 · 구조화 추출(사업 정보·성과지표)", "method": "형식 강제 + 검수본 SFT",
     "status": "Writer 와 같은 모델로 운영(구조화 출력) · 전용 학습 전",
     "next": "성과지표 정답셋 확보·평가. 4B 는 처리량 문제가 확인될 때만 검토"},
)
