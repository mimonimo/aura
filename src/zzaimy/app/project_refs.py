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
