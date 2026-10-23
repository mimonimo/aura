"""개발 현황(/dev) — 허브와 그 아래 화면: 처리 흐름(/dev/flow)·데이터 현황(/dev/db)·품질·성능(/dev/quality*)·
규정 검색 재측정(/dev/eval/*)·재색인(/dev/reindex)·반입 현황 JSON(/dev/api/intake)·문서 갈래 일괄 다시 정하기(/dev/route-all).

- 개발자(dev)만 — /dev 경로는 main.check_auth 가 막는다.
- DB·DB 경로(db_path)·화면 틀·화면 공통 맥락(page_ctx)·대화 근거 표(chat_sources)·문서 갈래 정하기(auto_route)와
  개발 현황 도우미 묶음(overview — make_overview_helpers 가 앱마다 만든다)은 main.create_app 이 app.state 에 올려 둔 것을 읽는다.
  문서·보고 라우터(dev_docs_routes)와 RAG 공간(dev_rag_routes)도 이 묶음의 docs/ 읽기·목록·재색인 도우미를 넘겨 받는다.
- 재색인·재측정은 배경 실행(zzaimy.app.detach.detached). 앱 시작 때 도는 자동 재색인 스레드는 main 에 두고 이 묶음을 부른다.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()


def make_overview_helpers(db, db_path, _DOCS_DIR: Path) -> SimpleNamespace:
    """앱마다 하나 — 문서 날짜 캐시·재색인 프로세스 표를 앱 안에 둔다."""
    from zzaimy.app.main import (DECISION_LABELS, DOC_TYPE_LABELS, EGRESS_VERDICT_LABELS, SECTOR_LABELS,
                                 STATUS_LABELS)   # 화면 용어 — 정본은 main(같은 표)

    def _dev_read(name: str, tail_lines: int | None = None) -> str:
        try:
            text = (_DOCS_DIR / name).read_text(encoding="utf-8")
            if tail_lines:
                text = "\n".join(text.splitlines()[-tail_lines:])
            return text
        except OSError:
            return ""

    def _data_overview() -> dict:
        """데이터 열람용 통계 개요 — 핵심 지표 타일 + 주요 현황 분해."""
        from zzaimy.app.database_backend import connect
        conn = connect(db_path, readonly=True)
        q = conn.execute

        def one(sql: str):
            try:
                return q(sql).fetchone()[0] or 0
            except Exception:
                return 0

        def group(sql: str, labels: dict | None = None) -> list[dict]:
            try:
                rows = q(sql).fetchall()
            except Exception:
                return []
            out = []
            for val, n in rows:
                key = "" if val is None else str(val)
                lbl = (labels or {}).get(key) or key or "(미지정)"
                out.append({"label": lbl, "n": n})
            return out

        from zzaimy.app import metrics as _m
        mv = _m.live(db)["values"]          # 같은 이름의 수는 지표 모듈 하나에서(/dev·/dev/intake·/dev/pii 와 같은 정의)
        n_docs = mv["documents"]
        n_reviewed = mv["documents_reviewed"]
        from datetime import datetime, timedelta
        cutoff = (datetime.now().astimezone() - timedelta(days=7)).isoformat(timespec='seconds')
        n_recent = q('SELECT COUNT(*) FROM documents WHERE created_at >= ?', (cutoff,)).fetchone()[0]
        n_proj = mv["projects_active"]                     # 보관된 지난 사업 묶음은 따로 센다(같은 표, 다른 집합)
        n_sess = one("SELECT COUNT(*) FROM chat_sessions")
        n_msg = one("SELECT COUNT(*) FROM chat_messages")
        n_reg = mv["regulation_chunks"]
        n_reg_docs = mv["regulation_docs"]
        n_ent = mv["entities"]
        n_qr_open = one("SELECT COUNT(*) FROM quality_reports WHERE status='open'")
        n_egr = one("SELECT COUNT(*) FROM egress_requests")

        # 타일마다 지표 이름(metric) — 화면이 지표 모듈의 정의를 툴팁으로 붙인다
        tiles = [
            {"icon": "solar:documents-linear", "label": "접수 문서", "metric": "documents",
             "value": n_docs, "sub": f"검토 완료 {n_reviewed} · 최근 7일 {n_recent}"},
            {"icon": "solar:folder-2-linear", "label": "프로젝트", "metric": "projects_active",
             "value": n_proj, "sub": f"진행 중 · 지난 사업 묶음 {mv['projects_archived']:,}개는 별도"},
            {"icon": "solar:chat-round-line-linear", "label": "대화",
             "value": n_sess, "sub": f"메시지 {n_msg}건"},
            {"icon": "solar:book-2-linear", "label": "규정 문단", "metric": "regulation_chunks",
             "value": n_reg, "sub": f"검색 근거 · 문서 {n_reg_docs}종"},
            {"icon": "solar:structure-linear", "label": "문서에서 뽑은 개체", "metric": "entities",
             "value": n_ent, "sub": "사업·부서·연도 이름 · 지식그래프와 별개"},
            {"icon": "solar:share-circle-linear", "label": "그래프 노드", "metric": "graph_nodes",
             "value": mv["graph_nodes"], "sub": f"관계 {mv['graph_edges']:,} · 문서 노드 {mv['graph_doc_nodes']:,}"},
        ]
        groups = []
        for title, sql, labels in [
            ("문서 상태",
             "SELECT status, COUNT(*) FROM documents GROUP BY status ORDER BY 2 DESC",
             STATUS_LABELS),
            ("문서 계열",
             "SELECT sector, COUNT(*) FROM documents GROUP BY sector ORDER BY 2 DESC",
             SECTOR_LABELS),
            ("담당자 판정",
             "SELECT decision, COUNT(*) FROM documents GROUP BY decision ORDER BY 2 DESC",
             DECISION_LABELS),
            ("문서 유형",
             "SELECT doc_type, COUNT(*) FROM documents GROUP BY doc_type ORDER BY 2 DESC",
             DOC_TYPE_LABELS),
        ]:
            rows = group(sql, labels)
            if rows:
                groups.append({"title": title, "rows": rows})
        if n_qr_open:
            groups.append({"title": "품질 리포트",
                           "rows": [{"label": "미해결", "n": n_qr_open}]})
        if n_egr:
            groups.append({"title": "외부 AI 참조 (자동 분류)",
                           "rows": group(
                               "SELECT verdict, COUNT(*) FROM egress_requests"
                               " GROUP BY verdict ORDER BY 2 DESC",
                               EGRESS_VERDICT_LABELS)})
        conn.close()
        return {"tiles": tiles, "groups": groups}

    def _run_dev_query(sql: str) -> dict:
        """읽기 전용 SELECT 1문만, 상한 50행 — 개발자 DB 점검용."""
        from zzaimy.app.database_backend import connect

        q = sql.strip().rstrip(";")
        if not q.lower().startswith("select") or ";" in q:
            return {"error": "SELECT 한 문장만 실행할 수 있습니다."}
        try:
            from contextlib import closing
            with closing(connect(db_path, readonly=True)) as conn:
                cur = conn.execute(q)
                cols = [d[0] for d in cur.description or []]
                rows = [
                    [str(v)[:200] if v is not None else "" for v in r]
                    for r in cur.fetchmany(50)
                ]
            return {"cols": cols, "rows": rows, "sql": sql}
        except Exception as e:
            return {"error": str(e), "sql": sql}

    def _dev_progress() -> dict:
        """구축 현황 — docs/progress.json 의 기능 상태 목록. 영역별 막대는 기능 상태에서
        계산한 완료 비율(완료/전체)이다. 수기로 적은 퍼센트는 쓰지 않는다(근거 없는 수치 금지)."""
        import json as _pj

        try:
            data = _pj.loads((_DOCS_DIR / "progress.json").read_text())
        except (OSError, ValueError):
            return {"updated": "", "features": [], "areas": []}
        feats = data.get("features", [])
        order: list[str] = []
        agg: dict[str, dict] = {}
        for f in feats:
            area = f.get("area") or "기타"
            if area not in agg:
                agg[area] = {"name": area, "done": 0, "total": 0, "features": []}
                order.append(area)
            agg[area]["total"] += 1
            agg[area]["features"].append(f)          # 기능별 상태는 영역별로 묶어 보인다
            if f.get("status") == "done":
                agg[area]["done"] += 1
        for a in agg.values():
            a["pct"] = round(100 * a["done"] / a["total"]) if a["total"] else 0
        data["areas"] = [agg[k] for k in order]
        # 기준일은 손으로 적은 updated 칸이 아니라 파일의 마지막 커밋일(없으면 수정 시각) — 고치고 날짜를 잊어도 낡지 않게
        data["updated"] = _doc_last_changed("progress.json")
        return data

    def _dev_doc_list(sub: str) -> list[dict]:
        """decisions/notes 목록 — 파일명 대신 문서 첫 제목을 보여준다."""
        d = _DOCS_DIR / sub
        out = []
        skip = {"README.md", "0000-template.md"}
        for f in sorted(d.glob("*.md")) if d.exists() else []:
            if f.name in skip:
                continue
            title = f.stem
            try:
                for ln in f.read_text(encoding="utf-8").splitlines()[:5]:
                    if ln.startswith("# "):
                        title = ln[2:].strip()
                        break
            except OSError:
                pass
            out.append({"file": f.name, "title": title, "sub": sub})
        return out

    def _dev_now() -> str:
        return _dev_read("dev-now.md")

    def _dev_now_parts() -> dict:
        """dev-now.md 를 허브용과 이력용으로 나눈다.

        허브(/dev)에는 '최근 작업' 중 최신 날짜 한 묶음만 보이고,
        변경 이력(/dev/history)에는 전체를 보인다.
        """
        text = _dev_now()
        head, sep, recent = text.partition("\n## 최근 작업")
        if not sep:
            return {"hub": text, "recent_all": "", "more": False}
        blocks = [b for b in re.split(r"\n(?=### \S)", recent) if b.strip()]
        if not blocks:
            return {"hub": head, "recent_all": "", "more": False}
        return {
            "hub": head.rstrip() + "\n\n## 최근 작업\n\n" + blocks[0].strip() + "\n",
            # 카드 제목이 '작업 기록'이므로 '## 최근 작업' 제목은 되풀이하지 않는다
            "recent_all": "\n\n".join(b.strip() for b in blocks) + "\n",
            "more": len(blocks) > 1,
        }

    _PAPER_LABELS = {
        "프로젝트-기획서.md": "프로젝트 기획서",
        "중간-발표자료.md": "중간 발표자료",
        "발표-자료-모음.md": "중간 발표 PPT 자료 모음",
        "논문-원재료.md": "구현 설명서",
        "실험-로그.md": "실험 기록",
        "논문-양식-가이드.md": "논문 양식 분석",
        "제안발표-내용.md": "제안 발표 내용",
    }

    # 읽는 순서 — 구현 설명서(지금 구조의 정본)·발표·실험 기록이 먼저, 기획 단계 자료는 뒤에
    _PAPER_ORDER = ["논문-원재료.md", "중간-발표자료.md", "발표-자료-모음.md", "실험-로그.md", "프로젝트-기획서.md",
                    "제안발표-내용.md", "논문-양식-가이드.md"]

    def _dev_papers() -> list[dict]:
        d = _DOCS_DIR / "paper"
        names = sorted((p.name for p in d.glob("*.md")) if d.exists() else [],
                       key=lambda n: (_PAPER_ORDER.index(n) if n in _PAPER_ORDER else 99, n))
        return [
            {"file": n, "label": _PAPER_LABELS.get(n, n.replace(".md", ""))}
            for n in names
        ]

    def _intake_snapshot() -> dict | None:
        try:
            from zzaimy.app import intake_status
            return intake_status.snapshot(db)
        except Exception:
            logging.getLogger("zzaimy.app.web").exception("반입 현황 조회 실패")
            return None

    _doc_dates: dict[str, str] = {}

    def _doc_last_changed(rel: str) -> str:
        """docs/ 파일의 마지막 변경일(YYYY-MM-DD) — 깃 기록이 있으면 그것, 없으면 파일 수정 시각."""
        if rel in _doc_dates:
            return _doc_dates[rel]
        import subprocess
        from datetime import datetime as _dt

        out = ""
        try:
            r = subprocess.run(["git", "log", "-1", "--format=%ad", "--date=short", "--", str(_DOCS_DIR / rel)],
                               capture_output=True, text=True, timeout=5, cwd=str(_DOCS_DIR.parent))
            out = (r.stdout or "").strip()
        except Exception:
            out = ""
        if not out:
            try:
                out = _dt.fromtimestamp((_DOCS_DIR / rel).stat().st_mtime).strftime("%Y-%m-%d")
            except OSError:
                out = ""
        _doc_dates[rel] = out
        return out

    _REINDEX_LOG = Path("/tmp/reindex.log")
    _reindex_proc: dict = {"p": None}

    def _reindex_running() -> bool:
        """재색인 체인이 아직 도는가 — 이 프로세스가 띄운 것은 직접, 그 외(앱 재시작 뒤)는
        로그의 종료 표식과 최근성으로 판단한다."""
        p = _reindex_proc["p"]
        if p is not None and p.poll() is None:
            return True
        try:
            if _REINDEX_LOG.exists():
                tail = _REINDEX_LOG.read_text(encoding="utf-8", errors="ignore")[-400:]
                fresh = (time.time() - _REINDEX_LOG.stat().st_mtime) < 1800
                return fresh and "REINDEX_DONE" not in tail and "중단" not in tail
        except OSError:
            pass
        return False

    def _start_reindex() -> None:
        import subprocess

        script = Path(__file__).resolve().parents[3] / "scripts" / "66_reindex.sh"
        with _REINDEX_LOG.open("w") as log_f:
            from zzaimy.app.detach import detached
            _reindex_proc["p"] = subprocess.Popen(
                detached(["nohup", "bash", str(script)], "reindex"),
                stdout=log_f, stderr=subprocess.STDOUT, start_new_session=True,
            )

    def _retrieval_eval_state() -> dict:
        from zzaimy.eval.retrieval_eval import dashboard_state

        return dashboard_state(Path(db_path).parent / "eval")

    return SimpleNamespace(
        dev_read=_dev_read, data_overview=_data_overview, run_dev_query=_run_dev_query, dev_progress=_dev_progress,
        dev_doc_list=_dev_doc_list, dev_now=_dev_now, dev_now_parts=_dev_now_parts, paper_labels=_PAPER_LABELS,
        dev_papers=_dev_papers, intake_snapshot=_intake_snapshot, doc_last_changed=_doc_last_changed,
        reindex_running=_reindex_running, start_reindex=_start_reindex, retrieval_eval_state=_retrieval_eval_state,
    )


@router.get("/dev/db", response_class=HTMLResponse)
def dev_db(
    request: Request, tab: str = "", q: str = "", type: str = "",
    doc: int | None = None, table: str = "", page: int = 0,
):
    """데이터 열람 — 문서 중심 탐색기. 탭: 원본 장부·문서·규정·채팅 기록.

    표·행 덤프 대신 문서 하나의 개요(마스킹 기록)·RAG 조각(임베딩 유무)·그래프
    연관을 본다. 조립은 zzaimy.app.data_explorer(순수 함수)가 하고, 모델이
    필요한 검색·그래프 생성은 여기서 함수로 넘긴다. 예전 ?table= 주소는
    가장 가까운 탭으로 보낸다.
    """
    from zzaimy.app import data_explorer as dx

    st = request.app.state
    db = st.db
    if tab == "corpus":                 # 예전 공개 문서 모음 탭(파일럿, 거둠) — 문서 탭으로
        return RedirectResponse("/dev/db?tab=docs", status_code=303)
    tab = dx.normalize_tab(tab, table)
    index = dx.embedding_index() if tab != "archive" else {}
    ledger = None
    if tab == "archive":
        from zzaimy.app import intake_status
        view = dx.archive_tab(db, q=q, page=page, everything=request.query_params.get("all") == "1")
        ledger = intake_status.ledger_breakdown(db)
    elif tab == "docs":
        scope = {"dept": getattr(request.state, "dept", "") or None,
                 "user": request.state.user, "role": request.state.role}
        view = dx.docs_tab(db, q=q, doc_type=type, doc_id=doc, index=index,
                           related_fn=lambda did: dx.related_from_kg(db, did, scope))
    elif tab == "regulation":
        def _search(text: str) -> list[dict]:
            from zzaimy.app.regulations import find_relevant

            return find_relevant(db, text, top_k=10)   # 채팅과 같은 운영 검색 경로

        view = dx.regulation_tab(db, q=q, doc_id=doc, search_fn=_search, index=index)
    else:
        view = dx.chat_tab(db, sources_by_session=st.chat_sources)
    return st.templates.TemplateResponse(request, "dev_db.html", st.page_ctx(request, {
        "tab": tab, "tabs": dx.TABS, "q": q, "type": type, "doc_id": doc,
        "view": view, "index": index, "lg": ledger, "extract_page": dx.EXTRACT_PAGE,
        "overview": st.overview.data_overview(),
    }))


@router.get("/dev", response_class=HTMLResponse)
def dev_dashboard(request: Request):
    # 허브 — 요약과 바로가기만. 상세는 전용 페이지(/dev/quality·/dev/docs·/dev/history)
    st = request.app.state
    from zzaimy.app import model_compare
    h = st.overview
    return st.templates.TemplateResponse(request, "dev.html", st.page_ctx(request, {
        "overview": h.data_overview(),   # 규모 타일 — /dev/db 와 같은 원천
        "dev_now": h.dev_now_parts()["hub"],
        "dev_now_more": h.dev_now_parts()["more"],
        "progress": h.dev_progress(),
        "reindex_running": h.reindex_running(),
        # 모델 비교 결과가 하나라도 있을 때만 Writer 줄에 바로가기
        "model_compare_runs": model_compare.list_runs(model_compare.root(st.db_path)),
    }))


@router.get("/dev/api/intake")
def dev_api_intake(request: Request):
    """반입 현황(JSON) — 문서 가져오기 화면과 같은 원천(app/intake_status)."""
    data = request.app.state.overview.intake_snapshot()
    if data is None:
        raise HTTPException(500, "반입 현황을 읽지 못했습니다")
    return data


@router.get("/dev/flow", response_class=HTMLResponse)
def dev_flow(request: Request):
    """처리 흐름 — 원본 수집부터 서빙까지 단계별 설명과 지금 수치(기계 산출물)."""
    from zzaimy.app import process_flow as pf

    st = request.app.state
    db = st.db
    return st.templates.TemplateResponse(request, "dev_flow.html", st.page_ctx(request, {
        "terms": pf.TERMS, "hosts": pf.HOSTS, "stages": pf.STAGES,
        "cycles": pf.CYCLES, "flow": pf.snapshot(db),
        "tech": __import__("zzaimy.app.tech_stack", fromlist=["snapshot"]).snapshot(db),
    }))


@router.get("/dev/quality", response_class=HTMLResponse)
def dev_quality(request: Request):
    """품질·성능 — 규정 검색 정확도(기계 산출물)와 추출 품질 백로그."""
    from zzaimy.app import metrics as _metrics_mod

    st = request.app.state
    db, db_path, h = st.db, st.db_path, st.overview
    return st.templates.TemplateResponse(request, "dev_quality.html", st.page_ctx(request, {
        "retrieval_eval": h.retrieval_eval_state(),
        # 지금 색인을 만든 임베딩 — 측정 당시 임베딩과 다르면 화면이 「옛 측정」 경고를 띄운다
        "index_model": __import__("zzaimy.app.embed_search", fromlist=["_index_model_name"])._index_model_name(),
        "reindex_running": h.reindex_running(),
        "quality": db.quality_report_stats(),
        "quality_open": db.list_quality_reports(status="open", limit=10),
        # 지금 규정 조각 수 — 측정 당시 조각 수와 다르면 지난 측정을 「구성 다름」으로 접는다
        "current_chunks": _metrics_mod.live(db)["values"].get("regulation_chunks", 0),
        # 사업 문서 검색 측정(관계 질문·풀텍스트 RAG 비교) — 결과 파일은 data/eval
        "flow": __import__("zzaimy.app.answer_flow", fromlist=["snapshot"]).snapshot(db, Path(db_path).parent.parent / "eval"),
    }))


@router.post("/dev/quality/{report_id}/done")
def dev_quality_done(
    request: Request, report_id: int, fix_note: str = Form("")
):
    if not request.app.state.db.resolve_quality_report(
        report_id, resolved_by=request.state.user, fix_note=fix_note.strip()
    ):
        raise HTTPException(404, "열려 있는 신고가 아닙니다")
    return RedirectResponse("/dev", status_code=303)


@router.post("/dev/reindex")
def dev_reindex(request: Request, back: str = Form("/dev")):
    """재색인 체인 실행 — 규정 조각 변경 후 질의·임베딩·앱 순차 갱신.

    실행 중이면 새로 띄우지 않는다(66 자체의
    flock과 이중 방어). 로그 핸들은 자식이 물려받은 뒤 닫는다. 끝나면 누른 화면(개발 현황·RAG 공간)으로 돌아간다."""
    h = request.app.state.overview

    back = back if back in ("/dev", "/dev/rag") else "/dev"

    if h.reindex_running():
        return RedirectResponse(f"{back}?err=재색인이 이미 실행 중입니다", status_code=303)
    h.start_reindex()
    return RedirectResponse(back, status_code=303)


@router.get("/dev/eval/status")
def dev_eval_status(request: Request):
    state = request.app.state.overview.retrieval_eval_state()
    return {"running": bool(state["running"]), "failed": bool(state["last_error"])}


@router.post("/dev/eval/run")
def dev_eval_run(request: Request):
    """규정 검색 품질 재측정 — 재색인과 같은 방식(배경 실행·같은 잠금)으로 53을 돌린다.

    합성 질의 세트가 없으면 실행하지 않고 그 사실을 돌려준다 — 가짜 수치는 없다.
    """
    import subprocess
    import sys

    from zzaimy.eval import retrieval_eval as rev

    db_path = request.app.state.db_path
    if not rev.QUERIES_PATH.exists():
        return HTMLResponse(f"아직 측정 없음 ({rev.NO_QUERY_SET_MSG})", status_code=409)
    if rev.running_state(Path(db_path).parent / "eval"):
        return HTMLResponse("이미 측정 중입니다", status_code=409)
    root = Path(__file__).resolve().parents[3]
    from zzaimy.app.detach import detached
    proc = subprocess.Popen(
        detached(["nohup", sys.executable, str(root / "scripts" / "53_eval_retrieval.py"),
                  "--db", str(Path(db_path).resolve())], "eval"),
        stdout=open(rev.LOG_PATH, "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )
    # 측정 스크립트가 표시를 쓰기 전에 화면이 먼저 돌아오면 「측정 중」 이 안 보인다 — 띄운 즉시 쓴다
    rev.mark_running(Path(db_path).parent / "eval", pid=proc.pid)
    return RedirectResponse("/dev/quality", status_code=303)


@router.post("/dev/route-all")
def dev_route_all(request: Request):
    """분류가 비어 있거나 뭉뚱그려진 문서를 한꺼번에 다시 정한다.

    확신이 없는 문서는 건드리지 않는다. 결과는 건수로만 알린다.
    """
    from urllib.parse import quote as _q

    st = request.app.state
    db = st.db
    changed = 0
    for d in db.list_documents():
        try:
            if st.auto_route(db, d["id"], user_chose_type=False):
                changed += 1
        except Exception:
            continue
    return RedirectResponse(
        "/?ok=" + _q(f"문서 {changed}건의 갈래·영역을 다시 정했습니다"),
        status_code=303,
    )
