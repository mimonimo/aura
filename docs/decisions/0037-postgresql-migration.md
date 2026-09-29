# ADR-0037 — 플랫폼 PostgreSQL 전환

- 상태: 채택(구현·이관 검증 중, 운영 연결은 아직 SQLite)
- 날짜: 2026-09-29
- 근거: 사용자가 다중 사용자·에이전트 동시 작업 확대 전에 데이터가 적을 때 이전하도록 승인.

## 결정

플랫폼 업무 데이터는 PostgreSQL로 이전한다. 원본 파일, Drive 문서, 별도 코퍼스 DB는
이 결정만으로 옮기거나 통합하지 않는다. 스키마와 SQL 호환을 검증하기 전 DSN만 바꾸지 않는다.
계정 비밀번호나 연결 비밀값을 코드·작업 로그·CLI 인수에 저장하지 않는다.

## 단계와 전환 조건

1. SQLite backup API를 이용한 일관된 복제본 보관(커밋된 WAL 포함).
2. 새 PostgreSQL 검증 스키마에 전체 사용자 테이블과 ID를 복사.
3. 테이블별 행 수·타입 포함 전체 값 해시 대조. 불일치 시 전체 적재 트랜잭션 롤백.
4. 앱 연결 계층, 자동 증가 키, UPSERT, JSON 검색, 읽기 전용 조회,
   질문 수정 동시성·트랜잭션을 PostgreSQL에서 검증.
5. 관계 무결성/기본값/인덱스/sequence를 포함한 운영 스키마 적용 및 전체 회귀 테스트.
6. 쓰기 작업과 백그라운드 작업을 중지하고 최종 복제·대조 후 앱 연결 전환.

현재 `migrate_platform_postgres.py`는 **2~3단계용 staging 도구**다. 기본값·인덱스·외래키·
자동 증가 sequence가 없는 검증 테이블이므로 이를 앱 운영 스키마로 사용하면 안 된다.
현재 코드의 SQLite 전용 직접 연결이 남은 동안 운영 전환 금지.

## 복구

2026-09-29 점검: 1차 복사 24테이블·5,106행 대조 통과. 런타임 연결/SQL 호환 계층과
실서버 HTTP 포함 5개 회귀 테스트 통과. 다만 원본 SQLite에 끊어진 참조가 있는 행 889개
(mask_events 272, doc_entities 617)가 확인되어 운영 데이터 적재/전환은 보류한다.
별도 보관으로 원본과 이력을 유지하는 정책을 사용자에게 확인하고, 문서 삭제 때 관계 정리도
수정한 뒤 전환한다. 값 복사의 성공과 운영 무결성 통과는 별개다.

기존 SQLite와 파일을 삭제하지 않는다. 전환 전 실패는 SQLite 운영 유지.
전환 후 쓰기가 발생했다면 이전 SQLite로 단순 복귀할 경우 새 데이터가 유실된다.
그때는 쓰기를 중단하고 PostgreSQL 변경분 보존·역이관 검증 후 복구한다.

## 실행

선택 의존성 `.[postgres]` 설치. DSN은 `ZZAIMY_MIGRATION_DSN` 환경변수 또는
libpq 서비스 설정 사용. 명령 인수로 비밀번호 전달 금지.

```sh
.venv/bin/python scripts/migrate_platform_postgres.py --source data/platform/platform.db --output data/platform/backup/pg-stage-YYYYMMDD-HHMM --schema aura_stage_YYYYMMDD_hhmm
```

출력 폴더는 새 경로만 허용하며 모드 0700, SQLite/결과 파일은 0600.
기존 스키마는 덮어쓰지 않는다. 오류에 원문 행이나 접속값이 포함될 수 있으므로 CLI는
예외 클래스만 출력한다. 결과 파일은 카운트와 해시만 포함하며 Git에 넣지 않는다.
