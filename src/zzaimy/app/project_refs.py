"""관련 보관 사업 참조 — 담당자 프로젝트(지금 하는 사업)에 과거 사업 묶음(보관 프로젝트)을 근거 자료로 잇는다(C-192).

참조는 보관 해제가 아니다. 묶음의 보관 상태·소유자·문서 소속을 바꾸지 않고 관계만 둔다(project_refs).
같은 과거 묶음을 여러 프로젝트가 참조할 수 있다. 문서 수는 보는 사람이 열람할 수 있는 것만 센다(절대 규칙 4, access_policy.visible).
후보 찾기: 프로젝트 이름과 보관 묶음 이름의 글자 겹침(2글자 조각, 연도·흔한 꼬리말 제외) — 특정 사업 이름을 코드에 두지 않는다.
"""

from __future__ import annotations

import re
import time

_SCHEMA = ("CREATE TABLE IF NOT EXISTS project_refs (project_id INTEGER NOT NULL, ref_project_id INTEGER NOT NULL,"
           " reason TEXT NOT NULL DEFAULT '', linked_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT '',"
           " PRIMARY KEY (project_id, ref_project_id))")
_ensured: set[str] = set()
_STOP = re.compile(r"\d{2,4}\s*(?:학년도|년도|년|차년도|차)?|사업|지원|선도|전문대학|대학|육성|\(.*?\)|[\s·\-_()]+")


def ensure(db) -> None:
    if db.path in _ensured:
        return
    try:                                      # 이미 있으면 아무것도 보내지 않는다(PostgreSQL 은 IF NOT EXISTS 도 잠금부터 잡는다)
        with db._conn() as conn:
            conn.execute("SELECT project_id FROM project_refs LIMIT 0")
    except Exception:
        with db._conn() as conn:
            conn.execute(_SCHEMA)
    _ensured.add(db.path)


def _grams(name: str) -> set[str]:
    core = _STOP.sub(" ", name or "")
    out: set[str] = set()
    for word in core.split():
        if re.fullmatch(r"[A-Za-z0-9.+]+", word):
            out.add(word.upper())
            continue
        out.update(word[i:i + 2] for i in range(len(word) - 1))
    return out


def _visible_counts(db, project_ids: list[int], scope: dict | None) -> dict[int, int]:
    if not project_ids:
        return {}
    marks = ",".join("?" * len(project_ids))
    with db._conn() as conn:
        rows = conn.execute(f"SELECT id, project_id, access_level, dept, owner FROM documents WHERE project_id IN ({marks})",
                            project_ids).fetchall()
    counts: dict[int, int] = {}
    if scope is None or scope.get("role") == "dev":
        for r in rows:
            counts[int(r[1])] = counts.get(int(r[1]), 0) + 1
        return counts
    from zzaimy.app.access_policy import visible
    for r in rows:
        if visible({"access_level": r[2], "dept": r[3], "owner": r[4]}, **scope):
            counts[int(r[1])] = counts.get(int(r[1]), 0) + 1
    return counts


def candidates(db, project: dict, q: str = "", scope: dict | None = None, limit: int = 12) -> list[dict]:
    """보관 묶음 후보 — 검색어가 있으면 이름에 그 글이 든 것, 없으면 프로젝트 이름과 겹치는 순. 볼 수 있는 문서가 없는 묶음은 뺀다."""
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, name, program FROM projects WHERE archived = 1 AND id <> ?",
                                              (int(project["id"]),)).fetchall()]
    linked = {r["ref_project_id"] for r in list_refs(db, int(project["id"]), None)}
    mine = _grams(project.get("name") or "")
    picked = []
    for r in rows:
        if r["id"] in linked:
            continue
        if q.strip():
            if q.strip().replace(" ", "") not in (r["name"] or "").replace(" ", ""):
                continue
            score, why = 1.0, f"검색어 「{q.strip()[:20]}」"
        else:
            theirs = _grams(r["name"] or "")
            common = mine & theirs
            if not common:
                continue
            score = len(common) / max(len(mine), 1)
            why = "이름 겹침: " + "·".join(sorted(common)[:6])
        picked.append({**r, "score": score, "why": why})
    picked.sort(key=lambda r: -r["score"])
    picked = picked[: limit * 3]
    counts = _visible_counts(db, [r["id"] for r in picked], scope)
    out = [{**r, "n_docs": counts.get(r["id"], 0)} for r in picked if counts.get(r["id"], 0)]
    return out[:limit]


def list_refs(db, project_id: int, scope: dict | None = None) -> list[dict]:
    ensure(db)
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT r.ref_project_id, r.reason, r.linked_by, r.created_at, p.name, p.archived FROM project_refs r"
            " JOIN projects p ON p.id = r.ref_project_id WHERE r.project_id = ? ORDER BY r.created_at", (project_id,)).fetchall()]
    if scope is not None:
        counts = _visible_counts(db, [r["ref_project_id"] for r in rows], scope)
        for r in rows:
            r["n_docs"] = counts.get(r["ref_project_id"], 0)
    return rows


def link(db, project_id: int, ref_project_id: int, reason: str = "", user: str = "") -> None:
    ensure(db)
    with db._conn() as conn:
        conn.execute("DELETE FROM project_refs WHERE project_id = ? AND ref_project_id = ?", (project_id, ref_project_id))
        conn.execute("INSERT INTO project_refs (project_id, ref_project_id, reason, linked_by, created_at) VALUES (?, ?, ?, ?, ?)",
                     (project_id, ref_project_id, reason[:200], user or "", time.strftime("%Y-%m-%dT%H:%M:%S")))


def unlink(db, project_id: int, ref_project_id: int) -> None:
    ensure(db)
    with db._conn() as conn:
        conn.execute("DELETE FROM project_refs WHERE project_id = ? AND ref_project_id = ?", (project_id, ref_project_id))


def _year_of(program_key: str) -> str:
    """보관 묶음 열쇠(「program:x|2023」「…|r2」「…|?」「…|*」)의 수행 연도 표시."""
    tail = (program_key or "").rsplit("|", 1)[-1] if "|" in (program_key or "") else ""
    if tail.isdigit():
        return tail
    if tail.startswith("r") and tail[1:].isdigit():
        return f"{tail[1:]}차년도"
    return ""


def _units(db, project_ids: list[int]) -> dict[int, list[str]]:
    """묶음마다 사업단(원본 최상위 폴더) — 문서가 많은 순."""
    if not project_ids:
        return {}
    marks = ",".join("?" * len(project_ids))
    out: dict[int, list[str]] = {}
    try:
        with db._conn() as conn:
            for pid, area, _n in conn.execute(
                    "SELECT d.project_id, a.area, COUNT(*) FROM documents d JOIN archive_files a ON a.rel = SUBSTR(d.stored_path, 7)"
                    f" WHERE d.stored_path LIKE 'dgx://%' AND d.project_id IN ({marks}) AND a.area <> ''"
                    " GROUP BY d.project_id, a.area ORDER BY 3 DESC", project_ids).fetchall():
                out.setdefault(int(pid), []).append(str(area))
    except Exception:
        pass
    return out


def browse(db, scope: dict | None, q: str = "", status: str = "archived", year: str = "", unit: str = "",
           for_project: int | None = None, limit: int = 200) -> dict:
    """통합 프로젝트 찾기(C-195) — 진행 중(본인 것)·보관(문서 열람 권한 기준)을 사업명·수행 연도·사업단·상태로.

    돌려주는 것 {"items": [{id, name, status, year, program, units, n_docs, why, linked}], "units": [...], "years": [...]}.
    - status: archived · active · all. 진행 중은 보는 사람의 것만, 보관은 볼 수 있는 문서가 하나라도 있는 묶음만(문서 수도 그 범위)
    - for_project 를 주면 그 프로젝트와의 관련 이유(이름 겹침)와 이미 참조됐는지(linked)를 함께 — 연결은 POST /project/{id}/refs
    검색·열람·참조는 보관 해제나 소유권 이전이 아니다."""
    user = (scope or {}).get("user")
    dev = (scope or {}).get("role") == "dev"
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, name, owner, archived, program FROM projects ORDER BY id").fetchall()]
    keep = []
    for r in rows:
        st = "archived" if r["archived"] else "active"
        if status in ("archived", "active") and st != status:
            continue
        if st == "active" and not dev and r["owner"] != user:
            continue
        if q.strip() and q.strip().replace(" ", "") not in (r["name"] or "").replace(" ", ""):
            continue
        r["status"], r["year"] = st, _year_of(r["program"])
        keep.append(r)
    counts = _visible_counts(db, [r["id"] for r in keep], scope)
    units = _units(db, [r["id"] for r in keep])
    linked: set[int] = set()
    mine: set[str] = set()
    if for_project:
        linked = {x["ref_project_id"] for x in list_refs(db, int(for_project))}
        proj = next((r for r in rows if r["id"] == int(for_project)), None)
        mine = _grams((proj or {}).get("name") or "")
    items = []
    for r in keep:
        n = counts.get(r["id"], 0)
        if r["status"] == "archived" and not n:
            continue                                   # 볼 수 있는 문서가 없는 보관 묶음은 이름도 보이지 않는다
        if year and r["year"] != year:
            continue
        us = units.get(r["id"], [])[:3]
        if unit and unit not in us:
            continue
        common = sorted(mine & _grams(r["name"] or "")) if mine else []
        items.append({"id": r["id"], "name": r["name"], "status": r["status"], "year": r["year"], "program": r["program"],
                      "units": us, "n_docs": n, "why": ("이름 겹침: " + "·".join(common[:6])) if common else "",
                      "linked": r["id"] in linked, "score": len(common) / max(len(mine), 1) if mine else 0.0})
    items.sort(key=lambda x: (-x["score"], -x["n_docs"], x["name"]))
    return {"items": items[:limit], "total": len(items),
            "units": sorted({u for it in items for u in it["units"]}),
            "years": sorted({it["year"] for it in items if it["year"]}, reverse=True)}
