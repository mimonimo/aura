"""조직 관리(/team)의 트리 자료와 부서 고치기 — 부서 위계를 단계 제한 없이 보이고 고친다(ADR-0066 「부서 단위와 등급」).

위계는 관리자가 확정한 관계(dept_dictionary 의 dept_parents.json)만 쓴다. 부서 만들기·지우기는 사용자·계정 화면과 같은 길
(rag_spaces.create_dept_rag · user_admin.delete_dept)을 쓴다. 이름 바꾸기는 rename_dept 한 함수가 계정 부서·겸직 등급·
위계·부서 RAG 설정·문서 부서·부서 공용 설정·사업 관리의 부서 지정까지 함께 바꾼다(바꾸기 전 판은 history/).

권한(서버에서 매번 다시 본다):
- 관리자: 모두 고친다(더하기·이름 바꾸기·지우기·옮기기)와 총괄 책임자 지정.
- 총괄 책임자: 자기가 맡은 처·단 아래(하위 부서)만 — 그 처·단 자체와 다른 처·단은 보기만.
- 그 밖의 교직원: 보기만. 학생은 열지 못한다.

경로: GET /api/org(nodes: 부서마다 바로 위 부서·권한) · POST /org/move · /org/rename · /org/delete · /org/create · /org/head (JSON 본문)
"""
from __future__ import annotations

import datetime
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import logging

from starlette.concurrency import run_in_threadpool

from zzaimy.app import dept_dictionary, grades, org_lock, rag_spaces

router = APIRouter()
log = logging.getLogger(__name__)

TOP_LEVEL = 4                     # dept_dictionary.LEVEL — 처·본부·실·원·단·사업 조직·기관 급이 맨 위(처·단)
_VIEW_ROLES = ("staff", "dev", "professor", "head")


# ---------------------------------------------------------------- 권한

class Actor:
    """요청한 사람이 조직도에서 할 수 있는 것. admin 이면 전부, 아니면 roots(자기가 총괄 책임자인 부서) 아래 하위 부서만."""

    def __init__(self, request: Request):
        st = request.app.state
        if getattr(request.state, "role", "") not in _VIEW_ROLES:
            raise HTTPException(403, "교직원 계정에서 볼 수 있습니다")
        self.user = getattr(request.state, "user", "") or ""
        self.data_dir = getattr(st, "data_dir", None)
        accounts = getattr(st, "accounts", None) or {}
        acct = accounts.get(self.user) or {}
        g = grades.request_grade(request)
        self.admin = g == "admin"
        self.roots: set[str] = set()
        self.covered: set[str] = set()
        if self.admin:
            return
        if getattr(request.state, "view_as", None):            # 테스트 보기 — 고른 등급을 소속 부서에
            d0 = str(getattr(request.state, "dept", "") or "").strip()
            if d0 and g == "head":
                self.roots = {d0}
                self.covered = {d0, *(dept_dictionary.children_map(self.data_dir).get(d0) or ())}
            return
        self.roots = {d for d in grades.depts_of(self.user, acct, self.data_dir)
                      if grades.grade_in(self.user, acct, d, self.data_dir) == "head"}
        self.covered = grades.covered_depts(self.user, acct, self.data_dir, "head") if self.roots else set()

    def can_edit(self, dept: str) -> bool:
        """이 부서를 고칠 수 있나(이름 바꾸기·지우기·옮기기) — 총괄 책임자는 맡은 처·단의 하위 부서만."""
        return self.admin or (dept in self.covered and dept not in self.roots)

    def can_hold(self, parent: str) -> bool:
        """이 처·단 아래에 부서를 놓거나 만들 수 있나 — 맨 위(parent 빈 값)는 관리자만."""
        return self.admin if not parent else (self.admin or parent in self.covered)

    def can_lend(self, dept: str) -> bool:
        """이 부서의 부서 RAG 를 다른 부서에 열 수 있나 — 관리자, 또는 그 부서를 맡은 총괄 책임자(맡은 처·단과 그 아래, ADR-0066)."""
        return self.admin or dept in self.covered

    @property
    def can_any(self) -> bool:
        return self.admin or bool(self.covered)


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise HTTPException(403, msg)


# ---------------------------------------------------------------- 나무 만들기

def _kind(name: str) -> str:
    return dept_dictionary.kind_of(name)


def is_top(name: str, over: dict, kids: dict) -> bool:
    """맨 위(처·단)인가 — 관리자가 「상위 없음」으로 정했거나(빈 값), 아래에 부서가 있거나, 정하지 않았으면 이름 갈래가 처·단 급."""
    if name in over:
        return not over[name]
    if kids.get(name):
        return True
    return dept_dictionary.LEVEL.get(_kind(name), 0) >= TOP_LEVEL


def _top_of(name: str, over: dict) -> str:
    """맨 위 조상(두 단계로 볼 때 이 부서가 들어갈 처·단). 상위가 없으면 빈 값."""
    seen, cur = {name}, over.get(name, "")
    top = ""
    while cur and cur not in seen:
        top = cur
        seen.add(cur)
        cur = over.get(cur, "")
    return top


def _people(accounts: dict, dept: str, data_dir, detail: bool = True, depts_of: dict | None = None) -> dict:
    """그 부서의 사람 수(소속·겸직)와 그 부서에서 바로 총괄 책임자·사업 책임자인 사람.
    보안: detail 이 아니면(관리자·그 부서를 맡은 총괄 책임자가 아니면) 사람 수와 책임자 표시 이름만 — 아이디·겸직 여부·겸직 수는 뺀다."""
    n = extra = 0
    heads, leads = [], []
    for uid, a in sorted(accounts.items()):
        if a.get("role") not in grades.STAFF or not a.get("active", True):
            continue
        primary = str(a.get("dept") or "").strip() == dept
        if primary:
            n += 1
        elif dept in (depts_of[uid] if depts_of is not None and uid in depts_of else grades.depts_of(uid, a, data_dir)):
            extra += 1
        else:
            continue
        g = grades.grade_in(uid, a, dept, data_dir)
        shown = (a.get("name") or "").strip()
        who = {"user": uid, "name": shown or uid, "extra": not primary} if detail else {"name": shown or "이름 없음"}
        if g == "head":
            heads.append(who)
        elif g == "lead":
            leads.append(who)
    if not detail:
        return {"n": n, "heads": heads, "leads": leads}
    return {"n": n, "n_extra": extra, "heads": heads, "leads": leads}


def tree(st, actor: Actor) -> dict:
    from zzaimy.app.user_admin import known_depts
    accounts = getattr(st, "accounts", None) or {}
    dd = getattr(st, "data_dir", None)
    depts = known_depts(st.db, accounts, dd)
    over = dept_dictionary.parents_override(dd)
    kids = dept_dictionary.children_map(dd)
    dic = dept_dictionary.load(dd)
    n_docs: dict[str, int] = {}
    if depts:
        with st.db._conn() as c:
            n_docs = {str(r[0]): int(r[1]) for r in c.execute(
                f"SELECT dept, COUNT(*) FROM documents WHERE dept IN ({','.join('?' * len(depts))}) GROUP BY dept", depts)}
    known = set(depts)
    # 사람마다 속한 부서(소속·겸직)는 한 번만 — 부서마다 다시 읽으면 부서 수 × 사람 수만큼 설정 파일을 읽는다
    member_of = {uid: set(grades.depts_of(uid, a, dd)) for uid, a in accounts.items()
                 if a.get("role") in grades.STAFF and a.get("active", True)}
    cache: dict[str, dict] = {}

    def node(d: str) -> dict:
        if d in cache:
            return dict(cache[d])
        u = dic.by_name.get(dic.normalize(d) or d) or {}
        k = _kind(d)
        detail = actor.admin or d in actor.covered          # 자세한 값(아이디·겸직·문서 수)은 관리자와 그 부서를 맡은 총괄 책임자만
        row = {"name": d, "kind": dept_dictionary.KINDS.get(k, ""), "aliases": list(u.get("aliases") or [])[:6],
               "can_edit": actor.can_edit(d), **_people(accounts, d, dd, detail, member_of)}
        if detail:
            row["docs"] = n_docs.get(d, 0)
        cache[d] = row
        return dict(row)

    tops, loose = [], []
    placed: dict[str, list[str]] = {}
    for d in depts:
        p = over.get(d, "")
        if p and p in known:
            t = _top_of(d, over)
            placed.setdefault(t if t in known else p, []).append(d)
    for d in depts:
        if over.get(d, "") in known:
            continue
        if is_top(d, over, kids):
            tops.append(d)
        else:
            loose.append(d)
    out_tops = []
    for t in tops:
        row = node(t)
        row["can_hold"] = actor.can_hold(t)
        row["children"] = []
        for c in sorted(placed.get(t, [])):
            ch = node(c)
            mid = over.get(c, "")
            ch["via"] = mid if mid != t else ""            # 세 단계로 남은 옛 관계 — 「○○ 아래」 로 적는다
            row["children"].append(ch)
        out_tops.append(row)
    shown = set(tops)
    loose += [c for t, cs in placed.items() if t not in shown for c in cs]     # 맨 위 조상을 찾지 못한 관계(옛 기록) — 정리할 것으로
    # 규정 제안 — 규정에서 뽑은 상위가 지금 위계와 다른 곳만(제안한 상위가 팀이면 그 처·단으로)
    sugg = []
    raw = dept_dictionary.suggested_parents(dd)
    tops_set = set(tops)
    for d in depts:
        sp = raw.get(dic.normalize(d) or d, "")
        if not sp or sp not in known or sp == d:
            continue
        if sp not in tops_set:
            sp = _top_of(sp, over) or ""
        cur = over.get(d, "")
        if sp and sp != cur and sp != d and sp in tops_set and not kids.get(d):
            sugg.append({"dept": d, "parent": sp, "can": actor.can_edit(d) and actor.can_hold(sp)})
    # 조직 관리 트리 — 부서마다 바로 위 부서(parent, 없으면 맨 위). 단계 수에 제한이 없다. 화면은 이것으로 세로 트리를 그린다
    team_all, team_depts = _team_view(st, actor)
    lend_cfg = rag_spaces.config(dd)
    mins = (lend_cfg.get("min_grade") or {}) if actor.admin else {}
    nodes = []
    for d in depts:
        row = node(d)
        p = over.get(d, "")
        row["parent"] = p if p in known and p != d else ""
        row["can_hold"] = actor.can_hold(d)
        row["team"] = team_all or d in team_depts           # 구성원을 볼 수 있나(관리자·총괄 책임자·그 부서 사업 책임자)
        if actor.admin:
            row["min_grade"] = mins.get(f"dept:{d}", "")
        if actor.can_lend(d):                              # 부서 RAG 를 열어 준 부서 — 열 수 있는 사람에게만
            row["can_lend"] = True
            row["lend_to"] = rag_spaces.receivers(lend_cfg, d)
        nodes.append(row)
    out = {"nodes": nodes, "tops": out_tops, "loose": [node(d) for d in loose], "suggestions": sugg,
           "admin": actor.admin, "can_any": actor.can_any, "can_top": actor.admin}
    if actor.can_any:
        out["roots"] = sorted(actor.roots)
    return out


def _team_view(st, actor: "Actor") -> tuple[bool, set[str]]:
    """구성원을 볼 수 있는 부서 — (전부인가, 부서 모음). 관리자는 전부, 총괄 책임자는 맡은 부서(하위 포함), 사업 책임자는 그 부서."""
    if actor.admin:
        return True, set()
    accounts = getattr(st, "accounts", None) or {}
    acct = accounts.get(actor.user) or {}
    dd = getattr(st, "data_dir", None)
    out = set(actor.covered)
    for d in grades.depts_of(actor.user, acct, dd):
        if grades.grade_in(actor.user, acct, d, dd) == "lead":
            out.add(d)
    return False, out


@router.get("/api/org")
def api_org(request: Request):
    actor = Actor(request)
    return JSONResponse(tree(request.app.state, actor))


# ---------------------------------------------------------------- 고치기

def _locked(fn, request: Request, b: dict):
    """조직도 고치기 — 권한·검사·쓰기·되돌리기를 공용 잠금(org_lock) 안에서 한 번에. 잠금은 스레드 풀에서 잡는다(이벤트 루프를 막지 않게)."""
    with org_lock.LOCK:
        return fn(request, b)


async def _body(request: Request) -> dict:
    try:
        got = await request.json()
    except Exception:
        got = None
    if not isinstance(got, dict):
        raise HTTPException(400, "요청 형식이 올바르지 않습니다")
    return got


def _name(v) -> str:
    return " ".join(str(v or "").split())


def _known(st) -> list[str]:
    from zzaimy.app.user_admin import known_depts
    return known_depts(st.db, getattr(st, "accounts", None) or {}, getattr(st, "data_dir", None))


def _fail(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=code)


def _check_parent(over: dict, kids: dict, dept: str, parent: str) -> str:
    """parent 아래에 dept 를 둘 수 있나(빈 값은 맨 위) — 문제면 문장. 단계 수는 제한하지 않는다(조직 관리 트리, 10-26).
    순환(자기 아래 부서를 상위로)은 dept_dictionary.set_parent 가 쓰기 전에 다시 본다."""
    if not parent:
        return ""
    if parent == dept:
        return "자기 자신 아래로 옮길 수 없습니다"
    up, seen = over.get(parent, ""), {parent}
    while up and up not in seen:
        if up == dept:
            return f"「{parent}」 은(는) 「{dept}」 아래 부서라 그 아래로 옮길 수 없습니다"
        seen.add(up)
        up = over.get(up, "")
    return ""


def _check_place(st, dept: str, parent: str) -> str:
    """dept 를 parent 아래(빈 값이면 맨 위)에 둘 수 있나. 문제면 문장."""
    dd = getattr(st, "data_dir", None)
    return _check_parent(dept_dictionary.parents_override(dd), dept_dictionary.children_map(dd), dept, parent)


@router.post("/org/move")
async def org_move(request: Request):
    b = await _body(request)
    return await run_in_threadpool(_locked, _org_move, request, b)


def _org_move(request: Request, b: dict):
    """부서를 처·단 아래로(parent) 또는 맨 위로(parent 빈 값) 옮긴다. unset 이면 관리자가 정하기 전으로(되돌리기)."""
    st = request.app.state
    actor = Actor(request)
    dept, parent, unset = _name(b.get("dept")), _name(b.get("parent")), bool(b.get("unset"))
    known = _known(st)
    if dept not in known or (parent and parent not in known):
        return _fail("부서와 처·단은 조직도에 있는 이름으로 골라 주세요")
    dd = getattr(st, "data_dir", None)
    if dd is None:
        return _fail("설정 위치가 없습니다")
    over = dept_dictionary.parents_override(dd)
    before = {"dept": dept, "parent": over.get(dept, ""), "unset": dept not in over}
    _need(actor.can_edit(dept), "이 부서는 옮길 수 없습니다 — 맡은 처·단의 하위 부서만 고칩니다")
    if "expect" in b:
        # 되돌리기 — 옮긴 뒤 그 사이에 다른 변경(다시 옮김·지움·이름 바꿈)이 있으면 하지 않는다. 권한은 아래에서 지금 기준으로 다시 본다
        exp = b.get("expect") or {}
        now = None if dept not in over else over[dept]
        want = None if exp.get("unset") else _name(exp.get("parent"))
        if now != want:
            return _fail("그 사이에 조직도가 바뀌어 되돌리지 않았습니다 — 화면을 새로 보고 다시 옮겨 주세요", 409)
    if unset:
        # 관리자가 정하기 전으로 — 이름 갈래에 따라 처·단이 되거나 상위 없는 부서가 되므로 관리자만
        _need(actor.admin, "되돌리기 전 자리는 관리자만 정합니다")
        _write_all(st, (), lambda: dept_dictionary.clear_parent(dd, dept))
        return {"ok": True, "msg": f"{dept} — 옮기기 전으로 되돌렸습니다", "undo": None}
    _need(actor.can_hold(parent), "그 자리에는 놓을 수 없습니다 — 맨 위(처·단)로 올리기와 다른 처·단 아래로 옮기기는 관리자가 합니다")
    err = _check_place(st, dept, parent)                 # 두 단계 — 쓰기 전에 모두 본다(순환은 set_parent 가 쓰기 전에 본다)
    if err:
        return _fail(err)
    err = _write_all(st, (), lambda: dept_dictionary.set_parent(dd, dept, parent))
    if err:
        return _fail(err)
    msg = f"{dept} → {parent} 아래로 옮겼습니다" if parent else f"{dept} → 처·단(맨 위)으로 올렸습니다"
    if "expect" in b:
        return {"ok": True, "msg": f"{dept} — 옮기기 전으로 되돌렸습니다", "undo": None}
    # 되돌리기 — 옮기기 전 자리와 지금 자리(expect). 정하기 전으로 돌리는 것(unset)은 관리자만 받는다
    undo = {**before, "expect": {"parent": parent, "unset": False}}
    if before["unset"] and not actor.admin:
        undo = None
    return {"ok": True, "msg": msg, "undo": undo}


def _backup_accounts(data_dir) -> None:
    if data_dir is None:
        return
    p = Path(data_dir) / "accounts.json"
    if p.is_file():
        hist = Path(data_dir) / "history"
        hist.mkdir(exist_ok=True)
        dst = hist / f"accounts.{time.strftime('%Y%m%d%H%M%S')}.json"
        shutil.copy2(p, dst)
        dst.chmod(0o600)


class Snapshot:
    """쓰기 전에 조직도가 건드리는 저장값을 잡아 두고, 쓰다가 실패하면 되돌린다 — 중간까지만 쓰인 상태가 남지 않게.
    대상: 계정 표(메모리·파일), rag_spaces.json, dept_parents.json, program_admin.json, 「열쇠:부서」 꼴 설정, 주어진 부서들의 문서·조각·
    라이브러리 폴더 부서. 되돌리기는 「내가 쓴 뒤 그대로인 것」만 — 파일은 쓴 직후 해시(org_lock 쓰기 기록)와 지금이 같을 때,
    문서·폴더는 지금 부서가 내가 바꾼 값(targets)일 때만. 그 밖은 덮지 않고 conflicts 에 남긴다."""

    FILES = ("rag_spaces.json", dept_dictionary.PARENTS_FILE, "program_admin.json", "accounts.json")

    def __init__(self, st, depts: tuple[str, ...] = (), targets: tuple[str, ...] = ()):
        import copy
        self.st = st
        self.dd = getattr(st, "data_dir", None)
        self.targets = set(targets)
        self.accounts = copy.deepcopy(getattr(st, "accounts", None) or {})
        self.files: dict[str, bytes | None] = {}
        if self.dd is not None:
            for f in self.FILES:
                p = Path(self.dd) / f
                self.files[f] = p.read_bytes() if p.is_file() else None
        self.settings = dict(st.db.all_settings() or {}) if st.db is not None else {}
        self.docs: list[tuple] = []
        self.folders: list[tuple] = []
        if st.db is not None and depts:
            q = ",".join("?" * len(depts))
            with st.db._conn() as c:
                self.docs = [tuple(r) for r in c.execute(f"SELECT id, dept, access_level FROM documents WHERE dept IN ({q})", depts)]
                try:
                    self.folders = [tuple(r) for r in c.execute(f"SELECT id, dept FROM library_folders WHERE dept IN ({q})", depts)]
                except Exception:
                    self.folders = []
        self.conflicts: list[str] = []

    def restore(self, journal: dict) -> None:
        """되돌린다. 남의 쓰기가 끼어든 것은 conflicts 에 남기고, 되돌리기 자체가 실패하면 예외를 그대로 올린다(숨기지 않음)."""
        import hashlib
        st = self.st
        accounts_back = True
        if self.dd is not None:
            for f, data in self.files.items():
                p = Path(self.dd) / f
                pre = hashlib.sha256(data).hexdigest() if data is not None else None
                cur = org_lock.digest(p)
                if cur == pre:
                    continue                                   # 바뀌지 않았다
                mine = journal.get(str(p.resolve()), "missing")
                if mine != cur:                                # 내가 쓴 뒤 다른 쪽이 썼거나 내가 쓴 것이 아니다 — 덮지 않는다
                    self.conflicts.append(f)
                    if f == "accounts.json":
                        accounts_back = False
                    continue
                if data is None:
                    p.unlink(missing_ok=True)
                else:
                    tmp = p.with_suffix(".restore")
                    tmp.write_bytes(data)
                    tmp.replace(p)
            dept_dictionary._cache.clear()
            try:
                from zzaimy.app import program_admin
                program_admin._CACHE.clear()
            except Exception:
                pass
        live = getattr(st, "accounts", None)
        if live is not None and accounts_back:
            live.clear()
            live.update(self.accounts)
        if st.db is not None:
            now = dict(st.db.all_settings() or {})
            for k in set(now) | set(self.settings):
                if now.get(k, "") != self.settings.get(k, ""):
                    st.db.set_setting(k, self.settings.get(k, ""))
            for did, dept, level in self.docs:
                d = st.db.get_document(did)
                if d is not None and d["dept"] in self.targets:
                    st.db.set_document_scope(did, dept=dept, access_level=level)
            if self.folders and self.targets:
                q = ",".join("?" * len(self.targets))
                with st.db._conn() as c:
                    for fid, dept in self.folders:
                        c.execute(f"UPDATE library_folders SET dept = ? WHERE id = ? AND dept IN ({q})", (dept, fid, *self.targets))


def _write_all(st, depts: tuple[str, ...], fn, targets: tuple[str, ...] = ()):
    """검사를 모두 마친 뒤 부르는 쓰기 묶음(잠금 안에서 불린다). 도중에 실패하면 내가 쓴 뒤 그대로인 것만 되돌리고 500.
    되돌리지 못한 것(다른 쪽이 그 사이 쓴 파일)이 있거나 되돌리기 자체가 실패하면 기록하고 그 사실을 500 으로 알린다(fail-closed)."""
    snap = Snapshot(st, depts, targets)
    journal = org_lock.start_journal()
    try:
        return fn()
    except HTTPException:
        raise
    except Exception:
        log.exception("조직도 쓰기 실패 — 되돌립니다")
        try:
            snap.restore(journal)
        except Exception:
            log.exception("조직도 되돌리기 실패 — 저장값이 중간 상태일 수 있습니다")
            raise HTTPException(500, "저장하다 멈췄고 되돌리기도 실패했습니다 — 관리자에게 알려 주세요(history/ 의 이전 판으로 복구)")
        if snap.conflicts:
            log.error("조직도 되돌리기: 그 사이 다른 쓰기가 있어 덮지 않은 파일 %s", snap.conflicts)
            raise HTTPException(500, "저장하다 멈췄고, 그 사이 다른 변경이 있던 파일(" + ", ".join(snap.conflicts)
                                + ")은 되돌리지 않았습니다 — 관리자에게 알려 주세요")
        raise HTTPException(500, "저장하다 멈춰 바꾸기 전으로 되돌렸습니다 — 잠시 뒤 다시 해 주세요")
    finally:
        org_lock.stop_journal()


def traces(st, name: str) -> list[str]:
    """지금 없는 부서 이름에 남은 권한 흔적 — 같은 이름으로 다시 만들거나 이름을 바꿔 차지하면 옛 권한이 되살아나는 것들.
    계정 겸직 등급(dept_grades)·부서 열어 주기(dept_grants 주는 쪽·받는 쪽)·최소 등급(min_grade dept:)·확정 위계(dept_parents)·
    사업 관리의 부서 지정(dept:)·부서 공용 구글 설정. 돌려주는 값은 사람이 읽을 갈래 목록(빈 목록이면 흔적 없음)."""
    dd = getattr(st, "data_dir", None)
    key = f"dept:{name}"
    out = []
    accounts = getattr(st, "accounts", None) or {}
    n = sum(1 for a in accounts.values() if name in (a.get("dept_grades") or {}))
    if n:
        out.append(f"계정 겸직 등급 {n}건")
    cfg = rag_spaces.config(dd)
    dg_ = cfg.get("dept_grants") or {}
    n = (1 if name in dg_ else 0) + sum(1 for to in dg_.values() if name in (to or []))
    if n:
        out.append(f"부서 열어 주기 {n}건")
    if key in (cfg.get("min_grade") or {}):
        out.append("RAG 최소 등급")
    over = dept_dictionary.parents_override(dd)
    if name in over or name in over.values():
        out.append("상위 부서 관계")
    try:
        from zzaimy.app import program_admin
        store = program_admin.load_store(dd)
        n = sum(1 for b in ("rows", "added") for r in (store.get(b) or {}).values()
                for f in program_admin.ROLES if key in (r.get(f) or []))
        if n:
            out.append(f"사업 관리 지정 {n}건")
    except Exception:
        pass
    if st.db is not None and any(k.partition(":")[2] == name and k.partition(":")[0].startswith("google_")
                                 and k.partition(":")[0] != "google_account" and (v or "").strip()
                                 for k, v in (st.db.all_settings() or {}).items()):
        out.append("부서 공용 구글 설정")
    return out


def purge_traces(st, name: str) -> list[str]:
    """traces 가 찾는 흔적을 모두 지운다(이전 판은 history/·.bak 에). 지운 갈래 목록을 돌려준다."""
    found = traces(st, name)
    if not found:
        return []
    dd = getattr(st, "data_dir", None)
    key = f"dept:{name}"
    accounts = getattr(st, "accounts", None) or {}
    if any(name in (a.get("dept_grades") or {}) for a in accounts.values()):
        _backup_accounts(dd)
        for a in accounts.values():
            dg = a.get("dept_grades") or {}
            if name in dg:
                dg.pop(name, None)
                if not dg:
                    a.pop("dept_grades", None)
        org_lock.save_accounts(st)
    if dd is not None:
        cfg = rag_spaces.config(dd)
        gr = {o: [x for x in (to or []) if x != name] for o, to in (cfg.get("dept_grants") or {}).items() if o != name}
        gr = {o: to for o, to in gr.items() if to}
        mg = {k: v for k, v in (cfg.get("min_grade") or {}).items() if k != key}
        if gr != (cfg.get("dept_grants") or {}) or mg != (cfg.get("min_grade") or {}):
            cfg["dept_grants"] = gr
            if "min_grade" in cfg:
                cfg["min_grade"] = mg
            rag_spaces.save_config(cfg, dd)
        dept_dictionary.forget_dept(dd, name)
    try:
        from zzaimy.app import program_admin
        store = program_admin.load_store(dd)
        changed = False
        for b in ("rows", "added"):
            for r in (store.get(b) or {}).values():
                for f in program_admin.ROLES:
                    if key in (r.get(f) or []):
                        r[f] = [x for x in r[f] if x != key]
                        changed = True
        if changed:
            program_admin.save_store(store, dd)
    except Exception:
        pass
    if st.db is not None:
        for k, v in (st.db.all_settings() or {}).items():
            pre, _, d = k.partition(":")
            if d == name and pre.startswith("google_") and pre != "google_account" and (v or "").strip():
                st.db.set_setting(k, "")
    return found


RECONCILE_MAX = 5          # 한 번에 이보다 많은 부서가 「없어짐」 으로 잡히면 설정이 깨진 것으로 보고 멈춘다(사람이 지운 때는 delete_dept 가 explicit 로)


def reconcile(st, *, explicit: bool = False) -> dict:
    """부서 흔적 맞추기 — 지금 있는 부서(rag_spaces.known_depts: 부서 RAG·폴더 짝·공간·계정 소속) 밖 이름이 남은 곳을 모두 정리한다.
    화면에서 지운 때만이 아니라 설정 파일을 손으로 고치거나 지운 때도 「어디는 되어 있고 어디는 안 된」 상태가 남지 않게(10-27 사용자),
    부서 지우기 끝(explicit)과 앱의 10분 주기에 돈다. 보안: 설정을 못 읽거나 부서가 비어 보이면 하지 않고, 주기 실행은 권한을 좁히는 정리만 —
    문서를 공통으로 돌리는 것(열람이 넓어짐)은 사람이 지운 때(explicit)만, 한 번에 RECONCILE_MAX 넘게 없어졌으면 멈추고 기록만. 정리: purge_traces(겸직 등급·부서 열어 주기·최소 등급·상위 관계·사업 관리 지정·구글 설정),
    빈 값으로 남은 부서 구글 설정 열쇠 지우기, 프로젝트 공유의 그 부서 줄 빼기, 그 부서 문서는 공통으로. 문서함 부서 폴더는 비었으면 지우고,
    문서가 남았으면 지우지 않고 돌려준다(화면이 알린다). 돌려주는 값: {orphans: [이름], docs: 공통으로 돌린 문서 수, folders_left: [이름]}."""
    import json as _json

    dd = getattr(st, "data_dir", None)
    accounts = getattr(st, "accounts", None) or {}
    # 보안(fail-closed): 부서 설정을 못 읽으면(파일 없음·깨짐) 「지금 있는 부서」 가 비어 보여 모든 흔적을 지우게 된다 — 그때는 아무것도 하지 않는다
    try:
        import json as _j0
        _j0.loads((Path(dd) / "rag_spaces.json").read_text(encoding="utf-8")) if dd is not None else None
    except (OSError, ValueError):
        return {"orphans": [], "docs": 0, "folders_left": [], "skipped": "부서 설정을 읽지 못함"}
    cfg = rag_spaces.config(dd)
    live = set(rag_spaces.known_depts(cfg, accounts)) | {"공통"}
    if len(live) <= 1:
        return {"orphans": [], "docs": 0, "folders_left": [], "skipped": "지금 있는 부서가 없음"}
    names: set[str] = set()
    for a in accounts.values():
        names |= set((a.get("dept_grades") or {}).keys())
    for o, to in (cfg.get("dept_grants") or {}).items():
        names |= {o, *(to or [])}
    names |= {k[5:] for k in (cfg.get("min_grade") or {}) if str(k).startswith("dept:")}
    if dd is not None:
        over = dept_dictionary.parents_override(dd)
        names |= set(over) | set(over.values())
    try:
        from zzaimy.app import program_admin
        store = program_admin.load_store(dd)
        for b in ("rows", "added"):
            for r in (store.get(b) or {}).values():
                for f in program_admin.ROLES:
                    names |= {str(x)[5:] for x in (r.get(f) or []) if str(x).startswith("dept:")}
    except Exception:
        pass
    settings = (st.db.all_settings() or {}) if st.db is not None else {}
    for k, v in settings.items():
        pre, _, d = k.partition(":")
        if pre.startswith("google_") and pre != "google_account" and d:
            if (v or "").strip():
                names.add(d)
            elif d not in live:
                st.db.delete_setting(k)                       # 지운 부서의 빈 열쇠 — 남겨 두지 않는다
    shares = {k: v for k, v in settings.items() if k.startswith("project_shares:")}
    for v in shares.values():
        try:
            names |= {str(i.get("to"))[5:] for i in (_json.loads(v or "{}").get("items") or []) if str(i.get("to", "")).startswith("dept:")}
        except Exception:
            pass
    docs_moved = 0
    if st.db is not None:
        with st.db._conn() as conn:
            names |= {str(r[0]) for r in conn.execute("SELECT DISTINCT dept FROM documents").fetchall() if r[0]}
    orphans = sorted(n for n in names - live if n)
    if not explicit and len(orphans) > RECONCILE_MAX:
        logging.getLogger("zzaimy.app.org").warning("부서 흔적 맞추기 멈춤 — 없어진 부서가 %d개(설정 이상 의심): %s", len(orphans), orphans[:20])
        return {"orphans": orphans, "docs": 0, "folders_left": [], "skipped": f"없어진 부서 {len(orphans)}개 — 설정 확인 필요"}
    docs_left = []
    for n in orphans:
        purge_traces(st, n)                               # 권한을 좁히는 정리(겸직·부서 열어 주기·최소 등급·위계·사업 지정·구글 설정)
        if st.db is not None:
            with st.db._conn() as conn:
                ids = [int(r[0]) for r in conn.execute("SELECT id FROM documents WHERE dept = ?", (n,)).fetchall()]
            if ids and explicit:                          # 공통으로 돌리면 열람이 넓어진다 — 사람이 부서를 지운 때만
                docs_moved += rag_spaces.move_docs(st.db, ids, "공통")
            elif ids:
                docs_left.append(n)
    if orphans:
        gone = {f"dept:{n}" for n in orphans}
        for k, v in shares.items():
            try:
                sh = _json.loads(v or "{}")
            except Exception:
                continue
            items = [i for i in (sh.get("items") or []) if i.get("to") not in gone]
            if len(items) != len(sh.get("items") or []):
                sh["items"] = items
                st.db.set_setting(k, _json.dumps(sh, ensure_ascii=False))
    folders_left = []
    if dd is not None:
        root = Path(dd) / "documents" / "기안기"
        if root.is_dir():
            for p in root.iterdir():
                if p.is_dir() and not p.name.startswith("~") and p.name not in live:
                    if any(x.is_file() for x in p.rglob("*")):
                        folders_left.append(p.name)
                    else:
                        import shutil
                        shutil.rmtree(p, ignore_errors=True)
    if docs_left:
        logging.getLogger("zzaimy.app.org").warning("없는 부서 문서가 남음(공통으로 돌리지 않음 — 부서 지우기로): %s", docs_left)
    return {"orphans": orphans, "docs": docs_moved, "folders_left": sorted(folders_left), "docs_left": docs_left}


def rename_dept(st, old: str, new: str) -> dict:
    """부서 이름을 한 번에 바꾼다 — 계정 소속·겸직 등급, 확정 위계, 부서 RAG 설정(부서 RAG·폴더 짝·부서 열어 주기·최소 등급),
    문서·조각·라이브러리 폴더의 부서, 부서 공용 구글 설정, 사업 관리 지정(dept:)·주관 부서. 바꾸기 전 판은 history/ 에 남는다.
    돌려주는 값: {"accounts": 바꾼 계정 수, "docs": 옮긴 문서 수}."""
    dd = getattr(st, "data_dir", None)
    accounts = getattr(st, "accounts", None) or {}
    # 계정 — 소속 부서와 겸직 등급 열쇠
    _backup_accounts(dd)
    n_acc = 0
    for a in accounts.values():
        hit = False
        if str(a.get("dept") or "").strip() == old:
            a["dept"] = new
            hit = True
        dg = a.get("dept_grades") or {}
        if old in dg:
            a["dept_grades"] = {(new if k == old else k): v for k, v in dg.items()}
            hit = True
        n_acc += hit
    if n_acc:
        org_lock.save_accounts(st)
    # 부서 RAG 설정과 문서
    cfg, n_docs = rag_spaces.rename_dept_rag(st.db, rag_spaces.config(dd), old, new)
    if dd is not None:
        rag_spaces.save_config(cfg, dd)
        dept_dictionary.rename_dept(dd, old, new)
    try:                                                   # 라이브러리 폴더의 부서
        with st.db._conn() as c:
            c.execute("UPDATE library_folders SET dept = ? WHERE dept = ?", (new, old))
    except Exception:
        pass
    # 부서 공용 구글 설정(google_account_dept:·google_root:… 「열쇠:부서」 꼴) — 사람 아이디 열쇠(google_account:)는 건드리지 않는다
    for k, v in (st.db.all_settings() or {}).items():
        pre, _, d = k.partition(":")
        if d == old and pre.startswith("google_") and pre != "google_account" and (v or "").strip():
            st.db.set_setting(f"{pre}:{new}", v)
            st.db.set_setting(k, "")
    # 사업 관리 — 관리 화면에서 지정한 「dept:부서」 와 고친 주관 부서
    try:
        from zzaimy.app import program_admin
        store = program_admin.load_store(dd)
        changed = False
        for bucket in ("rows", "added"):
            for r in (store.get(bucket) or {}).values():
                for f in program_admin.ROLES:
                    if f"dept:{old}" in (r.get(f) or []):
                        r[f] = [f"dept:{new}" if x == f"dept:{old}" else x for x in r[f]]
                        changed = True
                if r.get("dept") and old in [x.strip() for x in program_admin._SPLIT.split(r["dept"])]:
                    r["dept"] = ", ".join(new if x.strip() == old else x.strip() for x in program_admin._SPLIT.split(r["dept"]) if x.strip())
                    changed = True
        if changed:
            program_admin.save_store(store, dd)
    except Exception:
        pass
    return {"accounts": n_acc, "docs": n_docs}


@router.post("/org/rename")
async def org_rename(request: Request):
    b = await _body(request)
    return await run_in_threadpool(_locked, _org_rename, request, b)


def _org_rename(request: Request, b: dict):
    st = request.app.state
    actor = Actor(request)
    old, new = _name(b.get("dept")), _name(b.get("name"))
    known = _known(st)
    if old not in known:
        return _fail("없는 부서입니다")
    _need(actor.can_edit(old), "이 부서의 이름은 바꿀 수 없습니다 — 맡은 처·단의 하위 부서만 고칩니다")
    if not new or new == old:
        return _fail("새 이름을 적어 주세요")
    if new in known or new == "공통":
        return _fail(f"이미 있는 부서 이름입니다: {new}")
    if not rag_spaces._DEPT_NAME.match(new):
        return _fail("부서 이름은 2~30자의 한글·영문·숫자로 적습니다")
    # 보안: 지운 부서의 권한 흔적이 남은 이름으로는 바꾸지 않는다 — 바꾸는 순간 그 흔적(겸직 등급·부서 열어 주기·위계·사업 지정)을 차지한다
    left = traces(st, new)
    if left:
        return _fail(f"{new} 은(는) 예전 부서의 권한 기록({', '.join(left)})이 남아 있어 이 이름으로 바꿀 수 없습니다 — "
                     "관리자가 같은 이름으로 부서를 새로 만들면 남은 기록이 정리됩니다", 409)
    got = _write_all(st, (old,), lambda: rename_dept(st, old, new), targets=(new,))
    return {"ok": True, "msg": f"이름 바꿈: {old} → {new} · 계정 {got['accounts']}개 · 문서 {got['docs']}건",
            "undo": {"dept": new, "name": old}, "name": new}


@router.post("/org/delete")
async def org_delete(request: Request):
    b = await _body(request)
    return await run_in_threadpool(_locked, _org_delete, request, b)


def _org_delete(request: Request, b: dict):
    """부서 지우기 — 아래 부서는 kids 대로(lift: 지운 부서의 자리로 올리기, with: 함께 지우기). 함께 지울 부서도 모두 고칠 권한이 있어야 한다."""
    from zzaimy.app.user_admin import KID_MODES, delete_dept, delete_message, delete_plan
    st = request.app.state
    actor = Actor(request)
    name = _name(b.get("dept"))
    kids = str(b.get("kids") or "lift")
    if kids not in KID_MODES:
        return _fail("아래 부서를 어떻게 할지 골라 주세요")
    if name not in _known(st):
        return _fail("없는 부서입니다")
    _need(actor.can_edit(name), "이 부서는 지울 수 없습니다 — 맡은 처·단의 하위 부서만 고칩니다")
    accounts = getattr(st, "accounts", None) or {}
    plan = delete_plan(st, accounts, name, kids)
    _need(all(actor.can_edit(d) for d in plan["targets"]), "함께 지울 아래 부서 가운데 고칠 수 없는 부서가 있습니다")
    if plan["lift"]:
        _need(actor.can_hold(plan["up"]), "아래 부서를 올릴 자리(맨 위 처·단)는 관리자만 정합니다 — 함께 지우기를 고르거나 관리자에게 요청해 주세요")
    if plan["blocked"]:                                   # 쓰기 전에 막는다(delete_dept 도 다시 본다)
        return _fail(f"{', '.join('「' + d + '」' for d in plan['blocked'][:3])} 부서에 사람이 있어 지울 수 없습니다 — 먼저 사람을 다른 부서로 옮기세요", 409)
    err, moved = _write_all(st, tuple(plan["targets"]), lambda: delete_dept(st, accounts, name, kids), targets=("공통",))
    if err:
        return _fail(err, 409)
    return {"ok": True, "msg": delete_message(name, plan, moved), "deleted": plan["targets"], "lifted": plan["lift"]}


@router.post("/org/create")
async def org_create(request: Request):
    b = await _body(request)
    return await run_in_threadpool(_locked, _org_create, request, b)


def _org_create(request: Request, b: dict):
    st = request.app.state
    actor = Actor(request)
    dd = getattr(st, "data_dir", None)
    raw, parent = _name(b.get("name")), _name(b.get("parent"))
    known = _known(st)
    if parent and parent not in known:
        return _fail("처·단은 조직도에 있는 이름으로 골라 주세요")
    _need(actor.can_hold(parent), "그 자리에는 부서를 더할 수 없습니다 — 맡은 처·단 아래에만 더합니다")
    if dd is None:
        return _fail("설정 위치가 없습니다")
    name = dept_dictionary.load(dd).normalize(raw) or raw      # 별칭(옛 이름·줄임말)으로 적어도 사전의 정식 이름으로
    if name in known:
        return _fail(f"이미 있는 부서입니다: {name}")
    if not rag_spaces._DEPT_NAME.match(name) or name == "공통":
        return _fail("부서 이름은 2~30자의 한글·영문·숫자로 적습니다(「공통」은 쓸 수 없음)")
    # 새 부서라 아래 부서는 없다(같은 이름의 옛 위계는 아래 흔적 정리 대상) — 놓을 처·단만 본다
    err = _check_parent(dept_dictionary.parents_override(dd), dept_dictionary.children_map(dd), name, parent)
    if err:
        return _fail(err)
    _, err = rag_spaces.create_dept_rag(rag_spaces.config(dd), name, by=actor.user)     # 이름 꼴·중복 검사만(쓰지 않음)
    if err:
        return _fail(err)
    # 보안: 같은 이름의 지운 부서가 남긴 권한 흔적 — 정리는 남의 겸직 등급·부서 열어 주기·사업 지정을 지우는 일이라 관리자만.
    # 총괄 책임자는 흔적 있는 이름으로 만들지 못한다(관리자에게 요청)
    left = traces(st, name)
    if left and not actor.admin:
        return _fail(f"{name} 은(는) 예전 부서의 권한 기록({', '.join(left)})이 남아 있어 만들 수 없습니다 — 관리자에게 요청해 주세요", 409)
    # 여기까지 검사 — 아래부터 쓰기(도중 실패면 모두 되돌린다)

    def write() -> list[str]:
        cleaned = purge_traces(st, name) if left else []
        new_cfg, e = rag_spaces.create_dept_rag(rag_spaces.config(dd), name, by=actor.user)   # 정리한 뒤의 설정 위에(검사는 위에서 마침)
        if e:
            raise RuntimeError(e)
        rag_spaces.save_config(new_cfg, dd)
        over = dept_dictionary.parents_override(dd)
        kids = dept_dictionary.children_map(dd)
        if parent:
            e = dept_dictionary.set_parent(dd, name, parent)
            if e:
                raise RuntimeError(e)
        elif not is_top(name, over, kids):                # 맨 위에 둔 팀·센터 — 「상위 없음」 을 정한 것으로 적어 처·단으로 보이게
            dept_dictionary.set_parent(dd, name, "")
        return cleaned

    cleaned = _write_all(st, (), write)
    where = f"{parent} 아래에" if parent else "맨 위(처·단)에"
    msg = f"{name} 부서를 {where} 더했습니다" + (f" · 남아 있던 옛 기록 정리: {', '.join(cleaned)}" if cleaned else "")
    return {"ok": True, "msg": msg, "name": name, "cleaned": cleaned}


@router.get("/org/rag-lend/targets/{dept}")
def org_rag_lend_targets(request: Request, dept: str, q: str = "", limit: int = 30):
    """받을 부서 찾기 — 부서 고르기 칩(people-picker, data-endpoint)이 읽는 꼴. 부서 목록은 조직 관리와 같은 원본(user_admin.known_depts)."""
    actor = Actor(request)
    dept = _name(dept)
    _need(actor.can_lend(dept), "이 부서 RAG 는 관리자나 이 부서 총괄 책임자가 엽니다")
    words = [w for w in (q or "").lower().split() if w]
    names = [d for d in _known(request.app.state) if d != dept and all(w in d.lower() for w in words)]
    return {"people": [{"user": d, "name": d, "dept": "부서", "grade_label": ""} for d in names[:max(1, min(int(limit or 30), 80))]],
            "depts": [], "my_dept": ""}


@router.post("/org/rag-lend")
async def org_rag_lend(request: Request):
    """부서 RAG 를 다른 부서에 열기 — {dept: 주는 부서, to: [받는 부서…]} 로 받는 부서 목록을 통째로 정한다(ADR-0053 보충).
    관리자와 그 부서를 맡은 총괄 책임자만(Actor.can_lend). 계정 하나에 주는 길은 없다 — 받는 쪽은 늘 부서."""
    b = await _body(request)
    return await run_in_threadpool(_locked, _org_rag_lend, request, b)


def _org_rag_lend(request: Request, b: dict):
    st = request.app.state
    actor = Actor(request)
    dept = _name(b.get("dept"))
    known = set(_known(st))
    if dept not in known:
        return _fail("없는 부서입니다")
    _need(actor.can_lend(dept), "이 부서 RAG 는 관리자나 이 부서 총괄 책임자가 엽니다")
    raw = b.get("to")
    if not isinstance(raw, list):
        return _fail("받는 부서 목록이 필요합니다")
    to = list(dict.fromkeys(_name(x) for x in raw if _name(x)))
    bad = [x for x in to if x not in known or x in (dept, "공통")]
    if bad:
        return _fail("열 수 없는 부서: " + ", ".join(bad))
    dd = getattr(st, "data_dir", None)

    def write():
        cfg = rag_spaces.set_receivers(rag_spaces.config(dd), dept, to)
        rag_spaces.save_config(cfg, dd)
        return rag_spaces.receivers(cfg, dept)
    got = _write_all(st, (), write)
    msg = f"{dept} 부서 RAG — " + (", ".join(got) + "에 열었습니다" if got else "다른 부서에 열지 않음")
    return {"ok": True, "msg": msg, "dept": dept, "to": got}


@router.post("/org/head")
async def org_head(request: Request):
    b = await _body(request)
    return await run_in_threadpool(_locked, _org_head, request, b)


def _org_head(request: Request, b: dict):
    """처·단의 총괄 책임자 지정(관리자만) — 소속이 그 부서면 계정 등급을, 아니면 겸직 등급(dept_grades — 겸직 부서도 이것 한 곳)을.
    on=false 면 뺀다(소속은 사업 책임자로, 겸직은 그 부서 겸직째 푼다)."""
    st = request.app.state
    actor = Actor(request)
    _need(actor.admin, "총괄 책임자 지정은 관리자가 합니다")
    dept, uid, on = _name(b.get("dept")), _name(b.get("user")).lower(), b.get("on", True) is not False
    if dept not in _known(st):
        return _fail("없는 부서입니다")
    accounts = getattr(st, "accounts", None) or {}
    a = accounts.get(uid)
    if not a or a.get("role") not in grades.STAFF or not a.get("active", True):
        return _fail("사용 중인 교직원 계정만 지정할 수 있습니다")
    dd = getattr(st, "data_dir", None)
    over = dept_dictionary.parents_override(dd)
    if on and (over.get(dept, "") or not is_top(dept, over, dept_dictionary.children_map(dd))):
        return _fail("총괄 책임자는 처·단(맨 위)에 정합니다")
    primary = str(a.get("dept") or "").strip() == dept
    _write_all(st, (), lambda: _set_head(st, a, uid, dept, on, primary))
    who = (a.get("name") or "").strip() or uid
    msg = (f"{who} 님을 「{dept}」 총괄 책임자로 정했습니다" + ("" if primary else " · 겸직")) if on else f"{who} 님을 「{dept}」 총괄 책임자에서 뺐습니다"
    return {"ok": True, "msg": msg}


def _set_head(st, a: dict, uid: str, dept: str, on: bool, primary: bool) -> None:
    dd = getattr(st, "data_dir", None)
    if primary:
        a["grade"] = "head" if on else (grades.DEFAULT_GRADE if a.get("grade") == "head" else a.get("grade") or grades.DEFAULT_GRADE)
    else:
        dg = dict(a.get("dept_grades") or {})
        if on:
            dg[dept] = "head"
        else:
            dg.pop(dept, None)
        if dg:
            a["dept_grades"] = dg
        else:
            a.pop("dept_grades", None)
    a["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    org_lock.save_accounts(st)
