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

- 연도별 값: 줄마다 years{연도: {예산 6갈래·합계·책임자·행정 담당자·부서·기간 …, tasks(세부 과제 줄), order(엑셀 줄 순서)}}.
  사업 현황 엑셀(program_sheet)에서 채우거나 편집 창에서 손으로 적는다. 손으로 적은 칸(by_hand)은 엑셀 채우기가 기본으로 덮지 않는다.
  해마다의 기준일·대상기간·원본 열 순서·합계 줄은 저장값의 sheets{연도} 에 둔다
- 연도별 보기: 연도를 고르면 그해 진행한 사업만, 엑셀과 같은 순서·묶음의 표(구분·순·사업명·지원기관·책임자·행정 담당자·업무부서·합계,
  「기관·기간」「예산 내역」 열 묶음은 켜고 끔, 금액 단위 원·천원·백만원, 맨 아래 합계 줄)와 위쪽 요약 띠. 전체 연도 보기에는 연도별 막대
- 책임자 이름: 엑셀에 적힌 사람 이름(글). 계정 지정 칸(총괄 책임자·사업 책임자·행정 담당자)과 다르며, 이름이 계정 이름과 같으면 「계정 있음」만 표시한다

화면: GET /programs/admin · 줄 상세 GET /programs/admin/row · 내려받기 GET /programs/admin.csv · 연도 엑셀 GET /programs/admin.xlsx ·
저장 POST /programs/admin/save ·
목록에서 빼기·다시 넣기 POST /programs/admin/hide · 합치기 미리보기 GET /programs/admin/merge/preview · 합치기 POST /programs/admin/merge ·
떼어 내기 POST /programs/admin/unmerge · 프로젝트 잇기·끊기 POST /programs/admin/link · 새 프로젝트 POST /programs/admin/new-project
엑셀에서 채우기(관리자): 미리보기 POST /programs/admin/sheet/preview · 적용 POST /programs/admin/sheet/apply
"""
from __future__ import annotations

import copy
import csv
import hashlib
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

from zzaimy.app import grades, org_lock, program_sheet
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
    """관리 저장값 — rows·added 와(있으면) sheets(해마다의 엑셀 정보)·requests(신청)·notes(사업 관리 알림)."""
    try:
        got = json.loads((_dir(data_dir) / STORE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = {}
    out = {"rows": dict(got.get("rows") or {}), "added": dict(got.get("added") or {})}
    for k in ("sheets", "requests", "notes"):
        if got.get(k):
            out[k] = got[k]
    return out


def save_store(store: dict, data_dir=None) -> str:
    """저장 — 이전 판은 history/program_admin.<시각>.json 으로 남기고 그 이름의 시각 부분을 돌려준다(가져오기 되돌리기에 쓴다).
    조직·계정 저장값과 같은 잠금(org_lock) 안에서 쓴다."""
    from zzaimy.app import org_lock
    with org_lock.LOCK:
        stamp = _save_store(store, data_dir)
        org_lock.note(_dir(data_dir) / STORE)
        return stamp


def _save_store(store: dict, data_dir=None) -> str:
    d = _dir(data_dir)
    p = d / STORE
    hist = d / "history"
    hist.mkdir(parents=True, exist_ok=True)
    base = time.strftime("%Y%m%d%H%M%S")
    stamp, n = base, 1
    while (hist / f"program_admin.{stamp}.json").exists():
        stamp, n = f"{base}-{n}", n + 1
    if p.is_file():
        shutil.copy2(p, hist / f"program_admin.{stamp}.json")
    else:                                                  # 첫 저장 — 빈 판을 남겨 되돌릴 자리를 만든다
        (hist / f"program_admin.{stamp}.json").write_text('{"rows": {}, "added": {}}', encoding="utf-8")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    _CACHE.clear()
    return stamp


def row_version(store: dict, key: str) -> str:
    """한 줄의 판 — 그 줄의 저장값(rows·added)의 지문. 편집 창은 읽은 때의 판을 함께 보내고, 저장 때 지금 판과 다르면 409."""
    part = {"row": (store.get("rows") or {}).get(key), "added": (store.get("added") or {}).get(key)}
    return hashlib.sha256(json.dumps(part, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


STALE = "다른 사람이 먼저 고쳤습니다. 새로 불러온 뒤 다시 저장해 주세요"


def store_hash(data_dir=None) -> str:
    try:
        return hashlib.sha256((_dir(data_dir) / STORE).read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


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
    """장부 줄 + 관리 화면에서 고친 값 + 더한 줄. 줄마다 ids(이 사업으로 보는 projects.program 열쇠)·origin(출처 칩)을 단다.
    합친 줄(merged_into)은 숨기고, 합쳐 받은 이름은 별칭(terms_add)으로 붙는다."""
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
        r["terms"] = list(dict.fromkeys([*(r.get("terms") or []), *(o.get("terms_add") or [])]))
        r["hidden"] = bool(o.get("hidden", r.get("hidden")))
        r["merged_into"] = o.get("merged_into") or ""
        r["not_program"] = bool(o.get("not_program"))
        r["log"] = list(o.get("log") or [])
        r["years"] = {str(y): dict(v) for y, v in (o.get("years") or {}).items() if str(y).isdigit() and isinstance(v, dict)}
        r["period"] = re.sub(r"(\d{4})-(\d{2})-(\d{2})(?:[ T]00:00(?::00)?)?", r"\1.\2.\3", str(r.get("period") or "")).strip()   # 엑셀 날짜 값(… 00:00:00)은 날짜만
        r["ids"] = {r["key"], *("program:" + k for t in [r["name"], *r["terms"]] if (k := _norm(t)))}
        r["depts"] = [d for d in _SPLIT.split(r.get("dept") or "") if d]
        excel = r["source"] == "엑셀" or any(v.get("from") for v in r["years"].values())
        r["origin"] = "excel" if excel else ("ledger" if r["source"] == "장부" else "added")
    return rows


ORIGIN = {"excel": "엑셀", "ledger": "문서에서 찾음", "added": "직접 더함"}


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
    """볼 수 있는 줄 — 관리자·총괄 책임자는 전부(숨긴 줄은 관리자가 고를 때만), 사업 책임자·행정 담당자는 지정된 사업만."""
    if scope == "all":
        return [r for r in rows if show_hidden or not r["hidden"]]
    if scope == "dept":
        return [r for r in rows if not r["hidden"]]
    return [r for r in rows if not r["hidden"] and any(user in r[f] for f in ROLES)]


def my_dept(acct: dict) -> str:
    return str((acct or {}).get("dept") or "").strip()


def owns(r: dict, scope: str, user: str, acct: dict) -> bool:
    """이 줄을 직접 고칠 수 있나(지난 사업 여부는 따로) — 관리자는 전부, 총괄 책임자는 주관 부서가 자기 부서이거나
    총괄 책임자로 지정된 사업. 그 밖은 신청으로."""
    if scope == "all":
        return True
    if scope != "dept":
        return False
    cov = covered(acct)
    return bool(cov & set(r["depts"])) or user in r["heads"] or any(h[5:] in cov for h in r["heads"] if h.startswith("dept:"))


def covered(acct: dict) -> set[str]:
    """총괄 책임자가 맡는 부서들 — _scope_or_403 이 채운 값(없으면 소속 부서 하나)."""
    return set((acct or {}).get("_cov") or ())


def admin_users(accounts: dict) -> list[str]:
    return sorted(u for u, a in (accounts or {}).items() if a.get("active", True) and grades.program_scope(a) == "all")


def add_note(store: dict, users, text: str, req: int | None = None) -> None:
    """사업 관리 알림 — 받는 사람마다 쌓고(최근 50개) 사업 관리 화면 위 띠로 보인다."""
    notes = store.setdefault("notes", {})
    for u in dict.fromkeys(users):
        lst = notes.setdefault(u, [])
        lst.append({"at": time.strftime("%Y-%m-%d %H:%M"), "text": text, "req": req, "seen": False})
        del lst[:-50]


def _choices(accounts: dict, scope: str, acct: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """(총괄 책임자 후보, 사업 책임자 후보, 행정 담당자 후보) — 사용 중인 교직원 가운데 총괄 책임자는 총괄 책임자 등급,
    사업 책임자는 사업 책임자·총괄 책임자 등급, 행정 담당자는 행정 담당자 이상. 총괄 책임자가 고르는 두 칸은 맡은 부서(겸직·하위 부서 포함) 사람만."""
    heads, leads, clerks = [], [], []
    cov = covered(acct) if scope != "all" else set()
    for u, a in sorted(accounts.items()):
        if not a.get("active", True) or a.get("role") not in grades.STAFF:
            continue
        g = grades.grade_of(a)
        item = {"user": u, "label": (a.get("name") or "").strip() or u, "dept": a.get("dept") or "", "grade": grades.label(g)}
        if g == "head":
            heads.append(item)
        same = scope == "all" or (a.get("dept") or "").strip() in cov
        if g in ("lead", "head") and same:
            leads.append(item)
        if g in ("clerk", "lead", "head") and same:
            clerks.append(item)
    return heads, leads, clerks


def _back(msg: str, ok: bool = True, anchor: str = "") -> RedirectResponse:
    """결과 글을 붙여 사업 관리로 — 갈래·연도는 쿠키(pa_view)로 보던 대로 연다."""
    return RedirectResponse(f"/programs/admin?{'ok' if ok else 'err'}={quote(msg)}{anchor}", status_code=303)


def _scope_or_403(request: Request) -> tuple[str, str, dict]:
    """(scope, 계정, 판정용 계정 값). scope 는 소속 부서 등급(grades.program_scope). 총괄 책임자가 맡는 부서는 반드시
    grades.covered_depts(…, "head") 로 정하고 acct["_cov"] 에 둔다 — 겸직으로 어떤 부서의 총괄 책임자이면(소속 등급이 낮아도)
    그 부서 범위의 「dept」 로 연다. 테스트 보기 중에는 고른 등급·부서만 쓴다."""
    user, acct = _me(request)
    data_dir = getattr(request.app.state, "data_dir", None)
    scope = grades.program_scope(acct)
    cov: set[str] = set()
    if getattr(request.state, "view_as", None):
        if scope == "dept" and my_dept(acct):
            cov = {my_dept(acct)}
    else:
        try:
            cov = set(grades.covered_depts(user, acct, data_dir, "head"))
        except Exception:
            cov = {my_dept(acct)} if scope == "dept" and my_dept(acct) else set()
        if scope in ("own", "") and cov:
            scope = "dept"                                     # 겸직 총괄 책임자
    if not scope:
        raise HTTPException(403, "사업 관리는 사업 책임자 이상만 볼 수 있습니다")
    acct["_cov"] = cov
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


def _filter(rows: list[dict], q: str = "", dept=None, l1=None, st=None, mi=None, src: str = "", year: int = 0) -> list[dict]:
    """화면의 거르기와 같은 규칙 — 찾기 글, 부서·대분류·구분·지원기관(여럿 고르면 그 가운데 하나), 출처(엑셀에 없는 사업만)."""
    as_list = lambda v: [x for x in ([v] if isinstance(v, str) else (v or [])) if x]
    dept, l1, st, mi = as_list(dept), as_list(l1), as_list(st), as_list(mi)
    q = (q or "").strip().lower()
    out = []
    for r in rows:
        yv = (r.get("years") or {}).get(str(year)) or {} if year else program_sheet.latest(r.get("years") or {})
        if dept and not set(dept) & set(r["depts"]):
            continue
        if l1 and r["l1"] not in l1:
            continue
        if st and not set(st) & set(program_sheet.chips(yv.get("status") or "")):
            continue
        if mi and not set(mi) & set(program_sheet.chips(yv.get("ministry") or "")):
            continue
        if src == "noexcel" and r["origin"] == "excel":
            continue
        if q and q not in " ".join([r["name"], *r["terms"], r["dept"], r["l2"], r["l3"], yv.get("manager") or "",
                                    yv.get("officer") or ""]).lower():
            continue
        out.append(r)
    return out


def _filters(request: Request) -> dict:
    qp = request.query_params
    return {"q": qp.get("q") or "", "dept": qp.getlist("dept"), "l1": qp.getlist("l1"), "st": qp.getlist("st"),
            "mi": qp.getlist("mi"), "src": qp.get("src") or ""}


TABS = ("all", "current", "past")
PROBE_CACHE = "program_sheet_probe.json"


def sheet_docs(db, data_dir=None, limit: int = 80) -> tuple[list[dict], list[dict]]:
    """가져오기 창의 문서함 목록 — (사업 현황 엑셀, 다른 엑셀). 「사업명」 머리줄이 있는가는 문서마다 판정해 두고
    파일 지문(크기·수정 시각)이 바뀔 때만 다시 본다(cache/program_sheet_probe.json)."""
    if db is None:
        return [], []
    try:
        with db._conn() as conn:
            got = conn.execute("SELECT id, filename, stored_path, created_at FROM documents WHERE "
                               + " OR ".join("lower(stored_path) LIKE ?" for _ in SHEET_EXT) + " ORDER BY id DESC",
                               tuple("%" + e for e in SHEET_EXT)).fetchall()
    except Exception:
        return [], []
    cpath = _dir(data_dir) / "cache" / PROBE_CACHE
    try:
        cache = json.loads(cpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    changed = False
    good, other = [], []
    for i, name, path, at in got:
        p = Path(str(path or ""))
        if not p.is_file() or p.name.startswith("~$"):
            continue
        stt = p.stat()
        fp = f"{stt.st_size}:{int(stt.st_mtime)}"
        c = cache.get(str(i))
        if not c or c.get("fp") != fp:
            c = {"fp": fp, **program_sheet.probe(p)}
            cache[str(i)] = c
            changed = True
        item = {"id": int(i), "name": str(name or ""), "year": c.get("year"), "at": str(at or "")[:10]}
        (good if c.get("ok") else other).append(item)
        if len(good) + len(other) >= limit:
            break
    if changed:
        try:
            cpath.parent.mkdir(parents=True, exist_ok=True)
            cpath.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    good.sort(key=lambda d: (-(d["year"] or 0), -d["id"]))
    return good, other


def _select(request: Request, tab: str = "current", year: str = "", hidden: str = "") -> dict:
    """화면·내려받기가 같이 쓰는 줄 고르기 — 권한 범위(scope) 안의 줄, 갈래(tab), 연도(그해 진행한 사업만).
    정렬 기본: 연도 보기는 그해 합계 큰 순, 전체 연도는 이름 순(화면에서 머리를 눌러 바꾼다)."""
    scope, user, acct = _scope_or_403(request)
    st = request.app.state
    store = load_store(st.data_dir)
    rows = merged_rows(st.data_dir, store=store)
    link_projects(st.db, rows)
    if scope != "all":
        trim_projects(request, rows, user, acct)        # 볼 수 없는 프로젝트 이름은 보이지 않게
    shown = visible_rows(rows, scope, user, acct, show_hidden=bool(hidden) and scope == "all")
    this_year = int(time.strftime("%Y"))
    tab = tab if tab in TABS else "current"
    for r in rows:
        r["stage"] = stage(r, this_year)
    n_all = len(shown)
    n_past = sum(1 for r in shown if r["stage"] == "past")
    in_tab = [r for r in shown if tab == "all" or (r["stage"] == "past") == (tab == "past")]
    ys = {int(y) for r in in_tab for y in r["years"] if str(y).isdigit()} | {this_year}
    sel = int(year) if str(year).isdigit() else (this_year if tab == "current" else 0)
    if sel:
        ys.add(sel)
    picked = [r for r in in_tab if not sel or runs_in(r, sel) or (tab == "current" and r["stage"] == "unknown")]
    if sel:
        def order(r):
            yv = r["years"].get(str(sel)) or {}
            return (not yv, -(program_sheet.year_money(yv)["budget"] or 0), r["name"])
        picked.sort(key=order)
    else:
        picked.sort(key=lambda r: r["name"])
    return {"scope": scope, "user": user, "acct": acct, "store": store, "rows": rows, "in_tab": in_tab, "shown": picked,
            "tab": tab, "sel": sel, "this_year": this_year, "years": sorted(ys),
            "n_all": n_all, "n_past": n_past, "n_cur": n_all - n_past}


def _unit(unit: str) -> str:
    return unit if unit in program_sheet.UNITS else "auto"


def year_cells(r: dict, year: int, unit: str = "auto") -> dict:
    """연도별 보기의 한 줄 — 그해 값(없으면 줄 값), 예산 6갈래·합계(원과 고른 단위 글), 세부 과제 줄."""
    yv = (r.get("years") or {}).get(str(year)) or {}
    m = program_sheet.year_money(yv)
    fmt = lambda d: {f: program_sheet.fmt_unit(d.get(f), unit) for f in program_sheet.MONEY}
    tasks = []
    for t in yv.get("tasks") or []:
        tm = {f: t.get(f + "_won") for f in program_sheet.MONEY}
        tasks.append({**{k: v for k, v in t.items() if not k.endswith("_won")}, "m": tm, "mf": fmt(tm),
                      "chips": program_sheet.chips(t.get("status") or "")})
    pick = lambda f: yv.get(f) or ("" if yv else r.get(f) or "")
    return {"has": bool(yv), "seq": str(yv.get("seq") or ""), "chips": program_sheet.chips(yv.get("status") or ""),
            "ministry": yv.get("ministry") or "", "agency": yv.get("agency") or "", "period": yv.get("period") or r.get("period") or "",
            "year_period": yv.get("year_period") or "", "manager": pick("manager"), "officer": yv.get("officer") or "",
            "dept": yv.get("dept") or r.get("dept") or "", "account_dept": yv.get("account_dept") or "", "majors": yv.get("majors") or "",
            "m": m, "mf": fmt(m), "tasks": tasks, "hand_budget": "budget" in (yv.get("by_hand") or []),
            "budget_text": yv.get("budget") or ""}


def _dept_name(d: str) -> str:
    """부서 이름 — 부서 사전(dept_dictionary.normalize)이 있으면 별칭을 정식 이름으로, 없으면 그대로(원래 글은 칸 툴팁)."""
    try:
        from zzaimy.app import dept_dictionary   # 다른 작업자가 여는 모듈 — 아직 없을 수 있다
        return dept_dictionary.normalize(d) or d
    except Exception:
        return d


class _NameOf(dict):
    """계정 → 보이는 이름. 「dept:부서」 값은 「부서(부서)」."""

    def get(self, k, default=None):
        if isinstance(k, str) and k.startswith("dept:"):
            return f"{k[5:]}(부서)"
        return super().get(k, default if default is not None else k)


def _share(a: int, b: int) -> str:
    return f"{a / b * 100:.1f}%" if b else "—"


def year_summary(picked: list[dict], in_tab: list[dict], year: int, unit: str) -> dict:
    """요약 띠 — 그해 값이 있는 사업 수, 총 예산, 국고 비중, 신규·종료 수, 지난해 대비 증감(같은 권한·갈래 안의 줄로)."""
    cur = [r for r in picked if r["y"]["has"]]
    total = sum(r["y"]["m"]["budget"] or 0 for r in cur)
    nat = sum(r["y"]["m"]["national"] or 0 for r in cur)
    prev = [r for r in in_tab if str(year - 1) in (r.get("years") or {})]
    p_total = sum(program_sheet.year_money(r["years"][str(year - 1)])["budget"] or 0 for r in prev)
    delta = total - p_total
    f = lambda n: program_sheet.fmt_unit(n, unit)
    return {"n": len(cur), "total": f(total), "national_share": _share(nat, total),
            "n_new": sum(1 for r in cur if "신규" in r["y"]["chips"]), "n_end": sum(1 for r in cur if "종료" in r["y"]["chips"]),
            "prev": bool(prev), "prev_n": len(prev), "dn": len(cur) - len(prev),
            "delta": ("+" if delta > 0 else "") + f(delta), "delta_pct": (f"{delta / p_total * 100:+.1f}%" if p_total else ""),
            "unit": program_sheet.UNITS[unit][1]}


def year_trend(in_tab: list[dict]) -> list[dict]:
    """전체 연도 보기의 연도별 막대 — 해마다 그해 값이 있는 사업 수와 총 예산(막대 길이는 가장 큰 해 대비 %)."""
    agg: dict[int, list[int]] = {}
    for r in in_tab:
        for y, v in (r.get("years") or {}).items():
            if str(y).isdigit():
                a = agg.setdefault(int(y), [0, 0])
                a[0] += 1
                a[1] += program_sheet.year_money(v)["budget"] or 0
    if not agg:
        return []
    mn, mb = max(a[0] for a in agg.values()) or 1, max(a[1] for a in agg.values()) or 1
    return [{"year": y, "n": n, "total": b, "eok": f"{b / 10 ** 8:,.1f}억 원", "wn": round(n / mn * 100), "wb": round(b / mb * 100)}
            for y, (n, b) in sorted(agg.items())]


EDIT_YEAR = ("status", "year_period", "manager", "officer", "dept", *program_sheet.SPLIT, "budget")


def _edit_years(r: dict) -> dict:
    """편집 창의 연도별 표 — 해마다 구분·당해 기간·책임자·행정 담당자·업무부서·예산 6갈래(원)·합계·세부 과제, 어느 칸이 엑셀에서 왔고 어느 칸을 손으로 고쳤나."""
    out = {}
    for y, v in sorted(r["years"].items()):
        m = program_sheet.year_money(v)
        out[y] = {"status": v.get("status", ""), "year_period": v.get("year_period", ""), "manager": v.get("manager", ""),
                  "officer": v.get("officer", ""), "dept": v.get("dept", ""), "period": v.get("period", ""),
                  "money": {f: m[f] for f in (*program_sheet.SPLIT, "budget")}, "budget_text": v.get("budget", ""),
                  "excel": bool(v.get("from")), "hand": list(v.get("by_hand") or []),
                  "tasks": [{"sub": t.get("sub", ""), "status": t.get("status", ""), "year_period": t.get("year_period", ""),
                             "manager": t.get("manager", ""), "officer": t.get("officer", ""), "dept": t.get("dept", ""),
                             "money": {f: t.get(f + "_won") for f in program_sheet.SPLIT}} for t in v.get("tasks") or []]}
    return out


def merge_targets(rows: list[dict]) -> list[dict]:
    """합칠 곳 고르기 목록 — 목록에 보이는(빼지 않은) 모든 갈래의 사업. 이름·별칭·부서·연도로 찾는다."""
    out = []
    for r in sorted(rows, key=lambda x: x["name"]):
        if r["hidden"]:
            continue
        a, b = row_span(r)
        out.append({"key": r["key"], "name": r["name"], "dept": r.get("dept") or "", "terms": [t for t in r["terms"] if t != r["name"]][:6],
                    "span": (f"{a}~{b or ''}" if a and a != b else str(a or "")), "past": r.get("stage") == "past"})
    return out


def _page(request: Request, *, tab: str = "current", year: str = "", hidden: str = "", ok: str = "", err: str = "",
          sheet: dict | None = None, unit: str = "auto", view_: str = "", done: dict | None = None):
    """사업 관리 — 세 갈래(전체·현행·지난)와 연도 알약. 연도를 고르면 그해 진행한 사업만, 엑셀과 같은 묶음의 연도별 표로 보인다.
    view_="requests" 면 신청 목록(관리자는 전체, 그 밖은 내 신청)."""
    S = _select(request, tab, year, hidden)
    scope, user, acct, rows, shown, sel, tab = S["scope"], S["user"], S["acct"], S["rows"], S["shown"], S["sel"], S["tab"]
    unit = _unit(unit)
    st = request.app.state
    store = S["store"]
    accounts = getattr(st, "accounts", None) or {}
    heads, leads, clerks = _choices(accounts, scope, acct)
    may_create = grades.can_create_project(acct)
    name_of = _NameOf({u: ((a.get("name") or "").strip() or u) for u, a in accounts.items()})
    people = {(a.get("name") or "").strip() for a in accounts.values() if a.get("active", True) and (a.get("name") or "").strip()}
    for r in shown:
        r["v"] = view(r, sel)
        r["v"]["has_account"] = any(p in people for p in _SPLIT.split(r["v"]["manager"]) if p)
        bw = program_sheet.parse_won(r["v"]["budget"])
        r["v"]["budget_short"] = program_sheet.short_money(bw) if bw is not None else r["v"]["budget"]
        r["v"]["budget_full"] = program_sheet.won(bw) if bw is not None else r["v"]["budget"]
        r["v"]["budget_won"] = bw
        r["v"]["period_short"] = program_sheet.short_period(r["v"]["period"])
        ds = [_dept_name(d) for d in _SPLIT.split(r["v"]["dept"] or "") if d]
        r["v"]["dept_short"] = (ds[0] + (f" 외 {len(ds) - 1}" if len(ds) > 1 else "")) if ds else ""
        r["v"]["start"] = (row_span(r)[0] or 0)
        if sel:
            r["y"] = year_cells(r, sel, unit)
            r["y"]["money_json"] = {f: r["y"]["m"][f] for f in ("budget", *program_sheet.SPLIT)}
        live = r["stage"] != "past"
        mine = owns(r, scope, user, acct)
        r["can_edit"] = live and mine
        r["can_assign"] = r["can_edit"]
        r["can_request"] = live and scope == "dept" and not mine
        r["can_link"] = live and (mine or any(user in r[f] for f in ROLES))
        r["can_new"] = r["can_link"] and may_create
        r["who"] = {f: [name_of.get(u, u) for u in r[f]] for f in ROLES}
        r["edit"] = {k: r.get(k) for k in ("key", *TEXT_FIELDS, *ROLES, "source", "origin")}
        r["edit"]["who"] = {f: [{"user": u, "label": name_of.get(u, u)} for u in r[f]] for f in ROLES}
        r["edit"]["plist"] = [p for p in r.get("plist", []) if not p["archived"]]
        r["edit"]["years"] = _edit_years(r) if r["can_edit"] else {}
        a, b = row_span(r)
        r["edit"]["span"] = [a, b]
        r["edit"]["ver"] = row_version(store, r["key"])
        r["status_chips"] = (r["y"]["chips"] if sel else program_sheet.chips(program_sheet.latest(r["years"]).get("status") or ""))
        r["ministries"] = program_sheet.chips(((r["years"].get(str(sel)) or {}) if sel else program_sheet.latest(r["years"])).get("ministry") or "")
    ysum, foot, basis = None, {}, {}
    if sel:
        ysum = year_summary(shown, S["in_tab"], sel, unit)
        for f in ("budget", *program_sheet.SPLIT):
            foot[f] = program_sheet.fmt_unit(sum(r["y"]["m"][f] or 0 for r in shown), unit)
        basis = dict((store.get("sheets") or {}).get(str(sel)) or {})
    from zzaimy.app.program_tree import ORDER
    l1s = [x for x in ORDER if any(r["l1"] == x for r in shown)] + sorted({r["l1"] for r in shown if r["l1"] and r["l1"] not in ORDER})
    depts = sorted({d for r in shown for d in r["depts"]})
    sts = [x for x in ("기존", "신규", "종료", "추가") if any(x in r["status_chips"] for r in shown)] + \
        sorted({c for r in shown for c in r["status_chips"]} - {"기존", "신규", "종료", "추가"})
    mis = sorted({c for r in shown for c in r["ministries"]})
    reqs = list(store.get("requests") or [])
    if scope == "all":
        my_reqs = sorted(reqs, key=lambda q: (q["status"] != "wait", -q["id"]))
    else:
        my_reqs = sorted([q for q in reqs if q.get("by") == user], key=lambda q: -q["id"])
    by_key = {r["key"]: r for r in rows}
    for q in my_reqs:
        r = by_key.get(q["key"]) or {}
        q["before"] = _req_before(r, q, name_of)
        q["after"] = _req_after(q, name_of)
        q["by_label"] = name_of.get(q.get("by"), q.get("by"))
    removed = []
    if scope == "all":                                     # 뺀 사업·합친 사업 — 되살리기·떼어 내기
        name_by_key = {r["key"]: r["name"] for r in rows}
        for r in rows:
            if not r["hidden"]:
                continue
            o = store["rows"].get(r["key"]) or {}
            last = next((e for e in reversed(r["log"]) if e.get("field") in ("hidden", "합치기")), {})
            why = HIDE_REASONS.get(o.get("hide_reason") or "", "사업 아님" if r["not_program"] else "")
            removed.append({"key": r["key"], "name": r["name"], "kind": "merged" if r["merged_into"] else "hidden",
                            "into": r["merged_into"], "into_name": name_by_key.get(r["merged_into"], r["merged_into"]),
                            "reason": why, "note": o.get("hide_note") or "", "at": last.get("at", ""),
                            "by": name_of.get(last.get("by"), last.get("by")) if last.get("by") else "",
                            "undo_full": bool(o.get("merge_undo"))})
        removed.sort(key=lambda x: x["at"], reverse=True)
    notes = [n for n in (store.get("notes") or {}).get(user, []) if not n.get("seen")]
    may_import = scope == "all" or (scope == "dept" and bool(covered(acct)))
    good_docs, other_docs = (sheet_docs(st.db, st.data_dir) if may_import else ([], []))
    if may_import and scope != "all":
        seen = lambda d: _doc_ok(request, scope, st.db.get_document(d["id"]) or {})
        good_docs, other_docs = [d for d in good_docs if seen(d)], [d for d in other_docs if seen(d)]
    return st.templates.TemplateResponse(request, "programs_admin.html", st.page_ctx(request, {
        "rows": shown, "scope": scope, "can_add": scope == "all" and tab != "past", "any_action": any(r["can_link"] or r["can_request"] or r["can_edit"] for r in shown),
        "clerk_choices": clerks, "my_projects": _linkable_projects(st.db, scope, user) if any(r["can_link"] for r in shown) else [],
        "tab": tab, "year": sel, "years": S["years"], "this_year": S["this_year"], "n_all": S["n_all"], "n_cur": S["n_cur"],
        "n_past": S["n_past"], "head_choices": heads, "lead_choices": leads, "name_of": name_of, "l1s": l1s, "depts": depts,
        "sts": sts, "mis": mis, "origin_labels": ORIGIN,
        "show_hidden": bool(hidden), "n_hidden": sum(1 for r in rows if r["hidden"] and not r["merged_into"]) if scope == "all" else 0,
        "may_import": may_import, "good_docs": good_docs, "other_docs": other_docs, "sheet": sheet, "done": done,
        "labels": program_sheet.LABELS, "unit": unit, "units": [(k, v[1]) for k, v in program_sheet.UNITS.items()],
        "unit_div": program_sheet.UNITS[unit][0], "unit_label": program_sheet.UNITS[unit][1],
        "ysum": ysum, "foot": foot, "basis": basis, "trend": [] if sel else year_trend(S["in_tab"]),
        "split": program_sheet.SPLIT, "view": view_, "reqs": my_reqs, "n_wait": sum(1 for q in reqs if q["status"] == "wait"),
        "n_mine": sum(1 for q in reqs if q.get("by") == user), "notes": notes, "req_fields": REQ_FIELDS,
        "all_rows": merge_targets(rows) if scope == "all" else [], "removed": removed,
        "hide_reasons": HIDE_REASONS,
        "me": user, "ok": ok, "err": err, "me_dept": my_dept(acct)}))


VIEW_COOKIE = "pa_view"


def remembered_view(request: Request) -> dict:
    """쿠키 pa_view(「갈래.연도.단위」, 화면이 갈래·연도·단위를 바꿀 때 적는다) — 주소에 갈래·연도가 없으면 보던 대로 연다.
    예전에는 화면을 그린 뒤 스크립트가 기억한 연도로 주소를 바꿔 다시 불러 한 번 깜빡였다."""
    parts = (request.cookies.get(VIEW_COOKIE) or "").split(".")
    out = {}
    if len(parts) == 3:
        tab, year, unit = parts
        if tab in TABS:
            out["tab"] = tab
        if year.isdigit() and len(year) in (1, 4):
            out["year"] = year
        if unit in program_sheet.UNITS:
            out["unit"] = unit
    return out


@router.get("/programs/admin", response_class=HTMLResponse)
def programs_admin(request: Request, ok: str = "", err: str = "", hidden: str = "", tab: str = "", year: str = "",
                   unit: str = "", view: str = "", done: str = "", h: str = ""):
    d = {"stamp": done, "h": h} if done and h else None
    mem = remembered_view(request)
    if not tab and not year:
        tab, year = mem.get("tab", "current"), mem.get("year", "")
    unit = unit or mem.get("unit", "auto")
    return _page(request, tab=tab or "current", year=year, hidden=hidden, ok=ok, err=err, unit=unit, view_=view, done=d)


def year_lines(rows: list[dict], year: int) -> list[dict]:
    """연도 엑셀에 쓸 줄 — 그해 값이 있는 사업마다 세부 과제 줄(없으면 사업 줄 하나), 원본 엑셀 줄 순서."""
    out = []
    have = [r for r in rows if (r.get("years") or {}).get(str(year))]
    have.sort(key=lambda r: (int(r["years"][str(year)].get("order") or 10 ** 6), r["name"]))
    for r in have:
        yv = r["years"][str(year)]
        if yv.get("tasks"):
            out.extend({"name": r["name"], **t} for t in yv["tasks"])
            continue
        m = program_sheet.year_money(yv)
        out.append({"name": r["name"], **{f: yv.get(f) or "" for f in program_sheet.TASK_TEXT if f != "name"},
                    **{f + "_won": m[f] for f in program_sheet.MONEY if m[f] is not None}})
    return out


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _download(data: bytes, name: str, media: str) -> Response:
    return Response(data, media_type=media, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.get("/programs/admin.xlsx")
def programs_admin_xlsx(request: Request, year: str = "", tab: str = "current", hidden: str = "", all: str = ""):
    """내보내기 — 엑셀(원본 양식, 연도 하나) 또는 all=1 이면 전체 연도(연도마다 시트). 화면과 같은 권한·갈래·거르기(찾기·부서·대분류·구분·지원기관·출처)."""
    F = _filters(request)
    if all:
        S = _select(request, tab, "0", hidden)
        years = sorted({int(y) for r in S["in_tab"] for y in r["years"] if str(y).isdigit()})
        books = []
        for y in years:
            rows = _filter([r for r in S["in_tab"] if str(y) in r["years"]], year=y, **F)
            books.append((y, year_lines(rows, y), (S["store"].get("sheets") or {}).get(str(y))))
        span_ = f"{years[0]}~{years[-1]}" if years else "빈"
        return _download(program_sheet.write_books(books), f"재정지원사업현황({span_}).xlsx", XLSX)
    if not re.fullmatch(r"(19|20)\d{2}", year or ""):
        raise HTTPException(400, "연도를 골라 주세요")
    S = _select(request, tab, year, hidden)
    rows = _filter(S["shown"], year=int(year), **F)
    meta = (S["store"].get("sheets") or {}).get(year)
    return _download(program_sheet.write_book(int(year), year_lines(rows, int(year)), meta), f"재정지원사업현황({year}).xlsx", XLSX)


@router.get("/programs/admin.csv")
def programs_admin_csv(request: Request, hidden: str = "", tab: str = "all", year: str = ""):
    """내보내기 — CSV(지금 보이는 목록, 엑셀에서 바로 열리게 BOM 포함). 화면과 같은 권한·갈래·연도·거르기.
    수식으로 읽힐 글(= + - @ …)은 앞에 작은따옴표를 붙인다."""
    S = _select(request, tab, year or "0", hidden)
    scope, accounts = S["scope"], getattr(request.app.state, "accounts", None) or {}
    rows = _filter(S["shown"], year=S["sel"], **_filters(request))
    name_of = lambda u: f"{(accounts.get(u) or {}).get('name') or u}({u})"
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _, label in COLUMNS] + ["출처"] + (["숨김"] if scope == "all" else []))
    for r in rows:
        v = view(r, S["sel"])
        vals = [", ".join(name_of(u) for u in r[k]) if k in ROLES else v[k] if k in v else r.get(k, "")
                for k, _ in COLUMNS]
        vals += [ORIGIN[r["origin"]]] + (["숨김" if r["hidden"] else ""] if scope == "all" else [])
        w.writerow([program_sheet.csv_safe(x) for x in vals])
    return _download(("﻿" + buf.getvalue()).encode("utf-8"), f"사업 관리 {time.strftime('%Y-%m-%d')}.csv", "text/csv; charset=utf-8")


# ---------------------------------------------------------------- 고치기·변경 기록

YEAR_EDIT = ("budget", "manager", "dept", "period")         # 예전 편집 창의 연도별 칸(y_year·y_<칸>) — 그대로 받는다
REQ_FIELDS = [("dept", "주관 부서"), ("period", "기간"), ("budget", "예산"), ("manager", "책임자 이름"), ("l3", "소분류"),
              ("y:manager", "그해 사업 책임자"), ("y:officer", "그해 행정 담당자"), ("y:dept", "그해 업무진행부서"),
              ("y:year_period", "그해 당해 기간"), ("y:status", "그해 구분"), ("y:budget", "그해 예산")]
ROLE_LABEL = {"heads": "총괄 책임자", "leads": "사업 책임자", "clerks": "행정 담당자"}


def _clean(t, n: int = 120) -> str:
    return " ".join(str(t or "").split())[:n]


def current_value(r: dict, field: str, year: str = ""):
    if field in ROLES:
        return list(r.get(field) or [])
    if year:
        return ((r.get("years") or {}).get(str(year)) or {}).get(field, "")
    return r.get(field, "")


def set_field(store: dict, r: dict, field: str, value, by: str, via: str, year: str = "") -> None:
    """한 칸 바꾸기(신청 승인·되돌리기가 쓴다) — 장부 줄은 관리 값으로 덮고, 더한 줄은 그 줄 값을, 연도 칸은 years 에 손 칸으로. 기록을 남긴다."""
    key = r["key"]
    o = store["rows"].setdefault(key, {})
    before = current_value(r, field, year)
    if field in ROLES:
        o[field] = list(value or [])
        r[field] = list(value or [])
    elif year:
        cur = o.setdefault("years", {}).setdefault(str(year), {})
        if value:
            cur[field] = value
        else:
            cur.pop(field, None)
        if field in program_sheet.MONEY:
            n = program_sheet.parse_won(value)
            if n is None:
                cur.pop(field + "_won", None)
            else:
                cur[field + "_won"] = n
        cur["by_hand"] = sorted(set(cur.get("by_hand") or []) | {field})
        r.setdefault("years", {})[str(year)] = cur
    elif r.get("source") in ("추가", "엑셀"):
        store["added"].setdefault(key, {})[field] = value
        r[field] = value
    else:
        o[field] = value
        r[field] = value
    program_sheet.add_log(o, by, field, before, value, via, year)


def _date_pair(text: str) -> tuple[str, str]:
    return program_sheet.norm_period(text)


def apply_year_table(cur_years: dict, payload: dict, by: str, o: dict) -> tuple[dict, str]:
    """편집 창의 연도별 표(JSON) → years. 바뀐 칸만 적고 손 칸(by_hand)으로 표시, 기록을 남긴다. (새 years, 오류 한 줄).
    payload: {연도: {status, year_period, manager, officer, dept, money{국고…인센티브: 원 또는 「1.2억」}, tasks[…]|null, remove}}.
    세부 과제가 있으면 사업 금액 칸은 과제 합으로, 없으면 6갈래 합을 합계로."""
    ys = {y: dict(v) for y, v in (cur_years or {}).items()}
    this_year = int(time.strftime("%Y"))
    for y, p in (payload or {}).items():
        y = str(y)
        if not re.fullmatch(r"(19|20)\d{2}", y) or not (2000 <= int(y) <= this_year + 5):
            return ys, f"연도가 올바르지 않습니다: {y[:8]}"
        if not isinstance(p, dict):
            continue
        if p.get("remove"):
            if y in ys:
                program_sheet.add_log(o, by, "", y, "", "직접(연도 지움)", y)
                ys.pop(y)
            continue
        cur = ys.setdefault(y, {})
        hand = set(cur.get("by_hand") or [])

        def put(f, v):
            old = cur.get(f, "")
            if (v or "") == (old or ""):
                return
            if v:
                cur[f] = v
            else:
                cur.pop(f, None)
            hand.add(f)
            program_sheet.add_log(o, by, f, old, v, "직접", y)
        for f in ("status", "manager", "officer", "dept"):
            if f in p:
                put(f, _clean(p.get(f)))
        if "year_period" in p:
            v, e = _date_pair(p.get("year_period") or "")
            if e:
                return ys, f"{y}년 당해 기간: {e}"
            put("year_period", v)
        tasks = p.get("tasks")
        sums: dict[str, int] = {}
        if isinstance(tasks, list):
            clean_tasks = []
            for n, t in enumerate(tasks[:200], 1):
                if not isinstance(t, dict):
                    continue
                tt = {k: _clean(t.get(k)) for k in ("sub", "status", "manager", "officer", "dept") if _clean(t.get(k))}
                if t.get("year_period"):
                    v, e = _date_pair(t["year_period"])
                    if e:
                        return ys, f"{y}년 세부 과제 {n}: {e}"
                    tt["year_period"] = v
                for f in program_sheet.SPLIT:
                    raw = (t.get("money") or {}).get(f)
                    if raw in (None, ""):
                        continue
                    w = program_sheet.parse_won(raw)
                    if w is None or w < 0:
                        return ys, f"{y}년 세부 과제 {n}: 금액을 읽지 못했습니다({str(raw)[:20]})"
                    tt[f + "_won"] = w
                ws_ = [tt.get(f + "_won") for f in program_sheet.SPLIT if tt.get(f + "_won") is not None]
                if ws_:
                    tt["budget_won"] = sum(ws_)
                tt["seq"] = str(n)
                clean_tasks.append(tt)
            old_tasks = [{k: v for k, v in t.items() if k not in ("line", "extra", "name", "period", "fiscal_year", "agency",
                                                                   "ministry", "majors", "account_dept")} for t in cur.get("tasks") or []]
            if clean_tasks != old_tasks:
                cur["tasks"] = clean_tasks
                hand.add("tasks")
                program_sheet.add_log(o, by, "tasks", f"{len(old_tasks)}줄", f"{len(clean_tasks)}줄", "직접", y)
            for f in program_sheet.SPLIT:
                got = [t[f + "_won"] for t in clean_tasks if t.get(f + "_won") is not None]
                if got:
                    sums[f] = sum(got)
        elif isinstance(p.get("money"), dict):
            for f in program_sheet.SPLIT:
                raw = p["money"].get(f)
                if raw in (None, ""):
                    if cur.get(f + "_won") is not None and f in p["money"]:
                        sums[f] = None                         # 비운 칸
                    continue
                w = program_sheet.parse_won(raw)
                if w is None or w < 0:
                    return ys, f"{y}년 {program_sheet.LABELS[f]}: 금액을 읽지 못했습니다({str(raw)[:20]})"
                sums[f] = w
        if sums:
            for f, w in sums.items():
                if w == cur.get(f + "_won") and (w is not None):
                    continue
                program_sheet.add_log(o, by, f, cur.get(f, ""), program_sheet.won(w), "직접", y)
                if w is None:
                    cur.pop(f, None)
                    cur.pop(f + "_won", None)
                else:
                    cur[f], cur[f + "_won"] = program_sheet.won(w), w
                hand.add(f)
            tot = [cur.get(f + "_won") for f in program_sheet.SPLIT if cur.get(f + "_won") is not None]
            if tot and sum(tot) != cur.get("budget_won"):
                program_sheet.add_log(o, by, "budget", cur.get("budget", ""), program_sheet.won(sum(tot)), "직접", y)
                cur["budget"], cur["budget_won"] = program_sheet.won(sum(tot)), sum(tot)
                hand.add("budget")
        if hand:
            cur["by_hand"] = sorted(hand)
        if not any(k for k in cur if k not in ("by_hand",)):
            ys.pop(y, None)
    return ys, ""


def _check_people(want: dict, allowed: dict, old: dict, accounts: dict) -> str:
    """지정 후보 다시 검사 — 등급·부서가 맞지 않으면 거절 이유 한 줄(이미 지정돼 있던 사람은 그대로 둔다)."""
    for f, users in want.items():
        for u in users:
            if u in allowed[f] or u in (old.get(f) or []):
                continue
            if f == "heads" and u.startswith("dept:") and len(u) > 5:
                continue                                       # 부서 지정 — 그 부서(와 상위)의 총괄 책임자들
            a = accounts.get(u) or {}
            name = (a.get("name") or "").strip() or u
            if not a or not a.get("active", True):
                return f"{name}: 사용 중인 계정이 아닙니다"
            return (f"{name}: {ROLE_LABEL[f]}로 지정할 수 없습니다. "
                    + {"heads": "총괄 책임자 등급만 됩니다", "leads": "같은 부서의 사업 책임자 이상만 됩니다", "clerks": "같은 부서의 행정 담당자 이상만 됩니다"}[f])
    return ""


@router.post("/programs/admin/save")
async def programs_admin_save(request: Request):
    """줄 저장 — 읽기부터 쓰기까지는 잠금(org_lock) 안에서(_save_row)."""
    form = await request.form()
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_save_row, request, form)


@org_lock.serialized
def _save_row(request: Request, form):
    """줄 저장 — 관리자는 모든 줄·새 줄(key 비움), 총괄 책임자는 주관 부서가 자기 부서인 사업(값·사업 책임자·행정 담당자, 같은 부서 사람만).
    다른 부서 사업은 403(신청으로). 연도별 값은 표(years_json) 또는 예전 칸(y_year·y_<칸>). 바뀐 칸은 변경 기록에 「직접」으로."""
    scope, user, acct = _scope_or_403(request)
    if scope not in ("all", "dept"):
        raise HTTPException(403, "사업 고치기는 총괄 책임자와 관리자만 합니다")
    st = request.app.state
    accounts = getattr(st, "accounts", None) or {}
    key = str(form.get("key") or "").strip()
    store = load_store(st.data_dir)
    rows = merged_rows(st.data_dir, store=store)
    by_key = {r["key"]: r for r in rows}
    old = by_key.get(key)
    if key and old is None:
        raise HTTPException(404, "사업을 찾을 수 없습니다")
    ver = str(form.get("ver") or "").strip()
    if key and ver and ver != row_version(store, key):          # 편집 창을 연 뒤 다른 사람이 이 줄을 저장했다
        raise HTTPException(409, STALE)
    if scope == "dept":
        if old is None:
            raise HTTPException(403, "줄 추가는 관리자만 합니다")
        if old["hidden"]:
            raise HTTPException(404, "사업을 찾을 수 없습니다")
        if not owns(old, scope, user, acct):
            raise HTTPException(403, "다른 부서 사업은 고칠 수 없습니다. 「수정 신청」으로 관리자에게 요청하세요")
    if old is not None and stage(old, int(time.strftime("%Y"))) == "past":
        return _back("지난 사업은 보기만 됩니다", ok=False)
    ok_sets = dict(zip(ROLES, ({c["user"] for c in cs} for cs in _choices(accounts, scope, acct))))
    want = {f: list(dict.fromkeys(str(x) for x in form.getlist(f) if str(x))) for f in ROLES}
    if scope == "dept":
        want["heads"] = list(old["heads"])                     # 총괄 책임자 지정은 관리자만
    bad = _check_people(want, ok_sets, old or {}, accounts)
    if bad:
        return _back(bad, ok=False)
    full = "name" in form                                      # 값 칸이 있는 창(지정만 보내면 값은 그대로)
    vals = {f: _clean(form.get(f)) for f in TEXT_FIELDS} if full else {f: (old or {}).get(f, "") for f in TEXT_FIELDS}
    if len(vals["name"]) < 2:
        return _back("사업 이름을 적어 주세요", ok=False)
    if vals.get("period"):
        v, e = program_sheet.norm_period(vals["period"])
        if e:
            return _back(f"기간: {e}", ok=False)
        vals["period"] = v
    y_year = str(form.get("y_year") or "").strip()
    y_vals = {f: _clean(form.get("y_" + f)) for f in YEAR_EDIT}
    if y_year and not re.fullmatch(r"(19|20)\d{2}", y_year):
        return _back("연도는 네 자리 숫자로 적어 주세요(예: 2026)", ok=False)
    table = None
    if form.get("years_json"):
        try:
            table = json.loads(str(form.get("years_json")))
            assert isinstance(table, dict)
        except (ValueError, AssertionError):
            return _back("연도별 값을 읽지 못했습니다", ok=False)

    def years_with_hand(ys: dict, o: dict) -> tuple[dict, str]:
        ys = {y: dict(v) for y, v in (ys or {}).items()}
        if table is not None:
            ys, e = apply_year_table(ys, table, user, o)
            if e:
                return ys, e
        if y_year:
            cur = ys.setdefault(y_year, {})
            hand = set(cur.get("by_hand") or [])
            for f, v in y_vals.items():
                if v == (cur.get(f) or ""):
                    continue
                program_sheet.add_log(o, user, f, cur.get(f, ""), v, "직접", y_year)
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
        return ys, ""

    if not key:
        key = slug(vals["name"])
        if key in by_key:
            return _back(f"「{vals['name']}」 사업은 이미 있습니다", ok=False)
        o: dict = {}
        ys, e = years_with_hand({}, o)
        if e:
            return _back(e, ok=False)
        program_sheet.add_log(o, user, "", "", "줄 추가", "직접")
        store["added"][key] = {**vals, "added_by": user, "added_at": time.strftime("%Y-%m-%d")}
        store["rows"][key] = {k: v for k, v in {**want, "years": ys, "log": o.get("log")}.items() if v}
        save_store(store, st.data_dir)
        return _back(f"「{vals['name']}」 줄을 더했습니다")
    base = next((r for r in base_rows(load_ledger(st.data_dir)) if r["key"] == key), None)
    prev = store["rows"].get(key) or {}
    o = {k: v for k, v in prev.items() if k not in TEXT_FIELDS and k not in ROLES}
    ys, e = years_with_hand(prev.get("years") or {}, o)
    if e:
        return _back(e, ok=False)
    o["years"] = ys
    for f in TEXT_FIELDS:
        if vals[f] != (old.get(f) or ""):
            program_sheet.add_log(o, user, f, old.get(f) or "", vals[f], "직접")
    for f in ROLES:
        if want[f] != old[f]:
            program_sheet.add_log(o, user, f, old[f], want[f], "직접")
    if old["source"] in ("추가", "엑셀"):
        store["added"][key] = {**store["added"].get(key, {}), **vals}
    else:
        o.update({f: v for f, v in vals.items() if base is None or v != base.get(f, "")})
    o.update(want)
    store["rows"][key] = {k: v for k, v in o.items() if v}
    save_store(store, st.data_dir)
    return _back(f"「{vals['name']}」 줄을 저장했습니다", anchor=f"&row={quote(key)}")


def _admin(request: Request) -> tuple[str, dict]:
    scope, user, acct = _scope_or_403(request)
    if scope != "all":
        raise HTTPException(403, "관리자만 합니다")
    return user, acct


HIDE_REASONS = {"not_program": "사업 아님", "dup": "중복", "ended": "끝난 사업", "other": "그 밖"}


@router.post("/programs/admin/hide")
@org_lock.serialized
def programs_admin_hide(request: Request, key: str = Form(""), hidden: str = Form("1"), not_program: str = Form(""),
                        reason: str = Form("")):
    """목록에서 빼기·다시 넣기(관리자). reason 은 사업 아님·중복·끝난 사업·그 밖(HIDE_REASONS). 「사업 아님」은 다음 가져오기에서도
    그 이름을 건너뛴다. 옛 꼴 not_program=1 도 「사업 아님」으로 받는다. 뺀 줄은 「뺀 사업」 목록에서 다시 넣는다."""
    user, _ = _admin(request)
    st = request.app.state
    store = load_store(st.data_dir)
    rows = {r["key"]: r for r in merged_rows(st.data_dir, store=store)}
    if key not in rows:
        raise HTTPException(404, "사업을 찾을 수 없습니다")
    if rows[key]["merged_into"]:
        return _back("합친 줄은 「뺀 사업」 목록에서 떼어 냅니다", ok=False)
    reason = reason if reason in HIDE_REASONS else ("not_program" if not_program else "")
    o = store["rows"].setdefault(key, {})
    name = rows[key]["name"]
    if hidden == "1":
        o["hidden"] = True
        if reason == "not_program":
            o["not_program"] = True
        else:
            o.pop("not_program", None)
        if reason:
            o["hide_reason"] = reason
        else:
            o.pop("hide_reason", None)
        why = HIDE_REASONS.get(reason, "")
        program_sheet.add_log(o, user, "hidden", "", f"목록에서 뺌({why})" if why else "목록에서 뺌", "직접")
        save_store(store, st.data_dir)
        return _back(f"「{name}」을 목록에서 뺐습니다" + (f"({why})" if why else ""))
    for f in ("hidden", "not_program", "hide_reason", "hide_note"):
        o.pop(f, None)
    program_sheet.add_log(o, user, "hidden", "목록에서 뺌", "다시 넣음", "직접")
    save_store(store, st.data_dir)
    return _back(f"「{name}」을 목록에 다시 넣었습니다", anchor=f"&row={quote(key)}")


def _fp(o: dict) -> str:
    """받은 줄 저장값의 지문 — 합친 뒤 그대로인지(떼어 낼 때 받은 줄까지 되돌려도 되는지) 본다."""
    part = {f: o.get(f) for f in ("years", "terms_add", *ROLES)}
    return hashlib.sha256(json.dumps(part, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def merge_plan(src: dict, dst: dict, name_of=None) -> dict:
    """합치기 미리보기 — 남는 줄(dst)과 옮겨 갈 것: 별칭, 연도 값(없는 해는 옮기고 겹치는 해는 금액을 더하고 세부 과제를 잇는다),
    지정 계정, 이은 프로젝트(link_projects 를 먼저 부른 src 의 plist)."""
    name_of = name_of or {}
    have = {dst["name"], *dst["terms"]}
    aliases = [t for t in dict.fromkeys([src["name"], *src["terms"]]) if t and t not in have]
    years = []
    for y in sorted(src["years"]):
        sm = program_sheet.year_money(src["years"][y])["budget"]
        dm = program_sheet.year_money(dst["years"][y])["budget"] if y in dst["years"] else None
        years.append({"year": y, "how": "add" if y in dst["years"] else "move", "src": sm, "dst": dm,
                      "after": (sm or 0) + (dm or 0) if (sm is not None or dm is not None) else None})
    roles = {f: [name_of.get(u, u) for u in src[f] if u not in dst[f]] for f in ROLES}
    side = lambda r: {"key": r["key"], "name": r["name"], "dept": r.get("dept") or "",
                      "years": sorted(r["years"]), "terms": [t for t in r["terms"] if t != r["name"]][:8]}
    return {"src": side(src), "dst": side(dst), "aliases": aliases, "years": years, "roles": roles,
            "projects": [{"id": p["id"], "name": p["name"], "year": p.get("year") or ""} for p in src.get("plist") or []]}


def _merge_pair(request: Request, key: str, into: str):
    st = request.app.state
    store = load_store(st.data_dir)
    rows = {r["key"]: r for r in merged_rows(st.data_dir, store=store)}
    src, dst = rows.get(key), rows.get(into)
    if src is None or dst is None or key == into:
        return store, src, dst, "합칠 두 사업을 골라 주세요"
    if src["merged_into"]:
        return store, src, dst, "이미 다른 사업에 합친 줄입니다"
    if dst["merged_into"]:
        return store, src, dst, "이미 다른 줄에 합쳐진 줄로는 합치지 않습니다"
    return store, src, dst, ""


@router.get("/programs/admin/merge/preview")
def programs_admin_merge_preview(request: Request, key: str = "", into: str = ""):
    """합치기 미리보기(관리자) — 저장하지 않는다."""
    _admin(request)
    st = request.app.state
    _, src, dst, bad = _merge_pair(request, key, into)
    if bad:
        return {"error": bad}
    link_projects(st.db, [src])
    accounts = getattr(st, "accounts", None) or {}
    name_of = _NameOf({u: ((a.get("name") or "").strip() or u) for u, a in accounts.items()})
    return merge_plan(src, dst, name_of)


@router.post("/programs/admin/merge")
@org_lock.serialized
def programs_admin_merge(request: Request, key: str = Form(""), into: str = Form("")):
    """다른 사업과 합치기(관리자) — key 줄을 into 줄의 별칭으로: 연도 값(없는 해는 옮기고, 겹치는 해는 세부 과제를 잇고 금액을 더함)·
    지정·이은 프로젝트를 옮기고 원래 줄은 목록에서 뺀다. 떼어 내기(unmerge)에 쓸 앞 값(merge_undo)을 남기고 두 줄 모두 변경 기록에 남긴다."""
    user, _ = _admin(request)
    st = request.app.state
    store, src, dst, bad = _merge_pair(request, key, into)
    if bad:
        return _back(bad, ok=False)
    so, do = store["rows"].setdefault(key, {}), store["rows"].setdefault(into, {})
    before = {f: copy.deepcopy(do[f]) for f in ("years", "terms_add", *ROLES) if f in do}
    undo = {"into": into, "at": time.strftime("%Y-%m-%d %H:%M"), "by": user, "src_years": copy.deepcopy(so.get("years") or {}),
            "src_roles": {f: copy.deepcopy(so[f]) for f in ROLES if f in so}, "dst_before": before,
            "aliases": [t for t in dict.fromkeys([src["name"], *src["terms"]]) if t not in (do.get("terms_add") or [])], "projects": []}
    do["terms_add"] = list(dict.fromkeys([*(do.get("terms_add") or []), src["name"], *src["terms"]]))
    dys = do.setdefault("years", {})
    for y, v in (so.get("years") or {}).items():
        if y not in dys:
            dys[y] = copy.deepcopy(v)
            continue
        cur = dys[y]
        cur["tasks"] = list(cur.get("tasks") or []) + copy.deepcopy(list(v.get("tasks") or []))
        for f in program_sheet.MONEY:
            a, b = cur.get(f + "_won"), v.get(f + "_won")
            if b is not None:
                cur[f + "_won"] = (a or 0) + b
                cur[f] = program_sheet.won(cur[f + "_won"])
        for f in ("manager", "officer", "dept", "status", "sub", "ministry", "agency"):
            if v.get(f):
                cur[f] = ", ".join(dict.fromkeys([p for p in re.split(r"\s*,\s*", cur.get(f, "") + "," + v[f]) if p]))
    for f in ROLES:
        do[f] = list(dict.fromkeys([*dst[f], *src[f]]))
    moved = 0
    if st.db is not None:
        link_projects(st.db, [src])
        for p_ in src.get("plist") or []:
            old = (st.db.get_project(int(p_["id"])) or {}).get("program") or ""
            st.db.set_project_program(int(p_["id"]), program_value(into, p_.get("year") or ""))
            undo["projects"].append([int(p_["id"]), old])
            moved += 1
    undo["dst_after"] = _fp(do)
    so["hidden"], so["merged_into"], so["merge_undo"] = True, into, undo
    so.pop("years", None)
    program_sheet.add_log(so, user, "합치기", src["name"], dst["name"], "합치기")
    program_sheet.add_log(do, user, "합치기", src["name"], dst["name"], "합치기")
    save_store(store, st.data_dir)
    return _back(f"「{src['name']}」을 「{dst['name']}」에 합쳤습니다. 연도 값·지정을 옮기고 프로젝트 {moved}개를 다시 이었습니다",
                 anchor=f"&row={quote(into)}")


@router.post("/programs/admin/unmerge")
@org_lock.serialized
def programs_admin_unmerge(request: Request, key: str = Form("")):
    """합친 줄 떼어 내기(관리자) — 원래 줄을 목록에 다시 넣고 연도 값·지정·옮긴 프로젝트를 돌려놓는다. 받은 줄이 합친 뒤 그대로면
    받은 줄의 연도 값·별칭·지정도 합치기 앞으로, 그 뒤 고쳐졌으면 붙인 별칭만 떼고 연도 값은 그대로 둔다(알림에 적는다)."""
    user, _ = _admin(request)
    st = request.app.state
    store = load_store(st.data_dir)
    rows = {r["key"]: r for r in merged_rows(st.data_dir, store=store)}
    src = rows.get(key)
    so = store["rows"].get(key) or {}
    if src is None or not so.get("merged_into"):
        return _back("합친 줄이 아닙니다", ok=False)
    into = so["merged_into"]
    dst = rows.get(into)
    do = store["rows"].setdefault(into, {})
    u = so.get("merge_undo") or {}
    whole = bool(u) and _fp(do) == u.get("dst_after")
    if whole:
        for f in ("years", "terms_add", *ROLES):
            if f in u.get("dst_before", {}):
                do[f] = copy.deepcopy(u["dst_before"][f])
            else:
                do.pop(f, None)
    else:
        gone = set(u.get("aliases") or [src["name"]])
        do["terms_add"] = [t for t in do.get("terms_add") or [] if t not in gone]
    if u.get("src_years"):
        so["years"] = copy.deepcopy(u["src_years"])
    back = 0
    if st.db is not None:
        for pid, old in u.get("projects") or []:
            p = st.db.get_project(int(pid)) or {}
            if str(p.get("program") or "").partition("|")[0] == into:
                st.db.set_project_program(int(pid), old)
                back += 1
    for f in ("hidden", "merged_into", "merge_undo", "not_program", "hide_reason", "hide_note"):
        so.pop(f, None)
    dname = dst["name"] if dst else into
    program_sheet.add_log(so, user, "떼어 내기", dname, src["name"], "떼어 내기")
    program_sheet.add_log(do, user, "떼어 내기", src["name"], dname, "떼어 내기")
    save_store(store, st.data_dir)
    if not u:
        tail = ". 예전에 합친 줄이라 연도 값은 돌려놓지 못했습니다"
    elif whole:
        tail = f". 연도 값·지정·프로젝트 {back}개를 돌려놓았습니다"
    else:
        tail = f". 「{dname}」이 합친 뒤 고쳐져 그쪽 연도 값은 그대로 두었습니다(겹치는 해는 확인해 주세요)"
    return _back(f"「{src['name']}」을 「{dname}」에서 떼어 냈습니다{tail}", anchor=f"&row={quote(key)}")


@router.post("/programs/admin/revert")
@org_lock.serialized
def programs_admin_revert(request: Request, key: str = Form(""), i: str = Form("")):
    """변경 기록 한 줄 되돌리기(관리자) — 지금 값이 그 기록의 「후」 값과 같을 때만 「전」 값으로."""
    user, _ = _admin(request)
    st = request.app.state
    store = load_store(st.data_dir)
    rows = {r["key"]: r for r in merged_rows(st.data_dir, store=store)}
    r = rows.get(key)
    log = (store["rows"].get(key) or {}).get("log") or []
    if r is None or not i.isdigit() or int(i) >= len(log):
        raise HTTPException(404, "기록을 찾을 수 없습니다")
    e = log[int(i)]
    f, y = e.get("field") or "", e.get("year") or ""
    if not revertible(e):
        return _back("이 기록은 되돌릴 수 없습니다", ok=False)
    if current_value(r, f, y) != e.get("after"):
        return _back("그 뒤 다시 바뀐 칸이라 되돌리지 않습니다", ok=False)
    set_field(store, r, f, e.get("before"), user, f"되돌리기({e.get('at')})", y)
    save_store(store, st.data_dir)
    return _back(f"「{r['name']}」 {program_sheet.LABELS.get(f, ROLE_LABEL.get(f, f))} 칸을 되돌렸습니다")


def revertible(e: dict) -> bool:
    f = e.get("field") or ""
    return bool(f) and (f in TEXT_FIELDS or f in ROLES or (e.get("year") and f in program_sheet.YEAR_FIELDS))


# ---------------------------------------------------------------- 신청·승인

def _req_before(r: dict, q: dict, name_of: dict):
    if q["kind"] == "assign":
        return ", ".join(name_of.get(u, u) for u in (r.get(q["role"]) or [])) or "—"
    return current_value(r, q["field"], q.get("year", "")) or "—"


def _req_after(q: dict, name_of: dict):
    if q["kind"] == "assign":
        return ", ".join(name_of.get(u, u) for u in q.get("users") or []) or "—"
    return q.get("value") or "(비움)"


@router.post("/programs/admin/request")
async def programs_admin_request(request: Request):
    """신청(총괄 책임자 → 관리자) — 고칠 수 없는 다른 부서 사업에 「지정 신청」(자기를 총괄 책임자로·자기 부서 사람을 사업 책임자/행정 담당자로)
    또는 「수정 신청」(칸·새 값·사유). 관리자에게 알림."""
    scope, user, acct = _scope_or_403(request)
    if scope != "dept":
        raise HTTPException(403, "신청은 총괄 책임자가 합니다")
    st = request.app.state
    accounts = getattr(st, "accounts", None) or {}
    form = await request.form()
    key = str(form.get("key") or "")
    store = load_store(st.data_dir)
    r = next((x for x in merged_rows(st.data_dir, store=store) if x["key"] == key and not x["hidden"]), None)
    if r is None:
        raise HTTPException(404, "사업을 찾을 수 없습니다")
    if stage(r, int(time.strftime("%Y"))) == "past":
        return _back("지난 사업은 보기만 됩니다", ok=False)
    kind = str(form.get("kind") or "")
    reason = _clean(form.get("reason"), 300)
    q = {"kind": kind, "key": key, "name": r["name"], "by": user, "at": time.strftime("%Y-%m-%d %H:%M"), "reason": reason, "status": "wait"}
    if kind == "assign":
        role = str(form.get("role") or "")
        users = list(dict.fromkeys(str(x) for x in form.getlist("users") if str(x)))
        if role == "heads":
            users = [user]
        elif role in ("leads", "clerks"):
            _, leads, clerks = _choices(accounts, "dept", acct)
            ok_ = {c["user"] for c in (leads if role == "leads" else clerks)}
            bad = [u for u in users if u not in ok_]
            if bad or not users:
                return _back(f"{ROLE_LABEL[role]}는 우리 부서의 해당 등급 계정에서 고릅니다", ok=False)
        else:
            return _back("지정할 자리를 골라 주세요", ok=False)
        q.update({"role": role, "users": users})
    elif kind == "edit":
        field = str(form.get("field") or "")
        if field not in dict(REQ_FIELDS):
            return _back("바꿀 칸을 골라 주세요", ok=False)
        year = ""
        if field.startswith("y:"):
            field, year = field[2:], str(form.get("year") or "").strip()
            if not re.fullmatch(r"(19|20)\d{2}", year):
                return _back("그해 칸은 연도를 골라 주세요", ok=False)
        value = _clean(form.get("value"))
        if field in ("period", "year_period") and value:
            value, e = program_sheet.norm_period(value)
            if e:
                return _back(e, ok=False)
        if not reason:
            return _back("수정 신청에는 사유를 적어 주세요", ok=False)
        q.update({"field": field, "year": year, "value": value})
    else:
        return _back("신청 갈래를 골라 주세요", ok=False)
    reqs = store.setdefault("requests", [])
    q["id"] = max((x["id"] for x in reqs), default=0) + 1
    reqs.append(q)
    add_note(store, admin_users(accounts), f"새 신청 #{q['id']}: 「{r['name']}」 {'지정' if kind == 'assign' else '수정'} 신청", q["id"])
    save_store(store, st.data_dir)
    from zzaimy.app.notifications import notify                # 종(알림)에도 — 관리자에게
    notify(getattr(st, "db", None), admin_users(accounts),
           f"사업 관리 신청: {r['name']} {'지정' if kind == 'assign' else '수정'} 신청", "/programs/admin?view=requests")
    return _back(f"신청 #{q['id']}을 보냈습니다. 관리자가 승인하면 반영됩니다")


def _recheck(q: dict, accounts: dict, data_dir) -> str:
    """승인 시점 다시 검사 — 신청자가 아직 총괄 책임자인가, 지정할 사람이 아직 사용 중이고 등급·부서가 맞는가."""
    by = accounts.get(q.get("by") or "") or {}
    if not by.get("active", True):
        return "신청자 계정이 사용 중이 아닙니다"
    try:
        cov = set(grades.covered_depts(q["by"], by, data_dir, "head"))
    except Exception:
        cov = set()
    if grades.program_scope(by) == "dept" and (by.get("dept") or "").strip():
        cov.add(by["dept"].strip())
    if not cov:
        return "신청자가 이제 총괄 책임자가 아닙니다"
    if q.get("kind") != "assign":
        return ""
    need = {"heads": "head", "leads": "lead", "clerks": "clerk"}[q["role"]]
    for u in q.get("users") or []:
        a = accounts.get(u) or {}
        if not a.get("active", True) or a.get("role") not in grades.STAFF or _PICK_RANK.get(grades.grade_of(a), 0) < _PICK_RANK[need]:
            return f"{(a.get('name') or u)}: 이제 지정할 수 없는 계정입니다"
        if q["role"] != "heads" and (a.get("dept") or "").strip() not in cov:
            return f"{(a.get('name') or u)}: 신청자가 맡은 부서 사람이 아닙니다"
    return ""


@router.post("/programs/admin/request/decide")
@org_lock.serialized
def programs_admin_decide(request: Request, id: str = Form(""), decision: str = Form(""), note: str = Form("")):
    """신청 처리(관리자) — 승인하면 그대로 반영(변경 기록에 「신청 #n 승인」), 반려하면 사유와 함께 신청자에게 알림."""
    user, _ = _admin(request)
    st = request.app.state
    store = load_store(st.data_dir)
    q = next((x for x in store.get("requests") or [] if str(x["id"]) == id), None)
    if q is None:
        raise HTTPException(404, "신청을 찾을 수 없습니다")
    if q["status"] != "wait":
        return _back(f"신청 #{q['id']}은 이미 처리했습니다", ok=False)
    if decision not in ("ok", "no"):
        return _back("승인·반려를 골라 주세요", ok=False)
    note = _clean(note, 300)
    if decision == "no" and not note:
        return _back("반려 사유를 적어 주세요", ok=False)
    if decision == "ok":
        r = next((x for x in merged_rows(st.data_dir, store=store) if x["key"] == q["key"]), None)
        if r is None:
            return _back("사업이 없어졌습니다", ok=False)
        if r["hidden"] or stage(r, int(time.strftime("%Y"))) == "past":
            return _back(f"신청 #{q['id']}: 사업이 숨겨졌거나 끝나 반영하지 않습니다. 반려로 처리해 주세요", ok=False)
        bad = _recheck(q, accounts=getattr(st, "accounts", None) or {}, data_dir=st.data_dir)
        if bad:
            return _back(f"신청 #{q['id']}: {bad}. 반려로 처리해 주세요", ok=False)
        via = f"신청 #{q['id']} 승인"
        if q["kind"] == "assign":
            set_field(store, r, q["role"], list(dict.fromkeys([*r[q["role"]], *q["users"]])), user, via)
        else:
            set_field(store, r, q["field"], q.get("value") or "", user, via, q.get("year") or "")
    q.update({"status": decision, "done_by": user, "done_at": time.strftime("%Y-%m-%d %H:%M"), "note": note})
    add_note(store, [q["by"]], f"신청 #{q['id']} 「{q['name']}」 {'승인' if decision == 'ok' else '반려'}" + (f": {note}" if note else ""), q["id"])
    save_store(store, st.data_dir)
    from zzaimy.app.notifications import notify                # 종(알림)에도 — 신청자에게
    notify(getattr(st, "db", None), [q["by"]],
           f"사업 관리 신청 {'승인' if decision == 'ok' else '반려'}: {q['name']}" + (f": {note}" if note else ""), "/programs/admin?view=requests")
    return RedirectResponse(f"/programs/admin?view=requests&ok={quote('신청 #' + str(q['id']) + (' 승인' if decision == 'ok' else ' 반려'))}",
                            status_code=303)


@router.post("/programs/admin/notes/read")
@org_lock.serialized
def programs_admin_notes_read(request: Request):
    _, user, _ = _scope_or_403(request)
    st = request.app.state
    store = load_store(st.data_dir)
    for n in (store.get("notes") or {}).get(user, []):
        n["seen"] = True
    save_store(store, st.data_dir)
    return {"ok": True}


# ---------------------------------------------------------------- 사람 찾기(지정 칸의 사람 고르기 부품이 쓴다)

_PICK_RANK = {"member": 0, "clerk": 1, "lead": 2, "head": 3, "admin": 4}     # 지정 칸의 「그 등급 이상」(행정 담당자 < 사업 책임자)


@router.get("/api/people")
def api_people(request: Request, q: str = "", dept: str = "", grade: str = "", limit: int = 20, purpose: str = ""):
    """사람 찾기 — 사업 관리에서 지정할 수 있는 사람(관리자·총괄 책임자만 부른다). 사용 중인 교직원 계정만, 이름·아이디·부서로 찾는다.
    grade 는 그 등급 이상(clerk·lead·head). 돌려주는 값은 아이디·이름·부서·등급뿐(이메일·전화 같은 개인정보 없음).
    purpose=share 는 프로젝트 공유 창 — 교직원 누구나 부르고, 같은 학교 사용 중인 교직원 전체를 아이디·이름·부서만 돌려준다."""
    if purpose == "share":
        return _people_for_share(request, q, dept, limit)
    scope, user, acct = _scope_or_403(request)
    if scope not in ("all", "dept"):
        raise HTTPException(403, "사람 찾기는 총괄 책임자와 관리자만 합니다")
    accounts = getattr(request.app.state, "accounts", None) or {}
    words = [w for w in (q or "").lower().split() if w]
    out = []
    for u, a in sorted(accounts.items()):
        if not a.get("active", True) or a.get("role") not in grades.STAFF:
            continue
        g = grades.grade_of(a)
        if grade and _PICK_RANK.get(g, 0) < _PICK_RANK.get(grade, 99):
            continue
        d = (a.get("dept") or "").strip()
        if scope != "all" and d not in covered(acct):
            continue                                           # 총괄 책임자는 맡은 부서 사람만
        if dept and d != dept:
            continue
        name = (a.get("name") or "").strip() or u
        hay = f"{u} {name} {d}".lower()
        if words and not all(w in hay for w in words):
            continue
        out.append({"user": u, "name": name, "dept": d, "grade": g, "grade_label": grades.label(g)})
        if len(out) >= max(1, min(int(limit or 20), 50)):
            break
    depts_out = []
    if kind_dept := ("dept" in (request.query_params.get("kind") or "")):
        names = set()
        for a in accounts.values():
            if (a.get("dept") or "").strip():
                names.add(a["dept"].strip())
        try:
            for r in merged_rows(request.app.state.data_dir):
                names.update(r["depts"])
        except Exception:
            pass
        depts_out = [{"user": f"dept:{d}", "name": d, "dept": "부서", "grade_label": "부서 지정", "kind": "dept"}
                     for d in sorted(names) if not words or all(w in d.lower() for w in words)][:10]
    return {"people": out, "depts": depts_out if kind_dept else [], "my_dept": my_dept(acct)}


def _people_for_share(request: Request, q: str, dept: str, limit: int) -> dict:
    """공유 창의 사람·부서 찾기. 사람은 공유 저장(project_routes.project_set_shares)이 받는 계정과 같은 범위,
    부서는 저장이 받는 부서(known_depts) 가운데 이름이나 부서 사전(별칭 포함)이 맞는 것."""
    if getattr(request.state, "role", "") not in ("staff", "dev", "professor", "head"):
        raise HTTPException(403, "교직원 계정에서 쓸 수 있습니다")
    st = request.app.state
    accounts = getattr(st, "accounts", None) or {}
    me = getattr(request.state, "user", "") or ""
    words = [w for w in (q or "").lower().split() if w]
    cap = max(1, min(int(limit or 20), 50))
    out = []
    for u, a in sorted(accounts.items()):
        a = a or {}
        if not a.get("active", True) or a.get("role", "staff") not in grades.STAFF:
            continue
        d = (a.get("dept") or "").strip()
        if dept and d != dept:
            continue
        name = (a.get("name") or "").strip() or u
        if words and not all(w in f"{u} {name} {d}".lower() for w in words):
            continue
        out.append({"user": u, "name": name, "dept": d})
        if len(out) >= cap:
            break
    depts_out = []
    if "dept" in (request.query_params.get("kind") or ""):
        from zzaimy.app.user_admin import known_depts
        known = known_depts(getattr(st, "db", None), accounts, getattr(st, "data_dir", None))
        hit = [d for d in known if not words or all(w in d.lower() for w in words)]
        if q.strip():
            try:                                                   # 부서 사전 — 별칭·줄임말로도 찾는다
                from zzaimy.app import dept_dictionary as _dd
                for it in _dd.current(getattr(st, "db", None), getattr(st, "data_dir", None)).suggest(q, None, 20):
                    if it["name"] in known and it["name"] not in hit:
                        hit.append(it["name"])
            except Exception:
                pass
        mine = ((accounts.get(me) or {}).get("dept") or "").strip()
        if not words:                                              # 찾을 말이 없으면 내 부서 먼저, 그다음 처·단 급
            top = re.compile(r"(처|단|본부|실|원|추진단|사업단)$")
            hit.sort(key=lambda d: (d != mine, not top.search(d), d))
        count = {}
        for a_ in accounts.values():
            if (a_ or {}).get("active", True) and (a_ or {}).get("role", "staff") in grades.STAFF:
                k = ((a_ or {}).get("dept") or "").strip()
                count[k] = count.get(k, 0) + 1
        try:
            from zzaimy.app import dept_dictionary as _dd2
            parent = lambda d: _dd2.parent_of(d, getattr(st, "data_dir", None))
        except Exception:
            parent = lambda d: ""
        # 「부서 전체」 대신 무슨 뜻인지 보이게 — 부서를 고르면 그 부서 사람 모두와 공유(몇 명인지·어느 처·단 소속인지)
        depts_out = [{"user": f"dept:{d}", "name": d, "dept": "부서", "kind": "dept",
                      "grade_label": "부서 공유 · " + (f"{count[d]}명" if count.get(d) else "아직 계정 없음")
                                     + (f" · {parent(d)} 소속" if parent(d) else "")} for d in hit[:10]]
    return {"people": out, "depts": depts_out, "my_dept": ((accounts.get(me) or {}).get("dept") or "").strip()}


# ---------------------------------------------------------------- 엑셀에서 채우기(관리자)

_TOKEN = re.compile(r"[0-9a-f]{16}")
SHEET_EXT = (".xlsx", ".xlsm", ".xls", ".htm", ".html", ".mht", ".mhtml", ".xml")   # 꼴은 파일 앞머리로 다시 가린다(program_sheet.file_kind)
MAX_UPLOAD = program_sheet.MAX_BYTES


def _tmp(data_dir) -> Path:
    return _dir(data_dir) / "tmp" / "program_sheet"


def _importer(request: Request) -> tuple[str, str, dict]:
    """가져오기 — 관리자는 전체, 총괄 책임자는 자기 부서 사업 줄만(나머지는 건너뜀). 그 밖은 403."""
    scope, user, acct = _scope_or_403(request)
    if scope == "all" or (scope == "dept" and covered(acct)):
        return scope, user, acct
    raise HTTPException(403, "가져오기는 관리자와 총괄 책임자만 합니다")


def not_program_keys(ledger: dict, rows: list[dict] | None = None) -> set[str]:
    """「사업 아님」 이름의 열쇠 — 장부가 그렇게 확인한 이름과 관리 화면에서 「사업 아님으로 숨기기」 한 줄. 엑셀에 있어도 새 줄로 더하지 않는다."""
    keys = {k for e in ledger.get("programs") or [] if e.get("category") == "사업 아님"
            for t in [e.get("name") or "", *(e.get("terms") or [])] if (k := group_key(t))}
    for r in rows or []:
        if r.get("not_program"):
            keys |= {k for t in [r["name"], *r["terms"]] if (k := group_key(t))}
    return keys


MAX_FILES = 10                       # 한 번에 고르는 파일·문서 수
MAX_TOTAL = 30 * 1024 * 1024         # 한 번에 읽는 파일 합계
MAX_TMP = 200 * 1024 * 1024          # 올린 임시 파일 전체(미리보기를 거듭 불러도)


def _doc_ok(request: Request, scope: str, doc: dict) -> bool:
    """문서함에서 고른 엑셀 — 관리자 밖은 그 사람이 볼 수 있는 문서만(access_policy.visible)."""
    if scope == "all":
        return True
    from zzaimy.app import access_policy as _ap
    try:
        return bool(_ap.visible(doc, **_ap.request_viewer(request)))
    except Exception:
        return False


def _tmp_size(tmp: Path) -> int:
    return sum(p.stat().st_size for p in tmp.glob("*") if p.is_file())


async def _sources(request: Request, form, token: str, scope: str = "all", user: str = "") -> tuple[list[tuple[Path, str]], list[str], list[int], str]:
    """고른 문서(문서 id)와 올린 파일 → ([(경로, 이름)], 오류, 문서 id, 올린 파일 열쇠). 올린 파일은 data 의 tmp 에 잠시 둔다.
    상한은 읽기 전에 — 파일·문서 수, 파일마다 크기, 합계, 임시 폴더 전체. 올린 파일 열쇠는 올린 사람에게만 쓰인다(<열쇠>.who)."""
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
    if len(ids) > MAX_FILES:
        errs.append(f"문서는 한 번에 {MAX_FILES}개까지 고릅니다")
        ids = ids[:MAX_FILES]
    out: list[tuple[Path, str]] = []
    total = 0
    for i in ids:
        d = st.db.get_document(i) if st.db is not None else None
        p = Path(str((d or {}).get("stored_path") or ""))
        if not d or not _doc_ok(request, scope, d):
            errs.append(f"문서 {i}: 없습니다")                       # 볼 수 없는 문서는 있는지도 알리지 않는다
        elif p.suffix.lower() not in SHEET_EXT or not p.is_file() or p.name.startswith("~$"):
            errs.append(f"문서 {i}: 엑셀 원본 파일을 열 수 없습니다")
        elif p.stat().st_size > MAX_UPLOAD:
            errs.append(f"문서 {i}: {MAX_UPLOAD // (1024 * 1024)}MB 보다 큽니다")
        elif total + p.stat().st_size > MAX_TOTAL:
            errs.append(f"문서 {i}: 한 번에 읽는 파일 합계가 {MAX_TOTAL // (1024 * 1024)}MB 를 넘습니다")
        else:
            total += p.stat().st_size
            out.append((p, f"{d.get('filename') or p.name}"))
    tmp = _tmp(st.data_dir)
    ups = [f for f in form.getlist("files") if getattr(f, "filename", "")]
    who = hashlib.sha256(user.encode()).hexdigest()[:16]
    if ups:
        tmp.mkdir(parents=True, exist_ok=True)
        for old in tmp.glob("*"):                          # 하루 지난 임시 파일과 이 사람의 앞 미리보기 파일은 지운다
            if time.time() - old.stat().st_mtime > 86400:
                old.unlink(missing_ok=True)
        for w in tmp.glob("*.who"):
            if w.read_text().strip() == who:
                for q in tmp.glob(w.stem + "-*"):
                    q.unlink(missing_ok=True)
                w.unlink(missing_ok=True)
        if len(ups) + len(out) > MAX_FILES:
            errs.append(f"파일은 한 번에 {MAX_FILES}개까지 읽습니다")
            ups = ups[:max(0, MAX_FILES - len(out))]
        token = secrets.token_hex(8)
        (tmp / f"{token}.who").write_text(who)
        room = MAX_TMP - _tmp_size(tmp)
        for n, f in enumerate(ups):
            name = Path(f.filename).name
            if Path(name).suffix.lower() not in SHEET_EXT or name.startswith("~$"):
                errs.append(f"{name}: 엑셀 파일(.xlsx·.xls·웹 페이지로 저장한 .html)만 읽습니다")
                continue
            data = await f.read(MAX_UPLOAD + 1)
            if len(data) > MAX_UPLOAD:
                errs.append(f"{name}: {MAX_UPLOAD // (1024 * 1024)}MB 보다 큽니다. 필요한 시트만 남겨 다시 올려 주세요")
                continue
            if total + len(data) > MAX_TOTAL or len(data) > room:
                errs.append(f"{name}: 한 번에 읽는 파일 합계가 너무 큽니다. 나눠서 올려 주세요")
                continue
            total += len(data)
            room -= len(data)
            (tmp / f"{token}-{n}-{name}").write_bytes(data)
    if token and _TOKEN.fullmatch(token) and (tmp / f"{token}.who").is_file() and (tmp / f"{token}.who").read_text().strip() == who:
        for p in sorted(tmp.glob(f"{token}-*"))[:MAX_FILES]:
            out.append((p, p.name.split("-", 2)[-1]))
    else:
        token = ""
    return out, errs, ids, token


def _plan(request: Request, sources, overwrite: bool, user: str, scope: str = "all", acct: dict | None = None):
    """미리보기·적용이 같이 쓰는 계산 — (요약, 새 저장값, 파일 오류). 사업 현황 표가 아닌 파일은 그 파일만 빼고 알린다."""
    st = request.app.state
    items, metas, errs = [], [], []
    for p, label in sources:
        try:
            got, meta = program_sheet.read_sheets(p, hint=label)
        except ValueError as e:                            # 상한·옛 xls·잠금 파일 같은 꼴 안내
            errs.append(f"{label}: {e}")
            continue
        except Exception as e:                             # 깨진 파일·암호 걸린 파일
            errs.append(f"{label}: 읽지 못했습니다({type(e).__name__})")
            continue
        if not meta:
            errs.append(f"{label}: {program_sheet.NOT_SHEET}")
            continue
        if not got:
            errs.append(f"{label}: 사업 줄을 찾지 못했습니다")
        items.extend(got)
        metas.extend(meta)
    store = load_store(st.data_dir)
    ledger = load_ledger(st.data_dir)
    rows = merged_rows(st.data_dir, ledger=ledger, store=store)
    live = [r for r in rows if not r["merged_into"] and not r["not_program"]]   # 합쳐진 줄 이름은 받은 줄의 별칭으로, 사업 아님은 건너뜀
    allow, allow_new = None, True
    if scope != "all":
        by_key = {r["key"]: r for r in live}
        this_year = int(time.strftime("%Y"))
        allow = lambda k: (k in by_key and owns(by_key[k], scope, user, acct or {}) and not by_key[k]["hidden"]
                           and stage(by_key[k], this_year) != "past")
        allow_new = False
    summary, new_store = program_sheet.plan(items, live, store, overwrite=overwrite, not_program=not_program_keys(ledger, rows),
                                            by=user, sheets=metas if scope == "all" else None, allow=allow, allow_new=allow_new)
    return summary, new_store, errs


@router.post("/programs/admin/sheet/preview", response_class=HTMLResponse)
async def programs_admin_sheet_preview(request: Request):
    """가져오기 2단계(미리보기) — 저장하지 않고 연도별로 맞춘 사업·새 사업·애매함·채울 칸 수를 보인다."""
    scope, user, acct = _importer(request)
    form = await request.form()
    sources, errs, ids, token = await _sources(request, form, str(form.get("token") or ""), scope, user)
    if not sources:
        return _back(" · ".join(errs) or "문서를 고르거나 엑셀 파일을 올려 주세요", ok=False)
    overwrite = bool(form.get("overwrite"))
    summary, _, errs2 = _plan(request, sources, overwrite, user, scope, acct)
    sheet = {"summary": summary, "errors": errs + errs2, "ids": ids, "token": token, "names": [n for _, n in sources],
             "dept_only": scope != "all"}
    return _page(request, tab="all", year="0", sheet=sheet)


@router.post("/programs/admin/sheet/apply")
async def programs_admin_sheet_apply(request: Request):
    """가져오기 3단계(적용) — 미리보기와 같은 계산을 다시 해서 program_admin.json 에 쓴다(이전 판은 history 에). 장부·그래프는 그대로.
    끝나면 완료 요약과 되돌리기(관리자: 바로 앞 판으로) 단추가 보인다."""
    scope, user, acct = _importer(request)
    form = await request.form()
    sources, errs, _, token = await _sources(request, form, str(form.get("token") or ""), scope, user)
    if not sources:
        return _back(" · ".join(errs) or "고른 문서가 없습니다", ok=False)
    summary, new_store, _ = _plan(request, sources, bool(form.get("overwrite")), user, scope, acct)
    stamp = save_store(new_store, request.app.state.data_dir)
    if token:
        for p in [*_tmp(request.app.state.data_dir).glob(f"{token}-*"), _tmp(request.app.state.data_dir) / f"{token}.who"]:
            p.unlink(missing_ok=True)
    c = summary["cells"]
    msg = (f"가져왔습니다. 맞춘 사업 {len(summary['matched'])}개 · 새 줄 {len(summary['new'])}개 · 세부 과제 {summary['tasks']}줄 · "
           f"채운 칸 {c['fill']}개 · 바뀐 칸 {c['change']}개")
    if c["kept"]:
        msg += f" · 손으로 고친 칸 {c['kept']}개는 그대로"
    if summary["ambiguous"]:
        msg += f" · 애매해서 건너뛴 이름 {len(summary['ambiguous'])}개"
    if summary["skipped"]:
        msg += f" · 다른 부서 사업 {len(summary['skipped'])}개는 건너뜀"
    extra = f"&done={stamp}&h={store_hash(request.app.state.data_dir)}" if stamp and scope == "all" else ""
    return RedirectResponse(f"/programs/admin?tab=all&year=0&ok={quote(msg)}{extra}", status_code=303)


@router.post("/programs/admin/sheet/undo")
def programs_admin_sheet_undo(request: Request, stamp: str = Form(""), h: str = Form("")):
    """가져오기 되돌리기(관리자) — 적용 바로 앞 판(history/program_admin.<stamp>.json)으로. 그 뒤 다른 저장이 있었으면 하지 않는다."""
    _admin(request)
    d = _dir(request.app.state.data_dir)
    src = d / "history" / f"program_admin.{stamp}.json"
    if not re.fullmatch(r"\d{14}(?:-\d+)?", stamp or "") or not src.is_file():
        return _back("되돌릴 판을 찾지 못했습니다", ok=False)
    if store_hash(d) != h:
        return _back("가져온 뒤 다른 저장이 있어 되돌리지 않습니다. 줄의 변경 기록에서 칸별로 되돌리세요", ok=False)
    try:
        old = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _back("되돌릴 판을 읽지 못했습니다", ok=False)
    save_store(old, d)
    return _back("가져오기를 되돌렸습니다. 지금 판은 history 에 남겼습니다")


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
    """열쇠 → (이 사람이 볼 수 있는 줄, scope, user, acct). 총괄 책임자는 모든 사업을 읽기로 본다."""
    scope, user, acct = _scope_or_403(request)
    rows = merged_rows(request.app.state.data_dir)
    r = next((x for x in rows if x["key"] == key), None)
    if r is None or r not in visible_rows([r], scope, user, acct, show_hidden=scope == "all"):
        raise HTTPException(404, "사업을 찾을 수 없습니다")
    past = stage(r, int(time.strftime("%Y"))) == "past"
    if live_only and past:
        raise HTTPException(409, "지난 사업은 보기만 됩니다")
    return r, scope, user, acct


def _may_link(r: dict, scope: str, user: str, acct: dict) -> None:
    """프로젝트 잇기·새 프로젝트 — 고칠 수 있는 사업(관리자·맡은 부서 총괄 책임자) 또는 지정된 사업만. 읽기만 하는 사업은 403."""
    if owns(r, scope, user, acct) or any(user in r[f] for f in ROLES):
        return
    raise HTTPException(403, "이 사업에는 프로젝트를 잇지 못합니다. 맡은 부서 사업이나 지정된 사업만")


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
    label = lambda u: f"{u[5:]}(부서)" if u.startswith("dept:") else (((accounts.get(u) or {}).get("name") or "").strip() or u)
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
    years = []
    for y, v in sorted(r["years"].items(), key=lambda kv: kv[0], reverse=True):
        m = program_sheet.year_money(v)
        years.append({"year": y, **{f: v.get(f, "") for f in ("budget", "manager", "officer", "dept", "account_dept", "period",
                                                                "year_period", "sub", "status", "ministry", "agency", "from")},
                      "money": {f: m[f] for f in ("budget", *program_sheet.SPLIT)},
                      "tasks": [{f: t.get(f, "") for f in ("seq", "sub", "manager", "officer", "dept", "status", "year_period")}
                                | {"budget": t.get("budget_won")} for t in v.get("tasks") or []],
                      "by_hand": list(v.get("by_hand") or [])})
    for a, b in zip(years, years[1:]):                     # 책임자·행정 담당자가 지난해와 달라졌나(앞 해 값이 있을 때만)
        a["changed"] = [f for f in ("manager", "officer", "dept") if a.get(f) and b.get(f) and a[f] != b[f]]
    log = [{**e, "i": n, "label": program_sheet.LABELS.get(e.get("field") or "", ROLE_LABEL.get(e.get("field") or "", e.get("field") or "")),
            "before": _fmt_log(e.get("before"), label), "after": _fmt_log(e.get("after"), label),
            "can_revert": scope == "all" and revertible(e)} for n, e in enumerate(r.get("log") or [])][::-1][:60]
    latest = years[0] if years else {}                     # 위쪽 요약도 표와 같게 — 빈칸이면 가장 최근 해의 값
    pick = lambda f: r.get(f) or latest.get(f, "")
    return {"key": r["key"], "name": r["name"], "l1": r["l1"], "l2": r["l2"], "l3": r["l3"], "dept": pick("dept"),
            "period": pick("period"), "budget": pick("budget"), "manager": pick("manager"), "source": r["source"], "terms": r["terms"][:12],
            "stage": stage(r, int(time.strftime("%Y"))), "years": years,
            "roles": {f: [label(u) for u in r[f]] for f in ROLES},
            "projects": r["plist"], "hidden_projects": r.get("hidden_projects", 0), "docs": docs, "log": log,
            "origin": ORIGIN[r["origin"]], "can_edit": owns(r, scope, user, acct) and stage(r, int(time.strftime("%Y"))) != "past"}


def _fmt_log(v, label) -> str:
    if isinstance(v, list):
        return ", ".join(label(u) for u in v) or "—"
    return str(v) if v not in (None, "") else "—"


@router.post("/programs/admin/link")
def programs_admin_link(request: Request, key: str = Form(""), project_id: str = Form(""), year: str = Form(""),
                        unlink: str = Form("")):
    """프로젝트 잇기·끊기 — projects.program 을 「<사업 열쇠>|<연도>」로(끊으면 빈 값). 관리자는 아무 프로젝트나,
    그 밖에는 볼 수 있는 현행 사업에 자기가 만든 프로젝트만."""
    r, scope, user, acct = _row_for(request, key)
    _may_link(r, scope, user, acct)
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
    _may_link(r, scope, user, acct)
    if not grades.can_create_project(acct):
        raise HTTPException(403, "프로젝트를 만들 수 있는 등급이 아닙니다")
    y = year.strip() or time.strftime("%Y")
    if not re.fullmatch(r"(19|20)\d{2}", y):
        return _back("연도는 네 자리 숫자로 적어 주세요", ok=False)
    title = " ".join(name.split())[:80] or f"{y} {r['name']}"[:80]
    pid = request.app.state.db.create_project("grant", title, owner=user, program=program_value(r["key"], y))
    return RedirectResponse(f"/project/{pid}", status_code=303)
