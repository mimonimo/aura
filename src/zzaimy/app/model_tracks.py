"""개발 현황과 모델 학습 화면의 공통 계획. 실행 상태는 별도 실시간 조회."""
UPDATED = "2026-09-29"
TRACKS = (
    {"key": "embed", "name": "ZZAIMY-Embed", "base": "KURE-v1",
     "role": "근거 검색용 임베딩", "method": "대조학습",
     "status": "v2 운영 채택", "next": "새 실문서 평가셋으로 검색 품질 재측정"},
    {"key": "rerank", "name": "ZZAIMY-Rerank", "base": "bge-reranker-v2-m3",
     "role": "검색 후보 재정렬", "method": "목록 단위 순위 학습",
     "status": "v1 운영 채택", "next": "현재 코퍼스 기준 재측정·근거 하한 검증"},
    {"key": "answer", "name": "ZZAIMY-Writer", "base": "Qwen3.8-27B",
     "role": "문답·검토·초안·이미지 판독", "method": "bf16 LoRA SFT → DPO",
     "status": "베이스 운영 · 실문서 문답 검수 중",
     "next": "7개 문서 구조·문답 검수 → 평가 분리 → 실문서 베이스라인 → SFT"},
    {"key": "extract", "name": "ZZAIMY-Extract", "base": "Qwen3.8-27B",
     "role": "Writer 공용 베이스 · 구조화 추출", "method": "형식 강제 + 검수본 SFT",
     "status": "전용 학습·평가 준비 전",
     "next": "실적 카드 정답셋 확보·평가. 4B는 처리량 문제가 확인될 때만 검토"},
)
