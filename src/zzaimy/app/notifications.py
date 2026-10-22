"""알림 — 문서 알림(확인 여부를 계정마다 따로 기록)과 업무 알림(사업 관리 신청 같은 문서 밖 일, notify)."""
import hashlib
import json
import threading
import time

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


# ---------------------------------------------------------------- 업무 알림(문서 밖) — 종 위쪽 「업무 알림」 묶음

NOTICE_KEEP = 50                       # 사람마다 남기는 알림 수


def _notice_key(user: str) -> str:
    return "notices:" + user


def _notices(db, user: str) -> list[dict]:
    try:
        got = json.loads(db.get_setting(_notice_key(user), "[]") or "[]")
    except (ValueError, TypeError):
        got = []
    return [n for n in got if isinstance(n, dict)] if isinstance(got, list) else []


def notify(db, users, text: str, href: str = "") -> None:
    """받는 사람마다 알림 한 줄을 쌓는다(최근 NOTICE_KEEP 개). href 는 누르면 갈 앱 안 주소(/ 로 시작)만 남긴다.
    실패해도 부른 쪽 작업을 막지 않는다."""
    if db is None:
        return
    href = href if isinstance(href, str) and href.startswith("/") and not href.startswith("//") else ""
    text = str(text or "").strip()[:200]
    if not text:
        return
    try:
        with _lock:
            for u in dict.fromkeys(u for u in (users or []) if u):
                lst = _notices(db, u)
                nid = max((int(n.get("id") or 0) for n in lst), default=0) + 1
                lst.append({"id": nid, "at": time.strftime("%Y-%m-%d %H:%M"), "text": text, "href": href, "seen": False})
                db.set_setting(_notice_key(u), json.dumps(lst[-NOTICE_KEEP:], ensure_ascii=False))
    except Exception:
        pass


def unseen_notices(db, user: str) -> list[dict]:
    return [n for n in _notices(db, user) if not n.get("seen")]


@router.get("/api/notifications/notices")
def notices_list(request: Request):
    """내 업무 알림 가운데 아직 안 본 것(최근 것부터 10개)과 전체 수."""
    user = getattr(request.state, "user", "") or ""
    items = unseen_notices(request.app.state.db, user) if user else []
    return {"count": len(items), "items": [{k: n.get(k) for k in ("id", "at", "text", "href")} for n in reversed(items[-10:])]}


@router.post("/api/notifications/notices/read")
async def notices_read(request: Request):
    """알림 확인 — id 를 주면 그것만, 없으면 모두."""
    user = getattr(request.state, "user", "") or ""
    if not user:
        raise HTTPException(403)
    db = request.app.state.db
    form = await request.form()
    ids = {str(x) for x in form.getlist("id")}
    with _lock:
        lst = _notices(db, user)
        for n in lst:
            if not ids or str(n.get("id")) in ids:
                n["seen"] = True
        db.set_setting(_notice_key(user), json.dumps(lst, ensure_ascii=False))
    return {"ok": True}
