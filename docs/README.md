# 문서 안내

ZZAIMY의 설계·측정·결정 문서 목록이다. 설계 문서는 목적과 구조를, 측정 보고서는 측정 당시의 데이터·모델·설정에서
얻은 결과를 적는다. 현재 운영 구성은 인수인계 문서가 기준이다.

## 처음 읽을 문서

| 문서 | 내용 |
|---|---|
| [프로젝트 브리프](../PROJECT_BRIEF.md) | 목표, 범위, 설계 원칙, 산출물 상태 |
| [시스템 설계](architecture.md) | 데이터 흐름, 검색·생성·검증 구조, 열람 권한 |
| [문서 작업 흐름](document-work-flow.md) | 문서 세트 접수, 작업본 작성, 한글 산출 |
| [모델 개발 계획](model-plan.md) | 모델 4종의 역할, 학습 데이터, 일정 |

## 설계와 운영 기준

- [데이터 배치](data-layout.md): 문서·지식·학습 자료의 저장 위치
- [데이터셋 생성·검수](dataset-authoring.md): 실문서 문답 후보의 생성과 검증
- [품질 체계](quality-system.md): 추출·표·OCR 문제를 계층별로 막는 방법
- [평가 계획](eval-plan.md): 평가셋, 지표, 베이스라인 절차
- [파일럿 계획](pilot-plan.md): 파일럿 범위와 마일스톤
- [위험 관리](risks.md): 개인정보, 수치 환각, 파싱, 운영 위험과 대응
- [작업 방식](workflow.md): 저장소·결정·보고 규칙
- [결정 기록(ADR)](decisions/README.md): 구조·기술 선택과 그 근거

## 모델 카드

- [ZZAIMY-Embed v2](model-cards/zzaimy-embed-v2.md)
- [ZZAIMY-Rerank v1](model-cards/zzaimy-rerank-v1.md)

## 측정 보고서

- 검색: [리랭커 평가](llm-rerank-eval.md), [리랭커 베이스라인](rerank-baseline.md), [하이브리드 가중치 탐색](retrieval-weight-sweep.md),
  [검색 미니 베이스라인](retrieval-baseline-mini.md), [Embed v0 시험 학습](embed-v0-report.md), [이전 보고서](archive/README.md)
- 문서 판독: [OCR 문자 오류율](ocr-cer-bench.md), [OCR 엔진 비교](ocr-duel.md), [OCR 시각 비교](ocr-visual-review.md)
- 환경: [학습 장비 점검](env-report.md)

## 일정과 보고

- [캡스톤 실행계획](capstone-plan.md), [주간 보고](weekly/), [논문 자료](paper/)
- [원안 사업계획서](business-plan.md), [기획서 초안](proposal/기획서-초안.md): 착수 시점 원안(참고용)

## 운영과 작업 기록

- [인수인계](HANDOFF.md): 운영 구성, 실행 절차, 남은 일
- [현재 작업](dev-now.md), [변경 기록](dev-changelog.md)
- [기술 노트](notes/), [작업 맥락](context/working-notes.md), [1~2주차 계획](instructions/W1-W2.md)
- [착수 단계 기술 조사](../research/README.md)
