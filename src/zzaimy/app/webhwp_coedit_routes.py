"""칸 단위 공동 편집 경로(ADR-0070) — 규칙·표는 app/webhwp_coedit, 편집 화면 쪽은 static/webhwp-collab.js.

- GET  /hwp/{id}/coedit      처음·다시 이어질 때 전체 상태(칸 글·판·임대·같이 보는 사람·설정·이름), ?field=… 면 그 칸 글만
- 변경은 칸 글 전체 {seq, field, base, text} 또는 바뀐 부분 {seq, field, base, at, del, ins, fp}(서버가 맞춰 보고 안 맞으면 delta 로 돌려보냄)
- GET  /hwp/{id}/events      알림 흐름(SSE) — field-updated · lock-changed · lock-wanted · presence · doc-saved · committed · conflict
- POST /hwp/{id}/ops         칸 변경 묶음 {client, ops: [{seq, field, base, text, rich?}]} → op 마다 결과
- POST /hwp/{id}/lease       임대 {field, client, action: acquire|touch|grace|release}
- POST /hwp/{id}/leave       창을 닫을 때(sendBeacon) — 같이 보는 사람에서 빼고 이 창의 임대를 유예로
- GET  /hwp/{id}/conflicts   내 충돌 사본(관리자는 모두)
권한은 편집 화면과 같다(교직원·관리자, 열 수 있는 부서의 파일). 잠긴 공통 양식은 관리자만 고친다(읽기는 된다).
"""
from __future__ import annotations

import asyncio
import time
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from zzaimy.app import webhwp
from zzaimy.app import webhwp_coedit as ce
from zzaimy.app.webhwp_routes import _file, _need_staff

router = APIRouter()
MAX_BODY = 8_000_000       # 한 요청 본문 상한(한글 3바이트 × 칸 글 100만 자 두어 칸, 서식째 시험 포함) — 보통은 바뀐 부분만 간다
STREAMS_PER_USER = 8       # 한 사람이 한 문서에 여는 알림 흐름 수 상한(창·탭)
POLL = 1.0                 # 다른 프로세스가 쓴 알림을 읽는 주기(같은 프로세스는 바로 깨운다)
BEAT = 15.0                # 연결 유지 알림(beat) 간격 — 화면은 45초 동안 아무 알림이 없으면 새로 붙는다


def _user(request: Request) -> str:
    return getattr(request.state, "user", "") or ""


def _client(v) -> str:
    """창 번호 — webhwp_coedit.clean_client 한 곳에서 가른다(자르지 않고, 맞지 않으면 400)."""
    try:
        return ce.clean_client(v)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _field(v) -> str:
    try:
        return ce.clean_field(v)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _readable(request: Request, fid: int) -> dict:
    _need_staff(request)
    return _file(request, fid)


def _editable(request: Request, fid: int) -> dict:
    """칸 단위 공동 편집으로 고쳐도 되는 문서 — 잠긴 공통 양식은 누구도(관리자도) 칸 공동 편집으로 고치지 않는다.
    공통 양식은 모두의 한글 완성본이 원본 서식으로 쓰는 파일이라, 관리자가 고칠 때는 문서 전체 잠금(편집 화면의 필드 없는 문서와 같은 길)으로만."""
    f = _readable(request, fid)
    if webhwp.locked(request.app.state.db, fid):
        raise HTTPException(403, "공통 양식은 칸 공동 편집으로 고치지 않습니다 — 「사본 만들기」로 사본을 만들어 쓰세요")
    return f


def _name(db, user: str, cache: dict) -> str:
    if user not in cache:
        cache[user] = webhwp.display_name(db, user)
    return cache[user]


async def _json(request: Request) -> dict:
    try:
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            raise HTTPException(413, "보낸 내용이 너무 큽니다")
    except ValueError:
        pass
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "보낸 내용이 너무 큽니다")
    try:
        body = json.loads(raw or b"{}")
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}


@router.get("/hwp/{fid}/coedit")
def hwp_coedit(request: Request, fid: int, field: list[str] | None = Query(None), light: int = 0):
    _readable(request, fid)
    db = request.app.state.db
    if field:                                  # 고른 칸만(바뀐 부분 알림이 끊겨 칸 글 전체가 필요할 때)
        return {"fields": ce.state(db, fid, [_field(x) for x in field[:200]]), "now": time.time()}
    snap = ce.snapshot(db, fid, light=bool(light))
    names: dict = {}
    for x in [*snap["people"], *snap["locks"].values()]:
        _name(db, x["user"], names)
    for x in snap["fields"].values():
        _name(db, x["by"], names)
    from zzaimy.app import webhwp_agent
    aura = webhwp_agent.activity(db, fid)
    if aura.get("by") and aura["by"] != "AURA":
        _name(db, aura["by"], names)                      # 「AURA · 이름」 — 시킨 사람 이름도 함께
    snap.update(me=_user(request), names=names, aura=aura,
                readonly=webhwp.locked(db, fid) and getattr(request.state, "role", "") != "dev",
                fields_off=webhwp.locked(db, fid))      # 공통 양식 — 관리자도 칸 공동 편집 없이(문서 전체 잠금으로만)
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
    me = _user(request)
    if ce.HUB.count(fid, me) >= STREAMS_PER_USER:
        raise HTTPException(429, "이 문서를 연 창이 너무 많습니다 — 쓰지 않는 창을 닫아 주세요")
    names: dict = {}

    async def stream():
        nonlocal last_id
        seen: list[int] = []
        floor = last_id               # 화면이 이미 본 끝 — 앞을 다시 읽는 것(lookback)은 그 뒤에 늦게 확정된 것만 잡으려고. 본 것을 다시 보내면
        quiet = 0.0                   # 열자마자 제 「문서 저장」(줄 나눔 정리)을 받아 한 번 더 열었다
        ce.HUB.add(fid, loop, wake, me)
        try:
            yield "retry: 1500\n\n"
            while True:
                if await request.is_disconnected():
                    break
                wake.clear()
                evs = await run_in_threadpool(lambda: ce.events_since(db, fid, last_id, 20, full=False))
                for e in evs:
                    if e["id"] in seen or e["id"] <= last_id - 20 or e["id"] <= floor:
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
                        yield "event: beat\ndata: {}\n\n"   # 이름 붙은 알림 — 주석(: beat)은 화면 스크립트가 못 봐 죽은 연결을 알아채지 못했다
        finally:
            ce.HUB.remove(fid, loop, wake, me)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@router.post("/hwp/{fid}/ops")
async def hwp_ops(request: Request, fid: int):
    _editable(request, fid)
    body = await _json(request)
    ops = body.get("ops") if isinstance(body.get("ops"), list) else []
    client = _client(body.get("client"))
    if len(ops) > 200:
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
    action = str(body.get("action") or "acquire")
    field = _field(body.get("field")) if body.get("field") else ""
    client = _client(body.get("client")) if body.get("client") or action == "acquire" else ""
    st, user = request.app.state, _user(request)
    if action == "touch":
        fields = [_field(x) for x in body["fields"][:500]] if isinstance(body.get("fields"), list) else None
        return {"ok": True, "until": await run_in_threadpool(ce.touch, st.db, fid, user, fields, client or None)}
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
    raw = body.get("client") or request.query_params.get("client") or ""
    client = _client(raw) if raw else ""
    st, user = request.app.state, _user(request)

    def run():
        if client:
            ce.leave(st.db, fid, client, user)
        # 창을 닫으면 그 창의 칸은 바로 놓는다(편집 화면이 닫기 전에 남은 변경을 keepalive 로 먼저 보낸다 — 늦게 닿아도 판 번호로 받는다).
        # 연결만 끊긴 창은 여기 오지 않아 임대 시간 동안 그대로다
        for f, x in ce.locks(st.db, fid).items():
            if x["user"] == user and f != ce.STRUCT and client and x["client"] == client:
                ce.release(st.db, fid, f, user, base=Path(st.data_dir))
    await run_in_threadpool(run)
    return {"ok": True}


@router.get("/hwp/{fid}/conflicts")
def hwp_conflicts(request: Request, fid: int):
    _readable(request, fid)
    me = None if getattr(request.state, "role", "") == "dev" else _user(request)
    return {"conflicts": ce.conflicts(request.app.state.db, fid, me)}
