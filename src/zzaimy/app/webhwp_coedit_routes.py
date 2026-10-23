"""칸 단위 공동 편집 경로(ADR-0070) — 규칙·표는 app/webhwp_coedit, 편집 화면 쪽은 static/webhwp-collab.js.

- GET  /hwp/{id}/coedit      처음·다시 이어질 때 전체 상태(칸 글·판·임대·같이 보는 사람·설정·이름)
- GET  /hwp/{id}/events      알림 흐름(SSE) — field-updated · lock-changed · lock-wanted · presence · doc-saved · committed · conflict
- POST /hwp/{id}/ops         칸 변경 묶음 {client, ops: [{seq, field, base, text, rich?}]} → op 마다 결과
- POST /hwp/{id}/lease       임대 {field, client, action: acquire|touch|grace|release}
- POST /hwp/{id}/leave       창을 닫을 때(sendBeacon) — 같이 보는 사람에서 빼고 이 창의 임대를 유예로
- GET  /hwp/{id}/conflicts   내 충돌 사본(관리자는 모두)
권한은 편집 화면과 같다(교직원·관리자, 열 수 있는 부서의 파일). 잠긴 공통 양식은 관리자만 고친다(읽기는 된다).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from zzaimy.app import webhwp
from zzaimy.app import webhwp_coedit as ce
from zzaimy.app.webhwp_routes import _file, _need_staff

router = APIRouter()
POLL = 1.0                 # 다른 프로세스가 쓴 알림을 읽는 주기(같은 프로세스는 바로 깨운다)
BEAT = 15.0                # 연결 유지 주석 간격


def _user(request: Request) -> str:
    return getattr(request.state, "user", "") or ""


def _readable(request: Request, fid: int) -> dict:
    _need_staff(request)
    return _file(request, fid)


def _editable(request: Request, fid: int) -> dict:
    f = _readable(request, fid)
    if webhwp.locked(request.app.state.db, fid) and getattr(request.state, "role", "") != "dev":
        raise HTTPException(403, "공통 양식은 관리자만 고칠 수 있습니다 — 「사본 만들기」로 사본을 만들어 쓰세요")
    return f


def _name(db, user: str, cache: dict) -> str:
    if user not in cache:
        cache[user] = webhwp.display_name(db, user)
    return cache[user]


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}


@router.get("/hwp/{fid}/coedit")
def hwp_coedit(request: Request, fid: int):
    _readable(request, fid)
    db = request.app.state.db
    snap = ce.snapshot(db, fid)
    names: dict = {}
    for x in [*snap["people"], *snap["locks"].values()]:
        _name(db, x["user"], names)
    for x in snap["fields"].values():
        _name(db, x["by"], names)
    from zzaimy.app import webhwp_agent
    snap.update(me=_user(request), names=names, aura=webhwp_agent.activity(db, fid),
                readonly=webhwp.locked(db, fid) and getattr(request.state, "role", "") != "dev")
    return snap


@router.get("/hwp/{fid}/events")
async def hwp_events(request: Request, fid: int, last: int = 0):
    _readable(request, fid)
    db = request.app.state.db
    try:
        last_id = int(request.headers.get("last-event-id") or last or 0)
    except ValueError:
        last_id = 0
    if not last_id:
        last_id = await run_in_threadpool(ce.last_event_id, db, fid)
    loop = asyncio.get_running_loop()
    wake = asyncio.Event()
    ce.HUB.add(fid, loop, wake)
    names: dict = {}

    async def stream():
        nonlocal last_id
        seen: list[int] = []
        quiet = 0.0
        try:
            yield "retry: 1500\n\n"
            while True:
                if await request.is_disconnected():
                    break
                wake.clear()
                evs = await run_in_threadpool(ce.events_since, db, fid, last_id, 20)
                for e in evs:
                    if e["id"] in seen or e["id"] <= last_id - 20:
                        continue
                    seen.append(e["id"])
                    last_id = max(last_id, e["id"])
                    d = e["data"]
                    for k in ("by", "user", "holder"):
                        if d.get(k):
                            d[k + "_name"] = _name(db, d[k], names)
                    yield f"id: {e['id']}\nevent: {e['kind']}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n"
                    quiet = 0.0
                del seen[:-400]
                try:
                    await asyncio.wait_for(wake.wait(), timeout=POLL)
                except asyncio.TimeoutError:
                    quiet += POLL
                    if quiet >= BEAT:
                        quiet = 0.0
                        yield ": beat\n\n"
        finally:
            ce.HUB.remove(fid, loop, wake)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@router.post("/hwp/{fid}/ops")
async def hwp_ops(request: Request, fid: int):
    _editable(request, fid)
    body = await _json(request)
    ops = body.get("ops") if isinstance(body.get("ops"), list) else []
    client = str(body.get("client") or "")[:80]
    if not client or len(ops) > 200:
        raise HTTPException(400, "client 와 ops(200개까지)가 필요합니다")
    st = request.app.state
    res = await run_in_threadpool(ce.apply_ops, st.db, fid, _user(request), client, ops, base=Path(st.data_dir))
    names: dict = {}
    for r in res:
        if r.get("by"):
            r["by_name"] = _name(st.db, r["by"], names)
    return {"results": res}


@router.post("/hwp/{fid}/lease")
async def hwp_lease(request: Request, fid: int):
    _editable(request, fid)
    body = await _json(request)
    field = str(body.get("field") or "")[:200]
    action = str(body.get("action") or "acquire")
    client = str(body.get("client") or "")[:80]
    st, user = request.app.state, _user(request)
    if action == "touch":
        return {"ok": True, "until": await run_in_threadpool(ce.touch, st.db, fid, user, body.get("fields") if isinstance(body.get("fields"), list) else None)}
    if not field:
        raise HTTPException(400, "field 가 필요합니다(구조 잠금은 /lock)")
    if action == "acquire":
        r = await run_in_threadpool(ce.acquire, st.db, fid, field, user, client)
        if not r["ok"]:
            r["by_name"] = _name(st.db, r["by"], {})
        return r
    if action in ("grace", "release"):
        ok = await run_in_threadpool(lambda: ce.release(st.db, fid, field, user, grace=action == "grace", base=Path(st.data_dir)))
        return {"ok": ok}
    raise HTTPException(400, "action 은 acquire·touch·grace·release 가운데 하나입니다")


@router.post("/hwp/{fid}/leave")
async def hwp_leave(request: Request, fid: int):
    _readable(request, fid)
    body = await _json(request)
    client = str(body.get("client") or request.query_params.get("client") or "")[:80]
    st, user = request.app.state, _user(request)

    def run():
        if client:
            ce.leave(st.db, fid, client)
        for f, x in ce.locks(st.db, fid).items():
            if x["user"] == user and f != ce.STRUCT and (not client or x["client"] in ("", client)):
                ce.release(st.db, fid, f, user, grace=True)
    await run_in_threadpool(run)
    return {"ok": True}


@router.get("/hwp/{fid}/conflicts")
def hwp_conflicts(request: Request, fid: int):
    _readable(request, fid)
    me = None if getattr(request.state, "role", "") == "dev" else _user(request)
    return {"conflicts": ce.conflicts(request.app.state.db, fid, me)}
