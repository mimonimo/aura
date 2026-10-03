"""원본 목록 장부 — DGX 원본 보관소의 모든 파일을 VM 에서 알 수 있게(ADR-0047: 원본 보관은 DGX, 색인·분석은 VM).

사용자 2026-10-02: "어떤 문서가 DGX 에 있는지도 다 알아야 하지만 해당 부분 분석한 자료라던지 이런건 VM 에도 다 있어야 사용을 할거아냐".
파일 내용은 담지 않는다 — 경로·크기·수정 시각·형식과 사업 분류(graph/programs, 파일 이름·경로 기준)·갈래·연차, 문서함에 들인 경우 문서 번호.
DGX 에서 scripts/165 가 목록을 만들고 VM 에서 이 모듈로 들인다.
"""

from __future__ import annotations

import json
from datetime import datetime

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS archive_files (rel TEXT PRIMARY KEY, size BIGINT NOT NULL DEFAULT 0, mtime BIGINT NOT NULL DEFAULT 0,"
    " ext TEXT NOT NULL DEFAULT '', area TEXT NOT NULL DEFAULT '', program TEXT NOT NULL DEFAULT '', program_name TEXT NOT NULL DEFAULT '',"
    " status TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT '', year INTEGER, round INTEGER, dup_of TEXT NOT NULL DEFAULT '',"
    " doc_id INTEGER, analysis TEXT NOT NULL DEFAULT '', seen_at TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX IF NOT EXISTS archive_files_program ON archive_files (program)",
)
UPLOAD_PREFIX = "_플랫폼업로드/"
UNCLASSIFIED = "none"            # 사업 분류가 없는 원본의 보관 묶음 열쇠(담당자 프로젝트의 program '' 과 겹치지 않게)


SPLIT_MIN = 10                    # 사업 전체 문서가 이보다 적으면 연도로 나누지 않는다(1~3건짜리 묶음이 쌓이지 않게)


def bundle_key(program: str, year=None, round_=None) -> tuple[str, str]:
    """(묶음 열쇠, 이름 틀) — 사업 × 수행 연도(없으면 연차). 둘 다 모르면 「연도 미상」으로 두고 추정해 확정하지 않는다(C-192).
    연도·연차 근거는 원본 장부(경로·파일 이름 규칙)라 표지의 수행 연도와 다를 수 있다 — 그래프(157)의 연차 학습과 대조 검수 대상."""
    if not program:                       # 사업을 모르면 연도로 나누지 않는다 — 「사업 미분류」 하나
        return f"{UNCLASSIFIED}|?", "{name}"
    prog = program
    y, r = str(year or "").strip(), str(round_ or "").strip()
    if y and r:
        return f"{prog}|{y}", f"{y}년 {{name}} ({r}차년도)"
    if y:
        return f"{prog}|{y}", f"{y}년 {{name}}"
    if r:
        return f"{prog}|r{r}", f"{{name}} {r}차년도"
    return f"{prog}|?", "{name} (연도 미상)"


def program_project(db, program: str, name: str, cache: dict | None = None, year=None, round_=None,
                    whole: bool = False) -> int:
    """과거 사업 묶음(보관 프로젝트) — 사업 × 연도 열쇠로 찾고, 없으면 보관 상태로 만든다.

    프로젝트는 담당자가 지금 하는 사업(예: 「2026년 ○○ 지원사업」)의 작업 공간이다(사용자 2026-10-03). 동기화가 문서를 보고 묶는
    과거 사업은 연도별 보관 묶음으로 두고, 담당자가 「보관된 사업」에서 불러오거나 지침·기준 탭에서 참조한다.
    이름이 아니라 사업 id·연도로 찾아 같은 사업이 갈라지지 않게 한다."""
    key, title = bundle_key(program, year, round_)
    if whole and program:                 # 문서가 적은 사업 — 연도로 나누지 않은 사업 묶음 하나
        key, title = f"{program}|*", "{name}"
    if cache is not None and key in cache:
        return cache[key]
    with db._conn() as conn:
        row = conn.execute(
            "SELECT id FROM projects WHERE program = ? AND archived = 1"
            " AND owner = 'zzdev' AND archive_source = 'dgx' ORDER BY id LIMIT 1", (key,)
        ).fetchone()
    pid = int(row[0]) if row else db.create_project(
        "grant", title.format(name=name or "사업 미분류"), owner="zzdev", archived=True, program=key, archive_source="dgx")
    if cache is not None:
        cache[key] = pid
    return pid


def align_archived_projects(db) -> dict:
    """반입·DGX 보관 문서를 원본 장부의 현재 사업 분류·연도대로 연도별 보관 묶음에 넣는다(동기화마다).

    옮기는 문서는 프로젝트가 없거나 출처가 확인된 보관 자동 묶음(archive_source=dgx)에 있는 것만 — 담당자 프로젝트와
    불러온(보관 해제한) 묶음의 문서는 건드리지 않는다. 문서가 다 빠진 자동 묶음은 메모·기준·대화가 없을 때만 지운다."""
    ensure(db)
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT d.id, d.project_id, a.program, a.program_name, a.year, a.round FROM documents d"
            " JOIN archive_files a ON a.rel = SUBSTR(d.stored_path, 7)"
            " WHERE d.stored_path LIKE 'dgx://%' AND a.removed_at = ''").fetchall()
        rows += conn.execute(
            "SELECT d.id, d.project_id, a.program, a.program_name, a.year, a.round FROM documents d"
            " JOIN archive_files a ON a.doc_id = d.id"
            " WHERE d.stored_path NOT LIKE 'dgx://%' AND a.removed_at = ''").fetchall()
        auto = {int(r[0]): str(r[1] or "") for r in conn.execute(
            "SELECT id, program FROM projects WHERE archived = 1 AND owner = 'zzdev'"
            " AND archive_source = 'dgx'").fetchall()}
    cache: dict = {}
    moves: dict[int, list[int]] = {}
    per_prog: dict[str, int] = {}
    for _did, _pid, prog, *_rest in rows:
        per_prog[prog or ""] = per_prog.get(prog or "", 0) + 1
    for did, pid, prog, pname, year, rnd in rows:
        pid = int(pid) if pid is not None else None
        if pid is not None and pid not in auto:
            continue                                   # 담당자·불러온 프로젝트의 문서
        small = per_prog.get(prog or "", 0) < SPLIT_MIN
        want = program_project(db, prog or "", pname or "", cache, None if small else year, None if small else rnd,
                               whole=small)
        if pid != want:
            moves.setdefault(want, []).append(int(did))
    with db._conn() as conn:
        for pid, ids in moves.items():
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                conn.execute(f"UPDATE documents SET project_id = ? WHERE id IN ({','.join('?' * len(part))})", (pid, *part))
        empty = [int(r[0]) for r in conn.execute(
            "SELECT p.id FROM projects p WHERE p.archived = 1 AND p.owner = 'zzdev'"
            " AND p.archive_source = 'dgx'"
            " AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.project_id = p.id)"
            " AND NOT EXISTS (SELECT 1 FROM project_notes n WHERE n.project_id = p.id)"
            " AND NOT EXISTS (SELECT 1 FROM project_criteria k WHERE k.project_id = p.id)"
            " AND NOT EXISTS (SELECT 1 FROM chat_sessions c WHERE c.project_id = p.id)").fetchall()]
    for pid in empty:
        db.delete_project(pid)
    return {"moved": sum(len(v) for v in moves.values()), "removed_projects": len(empty)}


FIELDS = ("rel", "size", "mtime", "ext", "area", "program", "program_name", "status", "kind", "year", "round", "dup_of")


_ensured: set[str] = set()


def ensure(db) -> None:
    if db.path in _ensured:                  # 프로세스마다 한 번 — 칸 추가(ALTER)는 표 배타 잠금이라 자주 보내면 교착이 난다
        return
    # 표·칸이 이미 있으면 아무것도 보내지 않는다 — PostgreSQL 은 CREATE … IF NOT EXISTS 도 표 잠금부터 잡아,
    # 동기화가 장부를 크게 고치는 동안 시간 초과로 떨어진다(2026-10-03 실측)
    try:
        with db._conn() as conn:
            conn.execute("SELECT removed_at FROM archive_files LIMIT 0")
        _ensured.add(db.path)
        return
    except Exception:
        pass
    with db._conn() as conn:
        for q in _SCHEMA:
            conn.execute(q)
        try:
            cols = {str(r[0]) for r in conn.execute("SELECT removed_at FROM archive_files LIMIT 0").description or []}
        except Exception:
            cols = set()
    if cols:
        _ensured.add(db.path)
        return
    # 없어진 원본 표시(동기화) — 지우지 않고 표시만 해서 이어진 문서함 문서의 출처가 남는다
    for q in ("ALTER TABLE archive_files ADD COLUMN IF NOT EXISTS removed_at TEXT NOT NULL DEFAULT ''",   # PostgreSQL
              "ALTER TABLE archive_files ADD COLUMN removed_at TEXT NOT NULL DEFAULT ''"):                 # SQLite
        try:
            with db._conn() as conn:
                conn.execute(q)
            _ensured.add(db.path)
            break
        except Exception:
            continue


_CLASS = ("program", "program_name", "status", "kind", "year", "round", "dup_of", "ext", "area")


def sync(db, rows: list[dict], origins: dict[str, int] | None = None) -> dict:
    """DGX 의 지금 목록과 장부를 견줘 차이만 반영한다(억지로 덮어쓰지 않는다 — 사용자 2026-10-02 "동기화가 되게끔").

    새 파일 → 넣음, 크기·수정 시각이 바뀐 파일 → 갱신(이어진 문서는 다시 처리 대상), 이름·크기·시각이 같은데 경로만 다른 파일 →
    옮김(문서 번호 유지), 목록에서 사라진 파일 → removed_at 표시(문서는 지우지 않음), 다시 나타난 파일 → 표시 해제.
    분류만 바뀐 행도 고쳐 쓴다. 돌려주는 것: 각 갈래의 목록."""
    ensure(db)
    origins = origins or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with db._conn() as conn:
        cur = {r[0]: {"size": r[1], "mtime": r[2], "doc_id": r[3], "removed_at": r[4] or "",
                      **dict(zip(_CLASS, r[5:]))}
               for r in conn.execute("SELECT rel, size, mtime, doc_id, removed_at, " + ", ".join(_CLASS) + " FROM archive_files").fetchall()}
    inc = {r["rel"]: r for r in rows}
    # 플랫폼 업로드 원본(UPLOAD_PREFIX)은 DGX 목록이 아니라 VM 이 넣는다 — DGX 목록에 없다고 없어짐으로 보지 않는다
    present = {k for k, v in cur.items() if not v["removed_at"] and not k.startswith(UPLOAD_PREFIX)}
    added = [k for k in inc if k not in cur or cur[k]["removed_at"]]
    gone = [k for k in present if k not in inc]
    # 옮김 — 사라진 것과 새로 생긴 것의 (이름, 크기, 시각)이 하나씩 맞으면 같은 파일
    def sig(rel, size, mtime):
        return (rel.rsplit("/", 1)[-1], int(size or 0), int(mtime or 0))
    gone_sig: dict[tuple, list] = {}
    for k in gone:
        gone_sig.setdefault(sig(k, cur[k]["size"], cur[k]["mtime"]), []).append(k)
    added_sig: dict[tuple, list] = {}
    for k in added:
        added_sig.setdefault(sig(k, inc[k].get("size"), inc[k].get("mtime")), []).append(k)
    moved = []
    for k in list(added):
        key = sig(k, inc[k].get("size"), inc[k].get("mtime"))
        cands = gone_sig.get(key, [])
        if len(cands) == 1 and len(added_sig[key]) == 1 and k not in cur:
            old = cands.pop()
            moved.append((old, k, cur[old]["doc_id"]))
            added.remove(k)
            gone.remove(old)
    changed = [k for k in inc if k in cur and not cur[k]["removed_at"]
               and (int(cur[k]["size"] or 0) != int(inc[k].get("size") or 0) or int(cur[k]["mtime"] or 0) != int(inc[k].get("mtime") or 0))]
    reclass = [k for k in inc if k in cur and k not in changed and not cur[k]["removed_at"]
               and any((cur[k].get(f) or "") != (inc[k].get(f) or "") for f in _CLASS)]

    def vals(r):
        out = [r.get(k) if r.get(k) is not None else ("" if k not in ("size", "mtime", "year", "round") else None) for k in FIELDS]
        out[1], out[2] = int(r.get("size") or 0), int(r.get("mtime") or 0)
        return out
    with db._conn() as conn:
        for old, new, _d in moved:
            conn.execute("UPDATE archive_files SET rel = ?, seen_at = ? WHERE rel = ?", (new, now, old))
        for k in [m[1] for m in moved] + changed + reclass:
            r = inc[k]
            conn.execute("UPDATE archive_files SET size=?, mtime=?, ext=?, area=?, program=?, program_name=?, status=?, kind=?, year=?,"
                         " round=?, dup_of=?, removed_at='', seen_at=? WHERE rel=?", (*vals(r)[1:], now, k))
        for k in added:
            r = inc[k]
            conn.execute("DELETE FROM archive_files WHERE rel = ?", (k,))
            conn.execute("INSERT INTO archive_files (rel, size, mtime, ext, area, program, program_name, status, kind, year, round, dup_of,"
                         " doc_id, seen_at, removed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')",
                         (*vals(r), origins.get(k) or (cur.get(k) or {}).get("doc_id"), now))
        for k in gone:
            conn.execute("UPDATE archive_files SET removed_at = ? WHERE rel = ?", (now, k))
        # 목록 등록 뒤 반입이 끝나도 파일의 크기·시각은 변하지 않는다.
        # 원본 경로 장부의 명시적 연결을 반영하되 누락된 연결은 지우지 않는다.
        for k, doc_id in origins.items():
            if k in inc and doc_id is not None:
                conn.execute("UPDATE archive_files SET doc_id = ? WHERE rel = ?", (doc_id, k))
    return {"at": now, "added": added, "changed": [(k, origins.get(k) or cur[k]["doc_id"]) for k in changed], "moved": moved,
            "removed": [(k, cur[k]["doc_id"]) for k in gone], "reclassified": len(reclass)}


def load(db, rows: list[dict], origins: dict[str, int] | None = None) -> dict:
    """목록을 들인다(전체 교체가 아니라 덮어쓰기 — 사라진 파일은 seen_at 이 오래된 채 남아 '없어짐'으로 보인다)."""
    ensure(db)
    origins = origins or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    n = 0
    with db._conn() as conn:
        for r in rows:
            vals = [r.get(k) if r.get(k) is not None else ("" if k not in ("size", "mtime", "year", "round") else None) for k in FIELDS]
            vals[1] = int(r.get("size") or 0)
            vals[2] = int(r.get("mtime") or 0)
            doc_id = origins.get(r["rel"])
            conn.execute(
                "INSERT INTO archive_files (rel, size, mtime, ext, area, program, program_name, status, kind, year, round, dup_of, doc_id, seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(rel) DO UPDATE SET size = excluded.size, mtime = excluded.mtime, ext = excluded.ext, area = excluded.area,"
                " program = excluded.program, program_name = excluded.program_name, status = excluded.status, kind = excluded.kind,"
                " year = excluded.year, round = excluded.round, dup_of = excluded.dup_of,"
                " doc_id = COALESCE(excluded.doc_id, archive_files.doc_id), seen_at = excluded.seen_at",
                (*vals, doc_id, now))
            n += 1
    return {"rows": n, "at": now}


def summary(db) -> list[dict]:
    """사업 × 갈래별 파일 수와 문서함에 들인 수."""
    ensure(db)
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT program, program_name, kind, COUNT(*), SUM(CASE WHEN doc_id IS NOT NULL THEN 1 ELSE 0 END), SUM(size)"
            " FROM archive_files WHERE removed_at = '' GROUP BY program, program_name, kind ORDER BY COUNT(*) DESC").fetchall()
    return [{"program": r[0], "program_name": r[1], "kind": r[2], "files": int(r[3]), "in_store": int(r[4] or 0),
             "bytes": int(r[5] or 0)} for r in rows]


def find(db, program: str = "", kind: str = "", text: str = "", limit: int = 50) -> list[dict]:
    """원본 찾기 — 사업·갈래·경로 글로."""
    ensure(db)
    q = "SELECT rel, size, ext, program_name, kind, year, round, doc_id FROM archive_files WHERE removed_at = ''"
    args: list = []
    if program:
        q += " AND program = ?"
        args.append(program)
    if kind:
        q += " AND kind = ?"
        args.append(kind)
    if text:
        q += " AND rel LIKE ?"
        args.append(f"%{text}%")
    q += " ORDER BY rel LIMIT ?"
    args.append(limit)
    with db._conn() as conn:
        rows = conn.execute(q, args).fetchall()
    keys = ("rel", "size", "ext", "program_name", "kind", "year", "round", "doc_id")
    return [dict(zip(keys, r)) for r in rows]


def parse_jsonl(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
