"""문서 알림 — 확인 여부를 계정마다 따로 기록한다."""
import hashlib
import json
import threading

from fastapi import APIRouter, Request, HTTPException

router = APIRouter()
_lock = threading.Lock()


def version(doc):
    values = [doc.get(k) for k in ("id", "status", "decision", "updated_at", "ai_review", "error")]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def unread(db, owner, documents):
    try:
        seen = json.loads(db.get_setting("notification_seen:" + owner, "{}"))
    except (ValueError, TypeError):
        seen = {}
    return [d for d in documents if seen.get(str(d["id"])) != version(d)]


@router.post("/api/notifications/read")
async def acknowledge(request: Request):
    db = request.app.state.db
    owner = getattr(request.state, "user", "zzaimy")
    form = await request.form()
    ids = form.getlist("doc_id")
    if not ids or len(ids) > 100:
        raise HTTPException(400, "확인할 문서를 선택해 주세요")
    docs = []
    for ident in ids:
        try:
            doc = db.get_document(int(ident))
        except (TypeError, ValueError):
            raise HTTPException(400, "잘못된 문서 번호")
        if not doc or doc.get("owner") != owner:
            raise HTTPException(404)
        docs.append(doc)
    with _lock:
        key = "notification_seen:" + owner
        seen = json.loads(db.get_setting(key, "{}"))
        seen.update({str(d["id"]): version(d) for d in docs})
        db.set_setting(key, json.dumps(seen))
        # 드라이브에서 가져온 문서 알림 — 한 번 확인하면 처리 상태가 바뀌어도 다시 「가져옴」 으로 뜨지 않게 번호로 기억한다
        ikey = "gimport_seen:" + owner
        try:
            got = set(json.loads(db.get_setting(ikey, "[]") or "[]"))
        except (ValueError, TypeError):
            got = set()
        db.set_setting(ikey, json.dumps(sorted(got | {int(d["id"]) for d in docs})[-500:]))
    return {"ok": True}
