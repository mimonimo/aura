# 지운 스크립트 (2026-10-18)

끝난 실험·일회성 소급·새 스크립트로 대체된 스크립트를 저장소에서 지웠다. 어디서도 부르지 않는 것만 골랐다(크론·서비스·앱·다른 스크립트·문서·테스트 확인).
되살리려면: `git show 582877d1:scripts/archive/<파일 이름> > scripts/<파일 이름>`


끝난 실험과 일회성 데이터 처리 스크립트다. 현재 운영 경로에서는 쓰지 않는다.

## 2026-10-18 정리 — 어디서도 부르지 않는 일회성·대체된 스크립트

- `107_pool_size_check.py` — 후보 개수를 늘리면 검색이 나아지는가 — 리랭커를 GPU 에 올린 조건에서 잰다.
- `114_fill_doc_titles.py` — 이미 들어와 있는 문서의 이름을 본문에서 찾아 채운다.
- `117_serve_writer_on_thor02.sh` — ③ZZAIMY-Writer 베이스(Qwen3.8-27B)를 토르 02 에 올린다 — 문서 작업(채팅·검토·초안)용.
- `121_serve_ocr_on_thor.sh` — 토르에 OCR 서빙(쓰지 않음 — 판독은 DGX MinerU)
- `122_parse_path_duel.py` — 디지털 PDF 파싱 경로 비교 — 구조 추출(MinerU)과 글자층 직독을 같은 문서로 잰다.
- `132_access_backfill.py` — 열람 등급 소급 — 새 열(access_level)이 생긴 뒤 기존 문서·조각에 기본 규칙(access_policy)을 적용한다.
- `135_import_transcript.py` — 전사본 반입 — 쪽별 마크다운(제목 '## ', 표는 파이프 표 또는 <table>, 문단)을 문서의 조각으로 넣는다.
- `137_retable_text.py` — 표 평문 다시 만들기 — 표 셀 JSON 은 그대로 두고, 그 안의 검색·인용용 평문(text)과 본문(masked_text)의
- `166_load_inventory.py` — DGX 원본 목록(scripts/165 의 JSONL, 표준 입력)을 VM 원본 목록 장부(app/archive)로 들인다 — 문서함에 들인 원본은 문서 번호를 잇는다.
- `179_kind_backfill.py` — 서류 갈래 다시 매기기 — 반입 때 붙은 옛 갈래(documents.kind)를 지금 분류기(doc_routing.guess_kind)로 다시 정한다.
- `189_regulation_cleanup.py` — 기준 문서(규정) 정리 — 잘린 이름 고치기, 다른 기관 규정 되돌리기, 같은 규정의 겹친 판 되돌리기.
- `58_retrain_embed_guarded.sh` — Embed v0 재학습 가드 실행 (GPU 장비).
- `62_refix_overlay.py` — 예전 오버레이 수정 일회성 처리
- `87_spacing_check.py` — 붙어 버린 어절 길이 분포 — 띄어쓰기 복원 기준을 실측으로 정한다.
- `88_remask_documents.py` — 이미 적재된 문서를 지금 규칙으로 다시 가린다.
- `92_backfill_chat_topics.py` — 근거 기록이 없는 예전 대화에 근거를 채운다 — 대화 주제(chat_topics.py)를 정하기 위해.
- `serve-paddleocr-vl.sh` — PaddleOCR-VL 0.9B 도전자 서빙 — 학습 장비에서 실행.

## 2026-10-18 2차 — 대체된 서빙·반출 점검, 끝난 재반입·소급, 옛 임베딩 학습 사슬
되살리려면: `git show 72313f72:scripts/<파일 이름> > scripts/<파일 이름>`

- `85_ship_adapter.sh` — 장비 접속 정보는 저장소에 두지 않는다 — scripts/_hosts.sh 가 configs/hosts.env·.env.local 에서 읽는다.
- `86_serving_selftest.sh` — 학습→서빙 경로 자가 점검 (토르에서) — 실제 학습 없이 82·85 가 도는지 확인한다.
- `86_text_repair_check.py` — 글자 분해 복원 효과 측정 — 실제 적재 자료에 대고 얼마나 고쳐지는지 센다.
- `95_serving_selftest.sh` — 학습→서빙 경로 자가 점검 (토르에서) — 실제 학습 없이 82·94 가 도는지 확인한다.
- `98_train_embed_on_thor.sh` — 장비 접속 정보는 저장소에 두지 않는다 — scripts/_hosts.sh 가 configs/hosts.env·.env.local 에서 읽는다.
- `98_ui_check.sh` — UI 시각 검수 — 배포 전 필수.
- `115_reingest_all.py` — 문서함을 비우고 원본 파일에서 반입을 처음부터 다시 돌린다.
- `136_family_backfill.py` — 판본·갈래 소급 — 새 열(family·version_of·kind)이 생긴 뒤 기존 문서에 반입 규칙을 적용한다.
- `55_register_scraped.py` — 수집한 교내 문서를 선별해 기준 저장소에 일괄 등록한다.
- `56_clean_pages.py` — 긁어온 페이지 텍스트에서 사이트 공통 보일러플레이트를 걷어낸다.
- `89_backfill_titles.py` — 이미 적재된 문서에 정식 이름·기준 날짜를 채운다.
- `91_reprocess_docs.py` — 지정한 문서만 다시 처리한다 — 추출기를 고친 뒤 해당 문서에만 소급 적용할 때.
- `93_identify_all.py` — 사업 정보(문서 정체)를 아직 읽지 않은 문서를 한꺼번에 읽는다.
- `57_criteria_refresh_chain.sh` — 기준 저장소 갱신 체인 (GPU 장비에서 실행).
- `54_train_embed.py` — ZZAIMY-Embed v0.0 시험 파인튜닝 — 축 B 사이클 리허설.
