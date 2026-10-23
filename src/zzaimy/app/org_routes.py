"""조직도 — 부서를 두 단계(처·단 → 팀·센터)로 보이고 고친다(ADR-0066 「부서 단위와 등급」).

위계는 관리자가 확정한 관계(dept_dictionary 의 dept_parents.json)만 쓴다. 부서 만들기·지우기는 사용자·계정 화면과 같은 길
(rag_spaces.create_dept_rag · user_admin.delete_dept)을 쓴다. 이름 바꾸기는 rename_dept 한 함수가 계정 부서·겸직 등급·
위계·부서 RAG 설정·문서 부서·부서 공용 설정·사업 관리의 부서 지정까지 함께 바꾼다(바꾸기 전 판은 history/).

권한(서버에서 매번 다시 본다):
- 관리자: 모두 고친다(더하기·이름 바꾸기·지우기·옮기기)와 총괄 책임자 지정.
- 총괄 책임자: 자기가 맡은 처·단 아래(하위 부서)만 — 그 처·단 자체와 다른 처·단은 보기만.
- 그 밖의 교직원: 보기만. 학생은 열지 못한다.

경로: GET /api/org · POST /org/move · /org/rename · /org/delete · /org/create · /org/head (JSON 본문)
"""
from __future__ import annotations

import datetime
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from zzaimy.app import dept_dictionary, grades, rag_spaces

router = APIRouter()

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


def _people(accounts: dict, dept: str, data_dir, detail: bool = True) -> dict:
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
        elif dept in grades.depts_of(uid, a, data_dir):
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

    def node(d: str) -> dict:
        u = dic.by_name.get(dic.normalize(d) or d) or {}
        k = _kind(d)
        detail = actor.admin or d in actor.covered          # 자세한 값(아이디·겸직·문서 수)은 관리자와 그 부서를 맡은 총괄 책임자만
        row = {"name": d, "kind": dept_dictionary.KINDS.get(k, ""), "aliases": list(u.get("aliases") or [])[:6],
               "can_edit": actor.can_edit(d), **_people(accounts, d, dd, detail)}
        if detail:
            row["docs"] = n_docs.get(d, 0)
        return row

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
    out = {"tops": out_tops, "loose": [node(d) for d in loose], "suggestions": sugg,
           "admin": actor.admin, "can_any": actor.can_any, "can_top": actor.admin}
    if actor.can_any:
        out["roots"] = sorted(actor.roots)
    return out


@router.get("/api/org")
def api_org(request: Request):
    actor = Actor(request)
    return JSONResponse(tree(request.app.state, actor))


# ---------------------------------------------------------------- 고치기

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


def _check_place(st, dept: str, parent: str) -> str:
    """dept 를 parent 아래(빈 값이면 맨 위)에 둘 수 있나 — 두 단계 규칙. 문제면 문장."""
    dd = getattr(st, "data_dir", None)
    over = dept_dictionary.parents_override(dd)
    kids = dept_dictionary.children_map(dd)
    if not parent:
        return ""
    if parent == dept:
        return "자기 자신 아래로 옮길 수 없습니다"
    if not is_top(parent, over, kids) or over.get(parent, ""):
        return f"{parent} 아래에는 둘 수 없습니다 — 처·단이 아니라서 세 단계가 됩니다. 처·단 아래로 옮기세요"
    if kids.get(dept):
        return f"{dept} 아래에 부서가 있어 다른 처·단 아래로 옮기지 않습니다 — 하위 부서를 먼저 옮기세요"
    return ""


@router.post("/org/move")
async def org_move(request: Request):
    """부서를 처·단 아래로(parent) 또는 맨 위로(parent 빈 값) 옮긴다. unset 이면 관리자가 정하기 전으로(되돌리기)."""
    st = request.app.state
    actor = Actor(request)
    b = await _body(request)
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
        dept_dictionary.clear_parent(dd, dept)
        return {"ok": True, "msg": f"{dept} — 옮기기 전으로 되돌렸습니다", "undo": None}
    _need(actor.can_hold(parent), "그 자리에는 놓을 수 없습니다 — 맨 위(처·단)로 올리기와 다른 처·단 아래로 옮기기는 관리자가 합니다")
    err = _check_place(st, dept, parent) or dept_dictionary.set_parent(dd, dept, parent)
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


def traces(st, name: str) -> list[str]:
    """지금 없는 부서 이름에 남은 권한 흔적 — 같은 이름으로 다시 만들거나 이름을 바꿔 차지하면 옛 권한이 되살아나는 것들.
    계정 겸직 등급(dept_grades)·추가 권한(grants dept:)·최소 등급(min_grade dept:)·확정 위계(dept_parents)·
    사업 관리의 부서 지정(dept:)·부서 공용 구글 설정. 돌려주는 값은 사람이 읽을 갈래 목록(빈 목록이면 흔적 없음)."""
    dd = getattr(st, "data_dir", None)
    key = f"dept:{name}"
    out = []
    accounts = getattr(st, "accounts", None) or {}
    n = sum(1 for a in accounts.values() if name in (a.get("dept_grades") or {}))
    if n:
        out.append(f"계정 겸직 등급 {n}건")
    cfg = rag_spaces.config(dd)
    n = sum(1 for g in (cfg.get("grants") or {}).values() if key in (g or []))
    if n:
        out.append(f"추가 권한 {n}건")
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
        st.save_accounts()
    if dd is not None:
        cfg = rag_spaces.config(dd)
        gr = {u: [x for x in (g or []) if x != key] for u, g in (cfg.get("grants") or {}).items()}
        mg = {k: v for k, v in (cfg.get("min_grade") or {}).items() if k != key}
        if gr != (cfg.get("grants") or {}) or mg != (cfg.get("min_grade") or {}):
            cfg["grants"] = {u: g for u, g in gr.items() if g}
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


def rename_dept(st, old: str, new: str) -> dict:
    """부서 이름을 한 번에 바꾼다 — 계정 소속·겸직 등급, 확정 위계, 부서 RAG 설정(부서 RAG·폴더 짝·추가 권한·최소 등급),
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
        st.save_accounts()
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
    st = request.app.state
    actor = Actor(request)
    b = await _body(request)
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
    # 보안: 지운 부서의 권한 흔적이 남은 이름으로는 바꾸지 않는다 — 바꾸는 순간 그 흔적(겸직 등급·추가 권한·위계·사업 지정)을 차지한다
    left = traces(st, new)
    if left:
        return _fail(f"{new} 은(는) 예전 부서의 권한 기록({', '.join(left)})이 남아 있어 이 이름으로 바꿀 수 없습니다 — "
                     "관리자가 같은 이름으로 부서를 새로 만들면 남은 기록이 정리됩니다", 409)
    got = rename_dept(st, old, new)
    return {"ok": True, "msg": f"이름 바꿈: {old} → {new} · 계정 {got['accounts']}개 · 문서 {got['docs']}건",
            "undo": {"dept": new, "name": old}, "name": new}


@router.post("/org/delete")
async def org_delete(request: Request):
    from zzaimy.app.user_admin import delete_dept
    st = request.app.state
    actor = Actor(request)
    b = await _body(request)
    name = _name(b.get("dept"))
    if name not in _known(st):
        return _fail("없는 부서입니다")
    _need(actor.can_edit(name), "이 부서는 지울 수 없습니다 — 맡은 처·단의 하위 부서만 고칩니다")
    err, moved = delete_dept(st, getattr(st, "accounts", None) or {}, name)
    if err:
        return _fail(err, 409)
    return {"ok": True, "msg": f"{name} 부서를 지웠습니다 · 문서 {moved}건은 공통 RAG 로"}


@router.post("/org/create")
async def org_create(request: Request):
    st = request.app.state
    actor = Actor(request)
    b = await _body(request)
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
    # 보안: 같은 이름의 지운 부서가 남긴 권한 흔적은 먼저 지운다 — 새 부서가 옛 겸직 등급·추가 권한·위계·사업 지정을 물려받지 않게
    cleaned = purge_traces(st, name)
    if parent:
        err = _check_place(st, name, parent)
        if err:
            return _fail(err)
    cfg, err = rag_spaces.create_dept_rag(rag_spaces.config(dd), name, by=actor.user)
    if err:
        return _fail(err)
    rag_spaces.save_config(cfg, dd)
    over = dept_dictionary.parents_override(dd)
    kids = dept_dictionary.children_map(dd)
    if parent:
        dept_dictionary.set_parent(dd, name, parent)
    elif not is_top(name, over, kids):                    # 맨 위에 둔 팀·센터 — 「상위 없음」 을 정한 것으로 적어 처·단으로 보이게
        dept_dictionary.set_parent(dd, name, "")
    where = f"{parent} 아래에" if parent else "맨 위(처·단)에"
    msg = f"{name} 부서를 {where} 더했습니다" + (f" · 남아 있던 옛 기록 정리: {', '.join(cleaned)}" if cleaned else "")
    return {"ok": True, "msg": msg, "name": name, "cleaned": cleaned}


@router.post("/org/head")
async def org_head(request: Request):
    """처·단의 총괄 책임자 지정(관리자만) — 소속이 그 부서면 계정 등급을, 아니면 겸직 등급(dept_grades)과 겸직 부서(추가 권한)를.
    on=false 면 뺀다(소속은 사업 책임자로, 겸직은 그 부서 겸직째 푼다)."""
    st = request.app.state
    actor = Actor(request)
    _need(actor.admin, "총괄 책임자 지정은 관리자가 합니다")
    b = await _body(request)
    dept, uid, on = _name(b.get("dept")), _name(b.get("user")).lower(), b.get("on", True) is not False
    if dept not in _known(st):
        return _fail("없는 부서입니다")
    accounts = getattr(st, "accounts", None) or {}
    a = accounts.get(uid)
    if not a or a.get("role") not in grades.STAFF or not a.get("active", True):
        return _fail("사용 중인 교직원 계정만 지정할 수 있습니다")
    dd = getattr(st, "data_dir", None)
    primary = str(a.get("dept") or "").strip() == dept
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
        if dd is not None:                                 # 겸직 부서(추가 권한)도 같이 — 계정 화면과 같은 짝
            cfg = rag_spaces.config(dd)
            gr = {u: list(v) for u, v in (cfg.get("grants") or {}).items()}
            mine = [s for s in gr.get(uid, []) if s != f"dept:{dept}"]
            if on:
                mine.append(f"dept:{dept}")
            gr[uid] = mine
            cfg["grants"] = {u: v for u, v in gr.items() if v}
            rag_spaces.save_config(cfg, dd)
    a["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    st.save_accounts()
    who = (a.get("name") or "").strip() or uid
    msg = (f"{who} 님을 「{dept}」 총괄 책임자로 정했습니다" + ("" if primary else " · 겸직")) if on else f"{who} 님을 「{dept}」 총괄 책임자에서 뺐습니다"
    return {"ok": True, "msg": msg}
