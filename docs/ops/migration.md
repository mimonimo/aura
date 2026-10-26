# 서버 이전

플랫폼을 다른 서버로 옮길 때 돌아가는 서비스에 필요한 것만 옮긴다. 백업, 측정 산출물, 임시 파일, 캐시는 옮기지
않는다. 무엇을 옮길지는 `scripts/209_migration_manifest.py` 의 규칙표 한 곳에서 정하고, 이 문서는 그 규칙의 까닭과
순서를 적는다. 규칙을 바꾸면 이 문서도 같은 커밋에서 고친다.

## 도구

옛 서버에서 돌린다. 서버에 접속하는 일, 복사하는 일은 도구가 하지 않는다.

```
cd ~/zzaimy-capstone && set -a; . ./.env.local; set +a
PYTHONPATH=src .venv/bin/python scripts/209_migration_manifest.py report --db
PYTHONPATH=src .venv/bin/python scripts/209_migration_manifest.py pack --out ~/migration-pack --dest aura@<새 서버>
```

- report: 저장소 전체와 홈의 정해진 곳(`~/opt`, `~/ops`, `~/.ssh/id_ed25519_dgx_ro`, `~/.config/systemd/user`,
  `~/.cache/huggingface`, `~/app.log`)을 훑어 경로마다 갈래를 정하고, 갈래별 개수·크기를
  `data/platform/migration/manifest.json` 과 `manifest.md` 에 쓴다. 어느 규칙에도 안 걸린 경로는 「규칙 없음」으로
  따로 세고, 1MB 넘는 파일은 하나씩 적는다. 아무 목록에도 들지 않은 채 조용히 빠지는 것이 없게 하려는 것이다.
  `--strict` 를 붙이면 1MB 넘는 「규칙 없음」이 있을 때 종료 코드 1.
- report --db: DB 의 `documents.stored_path` 와 `files.path` 를 읽기만 해서, 「옮김」 밖(예: `data/inbox`,
  `data/platform/inbox`)을 가리키는 문서가 있는지 센다. 있으면 그 문서부터 정리하고 다시 돌린다.
- pack: `rsync -a --files-from` 에 바로 쓰는 목록 파일과 실행할 명령(`commands.sh`)을 쓴다. 목록은 저장소 기준
  (`files-repo.txt`), 홈 기준(`files-home.txt`), 절대 경로(`files-abs.txt`, 환경 변수가 가리키는 인증서 등)로 나뉜다.
  비밀값은 `secrets-*.txt`(권한 600)로 따로 나가고 `--chmod=F600` 으로 옮긴다.

비밀값은 「옮김」이지만 「비밀값 — 따로 안전하게 옮김」으로 표시된다. 도구는 파일 내용을 읽지 않고 경로만 적는다.
`.env.local` 은 경로 찾기에만 읽고 값은 어디에도 찍지 않는다.

`ZZAIMY_DATA_DIR`, `ZZAIMY_TRAIN_DIR` 을 바꿔 쓰는 서버면 규칙의 `data/platform/`, `data/train/` 이 그 경로로 따라간다.

## 갈래

| 갈래 | 뜻 |
|---|---|
| 옮김 | 서비스가 읽고 쓰는 원본·장부·설정. 그대로 복사한다 |
| 다시 만듦 | DB·코드·원격 장비에서 다시 만들 수 있다. 새 서버에서 명령으로 만든다 |
| 옮기지 않음 | 백업, 측정 산출물, 임시, 실행 기록, 가상환경, 모델 가중치 |
| 학습(아스트라) — 따로 | 학습 자료와 학습본. 아스트라가 따로 옮긴다 |

PostgreSQL 운영 DB(문서·조각·개체·장부·의미 검색 벡터의 정본, ADR-0037·0057)는 파일 목록에 없다. pg_dump 로 따로 옮긴다.

## 옮기는 것

| 경로 | 까닭 |
|---|---|
| `data/platform/documents/` | 1층 문서(반입·첨부·생성·보고)와 기안기 문서함(`documents/기안기/<부서>/`, `.판`·`.휴지통` 포함). DB 의 stored_path 가 가리킨다 |
| `data/platform/platform.db` | SQLite 가 아니라 DB 선택 안내 파일. PostgreSQL 연결이 이 경로 비교로 정해진다 |
| `data/platform/kg_external.json`, `class_review*.jsonl`, `origins.jsonl` | 그래프 입력 장부(사람이 정하거나 외부 검색으로 확인한 값) |
| `data/platform/dept_parents.json`, `program_admin.json`, `rag_spaces.json`, `history/` | 관리자가 정한 부서 위계·사업 관리·RAG 범위와 그 이력 |
| `data/platform/*_audit.jsonl`, `regulation_register.jsonl`, `llm_usage.json` | 감사·등록·사용량 기록 |
| `data/platform/*_episodes.jsonl`, `platform_actions.jsonl`, `fill_gaps.jsonl`, `gdocs_fills.json` | 에이전트 실행 기록(수집 자료 화면), 구글 문서 채운 칸 기록 |
| `data/platform/nas_state.json`, `.sync_seen`, `.sync_backlog`, `parse_failures.jsonl`, `doc_program_changes.jsonl` | 동기화 상태와 처리 기록. 없으면 처음부터 다시 훑는다 |
| `data/inbox/parsed/.offsets.json` | DGX 처리 결과를 읽은 자리. 없으면 결과 파일을 처음부터 다시 훑는다 |
| `data/generated/templates/gdocs/published.json` | 공통 양식 드라이브 게시 기록. 없으면 같은 양식이 다시 올라간다 |
| `~/.config/systemd/user/zzaimy*` | 사용자 서비스 단위 |
| `~/opt/webhwp/README.txt`, `~/opt/webhwp/conf/` | 기안기 Tomcat 설정과 설치 순서(설정만) |

비밀값(따로 안전하게 옮김): `.env.local`, `configs/hosts.env`, `data/platform/accounts.json`, `llm_connections.json`,
`gdrive_oauth.json`, `gdrive_tokens.json`, `nas_sources.json`, `.session_secret`(없으면 모두 다시 로그인),
`.collect_salt`(바뀌면 수집 자료 가명이 이어지지 않음), `initial_dev_password.txt`, `~/.ssh/id_ed25519_dgx_ro`
(DGX 학교 원본 읽기 전용 키), 기안기 라이선스(`~/opt/webhwp` 아래 `*licen*`, `*.lic`), 그리고 `.env.local` 의
`ZZAIMY_TLS_KEY` 처럼 환경 변수가 가리키는 열쇠 파일. `ZZAIMY_TLS_CERT` 같은 인증서는 옮기되 비밀값으로 치지 않는다.

## 다시 만드는 것

| 경로 | 다시 만드는 법 |
|---|---|
| 코드(`src`, `scripts`, `tests`, `docs`, `configs` 견본, `.git`) | `git clone` |
| `data/platform/knowledge/index/` | `bash scripts/66_reindex.sh`(조각 임베딩), 사업 문서 색인은 2분 주기 `zz_index.sh`(170 --index)가 따라잡는다 |
| 그래프와 `kg_built.stamp`, `.kg-dirty`, `program_core_docs.json` | `env PYTHONPATH=src .venv/bin/python scripts/157_build_kg.py --full`(약 22분) 또는 `touch data/platform/.kg-dirty` 로 1분 주기에 맡김 |
| `data/platform/ontology/` | `env PYTHONPATH=src .venv/bin/python scripts/177_ontology_export.py` |
| `data/platform/knowledge/exports/` | `env PYTHONPATH=src .venv/bin/python scripts/140_export_knowledge.py` |
| `data/platform/exports/`(수집 자료 묶음) | `/dev/collect` 에서 다시 만든다 |
| `data/platform/webhwp_forms/` | `PYTHONPATH=src .venv/bin/python scripts/200_webhwp_forms.py all` |
| `data/platform/cache/`, `data/cache/` | 쓰일 때 스스로 다시 만든다 |
| `data/inbox/parsed/`(`.offsets.json` 빼고), `archive_facts.jsonl` | 5분 주기 `zz_parsed.sh`(170 --parsed), 10분 주기 `zz_sync.sh` 가 DGX 에서 다시 받는다 |
| `sync_status.json`, `pipeline_audit.json`, `reimport_rels.json` | 다음 주기와 `scripts/175_pipeline_audit.py` 가 다시 쓴다 |
| `dept_dictionary.json` | 입력 지문이 바뀌면 스스로 다시 만든다 |
| `data/generated/`(게시 기록 빼고) | 동기화 주기(162·172)가 다시 만든다 |
| `~/opt/webhwp`(설정 빼고), `~/opt/node`, `~/opt/kordoc`, `~/opt/lo` | 같은 판으로 다시 설치. 기안기는 `~/opt/webhwp/README.txt` 순서, 판은 HANDOFF 의 kordoc 절 |

## 옮기지 않는 것

| 경로 | 까닭 |
|---|---|
| `*.bak*`, `*.retired-*.sqlite3`, `data/platform/backups/`, `backup/` | 백업. 원본과 PostgreSQL 덤프가 옮겨진다 |
| `data/platform/knowledge/eval/`, `data/eval/`, `data/interim/`(sft 빼고) | 측정 산출물과 측정 재료. 서비스가 읽지 않는다 |
| `data/platform/tmp/`, `data/tmp/`, `*.log`, `~/app.log`, `__pycache__` | 임시와 실행 기록 |
| `data/platform/inbox/` | 업로드 임시 자리. 처리되면 `documents/` 로 옮겨진다. report --db 로 남은 문서가 없는지 본다 |
| `data/inbox/core/`, 그 밖의 `data/inbox/` | DGX 학교 원본에서 받은 반입 뼈대(약 21G). 반입되면 `documents/` 로 옮겨지고, 남은 것은 동기화가 다시 받는다 |
| `data/scraped/` | 공개 국고 문서 수집본. 서비스는 읽지 않고 반입 점검(125)만 쓴다 |
| `data/external/`, `data/dist/`, `data/vision/`, `data/samples/` | 서비스 코드가 읽지 않는 참고 자료·배포 묶음·쪽 그림 실험·실물 표본 |
| `.venv`, `.venv-*` | 새 서버에서 다시 설치 |
| `models/`, `~/.cache/huggingface/` | 모델 가중치와 캐시. 다시 내려받는다. 새 서버가 밖으로 못 나가면 옛 서버의 캐시를 따로 복사한다 |
| `~/ops/` | 일회성 운영 보조 스크립트 |

학습(아스트라) — 따로: `data/train/`, `data/training/`, `data/interim/sft/`. 학습 자료와 학습본은 아스트라가 옮긴다.
운영 화면의 학습셋 목록은 DGX 에서 받으므로(197, `data/inbox/parsed/trainsets*`) 이 묶음 없이도 뜬다.

## 순서

옛 서버에서:

1. `report --db` 를 돌려 「규칙 없음」과 「옮김 밖을 가리키는 DB 경로」를 본다. 남은 것이 있으면 규칙을 더하거나 그 문서를
   정리하고 다시 돌린다.
2. `pack --out ~/migration-pack --dest aura@<새 서버>` 로 목록과 `commands.sh` 를 만든다. 명령을 읽고 목적지를 확인한다.

새 서버에서(먼저 준비):

3. 같은 계정(aura)과 같은 저장소 경로(`~/zzaimy-capstone`)를 쓴다. 문서 경로가 DB 에 절대 경로로 들어 있어 경로가 같으면
   고칠 것이 없다. 다르면 복원 뒤 `db.repath_files(옛 접두어, 새 접두어)` 로 바꾸고 `documents.stored_path` 도 같이 바꾼다.
4. 설치: python 3.12, PostgreSQL 14 와 pgvector, rsync, tesseract, LibreOffice(`~/opt/lo`), Node·kordoc(`~/opt/node`,
   `~/opt/kordoc`). `git clone` 뒤 `.venv` 를 만들고 의존성을 설치한다(torch 2.4.1 고정, 망이 없으면 HANDOFF 의 오프라인 휠 절차).
   `sudo loginctl enable-linger aura` 로 사용자 서비스가 부팅 때 뜨게 한다.
5. DB 만들기: `createdb aura_platform`, `psql -d aura_platform -c 'CREATE EXTENSION IF NOT EXISTS vector'`.

옛 서버에서(복사):

6. `bash ~/migration-pack/commands.sh`. 서비스를 멈추고 crontab 을 적어 둔 뒤 끄고, pg_dump, 일반 파일, 비밀값 순서로 보낸다.

새 서버에서(복원과 다시 만들기):

7. DB 복원: `pg_restore --no-owner -d aura_platform ~/migration/platform.dump`. `.env.local` 의 `ZZAIMY_DATABASE_URL` 과
   `ZZAIMY_DATABASE_SCHEMA` 가 새 DB 를 가리키는지 본다(스키마 이름은 그대로).
8. `.env.local` 에서 서버마다 다른 값(`ZZAIMY_PUBLIC_URL`, `ZZAIMY_TLS_CERT`·`ZZAIMY_TLS_KEY` 경로)을 고친다.
   맥의 `configs/hosts.env` 에서 `ZZAIMY_VM_SSH` 를 새 서버로 바꾼다(`99_deploy.sh` 가 이 값으로 접속한다).
9. 기안기: `~/opt/webhwp/README.txt` 순서로 다시 설치하고 옮긴 `conf/` 와 라이선스를 덮는다. 127.0.0.1:8090 에서 뜨는지 본다.
10. 모델 캐시: 다시 내려받거나 옛 서버의 `~/.cache/huggingface` 를 복사한다. 서비스는 `HF_HUB_OFFLINE=1` 로 돈다.
11. 다시 만들기: `bash scripts/66_reindex.sh`, `scripts/157_build_kg.py --full`, `scripts/177_ontology_export.py`,
    `scripts/200_webhwp_forms.py all`. 무거운 작업은 `scripts/zz_run.sh` 로 하나씩 돌린다.
12. DGX 읽기 전용 키: 옮긴 `~/.ssh/id_ed25519_dgx_ro` 가 DGX `authorized_keys` 의 rrsync 줄과 짝이다.
    `ssh -i ~/.ssh/id_ed25519_dgx_ro <DGX>` 가 목록만 보이는지 확인한다.
13. 시작: `systemctl --user daemon-reload && systemctl --user enable --now zzaimy.service`. 이후 배포는 맥에서
    `bash scripts/99_deploy.sh --restart`(--restart 가 없으면 서비스가 옛 코드로 돈다).
14. 확인을 마치면 `crontab ~/migration/crontab.txt` 로 주기 작업을 켠다(경로가 바뀌었으면 고친 뒤).

## 확인

- 로그인: `curl -sk -o /dev/null -w '%{http_code}' https://localhost/login` 이 200.
- 문서 수: 옛 서버와 새 서버에서 같은 수가 나온다.
  ```
  set -a; . ./.env.local; set +a
  PYTHONPATH=src .venv/bin/python -c "from zzaimy.app.database_backend import connect; c=connect('data/platform/platform.db', readonly=True); print([c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('documents','doc_chunks','files','projects')])"
  ```
- 파일 수: 새 서버에서 `report` 를 다시 돌려 「옮김」의 개수·크기가 옛 서버 manifest 와 같은지, `--db` 의 「디스크에 없는 경로」가
  늘지 않았는지 본다.
- 검색: 담당자 계정으로 로그인해 문서 검색과 프로젝트 대화에서 근거가 붙는지 본다. 서빙 연결은 `bash scripts/110_serving_check.sh`,
  사업 문서 RAG 는 `scripts/173_rag_probe.py` 로 확인한다.
- 화면: 개발 현황의 반입 현황 탭에서 주기 작업이 새 서버에서 도는지(`sync_status.json` 시각), 기안기 문서함이 열리는지,
  구글 연결이 다시 묻지 않는지(토큰을 옮겼으면 그대로 된다) 본다.
