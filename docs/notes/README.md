# 기록 자료 주제별 색인

기술 검토·작업 메모·측정 보고서·모델 카드·9월 설계 문서를 결정 기록(ADR)과 같은 주제로 묶었다. 같은 주제의 결정은
[결정 기록의 주제별 묶음](../decisions/README.md)에 있다. 개발자 화면의 기록 자료 목록은 이 묶음 이름을 설명 앞에 붙여
보인다(목록 검색창에 주제 이름을 넣으면 그 주제만 남는다). 새 기록을 쓰면 여기에도 한 줄 넣는다.

같은 측정이나 같은 검토를 여러 번 했으면 새 파일을 만들지 않고 원래 문서에 회차 절을 더한다.

### 문서 반입·판독

- [OCR 조사 — 엔진·전처리·고도화 레퍼런스](2026-09-02-ocr-refs.md)
- [공개 문서 소스](2026-09-02-external-doc-sources.md)
- [구글 드라이브 원천 — 설정·계정 체계·독스 편집 설계](2026-09-22-google-drive-source.md)
- [실문서 반입 전 검토](real-document-readiness-20260922.md)
- [3~4년치 문서 반입 분류 계약](bulk-ingest-classification-20260922.md)
- [kordoc 과의 비교(읽기)](2026-09-28-kordoc-comparison.md)
- [표·레이아웃 원본 불일치 진단](table-layout.md)
- [OCR 어절 정확도 벤치](../ocr-cer-bench.md)
- [OCR 엔진 대결](../ocr-duel.md)
- [OCR 시각 비교 절차](../ocr-visual-review.md)
- [품질 5계층 체계](../quality-system.md)

### 저장·데이터 체계

- [자료 보관·접근·삭제 범위](2026-09-24-data-retention.md)
- [원본·파생 자료 동기화 검수](2026-10-02-document-sync-review.md)

### 검색(RAG)

- [리랭커 평가(9/20)](../llm-rerank-eval.md)
- [리랭킹·질의 확장 회차별 결과(9/20)](../archive/README.md)
- [검색 미니 베이스라인(9/4)](../retrieval-baseline-mini.md)
- [하이브리드 가중 스윕(9/4)](../retrieval-weight-sweep.md)
- [리랭커 베이스라인(9/4)](../rerank-baseline.md)
- [Embed v0 시험 학습(9/3)](../embed-v0-report.md)
- [ZZAIMY-Embed v2 모델 카드](../model-cards/zzaimy-embed-v2.md)
- [ZZAIMY-Rerank v1 모델 카드](../model-cards/zzaimy-rerank-v1.md)

### 지식그래프·온톨로지

- [사업별 분석 — 원본 보관소 전체](2026-10-01-business-analysis.md)
- [그래프·온톨로지 고도화 반복 기록](2026-10-01-kg-iterations.md)
- [그래프 검색 범위 검사와 실문서 평가 후보](2026-10-02-graph-scope-check.md)
- [GraphRAG 대 풀텍스트 RAG 비교](2026-10-13-graphrag-vs-fulltext.md)
- [장부에 없는 사업 이름 검토 1차(10-13)](2026-10-13-program-name-review.md)
- [장부에 없는 사업 이름 검토 2차(10-19)](2026-10-19-program-name-review.md)

### 모델·학습·서빙

- [서빙 엔진 검토 — vLLM과 Ollama](2026-09-22-vllm-vs-ollama.md)
- [생성 모델 교체 절차](2026-10-15-model-swap.md)
- [자동화 파이프라인](automation-pipeline.md)
- [평가 계획](../eval-plan.md)

### 문서 작성·화면

- [독스·LibreOffice 줄 간격 실측](2026-09-29-line-spacing-measurement.md)
- [한글 → 구글 독스 변환 경로 견주기](2026-09-30-docs-path-compare.md)
- [kordoc 조사 — 서식 보존 작성](2026-09-30-kordoc-authoring-survey.md)
- [한글(HWPX) 에이전트 스킬 수집](hwpx-agent-skills.md)
- [플랫폼 업무 도우미](platform-helper.md)
- [UI/UX 전수 조사](uiux-audit.md)
- [UI 용어집](ui-glossary.md)
- [시스템 설계](../architecture.md)

### 권한·외부 전송

- [권한별 지식 베이스 설계(9/22)](2026-09-22-access-controlled-knowledge-base.md)
- [권한별 열람 범위 — 경로 × 계정(10/15)](2026-10-15-access-control.md)
- [외부 전송 경로와 통제 점검](2026-10-15-egress-audit.md)
- [문서 반분 외부 위탁·블록체인 검토](2026-09-22-external-split-and-blockchain.md)
- [위험 관리](../risks.md)

### 계획·작업 방식

- [2주차 지도 회의 메모](meeting-w2-20260908.md)
- [개선 백로그](backlog.md)
- [지운 스크립트](removed-scripts.md)
- [캡스톤 실행계획](../capstone-plan.md)
- [파일럿 계획](../pilot-plan.md)
- [작업 방식](../workflow.md)
