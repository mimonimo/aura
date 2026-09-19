# 협업 분담 (현재)

협업 절차의 원본은 `docs/collaboration/README.md` 다. 이 표는 그 절차에서 쓰는
파일 소유권만 담는다. 기록은 각자 `docs/collaboration/` 아래 자기 파일에 남긴다.

이 저장소의 개발 작업은 Claude(이 세션)로 일원화됐다. Codex 는 배정된 평가 하네스
과제를 맡는다. aura-7b 는 편집을 하지 않고 질의응답·중계만 한다.
두 사람 이상이 동시에 손댈 때 충돌을 막기 위한 파일 소유권 표다.
작업을 시작하기 전에 이 표를 보고, 맡은 칸을 자기 이름으로 바꾼 뒤 시작한다.
끝나면 비워 둔다. 남의 칸은 읽기만 한다.

## 지금 상태 (2026-09-19)

| 영역 | 파일 | 맡은 이 |
|---|---|---|
| 라우트·화면 뼈대 | `src/zzaimy/app/main.py` | 진행 중 |
| 공통 화면·떠 있는 에이전트 | `templates/base.html` | 진행 중 |
| 에이전트 채팅 | `templates/chat.html` | 진행 중 |
| 에이전트 조작 목록 | `src/zzaimy/app/actions.py` | 진행 중 |
| 반입 파이프라인 | `src/zzaimy/app/pipeline.py` | 진행 중 |
| 자료 계층 | `src/zzaimy/app/db.py` | 진행 중 |
| 글자 복원 | `src/zzaimy/app/text_repair.py` | 진행 중 |
| 반입 자가 점검 | `src/zzaimy/app/ingest_audit.py` | 진행 중 |
| 문서 정체 | `src/zzaimy/app/doc_identity.py` | 진행 중 |
| 문서 보기 | `templates/doc.html`, `render.py` | 비어 있음 — Codex C-20260919-04 로컬 검증 완료, Claude 배포 대기 |
| UI 템플릿 계열 (index·doc·project·graph·ocr·dev_db·dev_hwp·dev_corpus) | `templates/*.html` | 진행 중 (aura-7b 반납분 인계) |
| 한글 에이전트 도구 | `tools/hwp-agent/**` | 진행 중 (인계) |
| 이그레스 관문 | `src/zzaimy/app/egress.py` | 진행 중 (인계) |
| 검색 평가 하네스 | `src/zzaimy/eval/**`, `scripts/53`·`64`·`65` | Codex (요청 K-20260919-02) |
| 지식 그래프 화면 | `templates/graph.html` | 진행 중 (간선 3종 표시) |
| 검색·조각 품질 | `chunk_quality.py`, `embed_search.py`, `rerank.py`, `responder.py`, `regulations.py` | 비어 있음 |
| 개체·연관 | `src/zzaimy/graph/**` | 비어 있음 |
| 표·괘선 추출 | `src/zzaimy/ingest/parsers/**` | 비어 있음 |

## 지켜야 할 것

- 운영 서버는 `aura@192.168.16.226` 하나뿐이다. 배포(`scripts/99_deploy.sh`)와 서비스 재시작은
  **한 사람만** 한다. 지금은 라우트 담당(Claude)이 한다.
- **맥 사본과 VM 은 지금 내용이 같다** (2026-09-19 대조: 공유 파일 244개 전부 동일).
  배포는 맥 → VM rsync 이고 `--delete` 가 없어 덮어쓴다. VM 에서 직접 고칠 일이 생기면
  배포 담당에게 먼저 알린다. 배포 담당은 배포 직전에 양쪽을 md5 로 대조한다.
- 지금 그 서버에서 전 문서 재처리가 돌고 있다. 무거운 조회·작업을 걸지 않는다.
- 저장소 규칙은 `CLAUDE.md` 를 따른다. `data/` 는 커밋하지 않는다. 수치는 근거 있는 것만 쓰고, 확인 못 한 것은 미확인으로 적는다.
- 화면 문구는 격식체 완결 문장으로 쓰고, 설명을 여러 줄 늘어놓지 않는다.

## 지금 남은 일

1. 프로젝트 화면에서 요약·초안을 확인하고 말로 고쳐 최종본까지 가는 흐름
2. 반입 품질 재측정 (재처리가 끝난 뒤 `scripts/84_ingest_audit.py`)
3. 검색 색인 다시 만들기 (`scripts/66_reindex.sh`) — 재처리 뒤
4. 토르 서빙 (추론 포트가 아직 열려 있지 않음)
