"""DGX 원본 보관소 화면 — 원본 목록 장부(app/archive)를 사업 × 갈래로 보여 주고 원본을 찾는다. 문서함(/criteria)에서 들어온다."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask

from zzaimy.app import archive

router = APIRouter()

KIND_KO = {"plan": "계획서", "report": "실적보고서", "evaluation": "평가 결과", "basic_plan": "기본계획", "announcement": "공고",
           "guideline": "지침·매뉴얼", "criteria": "평가 기준", "regulation": "규정", "form": "양식", "table": "표·현황",
           "notice": "안내", "certificate": "증명서", "": "미정"}


def _hidden_docs(db, scope: dict | None) -> set[int]:
    """이 사용자가 열람할 수 없는 문서 번호 — 장부의 경로·이름·집계에서도 뺀다(절대 규칙 4, 문서함과 같은 visible 규칙)."""
    if not scope or scope.get("role") == "dev":
        return set()
    from zzaimy.app.access_policy import visible
    return {int(d["id"]) for d in db.list_documents() if not visible(d, **scope)}


def _hidden_rows(db, hidden: set[int]) -> list[dict]:
    if not hidden:
        return []
    archive.ensure(db)
    with db._conn() as conn:
        rows = conn.execute("SELECT program, kind, size, doc_id FROM archive_files WHERE removed_at = '' AND doc_id IS NOT NULL").fetchall()
    return [{"program": r[0], "kind": r[1], "size": int(r[2] or 0)} for r in rows if int(r[3]) in hidden]


def overview(db, scope: dict | None = None) -> dict:
    rows = archive.summary(db)
    for h in _hidden_rows(db, _hidden_docs(db, scope)):   # 볼 수 없는 문서는 집계에서도 뺀다
        for r in rows:
            if r["program"] == h["program"] and r["kind"] == h["kind"]:
                r["files"] -= 1
                r["in_store"] -= 1
                r["bytes"] -= h["size"]
                break
    rows = [r for r in rows if r["files"] > 0]
    progs: dict[str, dict] = {}
    for r in rows:
        p = progs.setdefault(r["program"] or "", {"program": r["program"], "name": r["program_name"] or "사업 미분류",
                                                  "files": 0, "in_store": 0, "bytes": 0, "kinds": defaultdict(int)})
        p["files"] += r["files"]
        p["in_store"] += r["in_store"]
        p["bytes"] += r["bytes"]
        p["kinds"][KIND_KO.get(r["kind"], r["kind"])] += r["files"]
    items = sorted(progs.values(), key=lambda p: -p["files"])
    for p in items:
        p["kinds"] = sorted(p["kinds"].items(), key=lambda t: -t[1])[:6]
    total = sum(p["files"] for p in items)
    return {"programs": items, "total": total, "in_store": sum(p["in_store"] for p in items),
            "gb": round(sum(p["bytes"] for p in items) / 1e9, 1)}


@router.get("/archive", response_class=HTMLResponse)
def archive_page(request: Request, program: str = "", kind: str = "", q: str = ""):
    st = request.app.state
    scope = {"dept": getattr(request.state, "dept", "") or None, "user": request.state.user, "role": request.state.role}
    data = overview(st.db, scope)
    hidden = _hidden_docs(st.db, scope)
    found = archive.find(st.db, program=program, kind=kind, text=q, limit=200) if (program or kind or q) else []
    found = [f for f in found if not (f.get("doc_id") and int(f["doc_id"]) in hidden)]
    for f in found:
        f["kind_label"] = KIND_KO.get(f.get("kind") or "", f.get("kind") or "")
        f["can_open"] = (bool(f.get("doc_id")) or scope["role"] == "dev") and not f["rel"].startswith(archive.UPLOAD_PREFIX)
    data.update({"found": found, "program": program, "kind": kind, "q": q, "kind_ko": KIND_KO})
    return st.templates.TemplateResponse(request, "archive.html", st.page_ctx(request, data))


@router.get("/archive/original")
def archive_original(request: Request, rel: str):
    from pathlib import PurePosixPath
    from zzaimy.app import archive_original as original
    from zzaimy.app.access_policy import visible

    db = request.app.state.db
    archive.ensure(db)
    with db._conn() as conn:
        row = conn.execute(
            "SELECT rel, size, mtime, doc_id FROM archive_files WHERE rel = ? AND removed_at = ''",
            (rel,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "원본 목록에서 파일을 찾을 수 없습니다.")
    record = dict(zip(("rel", "size", "mtime", "doc_id"), row))
    scope = {"dept": getattr(request.state, "dept", "") or None,
             "user": request.state.user, "role": request.state.role}
    if record["doc_id"]:
        doc = db.get_document(int(record["doc_id"]))
        if not doc or not visible(doc, **scope):
            raise HTTPException(403, "이 문서를 열람할 권한이 없습니다.")
    elif scope["role"] != "dev":
        raise HTTPException(403, "미연결 원본의 열람 권한은 개발자 확인이 필요합니다.")
    try:
        file = original.fetch(record)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    filename = PurePosixPath(rel).name
    inline_types = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
    mime = inline_types.get(PurePosixPath(rel).suffix.lower())
    return FileResponse(file, filename=filename, media_type=mime or "application/octet-stream",
                        content_disposition_type="inline" if mime else "attachment",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "sandbox"},
                        background=BackgroundTask(file.unlink, missing_ok=True))
