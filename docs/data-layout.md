# 데이터 배치 — 무엇이 어디에 있나

운영 서버 `~/zzaimy-capstone/data/` 기준이다. 경로 규칙은 `src/zzaimy/app/paths.py` 하나에서 나오며, 근거는 ADR-0030·0031이다.
문서·조각·개체·장부의 정본은 2026-09-29부터 PostgreSQL 운영 DB다(ADR-0037, 접속은 `ZZAIMY_DATABASE_URL`). 사업 문서 조각의
조밀 검색 벡터도 같은 DB 의 pgvector 표에 있다(ADR-0057). 아래 `platform.db` 는 전환 전 SQLite 로, 보존만 한다.

```
data/
  platform/
    platform.db                      전환 전 SQLite DB(보존). 지금 정본은 PostgreSQL
    accounts.json, llm_connections.json, nas_sources.json, gdrive_oauth.json, gdrive_tokens.json   설정(0600)
    access_audit.jsonl, gdocs_audit.jsonl, llm_usage.json                                        감사·사용 기록
    documents/                       1층 문서
      반입/<연도>/<유형>/<접수번호> <제목>/원본.<확장자>   (+ 원본_imgs/ 등 추출 부산물)
      첨부/<연도>/대화-<번호>/<시각> <파일명>
      생성/<연도>/<접수번호>/<시각> <종류>.<확장자>          초안·OCR·복원·추출결과 내보내기 사본
      보고/주간/<월요일>.md
      기안기/<부서>/<폴더>/<제목>.hwpx                   한글 문서함(부서 디렉터리). 부서마다 .판/(저장 전 판)·.휴지통/(지운 문서)
        공통/공통 양식/<목적>/                           어디서나 쓰는 빈 양식(플랫폼이 만든 것 + 지난 문서에서 고른 것)
        공통/지난 사업 문서/<사업>/<목적>/               사업 전용 양식(양식이 3개 이하인 사업은 목적 폴더 없이)
        ~<아이디>/                                       내 폴더 — 처음 열 때 생긴다. 부서 이름은 ~ 로 시작하지 않아 겹치지 않는다
    knowledge/                       2층 지식 — 문서에서 뽑은 파생 자료, 밖으로 낼 수 있다
      index/chunk_embeddings.npz(.meta.json), doc_vectors.npz, question_embeddings.npz
      eval/retrieval-*.json, llm-rerank-*                      검색 품질 측정
      exports/<날짜-시각>/manifest.json, documents.jsonl, chunks.jsonl, entities.jsonl, doc_entities.jsonl, embeddings.npz
      corpus_pilot/                                              옛 국고 파일럿(참고)
    cache/lines, pagecache, restored  재생성 가능. 지워도 된다
    backup/                          DB·조각·색인 백업, inbox-leftover
  train/                             3층 모델 — DGX 의 ~/zzaimy/train 과 같은 모양
    datasets/<모델>/<판>/sft.jsonl …
    baselines/<모델>/baseline.json (+ 날짜본)
    models/<모델>/<판>/               어댑터·병합본·NVFP4
    runs/<모델>/<판>/                 학습 로그·지표
  samples/                           실물 표본(git 제외)
```

옮기기. 뿌리 둘만 바꾼다: `ZZAIMY_DATA_DIR`(기본 data/platform), `ZZAIMY_TRAIN_DIR`(기본 data/train). 문서 폴더는 DB 의
`stored_path` 가 가리키므로 통째로 옮길 때는 `documents/` 를 같이 옮기고 경로 접두어만 바꾸면 된다(`db.repath_files`). 다른 서버로 옮길 때 무엇을 옮기고 뺄지는
[서버 이전](ops/migration.md)(`scripts/209_migration_manifest.py`).

한글 문서함. 파일 장부는 files 표(kind="webhwp")이고 올린 사람·제목은 설정 `webhwp_meta:<id>` 에 있다. 지난 문서에서 모은 양식은
`app/past_forms.py` 하나가 공통 양식·지난 사업 문서·휴지통으로 가른다(정리 `scripts/208_past_forms_tidy.py` plan·apply·restore, 새 양식 등록
`scripts/200_webhwp_forms.py register`). 10-23 정리 결과는 공통 양식 232 · 지난 사업 문서 489 · 휴지통 522 이다.

구글 토큰. 사용자 토큰과 부서 드라이브 토큰은 같은 보관소(`gdrive_tokens.json`, 메일별, 0600)에 둔다. 부서와 그 메일의 이음은 DB 설정
`google_account_dept:<부서>`, 부서 폴더는 `google_root:<부서>`, 부서가 직접 연결한 계정이면 `google_root_shared:<부서>`=1 이다.
부서 계정 토큰은 그 부서 폴더 안 문서 만들기·다루기에만 쓰고(`gdrive_files.doc_account_for`), 드라이브 찾기·가져오기·쓸 계정 목록은
본인이 연결한 계정만 받는다(`usable_accounts`). 서버를 옮길 때 토큰 파일은 「비밀값」 갈래로 따로 옮긴다([서버 이전](ops/migration.md)).

내보내기. `scripts/140_export_knowledge.py`(기본 전체 공개만). 결과 묶음은 다른 시스템에서 jsonl·npz 그대로 읽는다.

새 자료를 넣을 때. 파일이면 1층(`storage.adopt_original` 필수), 파생 자료면 2층, 학습 관련이면 3층. 경로를 코드에 직접
적지 않고 `paths.py` 에 함수를 하나 더한다.
