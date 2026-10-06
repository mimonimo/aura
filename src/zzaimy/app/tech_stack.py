"""핵심 기술 표 — RAG·지식그래프·온톨로지·판독·모델 서빙·화면에 쓰는 부품(대부분 오픈소스)과 우리 코드가 맡는 부분.

작업 현황(/dev 「핵심 기술」 탭)의 원천이다. 파이썬 패키지의 판·라이선스는 이 서버에 설치된 것에서 그때그때 읽고(importlib.metadata),
PostgreSQL 판은 DB 에 물어본다. 다른 장비(DGX·토르)에 있는 부품과 화면에 동봉한 JS 는 확인한 날짜와 함께 적는다.
부품을 바꾸면(예: ADR-0051 docling 제거) 여기도 같이 고친다.
"""
from __future__ import annotations

from importlib import metadata

# (영역, 이름, 하는 일, 파이썬 패키지 | None, 고정 판, 고정 라이선스, 어디서, 근거)
STACK: list[tuple] = [
    # ── RAG
    ("RAG", "PostgreSQL", "문서·조각·권한·부서(RAG 공간)·그래프 저장, 검색 단계 권한 필터", None, "", "PostgreSQL License", "운영 VM", "ADR-0049·0053"),
    ("RAG", "PostgreSQL 전문 색인(tsvector·GIN)", "사업 문서 어휘 색인(grant_lex) — 명사 겹침 후보 추리기", None, "", "PostgreSQL License", "운영 VM", "app/grant_lex.py"),
    ("RAG", "Kiwi(kiwipiepy)", "한국어 형태소 분석 — 질문·조각의 명사 뽑기", "kiwipiepy", "", "", "운영 VM", "app/regulations.py"),
    ("RAG", "NumPy", "의미 검색 벡터 배열(조각 임베딩) — pgvector 이전 검토 중", "numpy", "", "", "운영 VM", "app/grant_search.py"),
    ("RAG", "ZZAIMY-Embed v2 (베이스 KURE-v1)", "조밀 검색 임베딩 — 우리 데이터로 학습한 판", None, "v2", "Apache 2.0 계열(model-plan)", "토르 03", "ADR-0021·모델 카드"),
    ("RAG", "ZZAIMY-Rerank v1 (베이스 bge-reranker-v2-m3)", "규정 후보 재정렬(사업 문서는 대결에서 손해라 끔)", None, "v1", "Apache 2.0 계열(model-plan)", "토르 03", "ADR-0020·모델 카드"),
    ("RAG", "Qwen3.8-27B(Writer) + vLLM", "답변·초안 생성, 그래프 짝 판정(158)·공통 양식 묶기(172)", None, "NVFP4", "Apache 2.0 계열(model-plan)·vLLM Apache-2.0",
     "토르 02·03", "configs/serving.yaml"),
    ("RAG", "자체 코드", "하이브리드 검색(어휘+조밀 RRF)·SQL 범위·그래프 절 축·이웃 확장·문서당 상한·RAG 공간 권한", None, "", "우리 코드",
     "src/zzaimy/app", "grant_search·rag_spaces"),
    # ── 지식그래프
    ("지식그래프", "PostgreSQL 테이블(kg_nodes·kg_edges)", "노드·관계·근거 저장, 이웃·권한 조회", None, "", "PostgreSQL License", "운영 VM", "graph/kg_store.py"),
    ("지식그래프", "자체 코드(157 그래프 생성)", "사업·연차·문서·절·단위과제·성과지표·기관 노드와 근거 있는 관계만", None, "", "우리 코드", "scripts/157_build_kg.py",
     "ADR-0048·0055"),
    ("지식그래프", "Cytoscape.js", "그래프 탐색 화면 그리기", None, "3.30.4", "MIT", "화면(static 동봉)", "ADR-0054"),
    ("지식그래프", "cytoscape-fcose · cose-base · layout-base", "노드 자동 배치", None, "2.2.0 · 2.2.0 · 2.0.1", "MIT", "화면(static 동봉)", "ADR-0054"),
    # ── 온톨로지
    ("온톨로지", "자체 코드(사업 카드·별칭·표시명)", "노드 종류 8·관계 종류 정의, 같은 사업 카드 합치기, 사업 아님 걸러내기", None, "", "우리 코드",
     "graph/programs.py", "ADR-0048"),
    ("온톨로지", "외부 확인 장부(kg_external.json)", "사업 체계·이름 변경·편입·주관 부처·전담기관 — 항목마다 출처 URL", None, "", "우리 데이터", "운영 VM data/platform",
     "ADR-0055"),
    ("온톨로지", "온톨로지 점검(174)", "규칙 위반·고립 노드·근거 없는 관계 점검", None, "", "우리 코드", "scripts/174_ontology_audit.py", ""),
    # ── 판독(문서 읽기)
    ("판독", "kordoc", "한글(hwp/hwpx)·docx·xlsx 구조 읽기 1순위", None, "4.15.7", "MIT", "DGX", "ADR-0033·0051"),
    ("판독", "MinerU", "스캔 PDF OCR(표·레이아웃)", None, "3.4.5", "MinerU Open Source License", "DGX", "ADR-0007"),
    ("판독", "python-docx", "docx 읽기(kordoc 다음)", "python-docx", "", "", "운영 VM·DGX", "ADR-0051"),
    ("판독", "openpyxl", "xlsx 읽기(kordoc 다음)", "openpyxl", "", "", "운영 VM·DGX", "ADR-0051"),
    ("판독", "python-pptx", "pptx 읽기", "python-pptx", "", "", "운영 VM·DGX", "ADR-0051"),
    ("판독", "LibreOffice", "옛 형식(xls·doc·ppt) → 새 형식 변환", None, "24.2.7", "MPL-2.0", "DGX", "ADR-0051"),
    ("판독", "pypdf", "디지털 PDF 글자층 읽기", "pypdf", "", "", "운영 VM", ""),
    # ── 플랫폼
    ("플랫폼", "FastAPI · Uvicorn", "웹 서버", "fastapi", "", "", "운영 VM", ""),
    ("플랫폼", "Jinja2", "화면 템플릿", "jinja2", "", "", "운영 VM", ""),
    ("플랫폼", "psycopg", "PostgreSQL 연결", "psycopg", "", "", "운영 VM", ""),
]
CHECKED = "2026-10-06"          # 다른 장비 부품·동봉 JS 판을 확인한 날


def _pkg(name: str) -> tuple[str, str]:
    try:
        md = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return "", ""
    lic = (md.get("License-Expression") or "").strip()
    if not lic:
        lic = next((c.split("::")[-1].strip() for c in (md.get_all("Classifier") or []) if c.startswith("License ::")), "")
    if not lic:
        lic = (md.get("License") or "").strip().splitlines()[0][:40] if md.get("License") else ""
    return metadata.version(name), lic


def snapshot(db=None) -> dict:
    pg = ""
    if db is not None:
        try:
            with db._conn() as conn:
                row = conn.execute("SHOW server_version").fetchone()
                pg = str(row[0]).split()[0]
        except Exception:
            pg = ""          # SQLite(개발 환경)
    rows = []
    for area, name, role, pkg, ver, lic, where, ref in STACK:
        src = "고정(" + CHECKED + " 확인)" if ver else ""
        if pkg:
            v, l = _pkg(pkg)
            ver, lic, src = (v or "설치 안 됨"), (l or lic), "이 서버 설치본"
        if name == "PostgreSQL" and pg:
            ver, src = pg, "DB 에 물어봄"
        rows.append({"area": area, "name": name, "role": role, "version": ver, "license": lic, "where": where, "ref": ref,
                     "source": src, "ours": lic in ("우리 코드", "우리 데이터")})
    areas: dict[str, list] = {}
    for r in rows:
        areas.setdefault(r["area"], []).append(r)
    return {"areas": areas, "checked": CHECKED, "n_open": sum(1 for r in rows if not r["ours"]), "n_ours": sum(1 for r in rows if r["ours"])}
