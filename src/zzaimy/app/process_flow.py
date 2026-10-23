"""개발 「처리 흐름」 — 문서가 들어와 검색·답변·초안이 되고, 학습 데이터와 학습본이 되기까지를 단계별로 설명한다.

설명 글은 짧은 일상어로 적고(관련 코드는 code 칸에 따로), 설명의 근거는 코드·설계 기록(ADR)이며, 수치는 모두 기계 산출물에서 읽는다(metrics 의 live·audit 지표,
그래프 표, 온톨로지 검사 보고, 검색 품질·비교 측정 결과 파일). 산출물이 없으면 「—」 로 둔다.
상태는 세 가지로 가른다: 운영 중(지금 돌고 있음) · 일부 운영 · 준비 중(코드·계획은 있으나 돌리지 않음).
"""
from __future__ import annotations

import json
from pathlib import Path

from zzaimy.app.doc_routing import KINDS

# 핵심 용어 — 무엇인지(what), 우리 시스템에서(ours), 관련 코드(code — 화면에는 작은 회색 줄로)
TERMS = (
    {"name": "온톨로지", "what": "무엇을 어떤 종류로 기록할지 정한 규칙표",
     "ours": f"사업 → 연차 → 문서 → 절, 서류 종류 {len(KINDS)}종, 관계 이름과 관계마다 근거를 적는 규칙",
     "code": "app/kg_explore · graph/ontology_rdf · graph/shapes.ttl"},
    {"name": "지식그래프", "what": "규칙표대로 실제 사업·문서 사이 관계를 채운 데이터",
     "ours": "「LINC 3.0」 → 「2023년(2차년도)」 → 「2023 계획서」 → 「사업 실적」 절, 같은 연차 결과보고서와 짝",
     "code": "PostgreSQL 표 kg_nodes · kg_edges"},
    {"name": "RAG", "what": "답하기 전에 문서에서 관련 부분을 찾아 모델에 함께 건네는 방식",
     "ours": "문서를 조각으로 잘라 키워드·의미 색인으로 찾고, 찾은 조각을 근거로 27B 가 답한다",
     "code": "doc_chunks · regulation_chunks · grant_lex · grant_vec"},
    {"name": "GraphRAG", "what": "RAG 로 찾기 전에 지식그래프로 범위를 먼저 좁히는 방식",
     "ours": "「어느 사업·몇 차년도·어떤 서류」 를 먼저 정해 다른 해의 비슷한 문서가 섞이지 않게 한다",
     "code": "app/grant_search · graph/retrieve · graph/question_read"},
    {"name": "파인튜닝(SFT)", "what": "모델에게 일하는 방식을 가르치는 학습",
     "ours": "질문 읽기·근거로 절 쓰기 같은 방식만 가르치고, 학교 지식은 검색으로 공급",
     "code": "데이터·학습 화면 · DGX 학습"},
)

# 장비 — 역할과 사양
HOSTS = (
    {"name": "원본 보관 PC", "role": "학교 실문서 보관(사업별 폴더)", "spec": "DGX 로 복사(rclone)"},
    {"name": "DGX", "role": "원본 보관 · 문서 읽기(OCR) · 학습", "spec": "GB10 GPU · 통합 메모리 121GB · 디스크 3.7TB"},
    {"name": "운영 VM", "role": "플랫폼 화면 · DB · 검색 색인 · 지식그래프", "spec": "CPU 8 · 메모리 64GB · PostgreSQL(pgvector)"},
    {"name": "토르 02", "role": "Writer 27B 운영(대화·초안·문서 작업) · SFT 후보 writer-sft(LoRA 어댑터) 비교", "spec": "NVFP4 서빙 · 포트 8001"},
    {"name": "토르 03", "role": "검토·문서 읽기용 27B · 임베딩 Embed v2 · 리랭커 Rerank v1", "spec": "임베딩 8016 · 리랭커 8015"},
)

# 처리 단계 — 순서대로. how 는 4줄까지, code 는 화면에 작은 회색 「관련 코드」 줄. nums 는 (지표 키, 이름) — 값은 snapshot() 이 채운다
STAGES = (
    {"n": 1, "name": "원본 수집", "where": ["원본 보관 PC", "DGX", "운영 VM"], "state": "운영 중", "every": "DGX 목록 30분 · VM 장부 10분",
     "does": "학교 원본을 DGX 에 모으고, 운영 VM 이 파일마다 원본 장부에 적는다.",
     "how": ["원본 보관 PC → DGX, 같은 파일은 내용 지문으로 한 번만",
             "VM 장부에는 바뀐 것만 반영(새로·바뀜·옮김·없어짐)",
             "처리 대상: 문서 형식 20종(hwp·hwpx·pdf·오피스 등), 중복·지출 증빙 제외",
             "사진·압축·중복·증빙은 지우지 않고 따로 모아 두는 폴더로"],
     "out": "원본 장부",
     "why": "원본은 DGX 한 곳에 두고, VM 에는 처리 결과와 색인만 둔다",
     "code": "scripts/165 · 170 · app/archive_cleanup",
     "nums": [("archive_files", "원본 장부 전체"), ("archive_originals", "학교 원본"), ("audit_targets", "처리 대상 문서")],
     "link": ("/dev/intake", "문서 가져오기")},
    {"n": 2, "name": "문서 읽기", "where": ["DGX", "운영 VM"], "state": "운영 중", "every": "원본이 바뀌면 1분 안에",
     "does": "원본에서 본문과 구조(제목·표·그림·쪽)를 읽는다. 학교 원본은 DGX, 직접 올린 문서는 VM 이 같은 방식으로 읽는다.",
     "how": ["한글(hwp·hwpx): kordoc 우선, 안 되면 다른 읽기 도구로",
             "PDF: 글자가 든 PDF 는 바로 읽기, 스캔본은 OCR(MinerU)",
             "옛 오피스 형식은 LibreOffice 로 바꿔 읽기",
             "표는 병합 칸까지 칸 구조로, 읽지 못한 문서는 까닭을 남김(암호·손상 등)"],
     "out": "본문, 구조 조각, 표, 그림",
     "why": "표·절 구조가 남아야 「어느 절에 무엇이 있는가」 를 찾을 수 있다",
     "code": "ingest/parsers · scripts/167 · 168",
     "nums": [("audit_failed", "읽기 실패")], "link": ("/dev/intake", "문서 가져오기 — 실패 사유")},
    {"n": 3, "name": "문서 등록·분류", "where": ["운영 VM"], "state": "운영 중", "every": "5분",
     "does": "읽은 결과를 라이브러리에 문서로 등록하고 서류 종류·사업·연차·판본을 붙인다.",
     "how": [f"서류 종류 {len(KINDS)}종(공고·양식·계획서·결과보고서 등)은 27B 가 제목·폴더·본문 앞부분을 읽고 고름",
             "사업 배정은 제목·첫머리·폴더 경로로, 엇갈리면 「검토 대기」",
             "같은 내용은 한 번만, 거의 같은 문서는 판본으로 이어 최신판을 대표로",
             "문서 이름은 파일 이름이 아니라 본문 제목으로"],
     "out": "라이브러리 문서",
     "why": "종류·사업·연차가 붙어야 그래프가 문서를 제자리에 놓는다",
     "code": "app/kind_read · app/doc_family · app/doc_title",
     "nums": [("documents", "등록 문서"), ("grant_reviewed", "처리 완료 사업 문서")], "link": ("/", "라이브러리")},
    {"n": 4, "name": "조각·색인", "where": ["운영 VM", "토르 03"], "state": "운영 중", "every": "2분",
     "does": "문서를 검색 단위 조각으로 자르고 키워드 색인과 의미 색인을 만든다.",
     "how": ["사업 문서: 읽은 구조 그대로(본문·제목·표), 문서당 2,000조각까지",
             "규정: 조문(제N조) 단위",
             "키워드 색인은 한국어 형태소 분석(Kiwi), 의미 색인은 Embed v2 1024차원",
             "볼 권한이 없는 문서는 검색 후보에도 오르지 않음"],
     "out": "검색 조각, 키워드 색인, 의미 색인",
     "why": "키워드는 사업 이름 같은 드문 말에, 의미 색인은 표현이 다른 질문에 강하다",
     "code": "app/grant_lex · app/embed_search · app/regulations",
     "nums": [("grant_chunks", "사업 문서 조각"), ("embedding_index", "의미 색인 조각"), ("regulation_chunks", "규정 조각")],
     "link": ("/dev/db", "데이터 현황")},
    {"n": 5, "name": "지식그래프 구축", "where": ["운영 VM"], "state": "운영 중", "every": "문서가 바뀌면 15분에 한 번까지",
     "does": "문서를 사업 → 연차 → 문서 → 절로 세우고, 근거가 있는 관계만 잇는다.",
     "how": ["노드 8종: 사업 묶음 · 사업 · 기관 · 연차 · 문서 · 절 · 단위과제 · 성과지표",
             "관계: 포함 · 계획↔실적 짝 · 평가 · 앞뒤 단계 사업 · 주관 기관 · 성과지표",
             "관계마다 근거를 함께 저장, 근거 없는 관계는 만들지 않음",
             "사업 이름 변경·편입은 공개 출처(URL)가 있는 것만"],
     "out": "그래프 노드·관계",
     "why": "같은 이름의 문서가 연차마다 많아, 사업·연차·서류를 점 하나로 잡아야 검색이 맞는다",
     "code": "scripts/157 · graph/ · kg_external.json",
     "nums": [("graph_nodes", "노드"), ("graph_edges", "관계"), ("graph_doc_nodes", "문서 노드"),
              ("edge_plans_reports", "계획↔실적 짝(문서)"), ("edge_evaluates", "평가 관계")],
     "link": ("/graph/explore", "그래프 탐색")},
    {"n": 6, "name": "온톨로지 검사", "where": ["운영 VM"], "state": "운영 중", "every": "그래프를 다시 지을 때",
     "does": "그래프가 규칙표에 맞는지 검사한다. 어긋나면 위반, 확인이 필요하면 경고.",
     "how": ["그래프를 표준 형식(OWL/RDF)으로 내보냄",
             "규칙 6가지: 사업 이름 · 연차는 사업 하나에 · 문서 연도·종류 · 기관 연결 · 관계 근거 · 출처 URL",
             "사업·연차에 속하지 않는 문서는 「미분류」 경고로 사람이 확인"],
     "out": "검사 보고",
     "why": "그래프가 커질수록 잘못 이은 관계가 검색을 흐린다 — 기계가 먼저 거른다",
     "code": "scripts/174 · 177 · graph/shapes.ttl",
     "nums": [("shacl_violations", "위반"), ("shacl_warnings", "미분류 경고")], "link": ("/graph/explore", "그래프 탐색")},
    {"n": 7, "name": "규정 검색", "where": ["운영 VM", "토르 03"], "state": "운영 중", "every": "질문마다",
     "does": "규정·지침 질문에 맞는 조문을 찾는다.",
     "how": ["키워드 검색과 의미 검색을 섞어 후보 20개",
             "리랭커(Rerank v1)가 질문과 조문을 함께 읽고 순서를 다시 매김",
             "점수가 너무 낮으면 버리고, 근거가 없으면 없다고 답함",
             "검색 대상은 기준 문서로 등록한 규정만(새 판은 자동 등록)"],
     "out": "근거 조문",
     "why": "규정 질문은 맞는 조문 하나가 중요하다",
     "code": "app/regulations · app/embed_search",
     "nums": [("reg_r1", "운영 구성 R@1"), ("reg_r5", "운영 구성 R@5"), ("regulation_docs", "기준 문서")],
     "link": ("/dev/quality", "품질·성능")},
    {"n": 8, "name": "사업 문서 검색(GraphRAG)", "where": ["운영 VM", "토르 02"], "state": "운영 중", "every": "질문마다",
     "does": "질문에서 사업·연차·서류 종류를 읽어 그래프로 범위를 좁힌 뒤 그 안에서 찾는다. 새 사업도 문서만 들어오면 바로 대상이 된다.",
     "how": ["그래프로 사업 → 연차 → 문서 범위 정하기",
             "계획서·실적보고서를 함께 물으면 짝 문서도 함께",
             "27B 가 질문 뜻을 읽어 사업·관계를 고름(20초 안에 못 읽으면 규칙으로)",
             "좁힌 범위 안에서 키워드 + 의미 검색, 최종본만·문서당 2조각"],
     "out": "근거 조각 + 경로(사업 > 연차 > 문서)",
     "why": "풀텍스트 검색만으로는 정답 문서가 상위에 잘 들지 않는다 — 아래 수치",
     "code": "app/grant_search · graph/retrieve · graph/question_read",
     "nums": [("graph_read", "27B 질문 읽기"), ("cmp_fulltext", "풀텍스트 RAG"), ("cmp_graph", "온톨로지+RAG"), ("cmp_graph_read", "+27B 읽기")],
     "link": ("/dev/quality#grant-search", "품질·성능 › 사업 문서 검색")},
    {"n": 9, "name": "답변·문서 작업", "where": ["토르 02", "운영 VM"], "state": "운영 중", "every": "질문마다",
     "does": "27B(Writer)가 단계마다 도구를 골라 찾고 읽고 쓴다. 코드는 고른 도구를 실행하고 기록한다.",
     "how": ["답변 방식 3가지: 문서 근거(기본) · 모델 지식(「출처 없음」 표시) · 웹 검색(질문 글만 밖으로)",
             "도구: 찾기 · 절 읽기 · 문서 만들기 · 절 쓰기·고치기 · 값 묻기 · 한글로 내보내기",
             "근거에 없는 수치는 가리고, 출처는 실제로 찾은 문서만",
             "초안: 양식 고르기 → 절마다 쓰기 → 품질 점검 → 한글 양식에 서식 그대로 채우기"],
     "out": "답(출처 포함), 작업 문서, 한글 완성본",
     "why": "판단은 모델이, 수치와 사실은 근거에서만",
     "code": "app/doc_agent · app/answer_flow · app/drafting",
     "nums": [("projects_active", "진행 중 프로젝트")], "link": ("/chat", "에이전트 채팅")},
    {"n": 10, "name": "학습 데이터 만들기", "where": ["운영 VM", "DGX"], "state": "운영 중", "every": "필요할 때",
     "does": "학습 후보를 만들고 자동 검사와 사람 검수를 거쳐 승인된 것만 학습셋으로 낸다.",
     "how": ["두 묶음: 규정·발전계획 적용 문답 · 사업 구조·검색 활용",
             "실문서 절 작성쌍(양식 × 완성본)과 문서 구조 단계 문답",
             "실적·예산 같은 사실 문답은 넣지 않음 — 수치가 보이면 거부",
             "자동 검사(근거·구조·맥락·개인정보) → 사람 검수 → 승인"],
     "out": "승인 학습셋(학습·검증·시험)",
     "why": "사실을 학습시키면 모델이 수치를 지어낸다 — 일하는 방식만 가르친다",
     "code": "scripts/152 · 153 · dataset/",
     "nums": [("trainset", "SFT 학습셋(DGX 목록)")], "link": ("/dev/train", "데이터·학습")},
    {"n": 11, "name": "학습", "where": ["DGX", "토르 03"], "state": "일부 운영", "every": "승인 데이터가 모이면",
     "does": "검색 두 모델은 학습본으로 운영 중이다. Writer 는 SFT 를 마치고 운영 모델과 비교하고 있다.",
     "how": ["Embed v2: 합성 질의 1,493건 대조 학습 — 정확한 질문 R@1 0.811 → 0.856(운영 중)",
             "Rerank v1: 운영 후보 상위 10개로 순위 학습 — R@1 0.649 → 0.711(운영 중)",
             "Writer: writer-v1 학습셋(학습 4,434 · 검증 346 · 시험 220)으로 DGX 에서 LoRA SFT(bf16, r32·2에폭) 마침(10-22)",
             "어댑터 writer-sft 를 토르 02 에서 운영 Writer 와 비교 중(10-23~), 병합·NVFP4 재양자화본 writer-sft-merged 준비 중"],
     "out": "학습본, 모델 카드",
     "why": "학습 효과는 같은 시험지로 학습 전후를 견줘야 말할 수 있다",
     "code": "docs/model-cards · scripts/210",
     "nums": [("train_live", "최근 학습 기록")], "link": ("/dev/train", "데이터·학습")},
    {"n": 12, "name": "서빙·교체", "where": ["토르 02", "토르 03", "운영 VM"], "state": "운영 중", "every": "학습본이 생기면",
     "does": "학습본을 토르에 올려 서빙하고, 운영 VM 이 그 서비스를 부른다.",
     "how": ["SFT 후보는 운영 설정을 바꾸지 않고 비교 주소로 견준 뒤, 나으면 교체",
             "임베딩을 바꾸면 전체 재색인, 리랭커를 바꾸면 점수 하한을 다시 잼",
             "서비스가 꺼지면 VM 이 같은 학습본으로 대신 계산(비상용)"],
     "out": "서빙 주소, 점검 기록",
     "why": "질의·색인·리랭커가 같은 모델이어야 검색이 맞다",
     "code": "scripts/116 · 195",
     "nums": [], "link": ("/dev/tools", "도구·모델 연결")},
)

# 주기 작업 — (언제, 어디서, 하는 일). 담당자가 읽는 말로 적고, 내부 이름·조건은 설계 기록(ADR)·스크립트 주석에 둔다
CYCLES = (
    ("원본 폴더가 바뀌면(1분마다 확인)", "DGX", "새 원본 목록 만들기 · 새 원본 읽기(바뀐 게 없어도 한 시간에 한 번 전체 확인, 학습 중에는 쉼)"),
    ("1분마다", "운영 VM", "DGX 에서 읽은 결과 가져오기 · 새 문서의 서류 종류 판정(27B, 토르 03)"),
    ("바뀌면 바로(1분마다 확인)", "운영 VM", "원본 장부 맞추기 · 새 문서 가져오기 · 연동 점검(할 일이 없어도 한 시간에 한 번)"),
    ("1분마다", "운영 VM", "새 조각의 검색 색인 만들기(뜻·키워드)"),
    ("1분마다", "운영 VM", "학습 기록 받기(학습 상태 화면·TensorBoard)"),
    ("문서가 바뀌면(1분마다 확인)", "운영 VM", "올린 원본 보관 · 범위 밖 문서 정리 · 그래프·검색 색인 다시 만들기(최대 15분에 한 번)"),
    ("1분마다", "운영 VM", "규정 새 판 자동 등록 — 학교 원본 보관소에서 들어온 공개 규정의 새 판을 기준 문서로(담당자가 내린 것은 그대로)"),
    ("하루 한 번", "운영 VM", "DGX 에서 읽었는데 라이브러리에 없는 원본 다시 가져오기"),
    ("하루 한 번", "운영 VM", "처리 실패 문서 정리 — 다시 읽어 보고 그래도 안 되면 문서함에서 뺌"),
)


def _json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _trainset_line() -> str | None:
    """DGX 학습셋 목록(export.bundle.trainsets)의 승인본 한 줄 — 없으면 None(「—」)."""
    try:
        from zzaimy.export.bundle import trainsets
        sets = trainsets()
    except Exception:
        return None
    t = next((x for x in sets if x.get("kind") == "release"), None) or next((x for x in sets if x.get("kind") == "staging"), None)
    if not t:
        return None
    return " · ".join(v for v in (t.get("name"), t.get("status_label"), t.get("summary")) if v)


def _train_live_line() -> str | None:
    """DGX 학습 기록기의 최근 기록(model_tracks.live_training) — 없으면 None."""
    try:
        from zzaimy.app.model_tracks import live_training
        live = live_training()
    except Exception:
        return None
    return f"{live['text']} ({live['at']} 기록)" if live else None


def snapshot(db) -> dict:
    """단계별 수치 — 지표 키 → 보일 글. 산출물이 없으면 「—」."""
    from zzaimy.app import answer_flow, metrics

    data_dir = Path(db.path).parent
    vals: dict[str, object] = dict(metrics.live(db)["values"])
    audit = metrics.audit(data_dir)
    if audit:
        vals.update(audit["values"])
    edges = answer_flow._edge_counts(db)
    vals["edge_plans_reports"] = edges.get("plans_reports")
    vals["edge_evaluates"] = edges.get("evaluates")
    shacl = _json(data_dir / "ontology" / "shacl_report.json") or {}
    vals["shacl_violations"] = shacl.get("n_violations")
    vals["shacl_warnings"] = shacl.get("n_warnings")
    ev = _json(data_dir / "eval" / "retrieval-latest.json") or {}
    prod = next((r for r in ev.get("rows") or [] if r.get("production")), {})
    vals["reg_r1"] = prod.get("recall_at_1")
    vals["reg_r5"] = prod.get("recall_at_5")
    cmp_ = answer_flow.compare_rows(data_dir.parent / "eval")         # 비교 측정(scripts/188)은 data/eval — 품질·성능 「사업 문서 검색」 과 같은 곳
    doc_row = next((r for r in cmp_.get("rows") or [] if "정답 문서가 근거 상위" in r["label"]), None)
    for i, mode in enumerate(("cmp_fulltext", "cmp_graph", "cmp_graph_read")):
        vals[mode] = doc_row["vals"][i] if doc_row and i < len(doc_row["vals"]) else None
    vals["trainset"] = _trainset_line()
    vals["train_live"] = _train_live_line()
    import os
    vals["graph_read"] = "켜짐" if os.environ.get("ZZAIMY_GRAPH_READ", "0") == "1" else "꺼짐"
    out = {k: _fmt(v) for k, v in vals.items()}
    return {"vals": out, "cmp_note": "절 질문에서 정답 문서가 근거 상위 5건에 든 비율" if doc_row else "",
            "cmp_file": cmp_.get("file", ""), "eval_at": (ev.get("measured_at") or "")[:16].replace("T", " "),
            "shacl_at": "", "at": metrics.live(db)["at"]}
