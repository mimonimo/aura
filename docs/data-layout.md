# 데이터 배치 — 무엇이 어디에 있나

운영 서버 `~/zzaimy-capstone/data/` 기준이다. 경로 규칙은 `src/zzaimy/app/paths.py` 하나에서 나오며, 근거는 ADR-0030·0031이다.

```
data/
  platform/
    platform.db                      원본 DB — 문서·조각·개체·장부(files)·설정. 이것이 정본이다
    accounts.json, llm_connections.json, nas_sources.json, gdrive_oauth.json, gdrive_tokens.json   설정(0600)
    access_audit.jsonl, gdocs_audit.jsonl, llm_usage.json                                        감사·사용 기록
    documents/                       1층 문서
      반입/<연도>/<유형>/<접수번호> <제목>/원본.<확장자>   (+ 원본_imgs/ 등 추출 부산물)
      첨부/<연도>/대화-<번호>/<시각> <파일명>
      생성/<연도>/<접수번호>/<시각> <종류>.<확장자>          초안·OCR·복원·추출결과 내보내기 사본
      보고/주간/<월요일>.md
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
`stored_path` 가 가리키므로 통째로 옮길 때는 `documents/` 를 같이 옮기고 경로 접두어만 바꾸면 된다(`db.repath_files`).

내보내기. `scripts/140_export_knowledge.py`(기본 전체 공개만). 결과 묶음은 다른 시스템에서 jsonl·npz 그대로 읽는다.

새 자료를 넣을 때. 파일이면 1층(`storage.adopt_original` 필수), 파생 자료면 2층, 학습 관련이면 3층. 경로를 코드에 직접
적지 않고 `paths.py` 에 함수를 하나 더한다.
