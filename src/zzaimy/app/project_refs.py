"""관련 보관 사업 참조 — 담당자 프로젝트(지금 하는 사업)에 과거 사업 묶음(보관 프로젝트)을 근거 자료로 잇는다.

참조는 보관 해제가 아니다. 묶음의 보관 상태·소유자·문서 소속을 바꾸지 않고 관계만 둔다(project_refs).
같은 과거 묶음을 여러 프로젝트가 참조할 수 있다. 문서 수는 보는 사람이 열람할 수 있는 것만 센다(절대 규칙 4, access_policy.visible).
후보 찾기: 프로젝트 이름(과 기준 문서 제목·사업명)과 보관 묶음 이름의 낱말 겹침(형태소 명사·영문 약칭, 연도·일반 낱말 제외) — 특정 사업 이름을 코드에 두지 않는다.
"""

from __future__ import annotations

import re
import time
import unicodedata
from functools import lru_cache

_SCHEMA = ("CREATE TABLE IF NOT EXISTS project_refs (project_id INTEGER NOT NULL, ref_project_id INTEGER NOT NULL,"
           " reason TEXT NOT NULL DEFAULT '', linked_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT '',"
           " PRIMARY KEY (project_id, ref_project_id))")
_ensured: set[str] = set()


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


# 어느 사업에나 붙는 일반 낱말 — 겹쳐도 같은 사업이라는 단서가 아니다
_GENERAL = re.compile(r"^(?:사업|사업단|사업계획서|계획서|계획|지원|지원사업|선도|전문대학|대학|대학교|육성|분야|안내|제출|공고|"
                      r"결과|보고서|결과보고서|운영|추진|연도|차년도|내|프로젝트)$")


@lru_cache(maxsize=4096)
def _tokens(name: str) -> frozenset[str]:
    """이름의 낱말 — 형태소 분석 명사(regulations.extract_nouns)와 영문 약칭. 글자·자모 조각으로 견주지 않는다.
    이름은 먼저 NFKC 로 맞춘다(맥 파일 이름처럼 자모가 풀린 NFD 이름도 같은 낱말이 되게)."""
    from zzaimy.app.regulations import extract_nouns

    text = unicodedata.normalize("NFKC", name or "")
    text = re.sub(r"(?:19|20)\d{2}\s*(?:학년도|년도|년)?|\d+\s*차\s*년도", " ", text)
    words = {w.replace(" ", "") for w in extract_nouns(text)}
    words |= {w.upper() for w in re.findall(r"[A-Za-z][A-Za-z0-9.+]{1,}", text)}
    return frozenset(w for w in words if len(w) >= 2 and not _GENERAL.match(w))


def _probes(project_name: str, names: list[str] | None = None) -> list[tuple[str, str, frozenset, str]]:
    """견줄 이름들 — (출처 이름표, 보일 이름, 낱말, 같은 이름 열쇠). 프로젝트 이름 + 기준 문서 제목·사업명."""
    from zzaimy.app.program_names import display_name, group_key

    out = []
    for label, raw in [("이름", project_name or "")] + [("기준·사업명", n) for n in (names or []) if (n or "").strip()]:
        shown = display_name(raw)
        toks = _tokens(shown)
        if toks:
            out.append((label, shown, toks, group_key(shown)))
    return out


def _overlap(probes, name: str) -> tuple[float, str] | None:
    """가장 많이 겹치는 이름의 (점수, 까닭). 같은 이름 열쇠면 1. 기준 제목·사업명은 3할 이상 겹칠 때만."""
    from zzaimy.app.program_names import display_name, group_key

    shown = display_name(name or "")
    theirs, key = _tokens(shown), group_key(shown)
    best = None
    for label, p_shown, toks, p_key in probes:
        if p_key and p_key == key:
            cand = (1.0, f"{label} 일치: {p_shown[:30]}")
        else:
            common = toks & theirs
            share = len(common) / max(len(toks), 1)
            if not common or (label != "이름" and share < NAME_SHARE_MIN):
                continue
            cand = (share, f"{label} 같은 낱말: " + "·".join(sorted(common)[:6]))
        if best is None or cand[0] > best[0]:
            best = cand
    return best


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


NAME_SHARE_MIN = 0.3


def candidates(db, project: dict, q: str = "", scope: dict | None = None, limit: int = 12,
               names: list[str] | None = None) -> list[dict]:
    """보관 묶음 후보 — 검색어가 있으면 이름에 그 글이 든 것, 없으면 프로젝트 이름·기준 문서 제목·사업명(names)과 겹치는 순.
    볼 수 있는 문서가 없는 묶음은 뺀다.

    프로젝트 이름만 보면 「인공지능 분야 계획서」처럼 사업명이 이름에 없을 때 후보가 없다 — 프로젝트에 이은 공고·기준의 제목과
    문서에서 읽은 사업명도 같은 방식(글자 조각 겹침, 같은 이름 열쇠면 바로 일치)으로 견준다. 사업 이름은 코드에 두지 않는다."""
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT id, name, program FROM projects WHERE archived = 1 AND id <> ?",
                                              (int(project["id"]),)).fetchall()]
    linked = {r["ref_project_id"] for r in list_refs(db, int(project["id"]), None)}
    probes = _probes(project.get("name") or "", names)
    picked = []
    for r in rows:
        if r["id"] in linked:
            continue
        if q.strip():
            if q.strip().replace(" ", "") not in (r["name"] or "").replace(" ", ""):
                continue
            score, why = 1.0, f"검색어 「{q.strip()[:20]}」"
        else:
            best = _overlap(probes, r["name"] or "")
            if best is None:
                continue
            score, why = best
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


_PERIOD = re.compile(r"((?:19|20)\d{2})\s*(?:\.\d{1,2})?\s*~\s*((?:19|20)\d{2})?")


def _ledger_periods(db, program_ids: list[str]) -> dict[str, tuple[int, int | None]]:
    """사업 노드(그래프)의 외부 확인 기간 「2025~2027」 — 없거나 그래프가 없으면 빈 사전."""
    if not program_ids:
        return {}
    import json as _json
    out: dict[str, tuple[int, int | None]] = {}
    try:
        with db._conn() as conn:
            for pid, props in conn.execute(f"SELECT id, props FROM kg_nodes WHERE id IN ({','.join('?' * len(program_ids))})",
                                           program_ids).fetchall():
                p_ = props if isinstance(props, dict) else _json.loads(props or "{}")
                m = _PERIOD.search(str((p_.get("ledger") or {}).get("period") or ""))
                if m:
                    out[pid] = (int(m.group(1)), int(m.group(2)) if m.group(2) else None)
    except Exception:
        return {}
    return out


def ongoing(period: tuple[int, int | None] | None, years: set[int], this_year: int) -> bool:
    """사업이 올해 진행 중인가 — 외부 확인 기간이 있으면 그것이 올해를 품는가, 없으면 묶음 연도 가운데 올해 이후가 있는가.
    보관 묶음(지난 문서 모음)이라도 사업 자체가 진행 중이면 「지난 사업」이 아니다."""
    if period:
        start, end = period
        return start <= this_year <= (end or 9999)
    return bool(years) and max(years) >= this_year


def browse(db, scope: dict | None, q: str = "", status: str = "archived", year: str = "", unit: str = "",
           for_project: int | None = None, limit: int = 200, sort: str = "", names: list[str] | None = None,
           peers: tuple[str, ...] = (), shared: tuple[int, ...] | list[int] = ()) -> dict:
    """통합 프로젝트 찾기 — 진행 중(본인 것)·보관(문서 열람 권한 기준)을 사업명·수행 연도·사업단·상태로.

    돌려주는 것 {"items": [{id, name, status, year, round, when, group, group_name, program, units, n_docs, why, linked}],
    "units": [...], "years": [연도만, 최근 순], "rounds": [연도를 모르는 연차], "has_unknown": bool, "total", "sort"}.
    - status: archived · active · all. 진행 중은 보는 사람의 것과 같은 부서 동료(peers)의 것(읽기 전용, readonly), 보관은 볼 수 있는
      문서가 하나라도 있는 묶음만(문서 수도 그 범위)
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
    import datetime
    this_year = datetime.date.today().year
    # 사업마다 묶음 연도(필터 전 전체)와 외부 확인 기간 — 진행 중 사업 판정용
    prog_years: dict[str, set[int]] = {}
    for r in rows:
        if r["archived"] and r.get("program"):
            y_, rnd_ = _when(r["program"])
            b_ = r["program"].rsplit("|", 1)[0]
            if not y_ and rnd_ and b_ in starts:
                y_ = str(starts[b_] + rnd_ - 1)
            if y_:
                prog_years.setdefault(b_, set()).add(int(y_))
    periods = _ledger_periods(db, sorted({(r.get("program") or "").rsplit("|", 1)[0] for r in rows if r["archived"] and r.get("program")}))
    words = [w for w in (q or "").split() if w]
    # 사업의 다른 이름·약칭(그래프 사업 노드의 표시 이름·약칭)도 찾는다 — 묶음 이름에 약칭이 없는 사업도
    # 약칭으로 찾을 수 있게
    aliases: dict[str, str] = {}
    if words:
        try:
            import json as _json
            progs = sorted({(r.get("program") or "").split("|", 1)[0] for r in rows if r.get("program")})
            if progs:
                with db._conn() as conn:
                    for pid, label, props in conn.execute(
                            f"SELECT id, label, props FROM kg_nodes WHERE id IN ({','.join('?' * len(progs))})", progs).fetchall():
                        p_ = props if isinstance(props, dict) else _json.loads(props or "{}")
                        acr = p_.get("acronyms") or p_.get("acrs") or []
                        acr = list(acr.keys()) if isinstance(acr, dict) else list(acr)
                        aliases[pid] = " ".join([label or ""] + [str(a) for a in acr][:20])
        except Exception:
            aliases = {}
    keep = []
    for r in rows:
        st = "archived" if r["archived"] else "active"
        if status in ("archived", "active") and st != status:
            continue
        if st == "active" and not dev and r["owner"] != user and r["owner"] not in peers and int(r["id"]) not in set(shared):
            continue
        r["readonly"] = st == "active" and not dev and r["owner"] != user
        flat = ((r["name"] or "") + " " + aliases.get((r.get("program") or "").split("|", 1)[0], "")).replace(" ", "").lower()
        if words and not all(w.lower().replace(" ", "") in flat for w in words):     # 낱말마다 들어 있으면(순서·띄어쓰기 무관)
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
    probes: list = []
    if for_project:
        linked = {x["ref_project_id"] for x in list_refs(db, int(for_project))}
        proj = next((r for r in rows if r["id"] == int(for_project)), None)
        probes = _probes((proj or {}).get("name") or "", names)
    want_y, want_r, want_none = year_option(year)
    pool = []
    for r in keep:
        if for_project and r["id"] == int(for_project):
            continue                                   # 연결하려는 프로젝트 자신은 후보가 아니다
        n = counts.get(r["id"], 0)
        if r["status"] == "archived" and not n:
            continue                                   # 볼 수 있는 문서가 없는 보관 묶음은 이름도 보이지 않는다
        us = units.get(r["id"], [])[:3] if r["status"] == "archived" else ([r["unit"]] if r.get("unit") else [])
        hit = _overlap(probes, r["name"] or "") if probes else None
        when = (f"{r['year']}" + (f" · {r['round']}차년도" if r["round"] else "")) if r["year"] else \
            (f"{r['round']}차년도 (연도 미상)" if r["round"] else "연도 미상")
        future = bool(r["year"]) and int(r["year"]) > this_year         # 올해 뒤 연도 — 그 해 계획 자료다
        if future:
            when += " · 계획 연도"
        base = (r["program"] or "").rsplit("|", 1)[0]
        pool.append({"id": r["id"], "name": r["name"], "status": r["status"], "year": r["year"], "round": r["round"],
                     "year_derived": r["derived"], "when": when, "future": future, "program": r["program"],
                     "ongoing": r["status"] == "archived" and ongoing(periods.get(base), prog_years.get(base, set()), this_year),
                     "group": base if base and r["status"] == "archived" else f"project:{r['id']}",
                     "group_name": _group_name(r["name"] or "") if r["status"] == "archived" else (r["name"] or ""),
                     "units": us, "n_docs": n, "why": hit[1] if hit else "",
                     "linked": r["id"] in linked, "readonly": bool(r.get("readonly")), "score": hit[0] if hit else 0.0})

    def year_ok(it):
        if want_none:                                  # 「연도 미상」 하나 — 연차만 아는 묶음도 연도는 모른다
            return not it["year"]
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
            "has_unknown": any(not it["year"] for it in by_unit)}


def group_items(items: list[dict]) -> list[dict]:
    """화면 묶음 — 같은 사업의 연도별 묶음을 한 카드로(나온 순서 유지). 카드마다 문서 합계·연도 범위.
    보관 묶음의 사업 이름은 공용 규칙(program_names.display_name·group_names)으로 보이고 묶는다 — 사업 id 가 달라도
    띄어쓰기·괄호·때 꼬리표만 다른 이름이면 한 카드다. 진행 중 프로젝트는 각자 한 카드."""
    from zzaimy.app.program_names import display_name, group_names

    totals: dict[str, int] = {}
    for it in items:
        if it["status"] == "archived":
            n = display_name(it["group_name"]) or it["group_name"]
            totals[n] = totals.get(n, 0) + it["n_docs"]
    rep_of = group_names(totals)
    # 보일 이름은 라이브러리 사업 목록과 같은 장부 정식 이름(학교 재정지원사업 현황) — 같은 사업이 화면마다 다른 이름으로 서지 않게
    from zzaimy.app.program_names import load_ledger
    from zzaimy.app.program_tree import Ledger
    lg = Ledger(load_ledger())
    groups: dict[str, dict] = {}
    for it in items:
        if it["status"] == "archived":
            shown = display_name(it["group_name"]) or it["group_name"]
            name = rep_of.get(shown) or shown
            e = lg.find(name)
            name = (e.get("name") if e else None) or name
            key = "name:" + name
        else:
            name, key = it["group_name"], it["group"]
        g = groups.setdefault(key, {"key": it["group"], "name": name, "rows": [], "n_docs": 0,
                                    "status": it["status"], "units": [], "ongoing": False})
        g["rows"].append(it)
        g["n_docs"] += it["n_docs"]
        g["ongoing"] = g["ongoing"] or bool(it.get("ongoing"))
        for u in it["units"]:
            if u not in g["units"]:
                g["units"].append(u)
    out = list(groups.values())
    for g in out:
        ys = sorted(int(r["year"]) for r in g["rows"] if r["year"])
        g["span"] = (f"{ys[0]}~{ys[-1]}" if ys[0] != ys[-1] else str(ys[0])) if ys else ""
        g["units"] = g["units"][:3]
    return out
