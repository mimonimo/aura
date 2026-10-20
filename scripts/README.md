# scripts

저장소의 실행 스크립트 목록이다(2026-10-18 정리). 번호는 만든 순서이고 하는 일 묶음은 아래 표가 정본이다.
주기 작업은 VM·DGX 크론과 사용자 서비스가 부른다(경로를 옮기지 않는다). 끝난 일회성·대체된 스크립트는 지웠다 — docs/notes/removed-scripts.md.
학습·데이터셋 묶음은 아스트라 담당이라 협의 없이 옮기거나 지우지 않는다.

## 주기 작업(크론·서비스) (13)

- `167_dgx_parse.py` — DGX 원본 보관소의 문서를 DGX 에서 가볍게 처리 — 문서함과 같은 처리기(DocumentProcessor)로 파싱·조각까지만(검토 의견 LLM·비전 판독 없이).
- `_hosts.sh` — 주소·계정·포트는 공개 저장소에 두지 않는다. 저장소 밖 값은 두 곳에서 읽는다:
- `generate_grounded_candidates.py` — Resumable internal-model candidate generation. Does not approve or train.
- `sync_generated_candidates.py` — Idempotently publish generated candidates to the existing review project.
- `zz_dgx_cycle.sh` — DGX 주기 처리(크론, 1분 — 앞 회차가 돌면 잠금으로 건너뜀) — 원본 목록·파일 사실 장부 갱신(165) → 새로 생기거나 바뀐 원본만 가볍게 처리(167).
- `zz_index.sh` — 사업 문서 의미 색인(pgvector 표 grant_vec, ADR-0057)만 따로 따라잡는다(170 --index). 그래프 재구축(1분 주기 안)이 길어도 색인은 는다.
- `zz_logcap.sh` — 주기 작업 기록 파일이 상한(기본 200MB)을 넘으면 직전 판 하나(.1)만 남기고 새로 시작한다.
- `zz_parsed.sh` — DGX 가 가볍게 처리한 결과(~/parsed)를 1분마다 받아 문서함에 들이고 새 문서의 서류 갈래를 27B(토르 03, review)로 판정한다(170 --parsed).
- `zz_quick.sh` — 1분마다 그래프 입력 지문을 견주어 바뀌었으면 그래프·양식·색인을 다시 짓는다(170 --quick). 바꾼 쪽이 따로 알릴 필요 없음.
- `zz_reconcile.sh` — 하루 한 번 맞추기 — 연동 점검(175 --find-reimport)이 「DGX 에서 정상 처리됐는데 문서함에 없는 원본」을 찾아 168 --rels 로 다시 들인다.  처리 실패 문서도 정리한다(199).
- `zz_run.sh` — 배치 작업 실행기 — 메모리 상한(넘으면 그 작업만 죽는다)과 강제 종료 우선순위(웹·DB 보다 먼저)를 건다.
- `zz_sync.sh` — DGX 원본 → VM 갱신(170). cron 과 수동 실행이 겹치지 않게 flock.
- `zz_tracks_ls.sh` — 학습 후보(두 묶음) → Label Studio 올리기를 주기로. 후보는 생성 타이머(zzaimy-track-generation, 5분)가 만들고,

## 학습·데이터셋(아스트라) (16)

- `83_sft_writer_qlora.py` — ZZAIMY-Writer(27B) QLoRA 지도학습 준비·실행.
- `152_build_real_sft.py` — 실문서로 Writer 학습쌍을 만들고 Label Studio 검수 프로젝트에 올린다.
- `153_build_tree_cot.py` — 문서 구조 단계 문답(CoT) 학습쌍 — 사업명 → 개요 → 목차 → 절의 요구 항목 → 절의 뼈대.
- `audit_sft_quality.py` — 읽기 전용: python scripts/audit_sft_quality.py data/training/tree_cot_pairs.jsonl.
- `candidate_status.py` — Print candidate inventory, excluding older results for identical source windows.
- `generate_track_candidates.py` — 로컬 원문 명세에서 두 묶음 후보 생성. 외부 수집·학습·자동 승인 없음.
- `manage_review_workspace.py` — Plan or apply latest-only cleanup to the existing grounded review project.
- `ocr_training_source.py` — 지정 PDF의 OCR 산출물과 페이지별 대조 자료를 보존한다. 학습 등록은 별도 검수 후 수행.
- `prepare_institutional_sources.py` — 명시적으로 지정한 로컬 규정/발전계획 5개를 읽어 학습 원문 명세를 만든다.
- `prepare_program_structure_sources.py` — 반입 대장의 사업-문서유형 관계만 추출한다. 파일 전문·파일명·수치는 학습 입력에 넣지 않는다.
- `retire_legacy_candidates.py` — 옛 CoT 활성 경로를 복구 가능한 보관함으로 이동. LS 삭제는 검증 백업 후에만 수행.
- `review_candidate.py` — Record source-checked review findings without granting training approval.
- `review_track_candidate.py` — 생성본을 보존하고 직접 검수한 재작성 사유를 별도 기록한다.
- `revise_review_candidates.py` — Apply source-checked standalone revisions; default is read-only preflight.
- `stage_source_templates.py` — 선택한 DGX 원본 서식을 별도 작업 폴더에서 DOCX로 변환하고 구조 통계를 보존한다.
- `sync_track_candidates.py` — 두 묶음의 새 후보만 Label Studio에 게시. 승인·구버전 자동 복원 없음.

## 측정·대결·점검 (31)

- `53_eval_retrieval.py` — 규정 검색 품질 측정 CLI — 본체는 zzaimy.eval.retrieval_eval (대시보드 카드의 유일한 수치 출처).
- `68_ocr_cer_bench.py` — OCR 글자 오류율(CER) 자동 벤치 — 디지털 PDF의 원문을 정답으로 쓴다.
- `70_paddle_duel.py` — 도전자 PaddleOCR-VL(0.9B) 대결 — 현행 MinerU와 같은 잣대(어절 F1)로. CPU.
- `79_ocr_structure_check.py` — OCR 구조 점검 — 문서 1건을 파이프라인으로 다시 읽고 표·그림·캡션 구조를 보고한다 (운영 VM).
- `84_ingest_audit.py` — 반입 자가 점검 — 문서를 하나하나 열지 않고 반입 상태를 판정한다.
- `90_llm_rerank_eval.py` — LLM 리랭커 평가 — 같은 질의 세트·같은 표본으로 하이브리드·크로스인코더·LLM 리랭커를 비교한다.
- `101_rerank_eval_on_thor.sh` — . "$(dirname "$0")/_hosts.sh
- `106_rerank_holdout_check.py` — 리랭커 학습본 검증 — 학습에 쓰지 않은 문서의 질의만으로 운영 경로에서 비교한다.
- `109_embed_holdout_check.py` — ①Embed 학습본 검증 — 학습에 쓰지 않은 문서의 질의로 '후보 진입률'과 최종 지표를 비교한다.
- `110_serving_check.sh` — 서빙 점검 — 토르 서비스가 살아 있는지, VM 설정이 그걸 가리키는지, 하한이 모델과 맞는지 본다.
- `113_question_axis_check.py` — 조각별 질문 축(doc2query)의 융합 가중을 정한다 — 홀드아웃 문서로만 잰다.
- `125_intake_check.py` — 반입 경로 자가 점검 — 같은 파일을 실제 반입 경로로 임시 문서함에 들여서 결과를 본다.
- `133_writer_baseline.py` — ③ZZAIMY-Writer 베이스라인 측정 — 학습 전 베이스 모델로 초안을 만들어 검증 결과를 기록으로 남긴다.
- `144_flow_selfcheck.py` — 문서 작업 흐름 자가 점검 — 반입부터 에이전트 실행 재료까지 한 프로젝트를 놓고 스스로 확인한다.
- `145_hwp_convert_check.py` — 한글 → 워드 변환 회귀 검사 — 문서함의 한글 문서(hwp·hwpx) 전부를 변환해 원본 구조와 대조한다.
- `148_docs_roundtrip_check.py` — 한글 → docx → 구글 독스 왕복 품질 검사 — 어떤 한글 문서든 독스에서 깔끔한가를 쪽 단위 잣대로 잰다.
- `149_convert_corpus_check.py` — 변환 회귀 검사를 문서함 밖 파일 묶음에도 — 폴더의 hwp·hwpx 전부를 145 와 같은 잣대(글자·표·그림·구조 오류)로 잰다.
- `154_docs_path_compare.py` — 한글 → 구글 독스 변환 경로 견주기 — 어느 길이 독스에서 원본에 가장 가까운지 잰다.
- `171_ocr_duel_grants.py` — 사업 문서 OCR 대결 — 스캔 PDF 판독 도구(MinerU·docling·Writer 27B 비전)를 같은 쪽·같은 잣대로 견준다.
- `173_rag_probe.py` — 사업 문서 RAG 실측 — 그래프에서 시험 질문을 뽑아 검색(grant_search)과 답변(AgentResponder, 27B)을 실제로 돌린다.
- `174_ontology_audit.py` — 온톨로지 점검 — 지식 그래프(kg_nodes·kg_edges)가 지켜야 할 규칙을 기계로 센다. 고치지 않는다(읽기만).
- `175_pipeline_audit.py` — 반입 연동 점검 — 원본 → 문서함 → 색인 → 그래프까지 단계마다 다음 단계로 넘어가지 못한 수를 센다(읽기만, 고치지 않는다).
- `181_fill_gap_report.py` — 못 채운 절 모아 보기 — 절 작성이 비우거나 덜 채운 절을 원인 갈래별로 센다(app/fill_gaps).
- `184_graph_relation_probe.py` — 그래프 단계 실측 — 관계를 묻는 질문에서 검색(grant_search)이 관계의 양쪽 문서를 근거로 가져오나.
- `188_graphrag_vs_fulltext.py` — GraphRAG(온톨로지 + RAG) 대 풀텍스트 RAG — 같은 시험지로 검색 정확도·답변 시간·답변 품질을 견준다.
- `190_kind_read_sample.py` — 서류 갈래 표본 대결 — 27B 판정(app/kind_read)과 제목 낱말 규칙(doc_routing.guess_kind)을 같은 문서로 견준다.
- `193_hybrid_weight_clean_sweep.py` — 하이브리드 어휘 가중(HYBRID_W_A) 재측정 — 임베딩 학습에 쓰지 않은 문서의 질의만으로.
- `194_candidate_loss_audit.py` — 규정 검색 후보 단계 손실 감사 — 융합 순위에는 정답이 있는데 리랭커 입력 후보 20 에서 빠진 까닭.
- `195_model_swap_check.py` — 생성 모델 교체 점검 — 한 번의 실행으로 검색 실측·답 표본·갈래 판정 표본을 돌리고 기준(data/eval/model_baseline.json)과 표로 견준다.
- `audit_retrieval_cases.py` — Validate local selection candidates; optionally export independently reviewed gold.
- `build_graph_retrieval_probes.py` — Build business-scoped self-retrieval probes. These are not gold or SFT data.

## 그 밖(운영·처리·서빙·소급 도구) (67)

- `51_synth_queries.py` — 합성 질의 생성 배치 (model-plan §1 Embed 데이터 파이프라인의 축소판).
- `52_embed_chunks.py` — 규정 조각 임베딩 갱신 (임베딩 RAG 재료).
- `66_reindex.sh` — 재색인 — 규정 조각을 등록·해제·재분할한 뒤 조각 임베딩 색인을 맞춘다.
- `68_labelstudio_token.py` — Label Studio 고정 토큰 프로비저닝 보조 — scripts/68_labelstudio_token.sh 가 부른다.
- `68_labelstudio_token.sh` — Label Studio 고정 토큰 프로비저닝 — 사람이 토큰을 복사해 붙여넣지 않게 한다.
- `70_extract_entities.py` — 개체 추출 배치 (지식 그래프 2단계, ADR-0010).
- `74_ingest_corpus.py` — 수집 코퍼스 → RAG 기준문서(regulation chunk) 인제스트.
- `75_embed_corpus.py` — corpus_pilot.db 조각 KURE 임베딩 — /dev/corpus 의미검색용 (CPU).
- `75_rechunk_regulations.py` — 규정 조각 재분할 — 조각화 규칙이 바뀐 뒤 기존 문서에 소급 적용한다.
- `78_post_deploy_smoke.py` — 배포 후 실동작 점검(운영 VM, 실제 DB) — 화면·검색·마스킹·연동을 한 번에 돈다.
- `79_register_llm_connection.py` — OpenAI 호환 추론 서버를 LLM 연결로 등록한다.
- `80_install_smb.sh` — NAS(SMB) 수집용 smbprotocol 오프라인 설치 — Mac 에서 받은 휠을 서버 data/tmp/wheels 에 올린 뒤 실행한다.
- `82_serve_writer.sh` — ZZAIMY-Writer 서빙 — 젯슨 토르에서 vLLM 컨테이너로 OpenAI 호환 규격을 띄운다.
- `90_slice_demo.py` — 수직 슬라이스 관통 데모.
- `94_ship_adapter.sh` — . "$(dirname "$0")/_hosts.sh
- `96_embed_on_thor.sh` — . "$(dirname "$0")/_hosts.sh
- `97_paraphrase_queries.py` — 바꿔 말한 질의 세트 — 규정 문구를 모르는 담당자가 상황으로 묻는 질문을 흉내 낸다.
- `99_deploy.sh` — 운영 VM 배포 — 정본은 깃허브 하나다. VM 은 원격에서 받아 그 상태 그대로 돌린다.
- `102_serve_reranker_on_thor.sh` — . "$(dirname "$0")/_hosts.sh
- `103_serve_embed_on_thor.sh` — . "$(dirname "$0")/_hosts.sh
- `104_train_rerank_on_thor.sh` — . "$(dirname "$0")/_hosts.sh
- `105_rerank_floor.py` — 리랭커 점수 하한 재측정 — 모델을 바꾸면 눈금이 달라지므로 하한도 다시 정한다.
- `108_train_embed_v2_on_thor.sh` — . "$(dirname "$0")/_hosts.sh
- `111_chunk_questions.py` — 조각마다 '이 조각으로 답할 수 있는 질문'을 만들어 둔다 — 검색용 보조 축(doc2query).
- `112_question_index_on_thor.sh` — . "$(dirname "$0")/_hosts.sh
- `116_ship_and_serve.sh` — 학습본을 서빙 장비로 옮기고 서비스까지 바꾼다 — 이관 단계를 한 번에 잇는다.
- `118_fill_reviews.py` — 검토 의견이 비어 있는 문서를 채운다 — 반입 때 모델이 오르내려 놓친 것들.
- `119_after_ingest.sh` — 반입 뒤 마무리 — 검토 채우기 → 개체 추출 → 색인 재계산 → 점검.
- `123_promote_to_criteria.py` — '문서 추출'로 들어온 문서를 기준 문서로 올린다 — 다시 파싱하지 않고.
- `124_refresh_names.py` — 문서 이름을 지금의 규칙(app/doc_naming)으로 다시 매긴다.
- `126_serve_writer.sh` — ③ZZAIMY-Writer(Qwen3.8-27B)를 토르에 올린다 — 어느 토르든, 어느 판(bf16·NVFP4)이든 같은 스크립트로.
- `127_adr_index.py` — ADR 색인(docs/decisions/README.md 의 표)을 파일에서 만든다 — 손으로 적으면 줄이 빠지기 쉽다.
- `128_kure2_index_on_thor.sh` — . "$(dirname "$0")/_hosts.sh
- `131_access_scenarios.py` — 권한 밖 질문 시나리오 자가 점검 — 운영 DB 로, 모델 없이, 규칙과 검색 범위만 확인한다.
- `134_render_pages.py` — 전사(판독) 대상 문서의 쪽을 그림으로 뽑는다 — 사람이 원본을 보고 옮겨 적거나 판독 결과와 대조하기 위한 준비.
- `138_layout_migrate.py` — 저장 구조 이관 — inbox 에 임의 이름으로 쌓인 원본을 문서 폴더(ADR-0030)로 옮기고 DB(stored_path·doc_assets·files)를 맞춘다.
- `139_layout_knowledge.py` — 데이터 체계 이관(ADR-0031) — 지식(색인·측정·옛 파일럿)·캐시·학습 자료를 세 층 자리로 옮긴다.
- `140_export_knowledge.py` — 지식 내보내기(ADR-0031) — 문서 목록·조각·개체·문서-개체 간선·조각 임베딩을 외부에서 쓸 수 있는 형식으로 묶는다.
- `143_read_image_pages.py` — 접수 문서의 그림 쪽(글자층 없는 쪽)을 비전 모델로 판독해 조각으로 더한다 — 운영 서비스 밖에서 한 번에 여러 묶음.
- `147_kordoc_parity.py` — kordoc 대 우리 파서 대조 — 문서함의 한글 문서를 두 길로 읽어 시간·글자·표·그림을 나란히 (ADR-0033 채택·갱신 근거).
- `156_intake_files.py` — 고른 파일들을 한 프로젝트로 문서함에 반입한다 — 실문서 파일럿용(ADR-0047).
- `157_build_kg.py` — 온톨로지 v3 그래프를 만든다 — 사업 → 연차 → 문서 → 절, 관계마다 기준·근거(ADR-0048).
- `158_kg_judge.py` — 그래프 연결 정밀도 점검 — 이은 절 쌍과 버린 후보 쌍을 표본으로 27B 에 판정시킨다(평가에만, 그래프에는 쓰지 않는다).
- `161_select_core.py` — 사업별 뼈대 문서 고르기 — 원본 보관소에서 사업마다 기본계획·공고·계획서·실적보고서·평가·지침의 최종본만 고른다.
- `162_business_template.py` — 사업별 공통 양식 — 지식 그래프의 단위(여러 연차 문서에 되풀이되는 절)로 사업 × 문서 갈래(계획서·실적보고서)의 공통 뼈대를 만든다.
- `164_sync_apply.py` — 170(VM 동기화)이 받은 편지함에 받아 둔 새 뼈대 문서를 반입하고 그래프·양식을 다시 짓는다.
- `165_dgx_inventory.py` — DGX 원본 보관소 전체 목록 — 파일마다 경로·크기·수정 시각·형식·사업·갈래·연차(JSONL). VM 의 원본 목록 장부(app/archive)로 들인다.
- `168_import_parsed.py` — DGX 에서 가볍게 처리한 문서(scripts/167 의 JSONL, 표준 입력)를 VM 문서함에 'DGX 보관 문서'로 들인다.
- `169_upload_templates.py` — 폐기된 목차 요약 양식 게시 경로. 기존 호출은 외부 변경 없이 차단한다.
- `170_vm_sync.py` — VM 혼자 도는 원본 갱신 — DGX 원본 보관소를 읽기 전용 rsync 로 보고(파일 내용은 고른 것만), 원본 목록 장부·사업 분류·뼈대 반입·그래프·양식을 갱신한다.
- `172_common_templates.py` — 문서 갈래별 공통 양식 — 계획서·실적보고서처럼 어느 사업에든 쓰는 구글 독스 작업용 뼈대.
- `172_ocr_visual_review.py` — 동일 PDF 페이지를 이미지로 판독한 결과를 원본과 나란히 보관한다.
- `177_ontology_export.py` — 온톨로지 표준 내보내기·검사(ADR-0056) — OWL 설계도·RDF 인스턴스를 쓰고 SHACL 규칙으로 검사한다.
- `180_build_gdocs_templates.py` — 구글 독스 공통 양식 4종을 독스 API 로 직접 만든다(src/zzaimy/ingest/gdocs_templates.py).
- `182_criteria_kind_backfill.py` — 공고 갈래 기준 문서 소급 연결 — 기존 프로젝트의 공고·지침·평가 기준·규정·기본계획 갈래 문서를 프로젝트 기준 문서로 잇는다.
- `183_byproduct_cleanup.py` — 부산물 폴더 문서 정리(소급) — 규칙이 생기기 전에 처리·반입된 부산물 폴더 속 파일을 문서함·장부에서 걷는다.
- `186_school_program_ledger.py` — 교내 재정지원사업 현황(연도별 엑셀) → 외부 확인 장부(kg_external.json)의 사업 이름·별칭·연도 보강.
- `187_remove_outscope_docs.py` — 필요 없는 문서를 라이브러리에서 뺀다 — 지출·계약 증빙(절대 규칙 11)·같은 본문 여분·빈 문서·도면·개인 제출물(--what).
- `188_ledger_review_apply.py` — 검토를 마친 장부 변경(configs/ledger_reviews/*.json)을 외부 확인 장부(kg_external.json)에 적용한다.
- `191_kind_read_all.py` — 서류 갈래 27B 일괄 판정 — DGX 처리 결과(scripts/167 parsed-*.jsonl)의 문서마다 app/kind_read 로 갈래를 정해 ~/parsed/kind27
- `192_kind_apply.py` — 27B 갈래 판정(scripts/191 → kind27b-*.jsonl)을 문서 갈래(documents.kind)에 반영한다(VM).
- `196_program_rejudge.py` — 사업 분류 다시 보기 — 폴더 판정·이름 뽑기가 잘못 묶은 원본을 27B 가 파일 하나씩 보고 사업을 정한다.
- `197_trainset_catalog.py` — 학습셋 목록 — DGX 의 학습 준비 폴더(data/train 의 staging·releases·baselines)에서 설명 정보만 모아 ~/parsed/trainsets.json
- `198_forms_to_drive.py` — 양식을 드라이브에 맞춘다 — 원본 맞추기(164)가 새로 만든 공통 양식 docx 를 구글 독스로 바꿔 「ZZAIMY/공통 양식」에 올린다.
- `build_question_tree_review.py` — 실문서 완성본의 질문–정답 후보를 새 Label Studio 검수 묶음으로 생성. 삭제 없음.
- `publish_authored_qa.py` — 직접 작성한 JSON 문답을 SSH 로 교내 Label Studio 누적 프로젝트에 게시한다.
- `set_platform_database.py` — Set platform DB keys in an existing environment file (service must be stopped).
