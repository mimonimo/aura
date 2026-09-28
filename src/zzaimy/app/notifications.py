"""Document notifications have independent, account-scoped acknowledgement."""
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
    return {"ok": True}
