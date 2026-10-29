"""플랫폼 동작 — 플랫폼 업무 도우미가 화면의 단추와 같은 일을 대신 하되, 바로 하지 않고 담당자가 [실행] 을 눌러야 한다.

흐름: 도우미(27B, app/platform_helper)가 propose_action 으로 동작과 대상을 고른다 → check 가 권한·대상을 확인하고 「이렇게 하겠습니다」 글을 만든다
→ 대화에 제안으로 남는다(chat_proposals:{대화}) → 담당자가 [실행] 을 누르면 run_proposal 이 권한·대상을 다시 확인하고
화면과 같은 함수로 실행한다 → 결과와 바뀐 것을 대화에 적고 platform_actions.jsonl 에 남긴다.

권한은 화면과 같다 — 개발(/dev) 화면의 일은 관리자(dev)만. 제안할 때와 실행할 때 서버에서 두 번 본다(단추를 숨기는 데 기대지 않는다).
동작마다 같은 일을 하는 화면 자리(screen)와 가리킬 요소(mark — 속성, 선택자는 [mark])를 함께 돌려준다 — 화면 위 커서·강조가 쓴다.
대상 확인 실패·권한 없음은 OpRefused 로 알린다 — 도우미에는 까닭만 돌려주고 문서 이름은 권한 있는 계정에만 보인다.
화면 열기(propose_open)는 커서가 할 단계(steps)를 싣는다 — 열기·찾기·칸 채우기는 시키면 바로, 마지막 단추는 담당자가 친 글(Ctx.said)에
그 동사가 있을 때만(commanded). 커서가 실제로 한 결과는 화면이 보고하고(report) 대화의 완료 줄은 그 보고로만 생긴다.
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
MAX_ACT_STEPS = 6                   # 화면 열기 하나에 이어 할 커서 단계(창 열기 → 칸 채우기 → 마지막 단추) 상한


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
    may_create_project: bool = True                                  # 새 프로젝트를 만들 수 있는 등급인가(사업 책임자 이상 — grades.can_create_project)
    said: str = ""                                                   # 이번 요청에 담당자가 직접 친 글(문서·검색 결과·고른 글 아님) — 마지막 단추 허락의 근거
    page: str = ""                                                   # 담당자가 지금 보는 화면 주소 — 같은 화면이면 열기 단추 없이 그 자리에서 한다
    controls: list = field(default_factory=list)                     # 지금 화면에 보이는 칸·단추(clean_controls) — 안내 자료에 없는 요소도 그 자리에서 다룬다


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
    plan = (f"기준 문서 {len(todo)}건을 「{AUD[aud]}」{_ro(AUD[aud])} 바꿉니다: {_names(todo)}. "
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
    plan = (f"반입 자료의 규정 후보 {len(rows)}건을 기준 문서로 등록합니다: {_names(rows)}. "
            "조문 단위로 잘라 규정 검색에 넣고, 같은 규정의 옛 판이 기준 문서로 있으면 사업 문서로 돌립니다. "
            "몇 분 안에 뜻 검색에도 들어갑니다.")
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
    return (f"부서 「{name}」 을 만듭니다. 빈 부서 RAG 가 함께 생기고, 계정 부서·문서 공개 범위의 고르기 목록에 나옵니다.", {"name": name})


def _dept_run(ctx: Ctx, a: dict) -> tuple[str, list[str]]:
    from zzaimy.app import rag_spaces
    new, err = rag_spaces.create_dept_rag(dict(rag_spaces.config(ctx.data_dir)), a["name"], by=ctx.user)
    if err:
        raise OpRefused(err)
    rag_spaces.save_config(new, ctx.data_dir)
    return f"부서 「{a['name']}」 과 부서 RAG 를 만들었습니다.", [f"부서 RAG 「{a['name']}」 새로 만듦(문서 0건)"]


def _lend_check(ctx: Ctx, a: dict) -> tuple[str, dict]:
    """부서 RAG 다른 부서에 열기 — 관리자, 또는 그 부서를 맡은 총괄 책임자(조직 관리 /org/rag-lend 와 같은 규칙)."""
    from zzaimy.app import access_policy, grades, rag_spaces
    sq = lambda v: " ".join(str(v or "").split())  # noqa: E731
    dept = sq(a.get("dept"))
    known = set(ctx.depts())
    if dept not in known:
        raise OpRefused(f"「{dept}」 부서를 찾지 못했다.", ", ".join(sorted(known)[:20]))
    if ctx.role != "dev" and dept not in grades.covered_depts(ctx.user, access_policy._account(ctx.user, ctx.data_dir),
                                                               ctx.data_dir, "head"):
        raise OpRefused("이 부서 RAG 는 관리자나 이 부서 총괄 책임자가 연다.")
    whole = isinstance(a.get("to"), list)                # 실행 때 다시 확인 — 제안에 정리해 둔 받는 부서 목록 그대로
    add = [sq(x) for x in ((a.get("to") if whole else a.get("add")) or []) if sq(x)]
    drop = set() if whole else {sq(x) for x in (a.get("remove") or []) if sq(x)}
    bad = [x for x in [*add, *drop] if x not in known or x in (dept, "공통")]
    if bad:
        raise OpRefused("열 수 없는 부서: " + ", ".join(bad))
    if not whole and not add and not drop:
        raise OpRefused("더하거나 뺄 부서를 적는다.")
    cur = [] if whole else rag_spaces.receivers(rag_spaces.config(ctx.data_dir), dept)
    to = [d for d in dict.fromkeys([*cur, *add]) if d not in drop]
    plan = ", ".join(to) if to else "열어 준 부서 없음"
    return f"「{dept}」 부서 RAG 를 받는 부서를 바꿉니다: {plan}.", {"dept": dept, "to": to}


def _lend_run(ctx: Ctx, a: dict) -> tuple[str, list[str]]:
    from zzaimy.app import org_lock, rag_spaces
    with org_lock.LOCK:
        cfg = rag_spaces.set_receivers(rag_spaces.config(ctx.data_dir), a["dept"], a["to"])
        rag_spaces.save_config(cfg, ctx.data_dir)
    got = rag_spaces.receivers(cfg, a["dept"])
    return (f"「{a['dept']}」 부서 RAG: " + (", ".join(got) + "에 열었습니다." if got else "다른 부서에 열지 않습니다."),
            [f"{a['dept']} 부서 RAG 받는 부서: {', '.join(got) or '없음'}"])


def _clip(t, n: int) -> str:
    return " ".join(str(t or "").split())[:n]


def _project_check(ctx: Ctx, a: dict) -> tuple[str, dict]:
    if not ctx.may_create_project:
        raise OpRefused("프로젝트는 사업 책임자 이상이 만듭니다 — 공유받은 프로젝트에서 이어 가거나 사업 책임자에게 만들어 달라고 한다.")
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
            [f"프로젝트 「{a['name']}」 새로 만듦 (/project/{pid})", f"에이전트 대화 /chat/{sid} 시작, 첫 요청 「{a['first_request'][:80]}」"])


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
    return f"{where}에이전트 대화를 새로 열어 「{q}」 를 묻습니다. 문서 내용 질문·문서 작성은 거기서 이어 합니다.", {"question": q, "project_id": pid}


def _handoff_run(ctx: Ctx, a: dict) -> tuple[str, list[str]]:
    if ctx.start_chat is None:
        raise OpRefused("대화를 열 수 없는 자리다.")
    sid = ctx.start_chat(a.get("project_id"), a["question"])
    ctx.after["go"] = f"/chat/{sid}"
    return "에이전트 대화로 넘겼습니다.", [f"에이전트 대화 /chat/{sid} 시작"]


OPS: dict[str, Op] = {o.key: o for o in (
    Op("regs.audience", "기준 문서 공개 대상 바꾸기",
       "등록된 기준 문서(규정)를 학생 공개(학생 RAG 에 넣기) 또는 교직원만으로. args {documents: [문서 번호 또는 제목], audience: student|staff}",
       "/?type=all&use=criteria", ("dev",), _audience_check, _audience_run, 'data-crit="student"'),
    Op("regs.register", "규정 후보를 기준 문서로 등록",
       "반입 자료의 규정 후보(사업 문서로 들어온 규정·지침)를 기준 문서로 올리기. args {documents: [문서 번호 또는 제목]}",
       "/?type=all&use=criteria", ("dev",), _register_check, _register_run, 'data-crit="register"'),
    Op("dept_rag.create", "부서·부서 RAG 만들기",
       "새 부서와 빈 부서 RAG 만들기. args {name: 부서 이름}",
       "/team", ("dev",), _dept_check, _dept_run, 'data-org-add'),   # 조직 관리 트리 위 「부서」
    Op("dept_rag.lend", "부서 RAG 다른 부서에 열기",
       "부서 RAG 를 다른 부서가 함께 찾게 열거나 거두기 — 받는 쪽은 늘 부서(계정 하나에 주지 않는다). "
       "args {dept: 주는 부서, add: [받을 부서], remove: [거둘 부서]}",
       "/dev/rag#dept-rags", ("dev", "staff"), _lend_check, _lend_run),
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
             "target": p.get("target") or {}, "act": p.get("act") or {}, "reported": bool(p.get("reported"))}
            for p in load(db, session_id) if p.get("status") == "pending"]


# 마지막 단추를 시키는 말인지 — 사람이 친 글에서만 본다. 묻는 말(어떻게·방법·…까요·물음표)과 막는 말(하지 마·말고·안 돼)이 붙으면,
# 또 동사가 시키는 꼴로 끝나지 않으면(「만들어 둔 프로젝트」·「저장해도 돼?」) 시킨 것이 아니다 — 모르면 누르지 않는 쪽(가리키기만)으로
_ASKING = re.compile(r"어떻게|방법|하는\s*법|어디서|어디에서|뭐야|무슨\s*뜻|[?？]|까요|을까|ㄹ까|할까|될까|되나|나요|는지|은지|인가|건가|던가")
_NOT = re.compile(r"지\s*마|지\s*말|말고|말아|말구|않|안\s*[해돼되하누눌]|면\s*안|선\s*안|서는\s*안|금지|필요\s*없|없이|못|멈춰|취소")
_CLAUSE_END = re.compile(r"[.!。\n]|그리고|다음에|그다음|그\s*뒤")
# 시키는 활용형(…해·…어·…려) 뒤에 올 수 있는 말 — 바로 끝나거나(줘·주세요 같은 도움말 하나까지) 다음 일로 이어질 때만
_ORDER_TAIL = re.compile(r"\s*(?:줘요|줘|주세요|주십시오|주시고|주시오|줄래요|줄래|주라|주고|요|라|봐요|봐|놔|둬요|둬|버려|놓고|두고)?"
                         r"\s*(?:$|[.!,~。\n]|그리고|다음에|그다음|하고\b)")


def commanded(said: str, verbs: tuple[str, ...] | list[str]) -> bool:
    """담당자가 이번 요청에서 그 마지막 단추를 누르라고 시켰는가 — verbs(안내 자료 Task 에 적은 활용형) 가운데 하나가 글에 있고,
    시키는 꼴로 끝나며(이어 주는 꼴 …하고·…까지 는 그대로), 그 말 마디 안에 막는 말이 없고, 글 전체가 묻는 꼴이 아닐 때만.
    모델 판단과 상관없이 서버가 본다. said 는 담당자가 이번에 직접 친 글뿐이다(Ctx.said — 고른 글·문서·검색 결과는 들어오지 않는다)."""
    text = " ".join(str(said or "").split())
    if not text or not verbs or _ASKING.search(text):
        return False
    for v in verbs:
        v = " ".join(str(v or "").split())
        if not v:
            continue
        at = text.find(v)
        while at >= 0:
            rest = text[at + len(v):]
            end = _CLAUSE_END.search(rest)
            clause = rest[:end.start()] if end else rest
            joins = v.endswith(("고", "까지"))           # 이어 주는 꼴 — 뒤에 다른 일이 온다
            if not _NOT.search(clause[:24]) and (joins or _ORDER_TAIL.match(rest)):
                return True
            at = text.find(v, at + 1)
    return False


# 화면 주소 — 우리 화면 경로만(맨 앞 / 하나, 빈·점 마디·역슬래시·퍼센트·콜론 없음). //바깥 주소·/\바깥 주소·javascript: 같은 것은 버린다
_SAFE_PATH = re.compile(r"/(?:[\w\-]+(?:\.[\w\-]+)*(?:/[\w\-]+(?:\.[\w\-]+)*)*)?")


def safe_path(path: str) -> bool:
    return bool(_SAFE_PATH.fullmatch(str(path or "")))


def _page_of(path: str):
    """열 화면 — 안내 자료의 화면. 프로젝트(/project/번호)는 있는 프로젝트일 때만(볼 권한은 그 화면이 다시 본다)."""
    from zzaimy.app import platform_guide
    path = str(path or "")
    if len(path) > 1:
        path = path.rstrip("/")
    if not safe_path(path):                               # 열린 리다이렉트 막기 — 「//바깥」 이 「/」(라이브러리) 아래로 맞춰지던 것(10-27 보안 점검)
        return None, path
    p = platform_guide.page_for(path)
    if p is None:
        return None, path
    if "{" in p.path:
        m = re.fullmatch(r"/project/(\d+)(/activity)?", path.rstrip("/"))
        return (p, path.rstrip("/")) if m else (None, path)
    if "?" in p.path and path == p.path.split("?", 1)[0]:
        # 안내 자료 주소가 물음표 꼴(라이브러리 「/?type=all」)이면 그 주소 그대로 연다 — 앞에서 물음표 뒤를 떼어 「/」(새 채팅)가 열렸다(10-27)
        return p, p.path
    return p, path


# ---- 지금 화면의 칸·단추 ----
# 안내 자료에 적어 둔 요소만 다루면 적지 않은 칸·단추는 말로만 안내하게 된다(10-27 「플랫폼 사용에 대한 제약」). 화면이 요청을 보낼 때
# 보이는 칸·단추를 번호(c1…)와 이름으로 함께 보내고, 도우미는 지금 화면에서는 그 번호로 고른다. 칸 채우기·고르기·탭·링크는 바로 해도 되고,
# 단추는 늘 마지막 단추로 본다 — 담당자가 이번 글에 그 단추 이름으로 시켰을 때만 누른다(commanded). 비밀번호·파일 칸은 화면이 보내지 않는다.
CONTROL_KINDS = {"field": "칸", "select": "고르기", "check": "체크", "tab": "탭", "link": "링크", "button": "단추"}
CONTROL_AUTO = ("field", "select", "check", "tab", "link")
MAX_CONTROLS = 80
_CONTROL_ID = re.compile(r"c\d{1,3}")


def clean_controls(raw) -> list[dict]:
    """화면이 보낸 칸·단추 목록을 정리한다 — 번호·갈래가 맞는 것만, 글은 짧게. 화면 글은 자료일 뿐이다(이름에 지시가 있어도 따르지 않는다)."""
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for x in items if isinstance(items, list) else []:
        if not isinstance(x, dict):
            continue
        cid, kind = str(x.get("id") or ""), str(x.get("kind") or "")
        if not _CONTROL_ID.fullmatch(cid) or kind not in CONTROL_KINDS or cid in seen:
            continue
        label = _clip(" ".join(str(x.get("label") or "").split()), 40)
        if not label:
            continue
        seen.add(cid)
        out.append({"id": cid, "kind": kind, "label": label, "ctx": _clip(" ".join(str(x.get("ctx") or "").split()), 40),
                    "value": _clip(" ".join(str(x.get("value") or "").split()), 40) if kind in ("field", "select", "check") else ""})
        if len(out) >= MAX_CONTROLS:
            break
    return out


def controls_text(controls: list[dict]) -> str:
    """상태·결과에 싣는 한 줄씩 — 「c3 [칸] 성명 (값 …) · 프로필」."""
    return "\n".join(f"  {c['id']} [{CONTROL_KINDS[c['kind']]}] {c['label']}" + (f" (지금 값: {c['value']})" if c.get("value") else "")
                     + (f" · {c['ctx']}" if c.get("ctx") else "") for c in controls)


def _squash_el(t: str) -> str:
    return re.sub(r"[\s·「」()\[\]'\"]+", "", str(t or "")).lower()


def _verbs_of(label: str) -> tuple[str, ...]:
    """단추 이름에서 시키는 말 — 「저장」 → 저장해·저장하고·저장까지·저장 눌러 …"""
    s = re.sub(r"(하기|하세요)$", "", " ".join(label.split())).strip()
    if not s:
        return ()
    return (s + "해", s + "하고", s + "까지", s + " 해", s + " 눌러", s + "을 눌러", s + "를 눌러", s + " 단추 눌러", s + " 버튼 눌러")


def _resolve(ctx: Ctx, p, same: bool, element: str, text: str = ""):
    """도우미가 준 요소를 (선택자, 안내 자료 Task 또는 None, 화면 요소 또는 None) 로 — 안내 자료 선택자, 지금 화면 번호(c3),
    그리고 이름(「성명」「성명 입력 칸」)을 안내 자료의 할 일 이름·지금 화면의 칸 이름에 맞춰 본다. 못 찾으면 None."""
    marks = {t.selector: t for t in p.tasks if t.selector}
    el = str(element or "").strip()
    if el in marks:
        return el, marks[el], None
    controls = {c["id"]: c for c in (ctx.controls or [])} if same else {}
    m = re.fullmatch(r"\[?data-aura-c=?\"?(c\d{1,3})\"?\]?|#?(c\d{1,3})", el)
    cid = (m.group(1) or m.group(2)) if m else ""
    if cid in controls:
        return f'[data-aura-c="{cid}"]', None, controls[cid]
    want = _squash_el(re.sub(r"(입력\s*)?(칸|란|창|단추|버튼)$", "", el))
    if len(want) < 2:
        return None
    # 안내 자료의 할 일 이름(「성명 칸 채우기」) — 하나로 정해질 때만. 「찾기」 처럼 여럿에 걸리는 말은 같은 이름일 때만 고른다
    # (10-27: 두 번째 단계 「찾기」 가 「문서 이름으로 찾기」 칸으로 잘못 맞춰졌다)
    named = [t for t in p.tasks if t.selector and want in _squash_el(t.what)]
    if len(named) > 1:                                 # 넣을 글이 있으면 칸 채우기(마지막 단추 아님), 없으면 누르는 쪽으로 좁힌다
        named = [t for t in named if (not t.final) == bool(text)] or named
    exact = [t for t in named if _squash_el(t.what) == want]
    if len(named) == 1 or len(exact) == 1:
        t = named[0] if len(named) == 1 else exact[0]
        return t.selector, t, None
    hits = [c for c in controls.values() if want == _squash_el(c["label"])] or [c for c in controls.values() if want in _squash_el(c["label"])]
    if len(hits) == 1:
        return f'[data-aura-c="{hits[0]["id"]}"]', None, hits[0]
    return None


def propose_open(ctx: Ctx, session_id: int, page: str, why: str, selector: str = "", now: bool = False, text: str = "",
                 steps: list | None = None) -> dict:
    """화면 열기 제안 — 실행할 것이 없고 그 화면으로 옮겨 가 커서가 할 단계(steps)를 낸다. 화면·요소는 안내 자료에 있는 것만.
    now 면(담당자가 열어 달라·찾아 달라·채워 달라고 시킨 때) 화면이 바로 옮겨 가 단계를 차례로 한다.
    단계마다 바로 해도 되는지(ok)를 서버가 정한다 — 열기·찾기·칸 채우기(Task.auto)는 now 면 바로,
    마지막 단추(Task.final — 저장·보내기·지우기·등록)는 담당자가 이번 요청에 직접 친 글(ctx.said)에 그 Task 의 동사가 있을 때만(commanded).
    그 밖의 요소(자료를 바꾸는 단추)는 가리키기만 한다. 마지막 단추 뒤의 단계는 버린다."""
    path, _, anchor = str(page or "").partition("#")
    p, path = _page_of(path.split("?", 1)[0])
    if p is None:
        raise OpRefused("안내 자료에 있는 화면 주소만 연다 — platform_help 결과의 주소를 쓴다(프로젝트는 /project/번호).")
    if ctx.role not in p.roles:
        raise OpRefused(f"「{p.title}」 은(는) 이 계정으로 열 수 없는 화면이다 — 열 수 있는 계정(관리자 등)에게 요청하라고 안내한다.")
    url = path + (f"#{anchor}" if re.fullmatch(r"[\w-]{1,40}", anchor or "") else "")
    here = (ctx.page or "").split("?", 1)[0].split("#", 1)[0].rstrip("/") or ("/" if ctx.page else "")
    same = bool(here) and platform_guide_page(here) is p and ("{" not in p.path or here == path.rstrip("/"))
    want = ([{"element": selector, "text": text}] if selector else []) + [x for x in (steps or []) if isinstance(x, dict)]
    out: list[dict] = []
    missed: list[str] = []
    for x in want:
        got = _resolve(ctx, p, same, str(x.get("element") or ""), str(x.get("text") or ""))
        if got is None:
            missed.append(str(x.get("element") or "")[:30])
            continue
        if len(out) >= MAX_ACT_STEPS:
            break
        sel, task, ctl = got
        if task is not None:
            auto, final, what, verbs = task.auto, task.final, task.what, task.verbs
        else:                                            # 지금 화면의 칸·단추 — 단추는 늘 마지막 단추
            auto = ctl["kind"] in CONTROL_AUTO
            final = not auto
            what = (ctl["label"] + (" 칸 채우기" if ctl["kind"] == "field" else " 고르기" if ctl["kind"] in ("select", "check") else ""))[:40]
            verbs = _verbs_of(ctl["label"]) if final else ()
        ok = bool(now) and (auto or (final and commanded(ctx.said, verbs)))
        out.append({"sel": sel, "text": _clip(" ".join(str(x.get("text") or "").split()), 80), "final": final,
                    "auto": auto, "ok": ok, "what": what})
        if final:
            break
    if want and not out and same:                     # 같은 화면에서 고른 요소를 못 찾았다 — 아무것도 안 하고 「했다」 고 말하지 않게, 고를 수 있는 것을 보이고 다시 고르게
        raise OpRefused(f"고른 요소 {', '.join(missed) or '없음'} 을(를) 이 화면에서 찾지 못했다 — steps 의 element 에 아래 가운데 하나를 그대로 쓴다.\n"
                        + _elements_here(ctx, p, same))
    if same and not out:
        raise OpRefused("이미 그 화면이다 — 열기 단추는 내지 않았다. 할 일이 있으면 steps 에 아래 요소 가운데 하나를 넣고, 없으면 finish 로 안내한다.\n"
                        + _elements_here(ctx, p, same))
    items = load(ctx.db, session_id)
    if sum(1 for x in items if x.get("status") == "pending") >= MAX_PENDING:
        raise OpRefused(f"이번 요청에서 제안은 {MAX_PENDING}개까지다 — finish 로 알린다.")
    first = out[0] if out else {"sel": "", "text": ""}
    item = {"id": secrets.token_hex(6), "kind": "go", "op": "open", "label": f"{p.title} 열기", "args": {}, "plan": _clip(why, 200) or p.purpose,
            "screen": url, "target": {"path": path, "anchor": anchor if url.endswith("#" + anchor) else "", "selector": first["sel"]},
            "act": {"now": bool(now), "text": first["text"], "steps": out, "here": same},
            "status": "pending", "by": ctx.user, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    # 바로 하기는 여러 개가 순서대로 이어진다(화면 이동 → 다른 화면에서 이어 하기) — 화면이 낸 차례(at·저장 순서)대로 한다
    _save(ctx.db, session_id, items + [item])
    return item


def _elements_here(ctx: Ctx, p, same: bool) -> str:
    lines = [f"  {t.selector} — {t.what}" for t in p.tasks if t.selector]
    if same and ctx.controls:
        lines.append("지금 화면의 칸·단추(번호로 쓴다):\n" + controls_text(ctx.controls))
    return "\n".join(lines) or "  (고를 요소가 없다)"


def platform_guide_page(path: str):
    from zzaimy.app import platform_guide
    return platform_guide.page_for(path)


RESULTS = {"typed": "입력함", "picked": "고름", "clicked": "누름", "opened": "옮겨 감", "missing": "자리를 찾지 못함",
           "disabled": "단추가 꺼져 있어 누르지 못함", "pointed": "가리키기만 함(직접 눌러 주세요)", "blocked": "직접 눌러 주세요"}


def report(db, session_id: int, pid: str, results: list) -> str:
    """화면(AURA 커서)이 실제로 한 결과를 받아 대화에 한 줄로 남긴다 — 완료 문구는 이 결과로만 쓴다(모델 답은 예정형).
    결과는 단계 번호와 결과 갈래만 받고, 단계 이름·넣은 말은 서버가 낸 제안에서 읽는다(화면이 보낸 글을 그대로 싣지 않는다). 제안마다 한 번."""
    items = load(db, session_id)
    item = next((p for p in items if p.get("id") == pid and p.get("kind") == "go"), None)
    if item is None:
        raise KeyError(pid)
    if item.get("reported"):
        return ""
    steps = (item.get("act") or {}).get("steps") or []
    parts: list[str] = []
    for r in (results if isinstance(results, list) else [])[:MAX_ACT_STEPS + 1]:
        if not isinstance(r, dict):
            continue
        code = str(r.get("result") or "")
        if code not in RESULTS:
            continue
        i = r.get("step")
        if code == "opened" or not isinstance(i, int) or not (0 <= i < len(steps)):
            if code == "opened":
                parts.append(f"{item['label'].removesuffix(' 열기')} 화면으로 옮겨 감")
            continue
        st = steps[i]
        what = st.get("what") or "단계"
        parts.append(f"{what}" + (f" 「{st['text']}」" if st.get("text") and code in ("typed", "picked", "clicked") else "") + f"({RESULTS[code]})")
    if not parts:
        return ""
    item["reported"] = True
    item["results"] = [str(x) for x in parts]
    _save(db, session_id, items)
    return "화면에서 한 일: " + " · ".join(parts)


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
        raise OpRefused("이미 실행했거나 지난 제안입니다." if item.get("status") == "done" else "지난 제안입니다. 다시 요청해 주세요.")
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
