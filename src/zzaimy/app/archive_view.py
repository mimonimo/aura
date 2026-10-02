"""DGX 원본 보관소 화면 — 원본 목록 장부(app/archive)를 사업 × 갈래로 보여 주고 원본을 찾는다. 문서함(/criteria)에서 들어온다."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from zzaimy.app import archive

router = APIRouter()

KIND_KO = {"plan": "계획서", "report": "실적보고서", "evaluation": "평가 결과", "basic_plan": "기본계획", "announcement": "공고",
           "guideline": "지침·매뉴얼", "criteria": "평가 기준", "regulation": "규정", "form": "양식", "table": "표·현황",
           "notice": "안내", "certificate": "증명서", "": "미정"}


def overview(db) -> dict:
    rows = archive.summary(db)
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
    data = overview(st.db)
    found = archive.find(st.db, program=program, kind=kind, text=q, limit=200) if (program or kind or q) else []
    for f in found:
        f["kind_label"] = KIND_KO.get(f.get("kind") or "", f.get("kind") or "")
    data.update({"found": found, "program": program, "kind": kind, "q": q, "kind_ko": KIND_KO})
    return st.templates.TemplateResponse(request, "archive.html", st.page_ctx(request, data))
