"""사업 관리 — 교내 사업 장부를 표로 관리하고 사업마다 총괄 책임자·사업 책임자·행정 담당자를 정한다(ADR-0066).

바탕은 외부 확인 장부(kg_external.json)의 사업 항목이다. 장부는 학교의 재정지원사업 현황(연도별 엑셀, scripts/186)과
공개 출처로 채워져 있고 그래프·사업 목록 나무(program_tree)가 같이 읽으므로 여기서 고치지 않는다. 관리 화면에서 바꾼 값
(분류·이름·주관 부서·기간·예산·책임자 이름·지정 계정·연도별 값·숨김)과 새로 더한 줄은 따로 program_admin.json 에 둔다 — 장부 값 위에 덮어 보인다.

- 대·중분류: 장부 항목의 지원 주체·체계 갈래(program_tree.place). 소분류는 관리자가 적는다(기본 빈칸)
- 주관 부서: 학교 표의 업무 부서(school.depts). 총괄 책임자 범위 판정에 쓰이므로 계정의 부서 이름과 같게 고쳐 둘 수 있다
- 이어진 프로젝트: projects.program(「program:<열쇠>|<연도>」)의 열쇠가 그 사업의 이름·별칭 열쇠와 같은 것
- 권한: 관리자 전부(편집·추가·숨기기), 총괄 책임자는 자기 부서·총괄 책임자로 지정된 사업 읽기 + 사업 책임자·행정 담당자 지정
  (지난 사업은 전체를 확인용으로), 사업 책임자·행정 담당자는 지정된 사업 읽기
- 사업의 총괄 책임자·사업 책임자·행정 담당자(heads·leads·clerks) 지정은 그 사업에 이어진 프로젝트의 진행 현황 권한으로
  이어진다(grades.can_view_activity, roles_for_project)
- 프로젝트 연결: 사업 줄에서 이미 있는 프로젝트를 잇거나(projects.program = 「<사업 열쇠>|<연도>」) 그 사업·연도로 새 프로젝트를 만든다.
  프로젝트 화면에서 사업을 고를 때는 choices() 목록과 program_value() 를 쓴다

- 연도별 값: 줄마다 years{연도: {예산·책임자·주관 부서·기간 …}}. 연도를 고르면 그해 진행한 사업만, 칸은 그해 값으로 보인다.
  사업 현황 엑셀(program_sheet)에서 채우거나 편집 창에서 손으로 적는다. 손으로 적은 칸(by_hand)은 엑셀 채우기가 기본으로 덮지 않는다
- 책임자 이름: 엑셀에 적힌 사람 이름(글). 계정 지정 칸(총괄 책임자·사업 책임자·행정 담당자)과 다르며, 이름이 계정 이름과 같으면 「계정 있음」만 표시한다

화면: GET /programs/admin · 줄 상세 GET /programs/admin/row · 내려받기 GET /programs/admin.csv · 저장 POST /programs/admin/save ·
숨기기 POST /programs/admin/hide · 프로젝트 잇기·끊기 POST /programs/admin/link · 새 프로젝트 POST /programs/admin/new-project
엑셀에서 채우기(관리자): 미리보기 POST /programs/admin/sheet/preview · 적용 POST /programs/admin/sheet/apply
"""
from __future__ import annotations

import csv
import io
import json
import re
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from zzaimy.app import grades, program_sheet
from zzaimy.app.program_names import group_key
from zzaimy.app.program_register import slug
from zzaimy.app.program_tree import Ledger, place

router = APIRouter()

STORE = "program_admin.json"
TEXT_FIELDS = ("l1", "l2", "l3", "name", "dept", "period", "budget", "manager")
YEAR_EDIT = ("budget", "manager", "dept", "period")         # 편집 창에서 손으로 적는 연도별 칸
COLUMNS = (("l1", "대분류"), ("l2", "중분류"), ("l3", "소분류"), ("name", "사업"), ("dept", "주관 부서"), ("period", "기간"),
           ("budget", "예산"), ("manager", "책임자 이름"), ("heads", "총괄 책임자"), ("leads", "사업 책임자"), ("clerks", "행정 담당자"),
           ("projects", "진행 프로젝트"), ("archived", "보관 묶음"))
ROLES = ("heads", "leads", "clerks")                      # 총괄 책임자 · 사업 책임자 · 행정 담당자(계정 지정)
_SPLIT = re.compile(r"\s*[,/·]\s*")


def _norm(t: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(t or "").lower())


def _dir(data_dir) -> Path:
    if data_dir is None:
        from zzaimy.app import paths
        return paths.platform_dir()
    return Path(data_dir)


def load_store(data_dir=None) -> dict:
    try:
        got = json.loads((_dir(data_dir) / STORE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = {}
    return {"rows": dict(got.get("rows") or {}), "added": dict(got.get("added") or {})}


def save_store(store: dict, data_dir=None) -> None:
    """저장 — 이전 판은 history/program_admin.<시각>.json 으로 남긴다."""
    d = _dir(data_dir)
    p = d / STORE
    if p.is_file():
        hist = d / "history"
        hist.mkdir(exist_ok=True)
        shutil.copy2(p, hist / f"program_admin.{time.strftime('%Y%m%d%H%M%S')}.json")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    _CACHE.clear()


def load_ledger(data_dir=None) -> dict:
    try:
        return json.loads((_dir(data_dir) / "kg_external.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _years(e: dict) -> str:
    ys = sorted({int(y) for y in ((e.get("school") or {}).get("years") or []) if str(y).isdigit()})
    if not ys:
        return ""
    return str(ys[0]) if len(ys) == 1 else f"{ys[0]}~{ys[-1]}"


def base_rows(ledger: dict) -> list[dict]:
    """장부 항목 → 표의 줄(관리 화면 값을 덮기 전). 「사업 아님」 항목은 뺀다."""
    out, seen = [], set()
    cats = ledger.get("categories") or {}
    for e in ledger.get("programs") or []:
        name = " ".join(str(e.get("name") or "").split())
        if not name or e.get("category") == "사업 아님":
            continue
        key = slug(name)
        if key in seen:
            continue
        seen.add(key)
        l1, l2 = place(e, cats)
        terms = [t for t in [name, *(e.get("terms") or [])] if t]
        out.append({"key": key, "source": "장부", "l1": l1, "l2": l2, "l3": "", "name": name,
                    "dept": ", ".join((e.get("school") or {}).get("depts") or []), "period": str(e.get("period") or "") or _years(e),
                    "budget": str(e.get("budget") or ""), "manager": "", "terms": terms, "heads": [], "leads": [], "clerks": [], "hidden": False})
    return out


def merged_rows(data_dir=None, ledger: dict | None = None, store: dict | None = None) -> list[dict]:
    """장부 줄 + 관리 화면에서 고친 값 + 더한 줄. 줄마다 ids(이 사업으로 보는 projects.program 열쇠)를 단다."""
    ledger = load_ledger(data_dir) if ledger is None else ledger
    store = load_store(data_dir) if store is None else store
    rows = base_rows(ledger)
    for key, a in store["added"].items():
        rows.append({"key": key, "source": "추가", "l1": "", "l2": "", "l3": "", "name": "", "dept": "", "period": "",
                     "budget": "", "manager": "", "terms": [], "heads": [], "leads": [], "clerks": [], "hidden": False, **a})
    for r in rows:
        o = store["rows"].get(r["key"]) or {}
        for f in TEXT_FIELDS:
            if f in o:
                r[f] = o[f]
        for f in ROLES:
            if f in o:
                r[f] = list(o[f] or [])
        r["hidden"] = bool(o.get("hidden", r.get("hidden")))
        r["years"] = {str(y): dict(v) for y, v in (o.get("years") or {}).items() if str(y).isdigit() and isinstance(v, dict)}
        r["period"] = re.sub(r"(\d{4})-(\d{2})-(\d{2})(?:[ T]00:00(?::00)?)?", r"\1.\2.\3", str(r.get("period") or "")).strip()   # 엑셀 날짜 값(… 00:00:00)은 날짜만
        r["ids"] = {r["key"], *("program:" + k for t in [r["name"], *r["terms"]] if (k := _norm(t)))}
        r["depts"] = [d for d in _SPLIT.split(r.get("dept") or "") if d]
    return rows


def link_projects(db, rows: list[dict]) -> None:
    """줄마다 이어진 프로젝트 수 — 진행(projects, archived=0)과 보관 묶음(archived=1). 열쇠가 바로 맞지 않으면 그래프 사업 노드의
    이름을 줄 이름·별칭으로 찾는다(program_tree.Ledger 와 같은 이름 규칙)."""
    for r in rows:
        r["projects"], r["archived"], r["project_ids"], r["plist"] = 0, 0, [], []
    if db is None:
        return
    by_id = {i: r for r in rows for i in r["ids"]}
    with db._conn() as conn:
        found = conn.execute("SELECT id, program, archived, name FROM projects WHERE program <> ''").fetchall()
        labels: dict[str, str] = {}
        try:
            labels = {str(a): str(b or "") for a, b in conn.execute("SELECT id, label FROM kg_nodes WHERE type = 'program'")}
        except Exception:
            pass
    lg = Ledger({"programs": [{"name": r["name"], "terms": r["terms"], "_row": r} for r in rows]})
    for pid, program, archived, pname in found:
        key, _, pyear = str(program or "").partition("|")
        r = by_id.get(key)
        if r is None and labels.get(key):
            hit = lg.find(labels[key])
            r = hit.get("_row") if hit else None
        if r is None:
            continue
        if int(archived or 0):
            r["archived"] += 1
        else:
            r["projects"] += 1
            r["project_ids"].append(int(pid))
        r["plist"].append({"id": int(pid), "name": str(pname or ""), "year": pyear, "archived": bool(int(archived or 0))})


_CACHE: dict = {}


def roles_for_project(project: dict | None, data_dir=None) -> dict:
    """프로젝트가 이어진 사업의 지정 — {"heads": [...], "leads": [...], "clerks": [...]}. 사업에 이어지지 않았거나 지정이 없으면 빈 dict.
    장부·관리 값은 파일이 바뀔 때만 다시 읽는다."""
    key = str((project or {}).get("program") or "").split("|", 1)[0]
    if not key:
        return {}
    d = _dir(data_dir)
    stamp = tuple((p.stat().st_mtime if p.is_file() else 0) for p in (d / STORE, d / "kg_external.json"))
    if _CACHE.get("at") != (str(d), stamp):
        store = load_store(d)
        idx = {}
        if any(o.get(f) for o in store["rows"].values() for f in ROLES):
            for r in merged_rows(d, store=store):
                if any(r[f] for f in ROLES):
                    idx.update({i: {f: list(r[f]) for f in ROLES} for i in r["ids"]})
        _CACHE.clear()
        _CACHE.update({"at": (str(d), stamp), "idx": idx})
    return dict(_CACHE["idx"].get(key) or {})


# ---------------------------------------------------------------- 권한

def _me(request: Request) -> tuple[str, dict]:
    """(계정, 판정에 쓰는 계정 값) — 테스트 보기 중이면 고른 역할·부서·등급으로."""
    st = request.state
    user = getattr(st, "user", "") or ""
    acct = dict((getattr(request.app.state, "accounts", None) or {}).get(user) or {})
    va = getattr(st, "view_as", None)
    acct["role"] = getattr(st, "role", "") or acct.get("role", "")
    acct["dept"] = getattr(st, "dept", "") or ("" if va else acct.get("dept", ""))
    if va:
        acct["grade"] = va.get("grade") or grades.DEFAULT_GRADE
    return user, acct


def visible_rows(rows: list[dict], scope: str, user: str, acct: dict, show_hidden: bool = False) -> list[dict]:
    if scope == "all":
        return [r for r in rows if show_hidden or not r["hidden"]]
    mine = str(acct.get("dept") or "").strip()
    out = []
    for r in rows:
        if r["hidden"]:
            continue
        named = any(user in r[f] for f in ROLES)
        if scope == "dept" and ((mine and mine in r["depts"]) or named):
            out.append(r)
        elif scope == "own" and named:
            out.append(r)
    return out


def _choices(accounts: dict, scope: str, acct: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """(총괄 책임자 후보, 사업 책임자 후보, 행정 담당자 후보) — 사용 중인 교직원 가운데 총괄 책임자는 총괄 책임자 등급,
    사업 책임자는 사업 책임자·총괄 책임자 등급, 행정 담당자는 행정 담당자 이상. 총괄 책임자가 고르는 두 칸은 자기 부서 사람만."""
    heads, leads, clerks = [], [], []
    mine = str(acct.get("dept") or "").strip()
    for u, a in sorted(accounts.items()):
        if not a.get("active", True) or a.get("role") not in grades.STAFF:
            continue
        g = grades.grade_of(a)
        item = {"user": u, "label": (a.get("name") or "").strip() or u, "dept": a.get("dept") or "", "grade": grades.label(g)}
        if g == "head":
            heads.append(item)
        same = scope == "all" or (mine and (a.get("dept") or "").strip() == mine)
        if g in ("lead", "head") and same:
            leads.append(item)
        if g in ("clerk", "lead", "head") and same:
            clerks.append(item)
    return heads, leads, clerks


def _back(msg: str, ok: bool = True, anchor: str = "") -> RedirectResponse:
    return RedirectResponse(f"/programs/admin?{'ok' if ok else 'err'}={quote(msg)}{anchor}", status_code=303)


def _scope_or_403(request: Request) -> tuple[str, str, dict]:
    user, acct = _me(request)
    scope = grades.program_scope(acct)
    if not scope:
        raise HTTPException(403, "사업 관리는 사업 책임자 이상만 볼 수 있습니다")
    return scope, user, acct


_YEAR = re.compile(r"(19|20)\d{2}")


def span(period: str) -> tuple[int | None, int | None, bool]:
    """기간 글 → (시작 연도, 끝 연도, 끝이 열려 있나). 「2022~2027」「2025~」「2026.5~2028.2」「2017」 꼴."""
    t = str(period or "")
    ys = [int(m.group(0)) for m in _YEAR.finditer(t)]
    if not ys:
        return None, None, False
    open_end = bool(re.search(r"[~∼\-–]\s*$", t.strip())) and len(ys) == 1
    return ys[0], (None if open_end else ys[-1]), open_end


def row_span(r: dict) -> tuple[int | None, int | None]:
    """줄의 (시작 연도, 끝 연도) — 기간 글, 없으면 최근 해의 기간, 그리고 연도별 값이 있는 해까지 넓힌다. 끝이 열려 있으면 끝은 None."""
    ys = r.get("years") or {}
    a, b, open_end = span(r.get("period") or "")
    if a is None:
        a, b, open_end = span(program_sheet.latest(ys).get("period") or "")
    got = [int(y) for y in ys if str(y).isdigit()]
    if got:
        a = min(a, min(got)) if a is not None else min(got)
        if not open_end:
            b = max(b or 0, max(got))
    return a, b


def stage(r: dict, year: int) -> str:
    """현행(current) · 지난(past) · 기간 모름(unknown) — 끝 연도가 올해보다 앞이면 지난 사업."""
    a, b = row_span(r)
    if a is None:
        return "unknown"
    return "past" if (b is not None and b < year) else "current"


def runs_in(r: dict, year: int) -> bool:
    if str(year) in (r.get("years") or {}):
        return True
    a, b = row_span(r)
    return a is not None and a <= year and (b is None or b >= year)


def view(r: dict, year: int = 0) -> dict:
    """화면에 보일 칸 값 — 연도를 골랐으면 그해 값(없으면 줄 값), 전체 연도면 줄 값(빈칸이면 최근 해 값, 그 해를 note 로)."""
    ys = r.get("years") or {}
    out = {"note": "", "from": []}
    if year:
        yv = ys.get(str(year)) or {}
        for f in ("dept", "period", "manager"):
            out[f] = yv.get(f) or r.get(f) or ""
        out["budget"] = yv.get("budget") or ("" if ys else r.get("budget") or "")
        out["sub"] = yv.get("sub") or ""
        out["chip"] = str(year) if yv else ""
        out["n_years"] = len(ys)
        return out
    last_y = max((y for y in ys if y.isdigit()), key=int, default="")
    lv = ys.get(last_y) or {}
    for f in ("dept", "period", "manager", "budget"):
        out[f] = r.get(f) or ""
        if not out[f] and lv.get(f):
            out[f] = lv[f]
            out["from"].append(f)
            out["note"] = f"{last_y}년 값"
    out["sub"] = ""
    out["chip"] = last_y if out["from"] else ""
    out["n_years"] = len(ys)
    return out


def _filter(rows: list[dict], q: str = "", dept: str = "", l1: str = "") -> list[dict]:
    q = (q or "").strip().lower()
    out = []
    for r in rows:
        if dept and dept not in r["depts"]:
            continue
        if l1 and r["l1"] != l1:
            continue
        if q and q not in " ".join([r["name"], *r["terms"], r["dept"], r["l2"], r["l3"]]).lower():
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------- 화면

TABS = ("all", "current", "past")


def sheet_docs(db, limit: int = 40) -> list[dict]:
    """엑셀에서 채우기에 고를 수 있는 문서 — 문서함·반입 문서 가운데 엑셀 원본이 디스크에 있는 것(최근 순)."""
    if db is None:
        return []
    try:
        with db._conn() as conn:
            got = conn.execute("SELECT id, filename, stored_path FROM documents WHERE stored_path LIKE ? OR stored_path LIKE ?"
                               " ORDER BY id DESC", ("%.xlsx", "%.xlsm")).fetchall()
    except Exception:
        return []
    out = []
    for i, name, path in got:
        if path and Path(str(path)).is_file():
            out.append({"id": int(i), "name": str(name or "")})
            if len(out) >= limit:
                break
    return out


def _page(request: Request, *, tab: str = "current", year: str = "", hidden: str = "", ok: str = "", err: str = "",
          sheet: dict | None = None):
    """사업 관리 — 세 갈래: 전체 / 현행 사업(올해 진행 중·기간 모름 포함, 고치고 지정) / 지난 사업(끝난 사업, 확인용 읽기).
    연도를 고르면 그해 진행한 사업만, 칸은 그해 값으로 보인다."""
    scope, user, acct = _scope_or_403(request)
    st = request.app.state
    accounts = getattr(st, "accounts", None) or {}
    rows = merged_rows(st.data_dir)
    link_projects(st.db, rows)
    if scope != "all":
        trim_projects(request, rows, user, acct)        # 볼 수 없는 프로젝트 이름은 보이지 않게
    shown = visible_rows(rows, scope, user, acct, show_hidden=bool(hidden) and scope == "all")
    this_year = int(time.strftime("%Y"))
    tab = tab if tab in TABS else "current"
    for r in rows:
        r["stage"] = stage(r, this_year)
    if scope == "dept":                                    # 총괄 책임자는 지난 사업 전체를 확인용으로(숨긴 줄 빼고, 읽기만)
        have = {r["key"] for r in shown}
        shown = shown + [r for r in visible_rows(rows, "all", user, acct) if r["stage"] == "past" and r["key"] not in have]
    n_all = len(shown)
    n_past = sum(1 for r in shown if r["stage"] == "past")
    n_cur = n_all - n_past
    in_tab = [r for r in shown if tab == "all" or (r["stage"] == "past") == (tab == "past")]
    ys: set[int] = set()
    for r in in_tab:                                       # 이 갈래 사업이 진행한 연도들 — 연도 고르기 목록
        a, b = row_span(r)
        if a:
            ys.update(range(max(a, 1990), min(b or this_year, this_year + 5) + 1))
    years = sorted(ys, reverse=True)
    sel = int(year) if str(year).isdigit() else (this_year if tab == "current" else 0)
    shown = [r for r in in_tab if not sel or runs_in(r, sel) or (tab == "current" and r["stage"] == "unknown")]
    heads, leads, clerks = _choices(accounts, scope, acct)
    may_create = grades.can_create_project(acct)
    name_of = {u: ((a.get("name") or "").strip() or u) for u, a in accounts.items()}
    people = {(a.get("name") or "").strip() for a in accounts.values() if a.get("active", True) and (a.get("name") or "").strip()}
    for r in shown:
        r["v"] = view(r, sel)
        r["v"]["has_account"] = any(p in people for p in _SPLIT.split(r["v"]["manager"]) if p)
        live = r["stage"] != "past"
        r["can_edit"] = scope == "all" and live
        r["can_assign"] = scope in ("all", "dept") and live and (scope == "all" or r in visible_rows([r], "dept", user, acct))
        r["can_link"] = live and (scope == "all" or r in visible_rows([r], scope, user, acct))
        r["can_new"] = r["can_link"] and may_create
        r["who"] = {f: [name_of.get(u, u) for u in r[f]] for f in ROLES}
        r["edit"] = {k: r.get(k) for k in ("key", *TEXT_FIELDS, *ROLES, "source")}
        r["edit"]["plist"] = [p for p in r.get("plist", []) if not p["archived"]]
        r["edit"]["years"] = {y: {f: v.get(f, "") for f in YEAR_EDIT} for y, v in r["years"].items()}
    from zzaimy.app.program_tree import ORDER
    l1s = [x for x in ORDER if any(r["l1"] == x for r in shown)] + sorted({r["l1"] for r in shown if r["l1"] and r["l1"] not in ORDER})
    depts = sorted({d for r in shown for d in r["depts"]})
    return st.templates.TemplateResponse(request, "programs_admin.html", st.page_ctx(request, {
        "rows": shown, "scope": scope, "can_edit": scope == "all" and tab != "past",
        "can_assign": scope in ("all", "dept") and tab != "past", "any_action": any(r["can_link"] for r in shown),
        "clerk_choices": clerks, "my_projects": _linkable_projects(st.db, scope, user) if any(r["can_link"] for r in shown) else [],
        "tab": tab, "year": sel, "years": years, "this_year": this_year, "n_all": n_all, "n_cur": n_cur, "n_past": n_past,
        "head_choices": heads, "lead_choices": leads, "name_of": name_of, "l1s": l1s, "depts": depts,
        "show_hidden": bool(hidden), "n_hidden": sum(1 for r in rows if r["hidden"]) if scope == "all" else 0,
        "sheet_docs": sheet_docs(st.db) if scope == "all" else [], "sheet": sheet, "labels": program_sheet.LABELS,
        "ok": ok, "err": err, "me_dept": acct.get("dept") or ""}))


@router.get("/programs/admin", response_class=HTMLResponse)
def programs_admin(request: Request, ok: str = "", err: str = "", hidden: str = "", tab: str = "current", year: str = ""):
    return _page(request, tab=tab, year=year, hidden=hidden, ok=ok, err=err)


@router.get("/programs/admin.csv")
def programs_admin_csv(request: Request, q: str = "", dept: str = "", l1: str = "", hidden: str = ""):
    """보이는 줄을 CSV 로(엑셀에서 바로 열리게 BOM 포함). 화면과 같은 권한·거르기."""
    scope, user, acct = _scope_or_403(request)
    st = request.app.state
    accounts = getattr(st, "accounts", None) or {}
    rows = merged_rows(st.data_dir)
    link_projects(st.db, rows)
    if scope != "all":
        trim_projects(request, rows, user, acct)
    rows = _filter(visible_rows(rows, scope, user, acct, show_hidden=bool(hidden) and scope == "all"), q, dept, l1)
    name_of = lambda u: f"{(accounts.get(u) or {}).get('name') or u}({u})"
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _, label in COLUMNS] + (["숨김"] if scope == "all" else []))
    for r in rows:
        v = view(r)
        vals = [", ".join(name_of(u) for u in r[k]) if k in ROLES else v[k] if k in v else r.get(k, "")
                for k, _ in COLUMNS]
        w.writerow(vals + (["숨김" if r["hidden"] else ""] if scope == "all" else []))
    fname = quote(f"사업 관리 {time.strftime('%Y-%m-%d')}.csv")
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


@router.post("/programs/admin/save")
async def programs_admin_save(request: Request):
    """줄 저장 — 관리자는 모든 칸과 새 줄(key 비움), 총괄 책임자는 보이는 줄의 사업 책임자·행정 담당자만. 장부 값과 같은 칸은 따로 적지 않는다."""
    scope, user, acct = _scope_or_403(request)
    if scope not in ("all", "dept"):
        raise HTTPException(403, "사업 책임자·행정 담당자 지정은 총괄 책임자와 관리자만 합니다")
    st = request.app.state
    accounts = getattr(st, "accounts", None) or {}
    form = await request.form()
    key = str(form.get("key") or "").strip()
    store = load_store(st.data_dir)
    rows = merged_rows(st.data_dir, store=store)
    by_key = {r["key"]: r for r in rows}
    ok_sets = dict(zip(ROLES, ({c["user"] for c in cs} for cs in _choices(accounts, scope, acct))))
    heads_ok, leads_ok, clerks_ok = ok_sets["heads"], ok_sets["leads"], ok_sets["clerks"]
    want_leads = list(dict.fromkeys(str(x) for x in form.getlist("leads") if str(x)))
    want_clerks = list(dict.fromkeys(str(x) for x in form.getlist("clerks") if str(x)))
    if scope == "dept":
        r = by_key.get(key)
        if r is None or r not in visible_rows(rows, scope, user, acct):
            raise HTTPException(404, "사업을 찾을 수 없습니다")
        if stage(r, int(time.strftime("%Y"))) == "past":
            return _back("지난 사업은 확인용입니다 — 고치지 않습니다", ok=False)
        o = store["rows"].setdefault(key, {})
        for f, want, allowed in (("leads", want_leads, leads_ok), ("clerks", want_clerks, clerks_ok)):
            keep = [u for u in r[f] if u not in allowed]              # 다른 부서 사람이 이미 지정돼 있으면 그대로 둔다
            if [u for u in want if u not in allowed and u not in r[f]]:
                return _back("사업 책임자·행정 담당자는 같은 부서의 해당 등급 계정에서 고릅니다", ok=False)
            o[f] = list(dict.fromkeys(keep + want))
        save_store(store, st.data_dir)
        return _back(f"「{r['name']}」 사업 책임자·행정 담당자를 저장했습니다")
    vals = {f: " ".join(str(form.get(f) or "").split())[:120] for f in TEXT_FIELDS}
    if len(vals["name"]) < 2:
        return _back("사업 이름을 적어 주세요", ok=False)
    want_heads = list(dict.fromkeys(str(x) for x in form.getlist("heads") if str(x)))
    old = by_key.get(key)
    if old is not None and stage(old, int(time.strftime("%Y"))) == "past":
        return _back("지난 사업은 확인용입니다 — 고치지 않습니다", ok=False)
    y_year = str(form.get("y_year") or "").strip()
    y_vals = {f: " ".join(str(form.get("y_" + f) or "").split())[:120] for f in YEAR_EDIT}
    if y_year and not re.fullmatch(r"(19|20)\d{2}", y_year):
        return _back("연도는 네 자리 숫자로 적어 주세요(예: 2026)", ok=False)

    def years_with_hand(ys: dict) -> dict:
        """편집 창의 연도별 값을 years 에 — 바뀐 칸만 적고 손으로 고친 칸(by_hand)으로 표시한다."""
        ys = {y: dict(v) for y, v in (ys or {}).items()}
        if not y_year:
            return ys
        cur = ys.setdefault(y_year, {})
        hand = set(cur.get("by_hand") or [])
        for f, v in y_vals.items():
            if v == (cur.get(f) or ""):
                continue
            if v:
                cur[f] = v
            else:
                cur.pop(f, None)
            if f == "budget":
                cur.pop("budget_won", None)
            hand.add(f)
        if hand:
            cur["by_hand"] = sorted(hand)
        if not any(k for k in cur if k != "by_hand"):
            ys.pop(y_year, None)
        return ys

    bad = [u for u in want_heads if u not in heads_ok and u not in (old or {}).get("heads", [])] + \
          [u for u in want_leads if u not in leads_ok and u not in (old or {}).get("leads", [])] + \
          [u for u in want_clerks if u not in clerks_ok and u not in (old or {}).get("clerks", [])]
    if bad:
        return _back(f"지정할 수 없는 계정입니다: {', '.join(bad[:5])} — 총괄 책임자는 총괄 책임자 등급, 사업 책임자는 사업 책임자 이상, "
                     "행정 담당자는 행정 담당자 이상 교직원", ok=False)
    if not key:
        key = slug(vals["name"])
        if key in by_key:
            return _back(f"「{vals['name']}」 사업은 이미 있습니다", ok=False)
        store["added"][key] = {**vals, "added_by": user, "added_at": time.strftime("%Y-%m-%d")}
        store["rows"][key] = {k: v for k, v in {"heads": want_heads, "leads": want_leads, "clerks": want_clerks,
                                                    "years": years_with_hand({})}.items() if v}
        save_store(store, st.data_dir)
        return _back(f"「{vals['name']}」 줄을 더했습니다")
    if old is None:
        raise HTTPException(404, "사업을 찾을 수 없습니다")
    base = next((r for r in base_rows(load_ledger(st.data_dir)) if r["key"] == key), None)
    o = {k: v for k, v in (store["rows"].get(key) or {}).items() if k in ("hidden",)}
    o["years"] = years_with_hand((store["rows"].get(key) or {}).get("years") or {})
    if old["source"] in ("추가", "엑셀"):
        store["added"][key] = {**store["added"].get(key, {}), **vals}
    else:
        o.update({f: v for f, v in vals.items() if base is None or v != base.get(f, "")})
    o["heads"], o["leads"], o["clerks"] = want_heads, want_leads, want_clerks
    store["rows"][key] = {k: v for k, v in o.items() if v}
    save_store(store, st.data_dir)
    return _back(f"「{vals['name']}」 줄을 저장했습니다")


@router.post("/programs/admin/hide")
def programs_admin_hide(request: Request, key: str = Form(""), hidden: str = Form("1")):
    scope, _, _ = _scope_or_403(request)
    if scope != "all":
        raise HTTPException(403, "줄 숨기기는 관리자만 합니다")
    st = request.app.state
    store = load_store(st.data_dir)
    rows = {r["key"]: r for r in merged_rows(st.data_dir, store=store)}
    if key not in rows:
        raise HTTPException(404, "사업을 찾을 수 없습니다")
    o = store["rows"].setdefault(key, {})
    if hidden == "1":
        o["hidden"] = True
    else:
        o.pop("hidden", None)
    if not o:
        store["rows"].pop(key, None)
    save_store(store, st.data_dir)
    return _back(f"「{rows[key]['name']}」 줄을 {'숨겼습니다' if hidden == '1' else '다시 보이게 했습니다'}",
                 anchor="")


# ---------------------------------------------------------------- 엑셀에서 채우기(관리자)

_TOKEN = re.compile(r"[0-9a-f]{16}")
MAX_UPLOAD = 20 * 1024 * 1024


def _tmp(data_dir) -> Path:
    return _dir(data_dir) / "tmp" / "program_sheet"


def _admin_only(request: Request) -> str:
    scope, user, _ = _scope_or_403(request)
    if scope != "all":
        raise HTTPException(403, "엑셀에서 채우기는 관리자만 합니다")
    return user


def not_program_keys(ledger: dict) -> set[str]:
    """장부가 「사업 아님」으로 확인한 이름의 열쇠 — 엑셀에 있어도 새 줄로 더하지 않는다."""
    return {k for e in ledger.get("programs") or [] if e.get("category") == "사업 아님"
            for t in [e.get("name") or "", *(e.get("terms") or [])] if (k := group_key(t))}


async def _sources(request: Request, form, token: str) -> tuple[list[tuple[Path, str]], list[str], list[int], str]:
    """고른 문서(문서 id)와 올린 파일 → ([(경로, 이름)], 오류, 문서 id, 올린 파일 열쇠). 올린 파일은 data 의 tmp 에 잠시 둔다."""
    st = request.app.state
    errs: list[str] = []
    ids: list[int] = []
    raw = [str(x) for x in form.getlist("doc")] + re.split(r"[\s,]+", str(form.get("doc_ids") or ""))
    for x in raw:
        x = x.strip()
        if x.isdigit() and int(x) not in ids:
            ids.append(int(x))
        elif x:
            errs.append(f"문서 번호가 아닙니다: {x[:20]}")
    out: list[tuple[Path, str]] = []
    for i in ids:
        d = st.db.get_document(i) if st.db is not None else None
        p = Path(str((d or {}).get("stored_path") or ""))
        if not d:
            errs.append(f"문서 {i}: 없습니다")
        elif p.suffix.lower() not in (".xlsx", ".xlsm") or not p.is_file():
            errs.append(f"문서 {i}: 엑셀 원본 파일을 열 수 없습니다")
        else:
            out.append((p, f"{d.get('filename') or p.name}"))
    tmp = _tmp(st.data_dir)
    ups = [f for f in form.getlist("files") if getattr(f, "filename", "")]
    if ups:
        token = secrets.token_hex(8)
        tmp.mkdir(parents=True, exist_ok=True)
        for old in tmp.glob("*"):                          # 하루 지난 임시 파일은 지운다
            if time.time() - old.stat().st_mtime > 86400:
                old.unlink(missing_ok=True)
        for n, f in enumerate(ups):
            name = Path(f.filename).name
            if Path(name).suffix.lower() not in (".xlsx", ".xlsm"):
                errs.append(f"{name}: 엑셀(.xlsx) 파일만 읽습니다")
                continue
            data = await f.read()
            if len(data) > MAX_UPLOAD:
                errs.append(f"{name}: 20MB 보다 큽니다")
                continue
            (tmp / f"{token}-{n}-{name}").write_bytes(data)
    if token and _TOKEN.fullmatch(token):
        for p in sorted(tmp.glob(f"{token}-*")):
            out.append((p, p.name.split("-", 2)[-1]))
    else:
        token = ""
    return out, errs, ids, token


def _plan(request: Request, form, sources, overwrite: bool, user: str):
    st = request.app.state
    items, errs = [], []
    for p, label in sources:
        try:
            got = program_sheet.read_book(p, hint=label)
        except Exception as e:                             # 깨진 파일·암호 걸린 파일
            errs.append(f"{label}: 읽지 못했습니다({type(e).__name__})")
            continue
        if not got:
            errs.append(f"{label}: 「사업명」 머리줄이나 연도를 찾지 못했습니다")
        items.extend(got)
    store = load_store(st.data_dir)
    ledger = load_ledger(st.data_dir)
    rows = merged_rows(st.data_dir, ledger=ledger, store=store)
    summary, new_store = program_sheet.plan(items, rows, store, overwrite=overwrite, not_program=not_program_keys(ledger), by=user)
    return summary, new_store, errs


@router.post("/programs/admin/sheet/preview", response_class=HTMLResponse)
async def programs_admin_sheet_preview(request: Request):
    """엑셀에서 채우기 미리보기 — 저장하지 않고 맞춘 사업·새 줄·모호·채울 칸 수를 보인다."""
    user = _admin_only(request)
    form = await request.form()
    sources, errs, ids, token = await _sources(request, form, str(form.get("token") or ""))
    if not sources:
        return _back(" · ".join(errs) or "문서를 고르거나 엑셀 파일을 올려 주세요", ok=False)
    overwrite = bool(form.get("overwrite"))
    summary, _, errs2 = _plan(request, form, sources, overwrite, user)
    sheet = {"summary": summary, "errors": errs + errs2, "ids": ids, "token": token, "names": [n for _, n in sources]}
    return _page(request, tab="all", year="0", sheet=sheet)


@router.post("/programs/admin/sheet/apply")
async def programs_admin_sheet_apply(request: Request):
    """엑셀에서 채우기 적용 — 미리보기와 같은 계산을 다시 해서 program_admin.json 에 쓴다(이전 판은 history 에). 장부·그래프는 그대로."""
    user = _admin_only(request)
    form = await request.form()
    sources, errs, _, token = await _sources(request, form, str(form.get("token") or ""))
    if not sources:
        return _back(" · ".join(errs) or "고른 문서가 없습니다", ok=False)
    summary, new_store, _ = _plan(request, form, sources, bool(form.get("overwrite")), user)
    save_store(new_store, request.app.state.data_dir)
    if token:
        for p in _tmp(request.app.state.data_dir).glob(f"{token}-*"):
            p.unlink(missing_ok=True)
    c = summary["cells"]
    msg = (f"엑셀에서 채웠습니다 — 맞춘 사업 {len(summary['matched'])}개 · 새 줄 {len(summary['new'])}개 · "
           f"채운 칸 {c['fill']}개 · 바뀐 칸 {c['change']}개")
    if c["kept"]:
        msg += f" · 손으로 고친 칸 {c['kept']}개는 그대로"
    if summary["ambiguous"]:
        msg += f" · 모호해서 건너뛴 이름 {len(summary['ambiguous'])}개"
    return RedirectResponse(f"/programs/admin?tab=all&year=0&ok={quote(msg)}", status_code=303)


# ---------------------------------------------------------------- 프로젝트 연결

def program_value(key: str, year: int | str) -> str:
    """projects.program 에 적는 값 — 「<사업 열쇠>|<연도>」(link_projects·roles_for_project 가 읽는 꼴)."""
    return f"{key}|{year}" if str(year or "").strip() else key


def choices(scope: str, user: str = "", account: dict | None = None, data_dir=None, year: int | None = None) -> list[dict]:
    """프로젝트에서 사업을 고를 때 쓸 목록 — 이 사람이 사업 관리에서 볼 수 있는 숨기지 않은 사업.
    [{key, name, l1, l2, dept, stage, years}] (현행 먼저, 이름 순). scope 는 grades.program_scope(account) 값이며,
    빈 값(일반 등급)이어도 사업을 고를 수는 있어야 하므로 그때는 숨기지 않은 사업 전체를 준다."""
    rows = merged_rows(data_dir)
    this_year = int(year or time.strftime("%Y"))
    picked = visible_rows(rows, scope, user, account or {}) if scope in ("dept", "own") else [r for r in rows if not r["hidden"]]
    out = []
    for r in picked:
        a, b = row_span(r)
        out.append({"key": r["key"], "name": r["name"], "l1": r["l1"], "l2": r["l2"], "dept": r["dept"],
                    "stage": stage(r, this_year),
                    "years": sorted({int(y) for y in r["years"] if y.isdigit()}
                                    | (set(range(a, (b or this_year) + 1)) if a and (b or this_year) - a < 30 else set()))})
    out.sort(key=lambda x: (x["stage"] == "past", x["name"]))
    return out


def _linkable_projects(db, scope: str, user: str, limit: int = 300) -> list[dict]:
    """사업에 이을 수 있는 프로젝트 — 관리자는 진행 중인 프로젝트 전부, 그 밖에는 자기가 만든 프로젝트(최근 순)."""
    if db is None:
        return []
    sql = "SELECT id, name, program, owner FROM projects WHERE archived = 0"
    args: tuple = ()
    if scope != "all":
        sql += " AND owner = ?"
        args = (user,)
    try:
        with db._conn() as conn:
            got = conn.execute(sql + " ORDER BY id DESC", args).fetchall()
    except Exception:
        return []
    return [{"id": int(i), "name": str(n or ""), "program": str(p or ""), "owner": str(o or "")} for i, n, p, o in got[:limit]]


def _row_for(request: Request, key: str, live_only: bool = True) -> tuple[dict, str, str, dict]:
    """열쇠 → (이 사람이 볼 수 있는 줄, scope, user, acct). 총괄 책임자는 지난 사업도 읽기로 본다."""
    scope, user, acct = _scope_or_403(request)
    rows = merged_rows(request.app.state.data_dir)
    r = next((x for x in rows if x["key"] == key), None)
    if r is None:
        raise HTTPException(404, "사업을 찾을 수 없습니다")
    past = stage(r, int(time.strftime("%Y"))) == "past"
    seen = scope == "all" or r in visible_rows([r], scope, user, acct) or (scope == "dept" and past and not r["hidden"])
    if not seen:
        raise HTTPException(404, "사업을 찾을 수 없습니다")
    if live_only and past:
        raise HTTPException(409, "지난 사업은 확인용입니다")
    return r, scope, user, acct


def trim_projects(request: Request, rows: list[dict], user: str, acct: dict) -> None:
    """보안: 줄마다 이어진 프로젝트 목록(plist)을 이 사람이 볼 수 있는 것만 남긴다 — 자기 프로젝트, 공유받은 프로젝트,
    진행 현황을 볼 수 있는 프로젝트(grades.can_view_activity). 남은 수는 hidden_projects 로(이름은 보이지 않게)."""
    from zzaimy.app import access_policy as _ap, project_access as _pa
    st = request.app.state
    accounts = getattr(st, "accounts", None) or {}
    v = _ap.request_viewer(request)
    seen: dict[int, bool] = {}

    def _may(pid: int) -> bool:
        if pid in seen:
            return seen[pid]
        proj = st.db.get_project(pid) if st.db is not None else None
        ok = False
        if proj:
            owner_dept = str((accounts.get(proj.get("owner") or "") or {}).get("dept") or "").strip()
            ok = (proj.get("owner") == user
                  or bool(_pa.access(proj, user=user, role=v["role"], dept=v["dept"] or "", also=v["also"],
                                     share=_pa.shares(st.db, proj, owner_dept)))
                  or grades.can_view_activity(proj, user=user, account=acct, lead=_pa.lead(st.db, proj, accounts),
                                              project_dept=owner_dept, data_dir=st.data_dir))
        seen[pid] = ok
        return ok
    for r in rows:
        allowed = [p for p in r.get("plist") or [] if _may(int(p["id"]))]
        r["hidden_projects"] = len(r.get("plist") or []) - len(allowed)
        r["plist"] = allowed


@router.get("/programs/admin/row")
def programs_admin_row(request: Request, key: str = ""):
    """줄 상세(오른쪽 패널) — 연도별 값, 지정 계정, 이어진 프로젝트, 그 프로젝트의 최근 문서."""
    r, scope, user, acct = _row_for(request, key, live_only=False)
    st = request.app.state
    link_projects(st.db, [r])
    accounts = getattr(st, "accounts", None) or {}
    label = lambda u: ((accounts.get(u) or {}).get("name") or "").strip() or u
    docs = []
    if scope != "all":
        trim_projects(request, [r], user, acct)
    live = [p["id"] for p in r["plist"] if not p["archived"]]
    if live and st.db is not None:
        marks = ",".join("?" for _ in live[:50])
        try:
            with st.db._conn() as conn:
                got = conn.execute(f"SELECT id, filename, project_id FROM documents WHERE project_id IN ({marks})"
                                   " ORDER BY id DESC LIMIT 12", tuple(live[:50])).fetchall()
            docs = [{"id": int(i), "name": str(n or ""), "project_id": int(p)} for i, n, p in got]
            if scope != "all":                             # 문서도 열람 규칙(access_policy.visible) 안의 것만
                from zzaimy.app import access_policy as _ap2
                vv = _ap2.request_viewer(request)
                docs = [d for d in docs if _ap2.visible(st.db.get_document(d["id"]) or {}, **vv)]
        except Exception:
            docs = []
    years = [{"year": y, **{f: v.get(f, "") for f in ("budget", "manager", "dept", "period", "sub", "status", "from")},
              "by_hand": list(v.get("by_hand") or [])}
             for y, v in sorted(r["years"].items(), key=lambda kv: kv[0], reverse=True)]
    latest = years[0] if years else {}                     # 위쪽 요약도 표와 같게 — 빈칸이면 가장 최근 해의 값
    pick = lambda f: r.get(f) or latest.get(f, "")
    return {"key": r["key"], "name": r["name"], "l1": r["l1"], "l2": r["l2"], "l3": r["l3"], "dept": pick("dept"),
            "period": pick("period"), "budget": pick("budget"), "manager": pick("manager"), "source": r["source"], "terms": r["terms"][:12],
            "stage": stage(r, int(time.strftime("%Y"))), "years": years,
            "roles": {f: [label(u) for u in r[f]] for f in ROLES},
            "projects": r["plist"], "hidden_projects": r.get("hidden_projects", 0), "docs": docs}


@router.post("/programs/admin/link")
def programs_admin_link(request: Request, key: str = Form(""), project_id: str = Form(""), year: str = Form(""),
                        unlink: str = Form("")):
    """프로젝트 잇기·끊기 — projects.program 을 「<사업 열쇠>|<연도>」로(끊으면 빈 값). 관리자는 아무 프로젝트나,
    그 밖에는 볼 수 있는 현행 사업에 자기가 만든 프로젝트만."""
    r, scope, user, _ = _row_for(request, key)
    st = request.app.state
    if not project_id.isdigit():
        return _back("프로젝트를 골라 주세요", ok=False)
    proj = st.db.get_project(int(project_id))
    if not proj:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다")
    if scope != "all" and proj.get("owner") != user:
        raise HTTPException(403, "자기가 만든 프로젝트만 사업에 잇습니다")
    if unlink:
        if str(proj.get("program") or "").split("|", 1)[0] not in r["ids"]:
            return _back("이 사업에 이어진 프로젝트가 아닙니다", ok=False)
        st.db.set_project_program(int(project_id), "")
        return _back(f"「{proj.get('name')}」를 「{r['name']}」에서 끊었습니다")
    y = year.strip() or time.strftime("%Y")
    if not re.fullmatch(r"(19|20)\d{2}", y):
        return _back("연도는 네 자리 숫자로 적어 주세요", ok=False)
    st.db.set_project_program(int(project_id), program_value(r["key"], y))
    return _back(f"「{proj.get('name')}」를 「{r['name']}」 {y}년에 이었습니다")


@router.post("/programs/admin/new-project")
def programs_admin_new_project(request: Request, key: str = Form(""), year: str = Form(""), name: str = Form("")):
    """이 사업·연도로 새 프로젝트 — 만들 수 있는 계정(grades.can_create_project)만. 만든 사람이 주인이 된다."""
    r, scope, user, acct = _row_for(request, key)
    if not grades.can_create_project(acct):
        raise HTTPException(403, "프로젝트를 만들 수 있는 등급이 아닙니다")
    y = year.strip() or time.strftime("%Y")
    if not re.fullmatch(r"(19|20)\d{2}", y):
        return _back("연도는 네 자리 숫자로 적어 주세요", ok=False)
    title = " ".join(name.split())[:80] or f"{y} {r['name']}"[:80]
    pid = request.app.state.db.create_project("grant", title, owner=user, program=program_value(r["key"], y))
    return RedirectResponse(f"/project/{pid}", status_code=303)
