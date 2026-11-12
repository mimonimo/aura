"""대화 요청 대기열(11-12) — 답을 쓰는 동안 보낸 글 요청을 버리지 않고 차례로 처리한다.

긴 서식 작성(특성화 사업계획서 86단계)은 한 단계 답이 끝날 때마다 사람이 다음 요청을 넣어야 했다 — 답을 쓰는 중에는 보내기 단추가 막히고
서버도 그 사이 온 요청을 버렸다. 이제 그 사이 온 요청은 대화별 대기열(settings 「chat_queue:{대화}」)에 들어가고, 지금 답이 끝나면
다음 것이 같은 길(chat_routes.begin_request)로 시작한다. 담당자는 요청을 여러 개 넣어 두고 자리를 비울 수 있다.

넣기·꺼내기는 LOCK 안에서 「지금 답을 쓰는 중인가」 확인과 함께 한다 — 끝나는 순간에 온 요청이 대기열에 갇히거나 두 답이 겹치지 않게.
"""
from __future__ import annotations

import json
import threading
import time

LIMIT = 100                      # 한 대화에 쌓아 둘 수 있는 요청 수
PREVIEW = 5                      # 상태 응답에 보일 앞 몇 건
LOCK = threading.RLock()


def _key(session_id: int) -> str:
    return f"chat_queue:{int(session_id)}"


def items(db, session_id: int) -> list[dict]:
    try:
        v = json.loads(db.get_setting(_key(session_id), "") or "[]")
    except ValueError:
        return []
    return [x for x in v if isinstance(x, dict) and str(x.get("q") or "").strip()] if isinstance(v, list) else []


def _save(db, session_id: int, rows: list[dict]) -> None:
    if rows:
        db.set_setting(_key(session_id), json.dumps(rows, ensure_ascii=False))
    else:
        db.delete_setting(_key(session_id))


def add(db, session_id: int, item: dict) -> int:
    """대기열 끝에 넣는다 — 넣은 뒤 건수. 상한이면 ValueError."""
    with LOCK:
        rows = items(db, session_id)
        if len(rows) >= LIMIT:
            raise ValueError(f"대기 중인 요청이 {LIMIT}건이라 더 넣지 못했습니다")
        rows.append({**item, "at": item.get("at") or time.time()})
        _save(db, session_id, rows)
        return len(rows)


def pop(db, session_id: int) -> dict | None:
    """맨 앞 요청을 꺼낸다(없으면 None)."""
    with LOCK:
        rows = items(db, session_id)
        if not rows:
            return None
        first, rest = rows[0], rows[1:]
        _save(db, session_id, rest)
        return first


def drop(db, session_id: int, index: int) -> bool:
    """index 번째(0부터) 요청을 뺀다."""
    with LOCK:
        rows = items(db, session_id)
        if not 0 <= index < len(rows):
            return False
        del rows[index]
        _save(db, session_id, rows)
        return True


def clear(db, session_id: int) -> int:
    with LOCK:
        n = len(items(db, session_id))
        _save(db, session_id, [])
        return n


def preview(db, session_id: int) -> dict:
    """상태 응답용 — {queued: 건수, next: [{i, text(첫 줄 앞 60자)}…]}."""
    rows = items(db, session_id)
    return {"queued": len(rows),
            "next": [{"i": i, "text": (str(x.get("q") or "").strip().splitlines() or [""])[0][:60]} for i, x in enumerate(rows[:PREVIEW])]}


def waiting_sessions(db) -> list[int]:
    """대기열이 남은 대화 — 서버를 다시 켠 뒤 이어서 시작할 때."""
    out = []
    for k in db.settings_with_prefix("chat_queue:"):
        try:
            sid = int(k.split(":", 1)[1])
        except (IndexError, ValueError):
            continue
        if items(db, sid):
            out.append(sid)
    return out
