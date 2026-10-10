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
    unit = (project.get("unit") or "").strip()
    if unit and picked:                                # 담당자가 고른 사업단의 과거 묶음을 조금 앞으로(같은 조직의 자료)
        us = _units(db, [r["id"] for r in picked])
        for r in picked:
            if unit in us.get(r["id"], [])[:3]:
                r["score"] += 0.2
                r["why"] += f" · 같은 사업단({unit})"
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


def _plausible(y) -> bool:
    from zzaimy.graph.programs import plausible_year
    return plausible_year(y)


def _when(program_key: str) -> tuple[str, int | None]:
    """보관 묶음 열쇠(「program:x|2023」「…|r2」「…|?」「…|*」)의 (수행 연도, 연차). 있을 법하지 않은 연도(「|1968」)는 미상."""
    tail = (program_key or "").rsplit("|", 1)[-1] if "|" in (program_key or "") else ""
    if tail.isdigit():
        return (tail if _plausible(tail) else ""), None
    if tail.startswith("r") and tail[1:].isdigit():
        return "", int(tail[1:])
    return "", None


def _year_of(program_key: str) -> str:
    """수행 연도만(연차는 _when) — 연도 필터에 연차가 섞이지 않게."""
    return _when(program_key)[0]


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


SORTS = {"relevance": "관련도순", "year": "최근 연도순", "docs": "문서 많은 순", "name": "이름순"}
_NAME_WHEN = re.compile(r"^((?:19|20)\d{2})년\s+(.+?)\s+\((\d{1,2})차년도\)$")
_NAME_TRIM = (re.compile(r"^(?:19|20)\d{2}\s*(?:학년도|년도|년)?\s+"), re.compile(r"\s*\((?:\d{1,2}차년도|연도 미상)\)$"),
              re.compile(r"\s+\d{1,2}차년도$"))


def _group_name(name: str) -> str:
    """묶음 이름에서 연도·연차 꾸밈(archive.bundle_key 의 이름 틀)을 걷어낸 사업 이름."""
    n = name or ""
    for rx in _NAME_TRIM:
        n = rx.sub("", n)
    return n.strip() or (name or "")


def _round_starts(rows: list[dict]) -> dict[str, int]:
    """사업마다 시작 연도 — 같은 사업의 묶음 이름(「2024년 ○○ (2차년도)」)이 연도·연차를 함께 말하고 서로 맞을 때만.
    엇갈리면(준비년도를 0차로 세는 등) 환산하지 않는다."""
    seen: dict[str, set[int]] = {}
    for r in rows:
        m = _NAME_WHEN.match((r.get("name") or "").strip())
        base = (r.get("program") or "").rsplit("|", 1)[0]
        if m and base and _plausible(m.group(1)):
            seen.setdefault(base, set()).add(int(m.group(1)) - int(m.group(3)) + 1)
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def year_option(value: str) -> tuple[str, int | None, bool]:
    """필터 값 해석 — 「2024」 연도, 「r2」(옛 표기 「2차년도」) 연차, 「none」 연도 미상."""
    v = (value or "").strip()
    if v == "none":
        return "", None, True
    m = re.fullmatch(r"r(\d{1,2})|(\d{1,2})\s*차년도", v)
    if m:
        return "", int(m.group(1) or m.group(2)), False
    return (v if re.fullmatch(r"\d{4}", v) else ""), None, False


def browse(db, scope: dict | None, q: str = "", status: str = "archived", year: str = "", unit: str = "",
           for_project: int | None = None, limit: int = 200, sort: str = "") -> dict:
    """통합 프로젝트 찾기(C-195) — 진행 중(본인 것)·보관(문서 열람 권한 기준)을 사업명·수행 연도·사업단·상태로.

    돌려주는 것 {"items": [{id, name, status, year, round, when, group, group_name, program, units, n_docs, why, linked}],
    "units": [...], "years": [연도만, 최근 순], "rounds": [연도를 모르는 연차], "has_unknown": bool, "total", "sort"}.
    - status: archived · active · all. 진행 중은 보는 사람의 것만, 보관은 볼 수 있는 문서가 하나라도 있는 묶음만(문서 수도 그 범위)
    - year: 「2024」(연도) · 「r2」(연도를 모르는 2차년도) · 「none」(연도 미상). 연차만 아는 묶음은 같은 사업의 다른 묶음이
      연도·연차를 함께 말해 시작 연도가 하나로 정해질 때만 연도로 환산한다
    - 연도·사업단 목록은 그 필터를 걸기 전 범위에서 뽑는다(하나를 고르면 나머지 선택지가 사라지지 않게)
    - for_project 를 주면 그 프로젝트와의 관련 이유(이름 겹침)와 이미 참조됐는지(linked)를 함께 — 연결은 POST /project/{id}/refs
    검색·열람·참조는 보관 해제나 소유권 이전이 아니다."""
    user = (scope or {}).get("user")
    dev = (scope or {}).get("role") == "dev"
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, name, owner, archived, program, unit FROM projects ORDER BY id").fetchall()]
    starts = _round_starts([r for r in rows if r["archived"]])
    words = [w for w in (q or "").split() if w]
    keep = []
    for r in rows:
        st = "archived" if r["archived"] else "active"
        if status in ("archived", "active") and st != status:
            continue
        if st == "active" and not dev and r["owner"] != user:
            continue
        flat = (r["name"] or "").replace(" ", "").lower()
        if words and not all(w.lower() in flat for w in words):     # 낱말마다 들어 있으면(순서·띄어쓰기 무관)
            continue
        y, rnd = _when(r["program"])
        if st == "active" and not y:
            from zzaimy.graph.programs import _first_year
            y = str(_first_year(r["name"] or "") or "")
        base = (r["program"] or "").rsplit("|", 1)[0]
        derived = False
        if not y and rnd and base in starts:
            y, derived = str(starts[base] + rnd - 1), True
        r.update(status=st, year=y, round=rnd, derived=derived)
        keep.append(r)
    counts = _visible_counts(db, [r["id"] for r in keep], scope)
    units = _units(db, [r["id"] for r in keep])
    linked: set[int] = set()
    mine: set[str] = set()
    if for_project:
        linked = {x["ref_project_id"] for x in list_refs(db, int(for_project))}
        proj = next((r for r in rows if r["id"] == int(for_project)), None)
        mine = _grams((proj or {}).get("name") or "")
    want_y, want_r, want_none = year_option(year)
    pool = []
    for r in keep:
        n = counts.get(r["id"], 0)
        if r["status"] == "archived" and not n:
            continue                                   # 볼 수 있는 문서가 없는 보관 묶음은 이름도 보이지 않는다
        us = units.get(r["id"], [])[:3] if r["status"] == "archived" else ([r["unit"]] if r.get("unit") else [])
        common = sorted(mine & _grams(r["name"] or "")) if mine else []
        when = (f"{r['year']}" + (f" · {r['round']}차년도" if r["round"] else "")) if r["year"] else \
            (f"{r['round']}차년도 (연도 미상)" if r["round"] else "연도 미상")
        base = (r["program"] or "").rsplit("|", 1)[0]
        pool.append({"id": r["id"], "name": r["name"], "status": r["status"], "year": r["year"], "round": r["round"],
                     "year_derived": r["derived"], "when": when, "program": r["program"],
                     "group": base if base and r["status"] == "archived" else f"project:{r['id']}",
                     "group_name": _group_name(r["name"] or "") if r["status"] == "archived" else (r["name"] or ""),
                     "units": us, "n_docs": n, "why": ("이름 겹침: " + "·".join(common[:6])) if common else "",
                     "linked": r["id"] in linked, "score": len(common) / max(len(mine), 1) if mine else 0.0})

    def year_ok(it):
        if want_none:
            return not it["year"] and not it["round"]
        if want_r is not None:
            return not it["year"] and it["round"] == want_r
        return not want_y or it["year"] == want_y

    by_year = [it for it in pool if year_ok(it)]
    by_unit = [it for it in pool if not unit or unit in it["units"]]
    items = [it for it in by_year if not unit or unit in it["units"]]
    sort = sort if sort in SORTS else ("relevance" if for_project else "year")
    if sort == "relevance":
        items.sort(key=lambda x: (-x["score"], -x["n_docs"], x["name"]))
    elif sort == "docs":
        items.sort(key=lambda x: (-x["n_docs"], x["name"]))
    elif sort == "name":
        items.sort(key=lambda x: (x["group_name"], -int(x["year"] or 0), x["name"]))
    else:
        items.sort(key=lambda x: (-int(x["year"] or 0), -(x["round"] or 0), -x["n_docs"], x["name"]))
    return {"items": items[:limit], "total": len(items), "sort": sort,
            "units": sorted({u for it in by_year for u in it["units"]}),
            "years": sorted({it["year"] for it in by_unit if it["year"]}, reverse=True),
            "rounds": [f"r{n}" for n in sorted({it["round"] for it in by_unit if not it["year"] and it["round"]})],
            "has_unknown": any(not it["year"] and not it["round"] for it in by_unit)}


def group_items(items: list[dict]) -> list[dict]:
    """화면 묶음 — 같은 사업의 연도별 묶음을 한 카드로(나온 순서 유지). 카드마다 문서 합계·연도 범위."""
    groups: dict[str, dict] = {}
    for it in items:
        g = groups.setdefault(it["group"], {"key": it["group"], "name": it["group_name"], "rows": [], "n_docs": 0,
                                             "status": it["status"], "units": []})
        g["rows"].append(it)
        g["n_docs"] += it["n_docs"]
        for u in it["units"]:
            if u not in g["units"]:
                g["units"].append(u)
    out = list(groups.values())
    for g in out:
        ys = sorted(int(r["year"]) for r in g["rows"] if r["year"])
        g["span"] = (f"{ys[0]}~{ys[-1]}" if ys[0] != ys[-1] else str(ys[0])) if ys else ""
        g["units"] = g["units"][:3]
    return out
