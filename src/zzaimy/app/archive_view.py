"""DGX 원본 보관소 화면 — 원본 목록 장부(app/archive)를 사업 × 갈래로 보여 주고 원본을 찾는다. 문서함(/criteria)에서 들어온다."""

from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlencode

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


def overview(db, scope: dict | None = None, hidden: set[int] | None = None) -> dict:
    rows = archive.summary(db)
    for h in _hidden_rows(db, _hidden_docs(db, scope) if hidden is None else hidden):   # 볼 수 없는 문서는 집계에서도 뺀다
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


PAGE_SIZE = 50
SORTS = {"name": ("경로·이름순", "rel"),
         "size": ("큰 파일 먼저", "size DESC, rel"),
         "year": ("최근 연도 먼저", "year IS NULL, year DESC, round IS NULL, round DESC, rel")}
LINKED = {"yes": "문서함 연결됨", "no": "문서함 미연결"}
FILTER_KEYS = ("q", "program", "kind", "year", "round", "ext", "linked")


def _programs(db) -> dict[str, str]:
    """사업 열쇠 → 이름(장부 전체). 사업 칸에 이름을 쳐도, 표의 링크처럼 열쇠를 넘겨도 찾게."""
    archive.ensure(db)
    with db._conn() as conn:
        rows = conn.execute("SELECT program, MAX(program_name) FROM archive_files WHERE removed_at = '' GROUP BY program").fetchall()
    return {str(r[0] or ""): (r[1] or "사업 미분류") for r in rows}


def resolve_programs(value: str, names: dict[str, str]) -> list[str] | None:
    """사업 칸의 값 → 사업 열쇠 목록. 열쇠·정확한 이름·이름 일부 순으로 맞춘다. 빈 값이면 None(거르지 않음)."""
    value = (value or "").strip()
    if not value:
        return None
    if value in names:
        return [value]
    exact = [k for k, n in names.items() if n == value]
    if exact:
        return exact
    low = value.lower()
    return sorted(k for k, n in names.items() if low in n.lower())


def _ext(item: dict) -> str:
    ext = (item.get("ext") or "").lower().lstrip(".")
    if not ext:
        name = item["rel"].rsplit("/", 1)[-1]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext[:6]


def _size(n) -> str:
    n = int(n or 0)
    if n < 1_000_000:
        return f"{n / 1000:,.1f} KB"
    if n < 1_000_000_000:
        return f"{n / 1e6:,.1f} MB"
    return f"{n / 1e9:,.2f} GB"


def short_path(rel: str) -> str:
    """저장 위치는 마지막 폴더 셋만 — 전체 경로는 툴팁·펼침으로."""
    folders = rel.split("/")[:-1]
    return ("… / " if len(folders) > 3 else "") + " / ".join(folders[-3:])


def _where(f: dict, codes: list[str] | None, hidden: set[int]) -> tuple[str, list]:
    q, args = " WHERE removed_at = ''", []
    if codes is not None:
        q += (" AND program IN (" + ",".join("?" * len(codes)) + ")") if codes else " AND 1 = 0"
        args += codes
    for key in ("kind", "year", "round"):
        if f[key]:
            q += f" AND {key} = ?"
            args.append(f[key])
    if f["ext"]:
        q += " AND LOWER(ext) IN (?, ?)"
        args += [f["ext"], "." + f["ext"]]
    if f["linked"] == "yes":
        q += " AND doc_id IS NOT NULL"
    elif f["linked"] == "no":
        q += " AND doc_id IS NULL"
    for term in f["q"].split()[:6]:          # 여러 낱말은 모두 들어간 파일만(순서 무관)
        q += " AND LOWER(rel) LIKE ? ESCAPE '!'"
        args.append("%" + term.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%")
    if hidden:                               # 볼 수 없는 문서에 이어진 파일은 건수·목록 모두에서 뺀다(정수만 SQL 에 넣는다)
        q += " AND (doc_id IS NULL OR doc_id NOT IN (" + ",".join(str(int(i)) for i in sorted(hidden)) + "))"
    return q, args


def search(db, f: dict, hidden: set[int], role: str, names: dict[str, str] | None = None) -> dict:
    """원본 찾기 — 조건에 맞는 전체 건수와 한 쪽(PAGE_SIZE개)."""
    archive.ensure(db)
    names = _programs(db) if names is None else names
    where, args = _where(f, resolve_programs(f["program"], names), hidden)
    with db._conn() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM archive_files" + where, args).fetchone()[0])
        pages = max(1, -(-total // PAGE_SIZE))
        page = min(f["page"], pages)
        rows = conn.execute("SELECT rel, size, ext, program, program_name, kind, year, round, doc_id FROM archive_files"
                            + where + " ORDER BY " + SORTS[f["sort"]][1] + " LIMIT ? OFFSET ?",
                            args + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    keys = ("rel", "size", "ext", "program", "program_name", "kind", "year", "round", "doc_id")
    found = []
    for r in rows:
        item = dict(zip(keys, r))
        item.update(name=item["rel"].rsplit("/", 1)[-1], short_path=short_path(item["rel"]), ext_label=_ext(item),
                    size_label=_size(item["size"]), kind_label=KIND_KO.get(item["kind"] or "", item["kind"] or ""),
                    program_label=item["program_name"] or "사업 미분류",
                    can_open=(bool(item["doc_id"]) or role == "dev") and not item["rel"].startswith(archive.UPLOAD_PREFIX))
        found.append(item)
    return {"found": found, "hits": total, "page": page, "pages": pages,
            "first": (page - 1) * PAGE_SIZE + 1 if found else 0, "last": (page - 1) * PAGE_SIZE + len(found)}


def facets(db) -> dict:
    """세부 조건의 선택지 — 연도·연차·형식(많은 순)."""
    archive.ensure(db)
    with db._conn() as conn:
        rows = conn.execute("SELECT LOWER(ext), year, round, COUNT(*) FROM archive_files WHERE removed_at = ''"
                            " GROUP BY LOWER(ext), year, round").fetchall()
    exts: dict[str, int] = defaultdict(int)
    years, rounds = set(), set()
    for ext, year, round_, n in rows:
        if ext:
            exts[str(ext).lstrip(".")] += int(n)
        if year:
            years.add(int(year))
        if round_:
            rounds.add(int(round_))
    return {"years": sorted(years, reverse=True), "rounds": sorted(rounds),
            "exts": [e for e, _ in sorted(exts.items(), key=lambda t: -t[1])][:24]}


def page_url(f: dict, **change) -> str:
    """지금 조건에서 일부만 바꾼 주소 — 쪽 넘김·조건 지우기·공유용."""
    params = {k: f[k] for k in FILTER_KEYS}
    params.update(sort="" if f["sort"] == "name" else f["sort"], page=f["page"])
    params.update(change)
    if params["page"] in (1, "1", ""):
        params["page"] = ""
    params = {k: v for k, v in params.items() if v not in ("", None)}
    return "/archive" + ("?" + urlencode(params) if params else "") + "#archive-search"


def chips(f: dict, names: dict[str, str]) -> list[dict]:
    """걸린 조건 — 하나씩 지울 수 있게 그 조건만 뺀 주소를 붙인다."""
    label = {"q": lambda v: f"‘{v}’", "program": lambda v: "사업 " + names.get(v, v), "kind": lambda v: KIND_KO.get(v, v),
             "year": lambda v: f"{v}년", "round": lambda v: f"{v}차년도", "ext": lambda v: v.upper(),
             "linked": lambda v: LINKED.get(v, v)}
    return [{"key": k, "label": label[k](f[k]), "url": page_url(f, **{k: "", "page": 1})} for k in FILTER_KEYS if f[k]]


def _int(v: str) -> int | str:
    v = (v or "").strip()
    return int(v) if v.isdigit() and len(v) <= 4 else ""


@router.get("/archive", response_class=HTMLResponse)
def archive_page(request: Request, program: str = "", kind: str = "", q: str = "", year: str = "", round: str = "",
                 ext: str = "", linked: str = "", sort: str = "name", page: str = "1", partial: str = ""):
    st = request.app.state
    scope = {"dept": getattr(request.state, "dept", "") or None, "user": request.state.user, "role": request.state.role}
    hidden = _hidden_docs(st.db, scope)
    f = {"q": q.strip()[:200], "program": program.strip()[:200], "kind": kind if kind in KIND_KO and kind else "",
         "year": _int(year), "round": _int(round), "ext": "".join(c for c in ext.lower() if c.isalnum())[:10],
         "linked": linked if linked in LINKED else "", "sort": sort if sort in SORTS else "name",
         "page": max(1, _int(page) or 1)}
    names = _programs(st.db)
    active = any(f[k] for k in FILTER_KEYS)
    data = {"f": f, "active": active, "chips": chips(f, names), "sorts": SORTS, "linked_ko": LINKED, "kind_ko": KIND_KO,
            "found": [], "hits": 0, "page": 1, "pages": 1, "page_size": PAGE_SIZE, "prev_url": "", "next_url": ""}
    if active:
        data.update(search(st.db, f, hidden, scope["role"], names))
        if data["page"] > 1:
            data["prev_url"] = page_url(f, page=data["page"] - 1)
        if data["page"] < data["pages"]:
            data["next_url"] = page_url(f, page=data["page"] + 1)
    if partial:                              # 조건을 바꿀 때마다 결과 칸만 다시 그린다(위쪽 집계는 건너뜀)
        return st.templates.TemplateResponse(request, "_archive_results.html", data)
    data.update(overview(st.db, scope, hidden))
    data.update(facets(st.db))
    data["program_value"] = names.get(f["program"], f["program"]) if f["program"] else ""   # 열쇠로 와도 칸에는 사업 이름
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
