"""플랫폼 동작 — 플랫폼 업무 도우미가 화면의 단추와 같은 일을 대신 하되, 바로 하지 않고 담당자가 [실행] 을 눌러야 한다.

흐름: 도우미(27B, app/platform_helper)가 propose_action 으로 동작과 대상을 고른다 → check 가 권한·대상을 확인하고 「이렇게 하겠습니다」 글을 만든다
→ 대화에 제안으로 남는다(chat_proposals:{대화}) → 담당자가 [실행] 을 누르면 run_proposal 이 권한·대상을 다시 확인하고
화면과 같은 함수로 실행한다 → 결과와 바뀐 것을 대화에 적고 platform_actions.jsonl 에 남긴다.

권한은 화면과 같다 — 개발(/dev) 화면의 일은 관리자(dev)만. 제안할 때와 실행할 때 서버에서 두 번 본다(단추를 숨기는 데 기대지 않는다).
동작마다 같은 일을 하는 화면 자리(screen)와 가리킬 요소(mark — 속성, 선택자는 [mark])를 함께 돌려준다 — 화면 위 커서·강조가 쓴다.
대상 확인 실패·권한 없음은 OpRefused 로 알린다 — 도우미에는 까닭만 돌려주고 문서 이름은 권한 있는 계정에만 보인다.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

MAX_DOCS = 20                       # 제안 하나가 바꾸는 문서 수 상한
MAX_PENDING = 3


class OpRefused(Exception):
    """할 수 없는 동작 — 까닭(에이전트·담당자에게 보일 글)과 함께 보일 후보(data)."""

    def __init__(self, why: str, data: str = ""):
        super().__init__(why)
        self.data = data


@dataclass
class Ctx:
    db: object
    data_dir: Path
    user: str
    role: str
    depts: Callable[[], list[str]] = lambda: []
    sectors: dict = field(default_factory=dict)                       # 업무 영역 {키: 이름} — 프로젝트 만들기
    start_chat: Callable[[int | None, str], int] | None = None        # (프로젝트, 첫 요청) → 새 에이전트 대화 번호(답은 뒤에서 만든다)
    after: dict = field(default_factory=dict)                         # 실행 뒤 화면이 할 일(go: 옮겨 갈 주소)


@dataclass(frozen=True)
class Op:
    key: str
    label: str                       # [실행] 단추 옆에 보일 이름
    desc: str                        # 에이전트 도구 설명(args 꼴)
    screen: str                      # 같은 일을 하는 화면 자리
    roles: tuple[str, ...]
    check: Callable[[Ctx, dict], tuple[str, dict]]       # (이렇게 하겠습니다 글, 정리한 args) — 안 되면 OpRefused
    run: Callable[[Ctx, dict], tuple[str, list[str]]]    # (결과 글, 바뀐 것 목록)
    mark: str = ""                                       # 화면에서 같은 일을 하는 단추의 속성 — 선택자는 [mark]
    mark_in: str = ""                                    # 그 단추가 있는 템플릿(비면 screen 화면의 템플릿)

    @property
    def target(self) -> dict:
        return {"path": self.screen.split("#", 1)[0], "anchor": self.screen.partition("#")[2],
                "selector": f"[{self.mark}]" if self.mark else ""}


def _squash(t: str) -> str:
    return re.sub(r"[\s\-_()\[\]「」『』.,·]+", "", t or "").lower()


def _regulations(db) -> list[dict]:
    with db._conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, filename, audience FROM documents WHERE doc_type = 'regulation' AND status = 'reviewed' ORDER BY filename").fetchall()]


def _pick(pool: list[dict], wanted, title_key: str = "filename") -> list[dict]:
    """번호나 제목으로 고른 대상 — 제목은 그대로 같거나, 띄어쓰기를 빼고 한 건에만 들어 있을 때. 애매하면 OpRefused(후보와 함께)."""
    if isinstance(wanted, (str, int)):
        wanted = [wanted]
    if not isinstance(wanted, list) or not wanted:
        raise OpRefused("대상 문서를 documents 에 적어야 한다(문서 번호나 제목).")
    out: list[dict] = []
    for w in wanted[:MAX_DOCS]:
        s = str(w).strip().strip("「」《》")
        m = re.fullmatch(r"\(?#?(\d+)\)?", s)
        if m:
            hit = [r for r in pool if int(r["id"]) == int(m.group(1))]
        else:
            hit = [r for r in pool if (r[title_key] or "").strip() == s]
            if not hit and _squash(s):
                hit = [r for r in pool if _squash(s) in _squash(r[title_key])]
        if not hit:
            raise OpRefused(f"「{s}」 에 맞는 문서가 없다 — platform_lookup 으로 목록을 보고 번호로 다시 고른다.")
        if len(hit) > 1:
            raise OpRefused(f"「{s}」 에 맞는 문서가 {len(hit)}건이다 — 아래 후보에서 번호로 고른다.",
                            "\n".join(f"({r['id']}) {r[title_key]}" for r in hit[:8]))
        if hit[0] not in out:
            out.append(hit[0])
    return out


def _names(rows: list[dict], key: str = "filename", n: int = 6) -> str:
    s = ", ".join(f"「{(r[key] or '')[:50]}」" for r in rows[:n])
    return s + (f" 외 {len(rows) - n}건" if len(rows) > n else "")


AUD = {"student": "학생 공개", "staff": "교직원만"}


def _ro(word: str) -> str:
    """조사 「로·으로」 — 받침이 없거나 ㄹ 받침이면 로."""
    c = (word or " ")[-1]
    if not ("가" <= c <= "힣"):
        return "로"
    jong = (ord(c) - 0xAC00) % 28
    return "로" if jong in (0, 8) else "으로"


def _audience_check(ctx: Ctx, a: dict) -> tuple[str, dict]:
    aud = str(a.get("audience") or "student")
    if aud not in AUD:
        raise OpRefused("audience 는 student(학생 공개) 또는 staff(교직원만) 가운데 하나다.")
    rows = _pick(_regulations(ctx.db), a.get("documents"))
    todo = [r for r in rows if (r.get("audience") or "staff") != aud]
    if not todo:
        raise OpRefused(f"고른 기준 문서는 이미 {AUD[aud]}이다 — 바꿀 것이 없다.")
    plan = (f"기준 문서 {len(todo)}건을 「{AUD[aud]}」{_ro(AUD[aud])} 바꿉니다 — {_names(todo)}. "
            + ("학생 계정이 찾는 학생 RAG 에 들어갑니다." if aud == "student" else "학생 RAG 에서 빠집니다."))
    return plan, {"ids": [int(r["id"]) for r in todo], "audience": aud}


def _audience_run(ctx: Ctx, a: dict) -> tuple[str, list[str]]:
    aud = a["audience"]
    changed = []
    by_id = {int(r["id"]): r for r in _regulations(ctx.db)}
    for did in a["ids"]:
        r = by_id.get(int(did))
        if r is None or (r.get("audience") or "staff") == aud:
            continue
        ctx.db.set_document_audience(int(did), aud)
        changed.append(f"「{r['filename']}」 {AUD.get(r.get('audience') or 'staff')} → {AUD[aud]}")
    return f"기준 문서 {len(changed)}건을 {AUD[aud]}으로 바꿨습니다.", changed


def _register_pool(ctx: Ctx) -> list[dict]:
    from zzaimy.app import regulation_register
    return [{"id": x["id"], "filename": x["title"]} for x in regulation_register.candidates(ctx.db, "")["items"]]


def _register_check(ctx: Ctx, a: dict) -> tuple[str, dict]:
    rows = _pick(_register_pool(ctx), a.get("documents"))
    plan = (f"반입 자료의 규정 후보 {len(rows)}건을 기준 문서로 등록합니다 — {_names(rows)}. "
            "조문 단위로 잘라 규정 검색에 넣고, 같은 규정의 옛 판이 기준 문서로 있으면 사업 문서로 돌립니다. "
            "몇 분 안에 자동으로 뜻 검색에 들어갑니다.")
    return plan, {"ids": [int(r["id"]) for r in rows]}


def _register_run(ctx: Ctx, a: dict) -> tuple[str, list[str]]:
    from zzaimy.app import regulation_register
    titles = {int(r["id"]): r["filename"] for r in _register_pool(ctx)}
    ids = [i for i in a["ids"] if int(i) in titles]               # 그사이 후보에서 빠진 것(이미 등록 등)은 건너뛴다
    got = regulation_register.register(ctx.db, ids, ctx.data_dir, by=ctx.user)
    done = [f"「{titles[i]}」 기준 문서로" for i in ids if (ctx.db.get_document(i) or {}).get("doc_type") == "regulation"]
    msg = f"기준 문서 {got['registered']}건 등록 · 조문 조각 {got['chunks']}개"
    if got.get("replaced"):
        msg += f" · 옛 판 {got['replaced']}건은 사업 문서로"
    if got.get("skipped"):
        msg += f" · 건너뜀 {got['skipped']}건(본문 없음 등)"
    return msg + ". 몇 분 안에 자동으로 뜻 검색에 들어갑니다.", done


def _dept_check(ctx: Ctx, a: dict) -> tuple[str, dict]:
    from zzaimy.app import rag_spaces
    name = " ".join(str(a.get("name") or "").split())
    if name in ctx.depts():
        raise OpRefused(f"「{name}」 부서는 이미 있다.")
    _, err = rag_spaces.create_dept_rag(dict(rag_spaces.config(ctx.data_dir)), name)
    if err:
        raise OpRefused(err)
    return (f"부서 「{name}」 을 만듭니다 — 빈 부서 RAG 가 함께 생기고, 계정 부서·문서 공개 범위에서 고를 수 있게 됩니다.", {"name": name})


def _dept_run(ctx: Ctx, a: dict) -> tuple[str, list[str]]:
    from zzaimy.app import rag_spaces
    new, err = rag_spaces.create_dept_rag(dict(rag_spaces.config(ctx.data_dir)), a["name"], by=ctx.user)
    if err:
        raise OpRefused(err)
    rag_spaces.save_config(new, ctx.data_dir)
    return f"부서 「{a['name']}」 과 부서 RAG 를 만들었습니다.", [f"부서 RAG 「{a['name']}」 새로 만듦(문서 0건)"]


def _clip(t, n: int) -> str:
    return " ".join(str(t or "").split())[:n]


def _project_check(ctx: Ctx, a: dict) -> tuple[str, dict]:
    name, first = _clip(a.get("name"), 60), _clip(a.get("first_request"), 600)
    sector = str(a.get("sector") or "")
    if sector not in ctx.sectors:
        sector = next((k for k, v in ctx.sectors.items() if v == sector), "")
    if not sector:
        raise OpRefused("sector 는 " + ", ".join(f"{k}({v})" for k, v in ctx.sectors.items()) + " 가운데 하나다 — 모르면 담당자에게 묻는다.")
    if not name:
        raise OpRefused("name(프로젝트 이름 — 사업 이름+할 일)이 비었다 — 담당자에게 묻는다.")
    if not first:
        raise OpRefused("first_request(새 대화에 보낼 첫 요청 — 담당자 뜻을 정리한 한두 문장)가 비었다.")
    due = str(a.get("due_date") or "").strip()
    if due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
        raise OpRefused("due_date 는 2026-11-30 꼴이다(모르면 비운다).")
    plan = (f"프로젝트 「{name}」({ctx.sectors[sector]}{', 마감 ' + due if due else ''})를 만들고, 그 프로젝트의 에이전트 대화를 열어 "
            f"첫 요청 「{first}」 을 보냅니다. 실행하면 그 대화로 옮겨 갑니다.")
    return plan, {"sector": sector, "name": name, "due_date": due, "first_request": first}


def _project_run(ctx: Ctx, a: dict) -> tuple[str, list[str]]:
    if ctx.start_chat is None:
        raise OpRefused("대화를 열 수 없는 자리다.")
    pid = ctx.db.create_project(a["sector"], a["name"], due_date=a.get("due_date") or "", owner=ctx.user)
    sid = ctx.start_chat(pid, a["first_request"])
    ctx.after["go"] = f"/chat/{sid}"
    return (f"프로젝트 「{a['name']}」를 만들고 에이전트 대화에 첫 요청을 보냈습니다.",
            [f"프로젝트 「{a['name']}」 새로 만듦 (/project/{pid})", f"에이전트 대화 /chat/{sid} 시작 — 첫 요청 「{a['first_request'][:80]}」"])


def _own_project(ctx: Ctx, pid) -> int | None:
    if pid in (None, "", 0):
        return None
    try:
        proj = ctx.db.get_project(int(pid))
    except (TypeError, ValueError):
        proj = None
    if not proj or (proj.get("owner") != ctx.user and ctx.role != "dev"):
        raise OpRefused("고른 프로젝트를 찾을 수 없다 — platform_lookup what=projects 로 번호를 확인한다.")
    return int(proj["id"])


def _handoff_check(ctx: Ctx, a: dict) -> tuple[str, dict]:
    q = _clip(a.get("question"), 800)
    if not q:
        raise OpRefused("question(에이전트 대화에 넘길 질문)이 비었다.")
    pid = _own_project(ctx, a.get("project_id"))
    where = f"프로젝트 {pid} 의 " if pid else ""
    return f"{where}에이전트 대화를 새로 열어 「{q}」 를 묻습니다 — 문서 내용 질문·문서 작성은 거기서 이어 합니다.", {"question": q, "project_id": pid}


def _handoff_run(ctx: Ctx, a: dict) -> tuple[str, list[str]]:
    if ctx.start_chat is None:
        raise OpRefused("대화를 열 수 없는 자리다.")
    sid = ctx.start_chat(a.get("project_id"), a["question"])
    ctx.after["go"] = f"/chat/{sid}"
    return "에이전트 대화로 넘겼습니다.", [f"에이전트 대화 /chat/{sid} 시작"]


OPS: dict[str, Op] = {o.key: o for o in (
    Op("regs.audience", "기준 문서 공개 대상 바꾸기",
       "등록된 기준 문서(규정)를 학생 공개(학생 RAG 에 넣기) 또는 교직원만으로. args {documents: [문서 번호 또는 제목], audience: student|staff}",
       "/dev/rag#student-regs", ("dev",), _audience_check, _audience_run, 'data-rr="student"'),
    Op("regs.register", "규정 후보를 기준 문서로 등록",
       "반입 자료의 규정 후보(사업 문서로 들어온 규정·지침)를 기준 문서로 올리기. args {documents: [문서 번호 또는 제목]}",
       "/dev/rag#student-regs", ("dev",), _register_check, _register_run, 'data-rr="register"'),
    Op("dept_rag.create", "부서·부서 RAG 만들기",
       "새 부서와 빈 부서 RAG 만들기. args {name: 부서 이름}",
       "/dev/users#depts", ("dev",), _dept_check, _dept_run, 'data-modal-open="#deptCreate"'),
    Op("project.start", "프로젝트 만들고 문서 작성 대화 시작",
       "문서를 쓰고 싶다는 담당자에게 프로젝트를 만들고 그 프로젝트의 에이전트 대화에 첫 요청을 보내기. "
       "args {sector: 업무 영역, name: 프로젝트 이름, due_date: 마감 YYYY-MM-DD(선택), first_request: 담당자 뜻을 정리한 첫 요청}",
       "/chat", ("dev", "staff"), _project_check, _project_run, 'data-modal-open="#projectModal"', "base.html"),
    Op("chat.handoff", "에이전트 대화에서 이어 하기",
       "사업 문서 내용 질문·문서 작성 요청을 에이전트 대화(채팅)로 넘기기 — 도우미는 문서를 뒤지지 않는다. "
       "args {question: 넘길 질문(담당자 말 그대로 또는 정리), project_id: 이어 갈 프로젝트 번호(선택)}",
       "/chat", ("dev", "staff"), _handoff_check, _handoff_run),
)}


def describe() -> str:
    """도우미 도구 설명에 넣는 동작 목록."""
    return "; ".join(f"{o.key} — {o.desc} (화면 {o.screen}, {'·'.join(o.roles)} 계정만)" for o in OPS.values())


def _allowed(op: Op, role: str) -> None:
    if role not in op.roles:
        raise OpRefused(f"「{op.label}」 은(는) 관리자 계정만 할 수 있다 — 이 계정으로는 제안하지 않는다. "
                        f"담당자에게 관리자에게 요청하라고 안내한다(화면 {op.screen.split('#')[0]}).")


# ---- 보기(관리자) — 에이전트가 대상을 고를 때 보는 목록 ----

def lookup(ctx: Ctx, what: str, query: str = "") -> str:
    """동작 대상 목록 — registered(등록된 기준 문서·공개 대상) | candidates(규정 후보) | depts(부서 RAG) 는 관리자만,
    my_uploads(이 계정이 올린 문서와 처리 상태) | projects(이 계정의 프로젝트) 는 누구나 자기 것만."""
    if ctx.role != "dev" and what not in ("my_uploads", "projects"):
        raise OpRefused("이 목록은 관리자 계정만 본다.")
    words = [w for w in (query or "").split() if w][:4]

    def keep(t: str) -> bool:
        return all(_squash(w) in _squash(t) for w in words)
    if what == "registered":
        rows = [r for r in _regulations(ctx.db) if keep(r["filename"] or "")]
        n_stu = sum(1 for r in rows if r.get("audience") == "student")
        head = f"등록된 기준 문서 {len(rows)}건(학생 공개 {n_stu}건)" + (f" — 「{' '.join(words)}」 로 거름" if words else "")
        return head + "\n" + "\n".join(f"({r['id']}) {r['filename']} — {AUD.get(r.get('audience') or 'staff')}" for r in rows[:30])
    if what == "candidates":
        from zzaimy.app import regulation_register
        got = regulation_register.candidates(ctx.db, " ".join(words))
        return (f"반입 자료의 규정 후보 {got['total']}건\n" + "\n".join(f"({x['id']}) {x['title']}" for x in got["items"][:30]))
    if what == "depts":
        return "부서 RAG: " + (", ".join(ctx.depts()) or "(없음)")
    if what == "my_uploads":                       # 이 계정이 올린 문서만 — 남의 문서 이름은 보이지 않는다
        from zzaimy.app.vocab import STATUS_LABELS
        with ctx.db._conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, filename, status, doc_type, dept, created_at FROM documents WHERE owner = ? ORDER BY id DESC LIMIT 15",
                (ctx.user,)).fetchall()]
        rows = [r for r in rows if keep(r["filename"] or "")]
        return ("이 계정이 올린 최근 문서\n" + "\n".join(
            f"({r['id']}) {r['filename']} — {STATUS_LABELS.get(r['status'], r['status'])} · "
            f"{'기준 문서' if r['doc_type'] == 'regulation' else '사업 문서'} · 검색 범위 {r['dept'] or '공통'} · {str(r['created_at'])[:16]}"
            for r in rows)) if rows else "이 계정이 올린 문서가 없다."
    if what == "projects":
        projs = [p for p in ctx.db.list_all_projects() if p.get("owner") == ctx.user and not p.get("archived")]
        return ("이 계정의 프로젝트\n" + "\n".join(f"({p['id']}) {p['name']} — {ctx.sectors.get(p.get('sector'), p.get('sector'))}"
                                                + (f" · 마감 {p['due_date']}" if p.get("due_date") else "") for p in projs[:20])
                ) if projs else "이 계정의 프로젝트가 없다."
    raise OpRefused("what 은 registered · candidates · depts · my_uploads · projects 가운데 하나다.")


# ---- 제안 저장·실행 ----

def _key(session_id: int) -> str:
    return f"chat_proposals:{session_id}"


def load(db, session_id: int) -> list[dict]:
    try:
        got = json.loads(db.get_setting(_key(session_id), "") or "[]")
        return got if isinstance(got, list) else []
    except ValueError:
        return []


def _save(db, session_id: int, items: list[dict]) -> None:
    db.set_setting(_key(session_id), json.dumps(items[-12:], ensure_ascii=False))


def pending(db, session_id: int) -> list[dict]:
    """화면에 띄울 제안 — kind run 은 [실행], go 는 화면 열기. 대상 번호 등 내부 값은 빼고 보인다.
    target 은 가리킬 화면 요소(path·anchor·selector) — 화면 위 커서·강조가 쓴다."""
    return [{"id": p["id"], "kind": p.get("kind", "run"), "label": p["label"], "plan": p["plan"], "screen": p["screen"],
             "target": p.get("target") or {}}
            for p in load(db, session_id) if p.get("status") == "pending"]


def propose_open(ctx: Ctx, session_id: int, page: str, why: str, selector: str = "") -> dict:
    """화면 열기 제안 — 실행할 것이 없고 그 화면(자리·요소)으로 옮겨 갈 단추만 낸다. 화면은 안내 자료에 있는 것만."""
    from zzaimy.app import platform_guide
    path, _, anchor = str(page or "").partition("#")
    p = platform_guide.page_for(path)
    if p is None or "{" in p.path:
        raise OpRefused("안내 자료에 있는 화면 주소만 연다 — platform_help 결과의 주소를 쓴다.")
    if ctx.role not in p.roles:
        raise OpRefused(f"「{p.title}」 은(는) 이 계정으로 열 수 없는 화면이다 — 열 수 있는 계정(관리자 등)에게 요청하라고 안내한다.")
    marks = {t.selector for t in p.tasks if t.selector}
    sel = selector if selector in marks else ""
    url = path + (f"#{anchor}" if re.fullmatch(r"[\w-]{1,40}", anchor or "") else "")
    items = load(ctx.db, session_id)
    if sum(1 for x in items if x.get("status") == "pending") >= MAX_PENDING:
        raise OpRefused(f"이번 요청에서 제안은 {MAX_PENDING}개까지다 — finish 로 알린다.")
    item = {"id": secrets.token_hex(6), "kind": "go", "op": "open", "label": f"{p.title} 열기", "args": {}, "plan": _clip(why, 200) or p.purpose,
            "screen": url, "target": {"path": path, "anchor": anchor if url.endswith("#" + anchor) else "", "selector": sel},
            "status": "pending", "by": ctx.user, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _save(ctx.db, session_id, items + [item])
    return item


def expire(db, session_id: int) -> None:
    """새 요청이 오면 앞 요청의 제안은 내린다 — 오래된 제안을 모르고 누르지 않게."""
    items = load(db, session_id)
    if any(p.get("status") == "pending" for p in items):
        _save(db, session_id, [{**p, "status": "expired"} if p.get("status") == "pending" else p for p in items])


def propose(ctx: Ctx, session_id: int, key: str, args: dict) -> dict:
    op = OPS.get(str(key or ""))
    if op is None:
        raise OpRefused("없는 동작이다 — op 는 " + ", ".join(OPS) + " 가운데 하나다.")
    _allowed(op, ctx.role)
    plan, norm = op.check(ctx, args if isinstance(args, dict) else {})
    items = load(ctx.db, session_id)
    if sum(1 for p in items if p.get("status") == "pending") >= MAX_PENDING:
        raise OpRefused(f"이번 요청에서 제안은 {MAX_PENDING}개까지다 — finish 로 알린다.")
    same = next((p for p in items if p.get("status") == "pending" and p["op"] == op.key and p["args"] == norm), None)
    if same:
        return same
    item = {"id": secrets.token_hex(6), "kind": "run", "op": op.key, "label": op.label, "args": norm, "plan": plan, "screen": op.screen,
            "target": op.target, "status": "pending", "by": ctx.user, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _save(ctx.db, session_id, items + [item])
    return item


def record(data_dir: Path, rec: dict) -> None:
    p = Path(data_dir) / "platform_actions.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}, ensure_ascii=False) + "\n")


def run_proposal(ctx: Ctx, session_id: int, pid: str) -> str:
    """담당자가 누른 제안을 실행한다 — 권한과 대상을 지금 다시 확인한다. 돌려주는 글은 대화에 남길 결과."""
    items = load(ctx.db, session_id)
    item = next((p for p in items if p.get("id") == pid), None)
    if item is None:
        raise KeyError(pid)
    if item.get("kind") == "go":
        raise OpRefused("화면 열기 제안은 실행할 것이 없습니다.")
    if item.get("status") != "pending":
        raise OpRefused("이미 실행했거나 지난 제안입니다." if item.get("status") == "done" else "지난 제안입니다 — 다시 요청해 주세요.")
    op = OPS[item["op"]]
    _allowed(op, ctx.role)
    item["status"] = "running"                            # 두 번 눌러도 한 번만
    _save(ctx.db, session_id, items)
    try:
        again = dict(item["args"])
        if "ids" in again:                                # 제안 때 고른 문서 번호로 다시 확인(그사이 바뀐 것은 빠진다)
            again["documents"] = [str(i) for i in again["ids"]]
        _, norm = op.check(ctx, again)
        text, changed = op.run(ctx, norm)
    except Exception as e:
        item["status"], item["result"] = "failed", str(e)[:300]
        _save(ctx.db, session_id, items)
        record(ctx.data_dir, {"session": session_id, "op": op.key, "by": ctx.user, "role": ctx.role, "args": item["args"],
                              "ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"})
        raise
    item["status"], item["result"], item["changed"] = "done", text, changed[:MAX_DOCS]
    _save(ctx.db, session_id, items)
    record(ctx.data_dir, {"session": session_id, "op": op.key, "by": ctx.user, "role": ctx.role, "args": item["args"],
                          "ok": True, "result": text, "changed": changed[:MAX_DOCS]})
    return text + ("\n바뀐 것:\n" + "\n".join(f"- {c}" for c in changed[:MAX_DOCS]) if changed else "")
