# 문서 안내

설계 문서는 목적과 구조를, 측정 보고서는 당시 조건의 결과를 적는다. 지금 운영 상태는 인수인계와 현재 작업 문서가 기준이다.
과거 측정은 당시 문서·모델·설정에 대한 결과이며 현재 성능과 구분한다.

## 처음 볼 문서

- [프로젝트 브리프](../PROJECT_BRIEF.md): 목표, 범위, 설계 원칙
- [시스템 설계](architecture.md): 데이터 흐름, 검색·생성·검증 구조
- [문서 작업 흐름](document-work-flow.md): 문서 세트에서 작업본 작성·한글 산출까지
- [모델 개발 계획](model-plan.md): 모델 4종의 역할과 학습 계획

## 설계·운영 기준

- [데이터 배치](data-layout.md): 문서·지식·학습 자료의 저장 위치
- [데이터셋 생성·검수](dataset-authoring.md): 실문서 문답 후보의 생성과 검증
- [품질 체계](quality-system.md): 추출·표·OCR 문제를 계층별로 막는 방법
- [평가 계획](eval-plan.md) · [파일럿 계획](pilot-plan.md) · [위험 관리](risks.md)
- [작업 방식](workflow.md): 저장소·결정·보고 규칙
- [결정 기록(ADR)](decisions/)

## 측정 보고서

- 검색: [리랭커 평가](llm-rerank-eval.md), [리랭커 베이스라인](rerank-baseline.md), [하이브리드 가중 스윕](retrieval-weight-sweep.md),
  [검색 미니 베이스라인](retrieval-baseline-mini.md), [Embed v0 시험 학습](embed-v0-report.md), [옛 보고서](archive/)
- 문서 판독: [OCR 문자 오류율](ocr-cer-bench.md), [OCR 대결](ocr-duel.md), [OCR 시각 비교](ocr-visual-review.md)
- 환경: [학습 장비 점검(초기)](env-report.md)
- [모델 카드](model-cards/)

## 일정·보고

- [캡스톤 실행계획](capstone-plan.md) · [주간 보고](weekly/) · [논문 자료](paper/)
- [원안 사업계획서](business-plan.md) · [기획서 초안](proposal/) (참고용 원안, 고치지 않음)

## 운영·작업 기록

- [인수인계](HANDOFF.md): 운영 구성, 접속·실행 절차, 남은 일
- [현재 작업](dev-now.md) · [변경 기록(9월)](dev-changelog.md)
- [작업 노트](notes/) · [협업 기록](collaboration/) · [작업 맥락](context/working-notes.md) · [1~2주차 계획](instructions/W1-W2.md)
