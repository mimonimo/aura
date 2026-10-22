"""프로젝트 진행 현황(/project/{id}/activity) — 프로젝트 화면의 탭 하나. 하위 탭 넷: 참여자 · 요청 기록 · 문맥·토큰 · 작업 요약.

- 볼 수 있는 사람: may_view → grades.can_view_activity(관리자 전부, 총괄=자기 부서 프로젝트, 사업 책임=만든·책임자 지정 프로젝트).
  일반 등급·학생은 열지 않는다.
- 대화는 보는 사람이 읽을 수 있는 것만 센다(main._can_read_chat — 비공개 대화·업무 상담·범위 밖 동료의 대화는 빠진다).
- 사용량: 답마다 설정 chat_ctx_msg:<답 메시지 id>(main._answer_task 가 chat_ctx 와 같은 자리에서 저장). 그 전에 쓴 대화는
  chat_ctx:<대화 id> 한 건(마지막 답)으로 대신 센다 — 화면에 「이전 기록은 대화별 마지막 답만」.
- 작업 요약: 「요약 만들기」 를 누르면 그 사람의 요청·답 첫 줄·작업 문서 이름·작업 메모(chat_summary)를 모아 review 연결로 3~6줄.
  설정 project_activity_summary:<프로젝트 id>:<계정> = {"text", "at", "n"(요약 때의 요청 수)}. 모델이 안 닿으면 오류 문구만.
"""
from __future__ import annotations

import json
import logging
import re
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

router = APIRouter()

SUMMARY_KEY = "project_activity_summary:{pid}:{user}"
SUMMARY_PROMPT = """아래는 한 사람이 행정 업무 프로젝트 「{project}」 안에서 AURA 에게 한 요청과, 받은 답의 첫 줄, 만든 작업 문서, 대화별 작업 메모다.
이 사람이 이 프로젝트에서 무엇을 했는지 3~6줄로 요약한다. 줄마다 「- 」 로 시작하는 짧은 서술형.
규칙: 자료에 없는 사실·수치를 보태지 않는다. 같은 일을 되풀이한 요청은 한 줄로 묶는다. 인사·과정 설명은 뺀다."""


def _me(request: Request) -> str:
    return "" if getattr(request.state, "view_as", None) else (getattr(request.state, "user", "") or "")


def may_view(request: Request, proj: dict | None) -> bool:
    """이 요청의 사람이 프로젝트 진행 현황을 볼 수 있는가 — grades.can_view_activity(관리자 전부, 총괄은 자기 부서 프로젝트,
    사업 책임은 자기가 만든·책임자로 지정된 프로젝트, 일반·학생 불가). 프로젝트 화면이 「진행 현황」 탭을 숨길 때도 쓴다."""
    if not proj:
        return False
    st = request.app.state
    if getattr(request.state, "role", "") == "dev" and not getattr(request.state, "view_as", None):
        return True
    if getattr(request.state, "role", "") == "student":
        return False
    from zzaimy.app import grades
    from zzaimy.app import project_access as _pa
    accounts = getattr(st, "accounts", {}) or {}
    user = _me(request)
    owner_dept = ((accounts.get(proj.get("owner") or "") or {}).get("dept") or "").strip()
    return grades.can_view_activity(proj, user=user, account=accounts.get(user), lead=_pa.lead(st.db, proj, accounts),
                                    project_dept=owner_dept)


def _guard(request: Request, pid: int) -> dict:
    """진행 현황을 볼 수 있는 사람만 — 없으면 404, 권한이 없으면 403."""
    proj = request.app.state.db.get_project(int(pid))
    if proj is None:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    if not may_view(request, proj):
        raise HTTPException(403, "이 프로젝트의 진행 현황을 볼 권한이 없습니다.")
    return proj


def _first_lines(text: str, n: int = 2, cap: int = 160) -> str:
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    return "\n".join(ln[:cap] for ln in lines[:n])


def _load(raw: str) -> dict:
    try:
        v = json.loads(raw or "{}")
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _settings(db, keys: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    with db._conn() as conn:
        for i in range(0, len(keys), 400):
            part = keys[i:i + 400]
            q = "SELECT key, value FROM settings WHERE key IN (%s)" % ",".join("?" * len(part))
            out.update({r[0]: r[1] for r in conn.execute(q, part).fetchall()})
    return out


def _work_docs(db, sid: int) -> list[str]:
    """대화의 작업 문서 이름 — 구글 작업본(파일 기록·대화 연결)과 대화에 이은 한글 파일."""
    names: list[str] = []
    for f in db.list_files(kind="google", session_id=int(sid), limit=50):
        names.append(f.get("name") or "구글 문서")
    link = _load(db.get_setting(f"chat_google_doc:{int(sid)}", ""))
    if link.get("doc") and not db.list_files(kind="google", session_id=int(sid), limit=1):
        names.append("대화의 구글 작업 문서")
    try:
        from zzaimy.app import webhwp, webhwp_agent
        for fid in webhwp_agent.files_of(db, int(sid)):
            f = webhwp.get(db, int(fid))
            if f:
                names.append(f.get("name") or "한글 문서")
    except Exception:
        logging.getLogger("zzaimy.app.activity").debug("한글 작업 문서 읽기 실패", exc_info=True)
    return names


def _usage(u: dict) -> dict:
    return {"calls": int(u.get("calls") or 0), "prompt": int(u.get("prompt") or 0), "peak": int(u.get("peak") or 0),
            "limit": int(u.get("limit") or 0), "trimmed": bool(u.get("trimmed"))}


def _day(at, fallback: str = "") -> str:
    if isinstance(at, (int, float)) and at > 0:
        return time.strftime("%Y-%m-%d", time.localtime(at))
    return (fallback or "")[:10]


def collect(request: Request, proj: dict) -> dict:
    """화면·API 공통 자료 — 참여자, 요청 줄, 사용량(사람별·날짜별), 저장된 요약."""
    st = request.app.state
    db, accounts = st.db, getattr(st, "accounts", {}) or {}
    pid = int(proj["id"])
    can_read = st.project._can_read_chat
    with db._conn() as conn:
        sessions = [dict(r) for r in conn.execute("SELECT * FROM chat_sessions WHERE project_id = ? ORDER BY id", (pid,)).fetchall()]
    # 진행 현황을 볼 수 있는 사람(may_view)에게는 비공개가 아닌 프로젝트 대화 전부 — 총괄은 공유 대상이 아니어도 부서 프로젝트를 살핀다.
    # 비공개 대화·업무 상담은 만든 사람만(_can_read_chat 이 본인 것은 연다)
    sessions = [s for s in sessions if can_read(request, s)
                or (not s.get("is_private") and (s.get("channel") or "chat") == "chat")]
    by_sid = {int(s["id"]): s for s in sessions}
    msgs: list[dict] = []
    if by_sid:
        with db._conn() as conn:
            ids = list(by_sid)
            for i in range(0, len(ids), 400):
                part = ids[i:i + 400]
                q = ("SELECT id, session_id, role, content, created_at FROM chat_messages WHERE session_id IN (%s) "
                     "AND role IN ('user', 'assistant') ORDER BY id") % ",".join("?" * len(part))
                msgs += [dict(r) for r in conn.execute(q, part).fetchall()]

    # 사람 — 담당자, 공유 대상(사람·부서를 펼친 계정), 실제로 대화한 사람
    from zzaimy.app import project_access as _pa
    owner = proj.get("owner") or ""
    owner_dept = ((accounts.get(owner) or {}).get("dept") or "").strip()
    share = _pa.shares(db, proj, owner_dept)
    recips = _pa.recipients(share, accounts, owner) if not share.get("implicit") else {}
    people: dict[str, dict] = {}

    def person(u: str) -> dict:
        if u not in people:
            a = accounts.get(u) or {}
            people[u] = {"user": u, "name": a.get("name") or u, "dept": (a.get("dept") or "").strip(), "roles": [],
                         "requests": 0, "answers": 0, "last": "", "tokens": 0, "calls": 0, "docs": 0, "doc_names": [], "sessions": 0}
        return people[u]

    if owner:
        person(owner)["roles"].append("담당자")
    for u, lv in recips.items():
        person(u)["roles"].append(f"공유 · {_pa.LEVEL_LABEL.get(lv, lv)}")
    for s in sessions:
        p = person(s.get("owner") or "")
        p["sessions"] += 1
        if not p["roles"]:
            role = (accounts.get(p["user"]) or {}).get("role", "staff")
            if role == "dev":
                p["roles"].append("관리자")
            elif share.get("implicit") and p["dept"] and p["dept"] == owner_dept:
                p["roles"].append("부서 동료")         # 공유 범위를 정하지 않은 프로젝트의 기본(담당자 부서에 보기)
            else:
                p["roles"].append("예전 공유")          # 지금 공유 범위 밖이지만 이 프로젝트에서 대화한 사람
        for n in _work_docs(db, int(s["id"])):
            p["docs"] += 1
            p["doc_names"].append(n)
    shared_to = [{"to": i["to"].split(":", 1)[1], "kind": "부서" if i["to"].startswith("dept:") else "사람",
                  "level": _pa.LEVEL_LABEL.get(i.get("level"), i.get("level"))} for i in share.get("items") or []]

    # 요청 줄
    requests: list[dict] = []
    for m in msgs:
        s = by_sid[int(m["session_id"])]
        p = person(s.get("owner") or "")
        if (m.get("created_at") or "") > p["last"]:
            p["last"] = m.get("created_at") or ""
        if m["role"] == "user":
            p["requests"] += 1
            requests.append({"id": int(m["id"]), "session": int(s["id"]), "title": s.get("title") or "대화", "user": p["user"],
                             "name": p["name"], "at": (m.get("created_at") or "")[:16].replace("T", " "),
                             "head": _first_lines(m.get("content") or ""), "text": str(m.get("content") or "")[:2000]})
        else:
            p["answers"] += 1
    requests.reverse()                                   # 새 것부터

    # 사용량 — 답마다(chat_ctx_msg), 그 기록이 없는 대화는 chat_ctx(마지막 답) 한 건
    ans = [m for m in msgs if m["role"] == "assistant"]
    got = _settings(db, [f"chat_ctx_msg:{int(m['id'])}" for m in ans] + [f"chat_ctx:{sid}" for sid in by_sid])
    records: list[dict] = []
    with_msg: set[int] = set()
    for m in ans:
        raw = got.get(f"chat_ctx_msg:{int(m['id'])}")
        if raw:
            u = _load(raw)
            sid = int(m["session_id"])
            with_msg.add(sid)
            records.append({"user": by_sid[sid].get("owner") or "", "day": _day(u.get("at"), m.get("created_at") or ""),
                            "legacy": False, **_usage(u)})
    legacy = 0
    for sid, s in by_sid.items():
        if sid in with_msg or f"chat_ctx:{sid}" not in got:
            continue
        u = _load(got[f"chat_ctx:{sid}"])
        if not u.get("calls"):
            continue
        legacy += 1
        records.append({"user": s.get("owner") or "", "day": _day(u.get("at"), s.get("created_at") or ""), "legacy": True, **_usage(u)})

    def roll(key: str) -> list[dict]:
        acc: dict[str, dict] = {}
        for r in records:
            k = r[key]
            a = acc.setdefault(k, {"key": k, "answers": 0, "calls": 0, "prompt": 0, "peak": 0, "limit": 0, "trimmed": 0})
            a["answers"] += 1
            a["calls"] += r["calls"]
            a["prompt"] += r["prompt"]
            if r["peak"] >= a["peak"]:
                a["peak"], a["limit"] = r["peak"], r["limit"] or a["limit"]
            a["trimmed"] += 1 if r["trimmed"] else 0
        return list(acc.values())

    per_user = sorted(roll("user"), key=lambda a: -a["prompt"])
    for a in per_user:
        p = person(a["key"])
        a["name"] = p["name"]
        p["tokens"], p["calls"] = a["prompt"], a["calls"]
    per_day = sorted(roll("day"), key=lambda a: a["key"])
    top = max([a["prompt"] for a in per_user + per_day] or [0])

    # 요약 — 저장본과 그 뒤 새 요청 수
    for u, p in people.items():
        sm = _load(db.get_setting(SUMMARY_KEY.format(pid=pid, user=u), ""))
        p["summary"] = ({"text": sm.get("text", ""), "at": _day(sm.get("at")), "n": int(sm.get("n") or 0),
                         "new": max(0, p["requests"] - int(sm.get("n") or 0))} if sm.get("text") else None)

    order = {"담당자": 0}
    plist = sorted(people.values(), key=lambda p: (order.get((p["roles"] or [""])[0], 1), -p["requests"], p["user"]))
    plist = [p for p in plist if p["user"]]
    return {"project": {"id": pid, "name": proj.get("name") or ""}, "people": plist, "shared_to": shared_to,
            "share_mode": "implicit" if share.get("implicit") else share.get("mode"),
            "requests": requests, "usage": {"per_user": per_user, "per_day": per_day, "top": top, "legacy": legacy,
                                            "answers": len(records)}}


def _client(request: Request):
    """review 연결(배경 작업용). 시험은 app.state.activity_client 로 가짜를 넣는다."""
    fake = getattr(request.app.state, "activity_client", None)
    if fake is not None:
        return fake() if callable(fake) else fake
    from zzaimy.generate.client import VllmClient
    return VllmClient(role="review")


def summarize(request: Request, proj: dict, user: str) -> dict:
    st = request.app.state
    db = st.db
    data = collect(request, proj)
    p = next((x for x in data["people"] if x["user"] == user), None)
    if p is None or not p["requests"]:
        raise HTTPException(404, "이 사람의 요청 기록이 없습니다.")
    reqs = [r for r in reversed(data["requests"]) if r["user"] == user]
    sids = sorted({r["session"] for r in reqs})
    with db._conn() as conn:
        heads = {}
        for sid in sids:
            heads[sid] = [_first_lines(r[0], 1, 120) for r in conn.execute(
                "SELECT content FROM chat_messages WHERE session_id = ? AND role = 'assistant' ORDER BY id", (sid,)).fetchall()]
    from zzaimy.app import chat_memory
    parts = []
    for sid in sids:
        title = next((r["title"] for r in reqs if r["session"] == sid), "대화")
        lines = [f"[대화] {title}"]
        lines += [f"- 요청({r['at']}): {r['text'][:400]}" for r in reqs if r["session"] == sid]
        lines += [f"- 답 첫 줄: {h}" for h in heads.get(sid, []) if h][:12]
        memo = (chat_memory.load(db, sid).get("text") or "").strip()
        if memo:
            lines.append("[작업 메모]\n" + memo[:1500])
        parts.append("\n".join(lines))
    if p["doc_names"]:
        parts.append("[작업 문서] " + ", ".join(dict.fromkeys(p["doc_names"])))
    body = "\n\n".join(parts)
    messages = [{"role": "system", "content": SUMMARY_PROMPT.format(project=proj.get("name") or "")},
                {"role": "user", "content": f"[사람] {p['name']}\n\n{body}"}]
    from zzaimy.app import llm_budget
    try:
        client = _client(request)
        resp = llm_budget.create_fitted(client, messages, 600, [llm_budget.Cut(msg=1, text=body)], floor=200,
                                        temperature=0.2, extra_body=getattr(client, "_extra", {}))
        text = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        from zzaimy.generate.client import describe_llm_error
        logging.getLogger("zzaimy.app.activity").warning("작업 요약 실패 (프로젝트 %s, %s): %s", proj.get("id"), user, type(e).__name__)
        return {"ok": False, "error": describe_llm_error(e).replace("답변을", "요약을")}
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if not text:
        return {"ok": False, "error": "모델이 빈 요약을 돌려주었습니다. 잠시 후 다시 시도해 주세요."}
    val = {"text": text[:2000], "at": time.time(), "n": p["requests"]}
    db.set_setting(SUMMARY_KEY.format(pid=int(proj["id"]), user=user), json.dumps(val, ensure_ascii=False))
    return {"ok": True, "user": user, "text": val["text"], "at": _day(val["at"]), "n": val["n"], "new": 0}


@router.get("/api/project/{project_id}/activity")
def activity_api(request: Request, project_id: int):
    return collect(request, _guard(request, project_id))


@router.post("/api/project/{project_id}/activity/summary")
async def activity_summary(request: Request, project_id: int):
    proj = _guard(request, project_id)
    user = ""
    try:
        if "json" in (request.headers.get("content-type") or ""):
            user = str((await request.json()).get("user") or "")
        else:
            user = str((await request.form()).get("user") or "")
    except Exception:
        user = ""
    if not user.strip():
        raise HTTPException(400, "누구의 요약인지 알 수 없습니다.")
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(summarize, request, proj, user.strip())


@router.get("/project/{project_id}/activity", response_class=HTMLResponse)
def activity_page(request: Request, project_id: int):
    proj = _guard(request, project_id)
    st = request.app.state
    h = st.project
    try:
        groups = h._doc_groups(request, proj)
        counts = {k: len(groups.get(k) or []) for k in ("attached", "reference", "rules")}
    except Exception:
        counts = {"attached": 0, "reference": 0, "rules": 0}
    data = collect(request, proj)
    return h.templates.TemplateResponse(request, "project_activity.html", h.ctx(request, {
        "project": proj, "active_tab": proj.get("sector"), "doc_counts": counts, "activity": data,
    }))
