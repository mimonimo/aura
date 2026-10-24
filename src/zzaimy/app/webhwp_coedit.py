"""웹 한글 기안기 칸 단위 공동 편집(ADR-0070) — 칸(누름틀 필드)마다 편집 임대, 칸 글의 판 번호, 보낸 변경의 중복 막기, 충돌 사본, 알림 줄.

여러 사람이 한 문서를 열면 각자 커서가 든 칸의 편집 임대(lease)를 잡는다. 임대를 쥔 사람만 그 칸을 고치고(편집 화면은 양식 보호 모드로
남의 칸을 잠근다), 고친 글은 칸 글 전체를 판 번호와 함께 서버로 보낸다. 서버가 정답이다 — 받은 글은 이 모듈의 표에 먼저 남기고
(그 순간이 「저장됨」), 몇 초 뒤 한글 파일(hwpx)의 필드에 써 넣는다(webhwp_write.set_fields → webhwp.save, 앞 판은 .판/ 에).

- 임대: 활동(입력·커서·스크롤)이 있으면 저절로 늘린다. 다른 칸으로 옮기면 바로 풀지 않고 유예 뒤 풀린다. 다른 사람이 기다리면 짧게.
  모든 시간 값은 설정(conf) — 설정 표의 coedit:<키>, 없으면 환경 변수 ZZAIMY_COEDIT_<키>, 없으면 기본값.
- 구조 잠금(필드 이름 STRUCT): 필드 밖 본문·행 추가·페이지 설정처럼 칸이 아닌 고침은 문서 전체 잠금(예전 편집 잠금과 같은 뜻)을 쥔 사람만,
  그 사람이 문서 전체를 저장한다. 저장할 때 이 표의 칸 글을 다시 덮어 써(overlay) 다른 사람의 칸 글이 사라지지 않게 한다.
- 변경(op): (client, seq) 로 한 번만 반영한다(다시 보내도 같은 답). base(보낸 쪽이 마지막으로 본 판)가 지금 판과 다르고 그 사이 판을
  다른 창이 썼으면 반영하지 않고 충돌 사본으로 남긴다 — 조용히 버리지 않는다. 임대를 다른 사람이 쥐고 있어도 충돌 사본.
- 알림: 바뀐 칸·임대·같이 보는 사람은 알림 줄(webhwp_events)에 쌓고, 편집 화면은 SSE(/hwp/{id}/events)로 바로 받는다.
  같은 프로세스 안에서는 쓰는 즉시 깨우고, 다른 프로세스가 쓴 것은 짧은 주기로 표를 읽어 받는다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import zlib
from pathlib import Path

log = logging.getLogger("zzaimy.app.webhwp_coedit")

STRUCT = ""                       # 구조 잠금(문서 전체)의 필드 이름 자리
AGENT = "AURA"
TEXT_MAX = 1_000_000              # 한 칸 글 상한(webhwp_fields.TEXT_MAX 와 같다) — 넘으면 자르지 않고 받지 않는다(too_long)
DOC_TEXT_MAX = 8_000_000          # 한 문서 칸 글 합계 상한(webhwp_fields.DOC_TEXT_MAX) — 넘으면 too_big
RICH_MAX = 2_000_000
FIELDS_MAX = 2_000                # 한 문서에 새로 만들 수 있는 칸 글 줄 상한(양식의 필드 수보다 넉넉히)
CONFLICTS_KEEP = 200              # 사람·문서마다 남기는 충돌 사본 수(오래된 것부터 지운다)
SNAP_EVERY = 50                   # 고친 기록은 바뀐 부분으로 남기고 이 판마다 한 번 칸 글 전체(스냅숏)를 남긴다

_SCHEMA = """
CREATE TABLE IF NOT EXISTS webhwp_field_locks (fid INTEGER NOT NULL, field TEXT NOT NULL, user_id TEXT NOT NULL,
  client TEXT NOT NULL DEFAULT '', lease_until DOUBLE PRECISION NOT NULL DEFAULT 0, touched DOUBLE PRECISION NOT NULL DEFAULT 0,
  since DOUBLE PRECISION NOT NULL DEFAULT 0, wanted_by TEXT NOT NULL DEFAULT '', PRIMARY KEY (fid, field));
CREATE TABLE IF NOT EXISTS webhwp_field_state (fid INTEGER NOT NULL, field TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
  text TEXT NOT NULL DEFAULT '', rich TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '', client TEXT NOT NULL DEFAULT '',
  at DOUBLE PRECISION NOT NULL DEFAULT 0, committed INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (fid, field));
CREATE TABLE IF NOT EXISTS webhwp_field_ops (fid INTEGER NOT NULL, client TEXT NOT NULL, seq BIGINT NOT NULL,
  result TEXT NOT NULL DEFAULT '', at DOUBLE PRECISION NOT NULL DEFAULT 0, PRIMARY KEY (fid, client, seq));
CREATE TABLE IF NOT EXISTS webhwp_field_edits (id INTEGER PRIMARY KEY AUTOINCREMENT, fid INTEGER NOT NULL, field TEXT NOT NULL,
  version INTEGER NOT NULL, text TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '', at DOUBLE PRECISION NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS webhwp_conflicts (id INTEGER PRIMARY KEY AUTOINCREMENT, fid INTEGER NOT NULL, field TEXT NOT NULL,
  user_id TEXT NOT NULL DEFAULT '', base_version INTEGER NOT NULL DEFAULT 0, server_version INTEGER NOT NULL DEFAULT 0,
  text TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', at DOUBLE PRECISION NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS webhwp_events (id INTEGER PRIMARY KEY AUTOINCREMENT, fid INTEGER NOT NULL, kind TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '', at DOUBLE PRECISION NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS webhwp_events_fid ON webhwp_events (fid, id);
CREATE TABLE IF NOT EXISTS webhwp_presence (fid INTEGER NOT NULL, client TEXT NOT NULL, user_id TEXT NOT NULL,
  field TEXT NOT NULL DEFAULT '', at DOUBLE PRECISION NOT NULL DEFAULT 0, PRIMARY KEY (fid, client))
"""

# 설정 기본값(초, ms 는 이름에) — 하드코딩하지 않고 conf() 로 읽는다
DEFAULTS = {
    "lease": 300.0,            # 입력 없이 임대가 유지되는 시간
    "lease_contended": 120.0,  # 다른 사람이 그 칸을 기다릴 때의 입력 없음 시간
    "grace": 30.0,             # 다른 칸으로 옮긴 뒤 임대를 놓기까지의 유예
    "warn": 60.0,              # 임대가 풀리기 전 미리 알리는 시간
    "commit_delay": 3.0,       # 마지막 변경 뒤 한글 파일에 써 넣기까지
    "commit_max": 20.0,        # 변경이 이어져도 이 시간 안에는 한 번 써 넣는다
    "hash_check": 30.0,        # 편집 화면이 칸 글 지문을 서버와 맞춰 보는 주기
    "throttle_ms": 300.0,      # 입력 중 칸 글을 보내는 간격
    "pause_ms": 300.0,         # 받은 칸 글을 넣기 전에 기다리는 입력 멈춤
    "presence_ttl": 30.0,      # 같이 보는 사람 표시가 남는 시간
    "rich": 0.0,
    "aura_ttl": 20.0,          # AURA 작성 중 표시가 마지막 쓰기 뒤 남는 시간               # 1 이면 절 필드를 서식째(기안기 JSON 블록) 주고받는 시험 기능(M3)
}
_ENSURED: set[str] = set()
_FILE_LOCKS: dict[int, threading.Lock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def conf(db, key: str) -> float:
    raw = None
    try:
        raw = db.get_setting(f"coedit:{key}", None)
    except Exception:
        raw = None
    if raw in (None, ""):
        raw = os.environ.get(f"ZZAIMY_COEDIT_{key.upper()}")
    try:
        return float(raw) if raw not in (None, "") else DEFAULTS[key]
    except (TypeError, ValueError):
        return DEFAULTS[key]


def client_conf(db) -> dict:
    """편집 화면에 넘기는 설정(초·ms)과 한 칸 글 상한(field_max)."""
    out = {k: conf(db, k) for k in DEFAULTS}
    out["field_max"] = TEXT_MAX
    return out


_ENSURE_LOCK = threading.Lock()


def ensure(db) -> None:
    if db.path in _ENSURED:
        return
    with _ENSURE_LOCK:
        if db.path in _ENSURED:
            return
        with db._conn() as conn:
            conn.executescript(_SCHEMA)
        # 고친 기록에 바뀐 부분 칸(delta) — 앞서 만든 표에는 칸을 더한다(다른 프로세스가 먼저 더했으면 그대로)
        for table, col, ddl in (("webhwp_field_edits", "delta", "TEXT NOT NULL DEFAULT ''"),
                                ("webhwp_presence", "page", "INTEGER NOT NULL DEFAULT 0")):   # 같이 있는 사람의 쪽 번호
            try:
                with db._conn() as conn:
                    conn.execute(f"SELECT {col} FROM {table} LIMIT 0")
            except Exception:
                try:
                    with db._conn() as conn:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                except Exception:
                    log.info("%s.%s 칸 — 이미 있음", table, col)
        _ENSURED.add(db.path)


def _pg(conn) -> bool:
    return getattr(conn, "dialect", "") == "postgres"


def _txlock(conn, fid: int) -> None:
    """이 문서의 공동 편집 표를 고치는 동안 다른 쓰기를 막는다(같은 문서끼리만 줄 세운다)."""
    if _pg(conn):
        conn.execute("SELECT pg_advisory_xact_lock(CAST(? AS INTEGER), CAST(? AS INTEGER))", (26102401, int(fid)))
    else:
        conn.execute("BEGIN IMMEDIATE")


def ckey(user: str, client: str) -> str:
    """보낸 창의 표 안 열쇠 — 창 번호(client)는 편집 화면이 지어 보내는 값이라 로그인한 사람과 묶는다.
    그래야 남의 창 번호를 흉내 내어 그 사람의 (client, seq) 를 먼저 차지하거나 판 번호 검사를 건너뛸 수 없다."""
    return f"{user}\x1f{client}"


def craw(key: str) -> str:
    return (key or "").split("\x1f", 1)[-1]


def norm(text) -> str:
    """칸 글 비교용 — 줄바꿈을 \\n 하나로(기안기는 \\r\\n 으로 돌려준다), XML 이 받지 않는 글자는 뺀다. 자르지 않는다(상한은 apply_ops 가 거절로)."""
    from zzaimy.app import webhwp_fields as wf
    s = "" if text is None else str(text)
    return wf.clean_text(s.replace("\r\n", "\n").replace("\r", "\n"), None)


def fingerprint(text: str) -> str:
    """칸 글 지문 — 편집 화면(webhwp-collab.js 의 fp)과 같은 셈: UTF-8 바이트의 CRC32 를 16진수로."""
    return format(zlib.crc32(norm(text).encode("utf-8")) & 0xFFFFFFFF, "08x")


# ── 알림 줄 ──
class _Hub:
    """같은 프로세스 안의 SSE 연결을 깨운다 — 스레드(동기 경로)에서도 부를 수 있게 루프에 넘겨 깨운다."""

    def __init__(self):
        self.lock = threading.Lock()
        self.waiters: dict[int, set] = {}

    def count(self, fid: int, user: str) -> int:
        with self.lock:
            return sum(1 for w in self.waiters.get(int(fid), ()) if w[2] == user)

    def add(self, fid: int, loop, event, user: str = "") -> None:
        with self.lock:
            self.waiters.setdefault(int(fid), set()).add((loop, event, user))

    def remove(self, fid: int, loop, event, user: str = "") -> None:
        with self.lock:
            s = self.waiters.get(int(fid))
            if s:
                s.discard((loop, event, user))

    def wake(self, fid: int) -> None:
        with self.lock:
            targets = list(self.waiters.get(int(fid), ()))
        for loop, ev, _ in targets:
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:
                pass


HUB = _Hub()
_LAST_PRUNE = {"at": 0.0}


def emit(db, fid: int, kind: str, data: dict, conn=None) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    if conn is not None:
        conn.execute("INSERT INTO webhwp_events (fid, kind, data, at) VALUES (?, ?, ?, ?)", (int(fid), kind, payload, time.time()))
    else:
        ensure(db)
        with db._conn() as c:
            c.execute("INSERT INTO webhwp_events (fid, kind, data, at) VALUES (?, ?, ?, ?)", (int(fid), kind, payload, time.time()))
    if conn is not None:                              # 묶음 안 — 깨우기·정리는 묶음을 확정한 쪽이
        return
    HUB.wake(fid)
    now = time.time()
    if now - _LAST_PRUNE["at"] > 300:
        _LAST_PRUNE["at"] = now
        try:
            with db._conn() as c:
                c.execute("DELETE FROM webhwp_events WHERE at < ?", (now - 1800,))
                c.execute("DELETE FROM webhwp_field_ops WHERE at < ?", (now - 7 * 86400,))
        except Exception:
            log.exception("알림 줄 정리 실패")


def events_since(db, fid: int, last_id: int, lookback: int = 0, *, full: bool = True) -> list[dict]:
    """last_id 뒤 알림(PostgreSQL 은 번호 순서와 확정 순서가 다를 수 있어 lookback 만큼 앞에서부터 읽고 부르는 쪽이 본 번호를 거른다).
    full 이면 바뀐 부분만 실린 칸 알림에 그 칸의 지금 글(text)을 채운다 — 예전 칸 공유 경로(live_since)는 글 전체를 기대한다. 알림 흐름(SSE)은 full=False."""
    ensure(db)
    with db._conn() as conn:
        rows = conn.execute("SELECT id, kind, data FROM webhwp_events WHERE fid = ? AND id > ? ORDER BY id LIMIT 500",
                            (int(fid), max(0, int(last_id) - lookback))).fetchall()
    out = []
    for r in rows:
        try:
            out.append({"id": int(r[0]), "kind": r[1], "data": json.loads(r[2] or "{}")})
        except ValueError:
            continue
    if full:
        need = {e["data"].get("field") for e in out if e["kind"] == "field-updated" and "text" not in e["data"]}
        if need:
            cur = state(db, fid, [f for f in need if f], with_text=True)
            for e in out:
                d = e["data"]
                if e["kind"] == "field-updated" and "text" not in d and d.get("field") in cur:
                    d["text"] = cur[d["field"]]["text"]
    return out


def last_event_id(db, fid: int) -> int:
    ensure(db)
    with db._conn() as conn:
        r = conn.execute("SELECT MAX(id) FROM webhwp_events WHERE fid = ?", (int(fid),)).fetchone()
    return int(r[0] or 0) if r else 0


# ── 임대 ──
def _lock_row(conn, fid: int, field: str):
    return conn.execute("SELECT user_id, client, lease_until, touched, since, wanted_by FROM webhwp_field_locks WHERE fid = ? AND field = ?",
                        (int(fid), field)).fetchone()


def _alive(row, now: float) -> bool:
    return bool(row) and float(row[2] or 0) > now


def locks(db, fid: int) -> dict:
    """살아 있는 임대 — {필드: {user, until, since, wanted}}(구조 잠금은 필드 이름 STRUCT)."""
    ensure(db)
    now = time.time()
    with db._conn() as conn:
        rows = conn.execute("SELECT field, user_id, lease_until, since, wanted_by, client FROM webhwp_field_locks WHERE fid = ? AND lease_until > ?",
                            (int(fid), now)).fetchall()
    return {r[0]: {"user": r[1], "until": float(r[2]), "since": float(r[3] or 0), "wanted": r[4] or "", "client": r[5] or ""} for r in rows}


def holder(db, fid: int, field: str) -> str:
    return (locks(db, fid).get(field) or {}).get("user", "")


def _lease_for(db, wanted: str) -> float:
    return conf(db, "lease_contended") if wanted else conf(db, "lease")


def acquire(db, fid: int, field: str, user: str, client: str = "", *, ttl: float | None = None) -> dict:
    """임대 잡기·늘리기 — 다른 사람이 살아 있는 임대를 쥐고 있으면 {"ok": False, "by"}(그 사람에게 「기다리는 사람」 을 알린다)."""
    ensure(db)
    now = time.time()
    with db._conn() as conn:
        _txlock(conn, fid)
        row = _lock_row(conn, fid, field)
        if _alive(row, now) and row[0] != user:
            if row[5] != user:                         # 기다리는 사람이 생기면 쥔 사람의 입력 없음 시간을 짧게
                until = min(float(row[2]), float(row[3] or now) + conf(db, "lease_contended")) if ttl is None else float(row[2])
                conn.execute("UPDATE webhwp_field_locks SET wanted_by = ?, lease_until = ? WHERE fid = ? AND field = ?",
                             (user, until, int(fid), field))
                emit(db, fid, "lock-wanted", {"field": field, "by": user, "holder": row[0]}, conn=conn)
            out = {"ok": False, "by": row[0], "client": row[1] or "", "until": float(row[2]), "since": float(row[4] or 0)}
        else:
            mine = _alive(row, now) and row[0] == user
            since = float(row[4]) if mine else now
            wanted = (row[5] if mine else "") or ""
            until = now + (ttl if ttl is not None else _lease_for(db, wanted))
            if row:
                conn.execute("UPDATE webhwp_field_locks SET user_id = ?, client = ?, lease_until = ?, touched = ?, since = ?, wanted_by = ?"
                             " WHERE fid = ? AND field = ?", (user, client, until, now, since, wanted, int(fid), field))
            else:
                conn.execute("INSERT INTO webhwp_field_locks (fid, field, user_id, client, lease_until, touched, since, wanted_by)"
                             " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (int(fid), field, user, client, until, now, since, ""))
            if not mine or (row[1] or "") != client:
                emit(db, fid, "lock-changed", {"field": field, "user": user, "client": client, "until": until}, conn=conn)
            if field != STRUCT and client:
                # 한 창에 한 칸 — 이 창이 쥐고 있던 다른 칸은 놓는다(칸을 옮겨 다니며 임대가 쌓이지 않게)
                for (other,) in conn.execute("SELECT field FROM webhwp_field_locks WHERE fid = ? AND user_id = ? AND client = ? AND field <> ? AND field <> ?",
                                             (int(fid), user, client, field, STRUCT)).fetchall():
                    conn.execute("DELETE FROM webhwp_field_locks WHERE fid = ? AND field = ?", (int(fid), other))
                    emit(db, fid, "lock-changed", {"field": other, "user": "", "released_by": user, "client": client}, conn=conn)
            out = {"ok": True, "by": user, "until": until, "since": since}
    HUB.wake(fid)
    return out


def touch(db, fid: int, user: str, fields: list[str] | None = None, client: str | None = None) -> dict:
    """활동이 있었다 — 이 사람의 임대(고른 필드만·client 를 주면 그 창 것만, 없으면 전부)를 늘린다. {필드: until}."""
    ensure(db)
    now = time.time()
    out = {}
    with db._conn() as conn:
        _txlock(conn, fid)
        rows = conn.execute("SELECT field, wanted_by, client FROM webhwp_field_locks WHERE fid = ? AND user_id = ? AND lease_until > ?",
                            (int(fid), user, now)).fetchall()
        for f, wanted, cl in rows:
            if client is not None and (cl or "") != client:
                continue
            if f == STRUCT or (fields is not None and f not in fields):
                continue
            until = now + _lease_for(db, wanted)
            conn.execute("UPDATE webhwp_field_locks SET lease_until = ?, touched = ? WHERE fid = ? AND field = ?", (until, now, int(fid), f))
            out[f] = until
    return out


def release(db, fid: int, field: str, user: str, *, grace: bool = False, base: Path | None = None) -> bool:
    """임대 놓기 — 순서: (편집 화면이 보낼 것을 다 보내 답을 받은 뒤) 한글 파일에 써 넣고 → 알리고 → 지운다.
    grace=True 면 바로 지우지 않고 유예 시간 뒤 풀리게만 한다(다른 칸으로 옮겼을 때 — 돌아오면 그대로)."""
    ensure(db)
    now = time.time()
    if grace:
        with db._conn() as conn:
            _txlock(conn, fid)
            row = _lock_row(conn, fid, field)
            if not (_alive(row, now) and row[0] == user):
                return False
            until = min(float(row[2]), now + conf(db, "grace"))
            conn.execute("UPDATE webhwp_field_locks SET lease_until = ? WHERE fid = ? AND field = ?", (until, int(fid), field))
            emit(db, fid, "lock-changed", {"field": field, "user": user, "client": row[1] or "", "until": until, "grace": True}, conn=conn)
        HUB.wake(fid)
        return True
    if base is not None and field != STRUCT:
        # 놓을 때 써 넣기는 기다리지 않는다 — 451쪽 문서에서 0.5~1.3초 걸려 「칸 옮기기」 요청을 붙잡았다. 파일을 읽는 쪽(열기·내려받기·AURA)은
        # fresh 가 먼저 써 넣으므로 순서는 지켜진다
        schedule_commit(db, base, fid, wait=0.0)
    with db._conn() as conn:
        _txlock(conn, fid)
        row = _lock_row(conn, fid, field)
        if not (row and row[0] == user):
            return False
        conn.execute("DELETE FROM webhwp_field_locks WHERE fid = ? AND field = ?", (int(fid), field))
        emit(db, fid, "lock-changed", {"field": field, "user": "", "released_by": user, "client": row[1] or "", "wanted": row[5] or ""}, conn=conn)
    HUB.wake(fid)
    return True


# ── 칸 글 ──
def state(db, fid: int, fields: list[str] | None = None, *, with_text: bool = True) -> dict:
    """{필드: {version, text, by, at, fp}} — fields 를 주면 그것만."""
    ensure(db)
    with db._conn() as conn:
        rows = conn.execute("SELECT field, version, text, author, at, rich FROM webhwp_field_state WHERE fid = ?", (int(fid),)).fetchall()
    out = {}
    for r in rows:
        if fields is not None and r[0] not in fields:
            continue
        x = {"version": int(r[1]), "by": r[3], "at": float(r[4] or 0), "fp": fingerprint(r[2])}
        if with_text:
            x["text"] = r[2]
            if r[5]:
                x["rich"] = r[5]
        out[r[0]] = x
    return out


def _result(conn, fid: int, client: str, seq: int, res: dict) -> dict:
    conn.execute("INSERT INTO webhwp_field_ops (fid, client, seq, result, at) VALUES (?, ?, ?, ?, ?)",
                 (int(fid), client, int(seq), json.dumps(res, ensure_ascii=False), time.time()))
    return res


def _rich_kind(rich: str) -> str:
    """서식째 거리의 꼴 — 'hwpx'(서버 참여자의 조각) 또는 'json'(기안기 JSON 블록, 시험 기능)."""
    if rich.lstrip().startswith("{"):
        try:
            j = json.loads(rich)
            if isinstance(j, dict) and j.get("fmt") == "hwpx":
                return "hwpx"
        except ValueError:
            pass
    return "json"


def apply_ops(db, fid: int, user: str, client: str, ops: list[dict], *, base: Path | None = None, force: bool = False,
              trusted: bool = False) -> list[dict]:
    """칸 변경 묶음을 차례로 반영한다. 돌려주는 것은 op 마다 {seq, field, ok, version, [conflict, reason, text, by]}.
    force=True 는 판 번호 검사를 건너뛴다(에이전트가 지금 판 위에 쓸 때) — 남이 쥔 임대는 그래도 지킨다."""
    ensure(db)
    client = str(client or "")[:80]
    ck = ckey(user, client)
    results = []
    changed = False
    for op in ops or []:
        try:
            seq = int(op.get("seq"))
        except (TypeError, ValueError):
            results.append({"ok": False, "reason": "bad"})
            continue
        field = str(op.get("field") or "")[:200]
        if not field:
            results.append({"seq": seq, "ok": False, "reason": "bad"})
            continue
        # 바뀐 부분(delta) {at, del, ins, fp} — 자리·길이는 글자(코드 포인트) 단위. 서버가 지금 글에 넣어 보고 지문이 맞을 때만 받는다
        is_delta = isinstance(op.get("ins"), str) and "text" not in op
        if is_delta:
            try:
                d_at, d_del = int(op.get("at")), int(op.get("del") or 0)
            except (TypeError, ValueError):
                results.append({"seq": seq, "field": field, "ok": False, "reason": "bad"})
                continue
            d_ins, d_fp, text = norm(op.get("ins")), str(op.get("fp") or ""), ""
        else:
            text = norm(op.get("text"))
        if len(text) > TEXT_MAX:                       # 자르면 칸 끝 글이 말없이 사라진다 — 받지 않고 알린다
            results.append({"seq": seq, "field": field, "ok": False, "reason": "too_long", "max": TEXT_MAX})
            continue
        # 서식째 거리 — hwpx 조각은 서버 참여자(trusted, put_rich 가 검사한 것)만. 사람 화면이 보낸 hwpx 조각은 받지 않는다(남의 화면에
        # 아무 조각이나 넣게 된다). 기안기 JSON 블록은 시험 기능이 켜져 있을 때·그 칸 임대를 쥔 창에서만(아래 임대 검사)
        raw_rich = op.get("rich") if isinstance(op.get("rich"), str) else ""
        if len(raw_rich) > RICH_MAX:
            results.append({"seq": seq, "field": field, "ok": False, "reason": "too_long", "max": RICH_MAX})
            continue
        rich_kind = _rich_kind(raw_rich) if raw_rich else ""
        if rich_kind == "hwpx" and not trusted:
            results.append({"seq": seq, "field": field, "ok": False, "reason": "rich_denied"})
            continue
        rich = raw_rich if (rich_kind == "hwpx" or (rich_kind == "json" and conf(db, "rich") > 0)) else ""
        try:
            b = int(op.get("base") or 0)
        except (TypeError, ValueError):
            b = 0
        now = time.time()
        event = None
        with db._conn() as conn:
            _txlock(conn, fid)
            seen = conn.execute("SELECT result FROM webhwp_field_ops WHERE fid = ? AND client = ? AND seq = ?", (int(fid), ck, seq)).fetchone()
            if seen:
                results.append(json.loads(seen[0]))
                continue
            lk = _lock_row(conn, fid, field)
            st = conn.execute("SELECT version, text, client, author, rich FROM webhwp_field_state WHERE fid = ? AND field = ?", (int(fid), field)).fetchone()
            cur_v, cur_text, cur_client = (int(st[0]), st[1], st[2]) if st else (0, None, "")
            reason = ""
            if rich and rich_kind == "json" and not (_alive(lk, now) and lk[0] == user and (lk[1] or "") == client):
                rich = ""                                  # JSON 블록은 그 칸을 쥔 창이 보낸 것만 — 아니면 글만 받는다
            if _alive(lk, now) and lk[0] != user:
                reason = "locked"
            elif not force and b != cur_v and cur_client != ck:
                reason = "version"
            elif st is None and conn.execute("SELECT COUNT(*) FROM webhwp_field_state WHERE fid = ?", (int(fid),)).fetchone()[0] >= FIELDS_MAX:
                results.append({"seq": seq, "field": field, "ok": False, "reason": "too_many"})
                continue
            if is_delta:
                # 바뀐 부분은 보낸 쪽이 본 판(base)이 지금 판일 때만 — 아니면(남이 쥠·판 어긋남·지문 다름) 「delta」 로 돌려보내
                # 편집 화면이 칸 글 전체로 다시 보낸다(그러면 위 규칙대로 충돌 사본까지). 이 답은 기록하지 않는다(같은 seq 로 다시 와도 다시 본다)
                # 아직 표에 없는 칸은 파일의 지금 값이 바탕(편집 화면도 파일에서 연 글에 대해 바뀐 부분을 셌다)
                cur = cur_text if st is not None else (_file_values(db, fid).get(field, "") if b == 0 else "")
                ok_d = not reason and b == cur_v and 0 <= d_at <= len(cur) and 0 <= d_del <= len(cur) - d_at
                if ok_d:
                    text = cur[:d_at] + d_ins + cur[d_at + d_del:]
                    ok_d = not d_fp or fingerprint(text) == d_fp
                if not ok_d:
                    results.append({"seq": seq, "field": field, "ok": False, "reason": "delta", "version": cur_v})
                    continue
                if len(text) > TEXT_MAX:
                    results.append({"seq": seq, "field": field, "ok": False, "reason": "too_long", "max": TEXT_MAX})
                    continue
            if not reason and (st is None or cur_text != text):
                other = conn.execute("SELECT COALESCE(SUM(LENGTH(text)), 0) FROM webhwp_field_state WHERE fid = ? AND field <> ?",
                                     (int(fid), field)).fetchone()[0]
                if int(other or 0) + len(text) > DOC_TEXT_MAX:
                    results.append({"seq": seq, "field": field, "ok": False, "reason": "too_big", "max": DOC_TEXT_MAX})
                    continue
            if reason:
                sql = ("INSERT INTO webhwp_conflicts (fid, field, user_id, base_version, server_version, text, reason, at)"
                       " VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
                args = (int(fid), field, user, b, cur_v, text, reason, now)
                cid = (conn.execute(sql + " RETURNING id", args).fetchone()[0] if _pg(conn) else conn.execute(sql, args).lastrowid)
                conn.execute("DELETE FROM webhwp_conflicts WHERE fid = ? AND user_id = ? AND id NOT IN (SELECT id FROM webhwp_conflicts"
                             " WHERE fid = ? AND user_id = ? ORDER BY id DESC LIMIT ?)", (int(fid), user, int(fid), user, CONFLICTS_KEEP))
                res = {"seq": seq, "field": field, "ok": False, "conflict": cid or 0, "reason": reason, "version": cur_v,
                       "text": cur_text or "", "by": (lk[0] if reason == "locked" else (st[3] if st else ""))}
                results.append(_result(conn, fid, ck, seq, res))
                event = ("conflict", {"field": field, "user": user, "reason": reason})
            elif st is not None and cur_text == text and (st[4] or "") == rich:
                results.append(_result(conn, fid, ck, seq, {"seq": seq, "field": field, "ok": True, "version": cur_v}))
            else:
                v = cur_v + 1
                if st is None:
                    conn.execute("INSERT INTO webhwp_field_state (fid, field, version, text, rich, author, client, at, committed)"
                                 " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)", (int(fid), field, v, text, rich, user, ck, now))
                else:
                    conn.execute("UPDATE webhwp_field_state SET version = ?, text = ?, rich = ?, author = ?, client = ?, at = ?"
                                 " WHERE fid = ? AND field = ?", (v, text, rich, user, ck, now, int(fid), field))
                # 고친 기록 — 바뀐 부분만, SNAP_EVERY 판마다·처음·전체 글로 온 고침은 칸 글 전체(history 가 스냅숏 + 바뀐 부분으로 되살린다)
                snap = not is_delta or st is None or v % SNAP_EVERY == 0
                delta_json = "" if snap else json.dumps({"base": cur_v, "at": d_at, "del": d_del, "ins": d_ins}, ensure_ascii=False)
                conn.execute("INSERT INTO webhwp_field_edits (fid, field, version, text, author, at, delta) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (int(fid), field, v, text if snap else "", user, now, delta_json))
                # 고치는 것도 활동 — 이 창이 쥔 임대면 늘린다. 임대가 없으면 만들지 않는다(임대는 편집 화면이 처음 고칠 때 /lease 로 잡는다 —
                # 변경만으로 임대를 만들면 칸을 옮겨 다닐 때 남은 변경이 옛 칸 임대를 되살렸다)
                until = 0.0
                if _alive(lk, now) and lk[0] == user and (lk[1] or "") == client:
                    until = now + _lease_for(db, lk[5] or "")
                    conn.execute("UPDATE webhwp_field_locks SET lease_until = ?, touched = ? WHERE fid = ? AND field = ?", (until, now, int(fid), field))
                # 알림도 바뀐 부분만(받는 화면은 판이 이어지면 그 자리에 넣고, 끊기면 칸 글을 다시 받는다). 전체 글로 온 고침은 전체 글
                if is_delta:
                    data = {"field": field, "version": v, "base": cur_v, "at": d_at, "del": d_del, "ins": d_ins, "fp": fingerprint(text),
                            "by": user, "client": client}
                else:
                    data = {"field": field, "version": v, "text": text, "fp": fingerprint(text), "by": user, "client": client}
                if rich:
                    data["rich"] = rich
                    data["trusted"] = rich_kind == "hwpx"          # 화면은 hwpx 조각을 서버가 검사한 것만 넣는다
                emit(db, fid, "field-updated", data, conn=conn)
                res = {"seq": seq, "field": field, "ok": True, "version": v}
                if until:
                    res["until"] = until
                results.append(_result(conn, fid, ck, seq, res))
                changed = True
        if event:
            emit(db, fid, *event)
        HUB.wake(fid)
    if changed and base is not None:
        schedule_commit(db, base, fid)
    return results


def put(db, fid: int, user: str, values: dict, *, base: Path | None = None, client: str = "") -> tuple[list[str], dict]:
    """에이전트·서버 쪽 쓰기 — 사람과 같은 길(임대·판 번호·알림)로. 남이 임대를 쥔 칸은 쓰지 않는다.
    돌려주는 것은 (쓴 필드, {못 쓴 필드: 쥔 사람})."""
    client = client or f"{user}:server"
    seq0 = int(time.time() * 1000)
    # 바뀐 부분으로 보낸다(받는 화면이 칸 전체를 다시 그리지 않게) — 사이에 판이 바뀌어 안 받으면 그 칸만 전체 글로
    ops = [delta_op(db, fid, k, v, seq0 + i) for i, (k, v) in enumerate(values.items())]
    res = apply_ops(db, fid, user, client, ops, force=True)
    retry = [i for i, r in enumerate(res) if r.get("reason") == "delta"]
    if retry:
        full = apply_ops(db, fid, user, client, [{"seq": seq0 + len(ops) + j, "field": ops[i]["field"], "text": values[ops[i]["field"]]}
                                                  for j, i in enumerate(retry)], force=True)
        for i, r in zip(retry, full):
            res[i] = r
    done = [r["field"] for r in res if r.get("ok")]
    held = {r["field"]: r.get("by", "") for r in res if not r.get("ok") and r.get("field")}
    for f in done:                                     # 에이전트는 쓰고 바로 놓는다(사람이 이어서 잡을 수 있게)
        release(db, fid, f, user)
    if base is not None and done:
        commit_now(db, base, fid)
    return done, held


def _diff(a: str, b: str) -> tuple[int, int, str]:
    """앞뒤 같은 부분을 뺀 가운데 — (자리, 지울 글자 수, 넣을 글)."""
    n, i = min(len(a), len(b)), 0
    while i < n and a[i] == b[i]:
        i += 1
    j = 0
    while j < n - i and a[len(a) - 1 - j] == b[len(b) - 1 - j]:
        j += 1
    return i, len(a) - i - j, b[i:len(b) - j]


def delta_op(db, fid: int, field: str, text: str, seq: int) -> dict:
    """서버 쪽 참여자(AURA 등)의 쓰기를 바뀐 부분 변경으로 — 지금 판(없으면 파일 값)과 견줘서. 바뀐 부분이 글 전체만 하면 전체 글."""
    text = norm(text)
    st = state(db, fid, [field], with_text=True).get(field)
    cur, v = (st["text"], st["version"]) if st else (_file_values(db, fid).get(field), 0)
    if cur is None:
        return {"seq": seq, "field": field, "text": text}
    at, dl, ins = _diff(cur, text)
    if dl + len(ins) >= len(text) and len(text) > 0:
        return {"seq": seq, "field": field, "base": v, "text": text}
    return {"seq": seq, "field": field, "base": v, "at": at, "del": dl, "ins": ins, "fp": fingerprint(text)}


class SectionStream:
    """AURA 가 절 하나를 쓰는 동안 조금씩 흘려 보내기 — 그 절만의 AURA 창 번호로 임대를 잡아(여러 절을 따로 동시에 쓸 수 있다)
    write(지금까지 쓴 글) 마다 바뀐 부분만 보내고, close() 에 놓는다. 사람이 쥔 칸이면 열지 않는다(ok=False, held=쥔 사람)."""

    def __init__(self, db, fid: int, field: str, *, user: str = AGENT, base: Path | None = None, ttl: float = 120.0):
        self.db, self.fid, self.field, self.user, self.base = db, int(fid), field, user, base
        self.client = f"aura:{abs(hash(field)) % 10**8}"
        r = acquire(db, fid, field, user, self.client, ttl=ttl)
        self.ok, self.held = bool(r.get("ok")), ("" if r.get("ok") else r.get("by", ""))
        self.seq, self.sent = int(time.time() * 1000), None

    def write(self, text: str) -> bool:
        if not self.ok:
            return False
        self.seq += 1
        r = apply_ops(self.db, self.fid, self.user, self.client, [delta_op(self.db, self.fid, self.field, text, self.seq)], force=True)[0]
        if r.get("reason") == "delta":
            self.seq += 1
            r = apply_ops(self.db, self.fid, self.user, self.client, [{"seq": self.seq, "field": self.field, "text": text}], force=True)[0]
        if r.get("ok"):
            self.sent = text
        return bool(r.get("ok"))

    def close(self) -> None:
        if self.ok:
            release(self.db, self.fid, self.field, self.user, base=self.base)
            self.ok = False


def put_sections(db, fid: int, values: dict, *, user: str = AGENT, base: Path | None = None, pace: float = 0.0) -> tuple[list[str], dict]:
    """여러 절을 나란히 흘려 쓰기 — 절마다 따로 임대(AURA 창 번호), 문단 하나씩 돌아가며 보낸다(받는 화면은 작은 변경을 조금씩 넣는다).
    돌려주는 것은 (쓴 절, {못 쓴 절: 쥔 사람})."""
    streams = {k: SectionStream(db, fid, k, user=user, base=None) for k in values}
    held = {k: s.held for k, s in streams.items() if not s.ok}
    parts = {k: norm(v).split("\n") for k, v in values.items() if streams[k].ok}
    done: list[str] = []
    try:
        for step in range(max((len(p) for p in parts.values()), default=0)):
            for k, ps in parts.items():
                if step < len(ps):
                    streams[k].write("\n".join(ps[:step + 1]))
            if pace:
                time.sleep(pace)
        done = [k for k in parts if streams[k].sent == norm(values[k])]
    finally:
        for s_ in streams.values():
            s_.close()
    if base is not None and done:
        schedule_commit(db, base, fid, wait=0.0)
    return done, held


_FILE_VALUES: dict[int, tuple] = {}


def _file_values(db, fid: int) -> dict:
    """한글 파일의 필드 값(공동 편집 비교 꼴) — 파일 수정 시각·크기가 같으면 다시 풀지 않는다."""
    from zzaimy.app import webhwp, webhwp_write as ww
    f = webhwp.get(db, fid)
    p = Path(f["path"]) if f else None
    if not p or not p.exists():
        return {}
    st = p.stat()
    key = (st.st_mtime_ns, st.st_size)
    hit = _FILE_VALUES.get(int(fid))
    if hit and hit[0] == key:
        return hit[1]
    try:
        vals = {k: norm(v) for k, v in ww.field_values(p.read_bytes()).items()}
    except Exception:
        vals = {}
    _FILE_VALUES[int(fid)] = (key, vals)
    return vals


def history(db, fid: int, field: str, upto: int | None = None) -> list[dict]:
    """한 칸의 판마다 글 — 고친 기록(스냅숏 + 바뀐 부분)으로 되살린다. [{version, text, author, at}] (오래된 것부터)."""
    ensure(db)
    with db._conn() as conn:
        rows = conn.execute("SELECT version, text, delta, author, at FROM webhwp_field_edits WHERE fid = ? AND field = ? ORDER BY id",
                            (int(fid), field)).fetchall()
    out, cur = [], None
    for v, text, delta, author, at in rows:
        if upto is not None and int(v) > upto:
            break
        if delta:
            d = json.loads(delta)
            if cur is None:
                continue                                   # 앞 스냅숏이 지워졌다 — 다음 스냅숏부터
            cur = cur[:d["at"]] + d["ins"] + cur[d["at"] + d["del"]:]
        else:
            cur = text
        out.append({"version": int(v), "text": cur, "author": author, "at": float(at or 0)})
    return out


def put_rich(db, fid: int, field: str, text: str, hwpx: bytes, *, user: str = AGENT, client: str = "", base: Path | None = None) -> dict:
    """서버 참여자(AURA·논문 작성기)가 칸 하나를 서식째 — hwpx 조각(표·꾸밈 스타일·캡션)과 그 글. 열린 편집 화면은 조각을 그 칸에 넣는다
    (SetTextFile insertfile). 파일 써 넣기는 아직 글만이다(조각의 문단 모양·표를 파일 머리(header)에 합치는 일은 남음 — ADR-0070)."""
    import base64
    hwpx = strip_fragment_fields(hwpx)                 # 조각 안 누름틀(쓰는 쪽 표시용 「절:조각」 등)은 떼어 낸다 — 칸 안에 칸이 생기지 않게
    why = fragment_problem(hwpx)
    if why:
        return {"ok": False, "reason": "rich_bad", "why": why}
    rich = json.dumps({"fmt": "hwpx", "b64": base64.b64encode(hwpx).decode()})
    if len(rich) > RICH_MAX:
        return {"ok": False, "reason": "too_long", "max": RICH_MAX}
    client = client or f"{user}:server"
    r = apply_ops(db, fid, user, client, [{"seq": int(time.time() * 1000), "field": field, "text": text, "rich": rich}], base=base, force=True,
                  trusted=True)[0]
    return r


_FRAG_ENTRY = re.compile(r"^(mimetype|version\.xml|settings\.xml|META-INF/[^/]+|Contents/(header|content|section\d+)\.(xml|hpf)|Preview/[^/]+|"
                         r"BinData/[^/]+\.(png|jpe?g|gif|bmp))$", re.I)
_FRAG_BAD = re.compile(r"<hp:fieldBegin\b|<hp:fieldEnd\b|<hp:ole\b|<hp:video\b|<hp:(btn|checkBtn|radioBtn|comboBox|listBox|edit|scrollBar)\b|"
                       r"HYPERLINK|\b(href|src)=\"(?!#)[^\"]+\"", re.I)


def strip_fragment_fields(data: bytes) -> bytes:
    """조각의 누름틀(CLICK_HERE) 여닫는 ctrl 만 떼어 낸다(글은 그대로). 열 수 없으면 그대로 돌려줘 fragment_problem 이 거절한다."""
    import io
    import zipfile
    try:
        zin = zipfile.ZipFile(io.BytesIO(data))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zout:
            for i in zin.infolist():
                b = zin.read(i.filename)
                if re.match(r"Contents/section\d+\.xml$", i.filename):
                    x = b.decode("utf-8")
                    x = re.sub(r'<hp:ctrl><hp:fieldBegin\b[^>]*type="CLICK_HERE"[^>]*>.*?</hp:fieldBegin></hp:ctrl>', "", x, flags=re.S)
                    x = re.sub(r'<hp:ctrl><hp:fieldBegin\b[^>]*type="CLICK_HERE"[^>]*/></hp:ctrl>', "", x)
                    x = re.sub(r"<hp:ctrl><hp:fieldEnd\b[^>]*/></hp:ctrl>", "", x)
                    b = x.encode("utf-8")
                zout.writestr(i, b, compress_type=zipfile.ZIP_STORED if i.filename == "mimetype" else zipfile.ZIP_DEFLATED)
        return buf.getvalue()
    except Exception:
        return data


def fragment_problem(data: bytes) -> str:
    """서식째 조각(hwpx)을 남의 화면에 넣기 전에 — 들인 것이 글·문단·표·그림만인지. 문제가 있으면 까닭, 없으면 빈 글.
    필드(조각 안 누름틀이 대상 칸 밖 이름과 겹치거나 칸을 벗어남)·OLE·동영상·양식 개체·하이퍼링크·외부 주소·스크립트 같은 다른 파일은 받지 않는다."""
    import io
    import zipfile
    from zzaimy.app import webhwp_fields as wf
    if not data or data[:2] != b"PK" or len(data) > RICH_MAX * 3 // 4:
        return "hwpx 조각이 아니거나 너무 큽니다"
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        wf.check_zip(z)
    except Exception:
        return "hwpx 조각을 열 수 없습니다"
    for n in z.namelist():
        if not _FRAG_ENTRY.match(n):
            return f"조각에 넣을 수 없는 부분: {n[:80]}"
        if re.match(r"Contents/section\d+\.xml$", n) and _FRAG_BAD.search(z.read(n).decode("utf-8", "replace")):
            return "조각에 필드·개체·링크·외부 주소가 있습니다"
    return ""


def conflicts(db, fid: int, user: str | None = None, limit: int = 50) -> list[dict]:
    ensure(db)
    q = "SELECT id, field, user_id, base_version, server_version, text, reason, at FROM webhwp_conflicts WHERE fid = ?"
    args: list = [int(fid)]
    if user:
        q += " AND user_id = ?"
        args.append(user)
    with db._conn() as conn:
        rows = conn.execute(q + " ORDER BY id DESC LIMIT ?", (*args, int(limit))).fetchall()
    return [{"id": r[0], "field": r[1], "user": r[2], "base": r[3], "server": r[4], "text": r[5], "reason": r[6], "at": r[7]} for r in rows]


# ── 같이 보는 사람 ──
def presence(db, fid: int, user: str, client: str, field: str, page: int = 0) -> None:
    """같이 보는 사람 — 커서 칸과 쪽 번호(편집 화면이 기안기 KeyIndicator 로 안다). 칸·쪽이 바뀌면 알린다."""
    ensure(db)
    now = time.time()
    field = (field or "")[:200]
    page = max(0, min(int(page or 0), 100_000))
    ck = ckey(user, client)
    changed = False
    with db._conn() as conn:
        row = conn.execute("SELECT field, at, page FROM webhwp_presence WHERE fid = ? AND client = ?", (int(fid), ck)).fetchone()
        if row:
            changed = row[0] != field or int(row[2] or 0) != page or now - float(row[1] or 0) > conf(db, "presence_ttl")
            conn.execute("UPDATE webhwp_presence SET field = ?, at = ?, page = ? WHERE fid = ? AND client = ?", (field, now, page, int(fid), ck))
        else:
            changed = True
            conn.execute("INSERT INTO webhwp_presence (fid, client, user_id, field, at, page) VALUES (?, ?, ?, ?, ?, ?)",
                         (int(fid), ck, user, field, now, page))
    if changed:
        emit(db, fid, "presence", {"user": user, "client": client, "field": field, "page": page})


def leave(db, fid: int, client: str, user: str) -> None:
    """이 사람의 그 창만 뺀다(남의 창 번호로는 남을 뺄 수 없다)."""
    ensure(db)
    with db._conn() as conn:
        n = conn.execute("DELETE FROM webhwp_presence WHERE fid = ? AND client = ?", (int(fid), ckey(user, client))).rowcount
    if n:
        emit(db, fid, "presence", {"user": user, "client": client, "left": True})


def people(db, fid: int) -> list[dict]:
    """지금 이 문서를 연 창 — 사람마다 하나(가장 최근 창). [{user, field, at}]."""
    ensure(db)
    now = time.time()
    with db._conn() as conn:
        rows = conn.execute("SELECT user_id, field, at, client, page FROM webhwp_presence WHERE fid = ? AND at > ? ORDER BY at",
                            (int(fid), now - conf(db, "presence_ttl"))).fetchall()
    by: dict[str, dict] = {}
    for r in rows:
        by[r[0]] = {"user": r[0], "field": r[1], "at": float(r[2]), "client": craw(r[3]), "page": int(r[4] or 0)}
    return list(by.values())


def windows(db, fid: int) -> list[dict]:
    """지금 살아 있는 창 모두(같은 사람의 여러 창 포함) — [{user, client}]. 같은 사람의 옛 창이 쥔 칸을 새 창이 이어받을지 가를 때."""
    ensure(db)
    with db._conn() as conn:
        rows = conn.execute("SELECT user_id, client FROM webhwp_presence WHERE fid = ? AND at > ?",
                            (int(fid), time.time() - conf(db, "presence_ttl"))).fetchall()
    return [{"user": r[0], "client": craw(r[1])} for r in rows]


# ── 한글 파일에 써 넣기 ──
def file_lock(fid: int) -> threading.Lock:
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(int(fid), threading.Lock())


def overlay(db, fid: int, data: bytes, *, have: dict | None = None, only_dirty: bool = False) -> tuple[bytes, list[str], dict]:
    """한글 파일 바이트에 표의 칸 글을 덮어 쓴다 — (새 바이트, 파일과 달라 바꾼 필드, {필드: 써 넣은 판}).
    have 는 그 파일의 필드 값(이미 안다면 — 써 넣기는 파일 지문으로 캐시한 값), only_dirty 면 아직 안 써 넣은 칸만 본다."""
    from zzaimy.app import webhwp_write as ww
    ensure(db)
    q = "SELECT field, version, text FROM webhwp_field_state WHERE fid = ?" + (" AND version > committed" if only_dirty else "")
    with db._conn() as conn:
        st = {r[0]: {"version": int(r[1]), "text": r[2]} for r in conn.execute(q, (int(fid),)).fetchall()}
    if not st or data[:2] != b"PK":
        return data, [], {}
    if have is None:
        try:
            have = ww.field_values(data)
        except Exception:
            return data, [], {}
    want = {k: v["text"] for k, v in st.items() if k in have}
    diff = {k: v for k, v in want.items() if norm(have.get(k, "")) != norm(v)}
    versions = {k: st[k]["version"] for k in want}
    if not diff:
        return data, [], versions
    new, done = ww.set_fields(data, diff)
    return new, done, versions


def _mark_committed(db, fid: int, versions: dict) -> None:
    with db._conn() as conn:
        for k, v in versions.items():
            conn.execute("UPDATE webhwp_field_state SET committed = ? WHERE fid = ? AND field = ? AND committed < ?", (int(v), int(fid), k, int(v)))


def dirty(db, fid: int) -> list[str]:
    ensure(db)
    with db._conn() as conn:
        return [r[0] for r in conn.execute("SELECT field FROM webhwp_field_state WHERE fid = ? AND version > committed", (int(fid),)).fetchall()]


def commit_now(db, base: Path, fid: int) -> list[str]:
    """아직 파일에 안 들어간 칸 글을 한글 파일에 써 넣는다(앞 판은 webhwp.save 가 .판/ 에). 써 넣은 필드 목록."""
    from zzaimy.app import webhwp
    if not dirty(db, fid):
        return []
    with file_lock(fid):
        f = webhwp.get(db, fid)
        if not f or not Path(f["path"]).exists():
            return []
        data = Path(f["path"]).read_bytes()
        have = _file_values(db, fid)                    # 파일 지문(수정 시각·크기)이 같으면 다시 풀지 않는다
        new, done, versions = overlay(db, fid, data, have=have or None, only_dirty=True)
        if done:
            with db._conn() as conn:
                r = conn.execute("SELECT author FROM webhwp_field_state WHERE fid = ? ORDER BY at DESC LIMIT 1", (int(fid),)).fetchone()
            p = webhwp.save(db, base, fid, new, user=(r[0] if r else "") or "공동 작성", quiet=True)
            try:                                         # 써 넣은 값으로 캐시를 이어 둔다(다음 써 넣기가 파일을 다시 풀지 않게)
                vals = dict(have or {})
                with db._conn() as conn:
                    for k, t in conn.execute("SELECT field, text FROM webhwp_field_state WHERE fid = ?", (int(fid),)).fetchall():
                        if k in done:
                            vals[k] = norm(t)
                st_ = Path(p).stat()
                _FILE_VALUES[int(fid)] = ((st_.st_mtime_ns, st_.st_size), vals)
                if _NORMALIZED.get(int(fid)) is not None:
                    _NORMALIZED[int(fid)] = (st_.st_mtime_ns, st_.st_size)   # 써 넣기는 문단으로 쓰므로 줄 나눔 정리를 다시 할 것이 없다
            except Exception:
                _FILE_VALUES.pop(int(fid), None)
        _mark_committed(db, fid, versions)
    if done:
        emit(db, fid, "committed", {"fields": done})
    return done


def fresh(db, base: Path, fid: int) -> None:
    """파일을 읽어 내보내기 전에 — 아직 안 들어간 칸 글을 먼저 써 넣고, 필드 안 줄 나눔을 문단 나눔으로 바꾼다(실패해도 읽기는 이어 간다)."""
    try:
        commit_now(db, base, fid)
    except Exception:
        log.exception("써 넣기 실패")
    try:
        normalize_file(db, base, fid)
    except Exception:
        log.exception("줄 나눔 정리 실패")


_NORMALIZED: dict[int, tuple] = {}


def _keep_version(p: Path) -> Path:
    """지금 파일을 앞 판으로 따로 남긴다(.판/<이름> YYYYMMDD-HHMMSS[-n].<확장자> — webhwp 의 판 목록과 같은 꼴)."""
    import shutil
    from datetime import datetime
    vdir = p.parent / ".판"
    vdir.mkdir(exist_ok=True)
    stamp, n = datetime.now().strftime("%Y%m%d-%H%M%S"), 1
    dest = vdir / f"{p.stem} {stamp}{p.suffix}"
    while dest.exists():
        n += 1
        dest = vdir / f"{p.stem} {stamp}-{n}{p.suffix}"
    shutil.copy2(p, dest)
    return dest


def normalize_file(db, base: Path, fid: int) -> int:
    """필드 안 줄 나눔(lineBreak)을 문단 나눔으로 한 번 바꾼다 — 기안기 GetFieldText 는 줄 나눔을 돌려주지 않아 칸 공동 편집이
    한 번 지나가면 그 칸의 줄이 모두 붙었다. 바꾸기 전 판은 .판/ 에 남기고, 열린 편집 화면에는 「문서 저장」 을 알려 새 판을 열게 한다.
    잠긴 공통 양식·hwp 는 손대지 않는다. 같은 파일(수정 시각·크기)은 다시 보지 않는다. 바꾼 줄 나눔 수."""
    from zzaimy.app import webhwp, webhwp_write as ww
    f = webhwp.get(db, fid)
    if not f:
        return 0
    p = Path(f["path"])
    if not p.exists() or p.suffix.lower() != ".hwpx" or webhwp.locked(db, fid):
        return 0
    st = p.stat()
    if _NORMALIZED.get(int(fid)) == (st.st_mtime_ns, st.st_size):
        return 0
    n = 0
    with file_lock(fid):
        data = p.read_bytes()
        try:
            new, n = ww.normalize_breaks(data)
        except Exception:
            log.exception("줄 나눔 정리 — 파일을 읽지 못함(그대로 둔다)")
            new, n = data, 0
        if n:
            _keep_version(p)
            p = webhwp.save(db, base, fid, new, user=AGENT, quiet=False)
        st = p.stat()
        _NORMALIZED[int(fid)] = (st.st_mtime_ns, st.st_size)
    return n


_TIMERS: dict[int, dict] = {}
_TIMERS_GUARD = threading.Lock()


def schedule_commit(db, base: Path, fid: int, wait: float | None = None) -> None:
    """변경 뒤 commit_delay 초 조용하면 써 넣는다. 변경이 이어져도 commit_max 초 안에는 한 번. wait 를 주면 그 뒤에(놓을 때 0)."""
    delay, most = (conf(db, "commit_delay") if wait is None else wait), conf(db, "commit_max")
    now = time.time()
    with _TIMERS_GUARD:
        cur = _TIMERS.get(int(fid))
        first = cur["first"] if cur else now
        if cur:
            cur["timer"].cancel()
        wait = max(0.0, min(delay, first + most - now))

        def run():
            with _TIMERS_GUARD:
                if _TIMERS.get(int(fid), {}).get("timer") is t:
                    _TIMERS.pop(int(fid), None)
            try:
                commit_now(db, base, fid)
            except Exception:
                log.exception("칸 글 써 넣기 실패 — 표에 남아 다음에 다시 써 넣는다")
        t = threading.Timer(wait, run)
        t.daemon = True
        _TIMERS[int(fid)] = {"timer": t, "first": first}
        t.start()


def flush_all(db, base: Path) -> None:
    """프로세스를 내리기 전 — 기다리는 써 넣기를 바로."""
    with _TIMERS_GUARD:
        items = list(_TIMERS.items())
        _TIMERS.clear()
    for fid, x in items:
        x["timer"].cancel()
        try:
            commit_now(db, base, fid)
        except Exception:
            log.exception("써 넣기 실패")


def snapshot(db, fid: int, me: str = "", *, light: bool = False) -> dict:
    """편집 화면이 처음 열 때·다시 이어질 때 받는 전체 상태. light 면 칸 글 없이 판·지문만(30초 지문 대조 — 451쪽에서 칸 글 전체가 758KB였다)."""
    return {"fields": state(db, fid, with_text=not light), "locks": locks(db, fid), "people": people(db, fid), "windows": windows(db, fid), "last": last_event_id(db, fid),
            "conf": client_conf(db), "now": time.time()}
