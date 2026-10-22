"""사업 관리 — 교내 사업 장부를 표로 관리하고 사업마다 총괄 책임자·사업 담당자를 정한다(ADR-0066).

바탕은 외부 확인 장부(kg_external.json)의 사업 항목이다. 장부는 학교의 재정지원사업 현황(연도별 엑셀, scripts/186)과
공개 출처로 채워져 있고 그래프·사업 목록 나무(program_tree)가 같이 읽으므로 여기서 고치지 않는다. 관리 화면에서 바꾼 값
(분류·이름·주관 부서·기간·예산·총괄 책임자·사업 담당자·숨김)과 새로 더한 줄은 따로 program_admin.json 에 둔다 — 장부 값 위에 덮어 보인다.

- 대·중분류: 장부 항목의 지원 주체·체계 갈래(program_tree.place). 소분류는 관리자가 적는다(기본 빈칸)
- 주관 부서: 학교 표의 업무 부서(school.depts). 총괄 책임자 범위 판정에 쓰이므로 계정의 부서 이름과 같게 고쳐 둘 수 있다
- 이어진 프로젝트: projects.program(「program:<열쇠>|<연도>」)의 열쇠가 그 사업의 이름·별칭 열쇠와 같은 것
- 권한: 관리자 전부(편집·추가·숨기기), 총괄 책임자은 자기 부서·총괄 책임자로 지정된 사업 읽기 + 사업 담당자 지정, 사업 담당자은 자기 사업 읽기
- 사업의 총괄 책임자·사업 담당자 지정은 그 사업에 이어진 프로젝트의 진행 현황 권한으로 이어진다(grades.can_view_activity)

화면: GET /programs/admin · 내려받기 GET /programs/admin.csv · 저장 POST /programs/admin/save · 숨기기 POST /programs/admin/hide
"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from zzaimy.app import grades
from zzaimy.app.program_register import slug
from zzaimy.app.program_tree import Ledger, place

router = APIRouter()

STORE = "program_admin.json"
TEXT_FIELDS = ("l1", "l2", "l3", "name", "dept", "period", "budget")
COLUMNS = (("l1", "대분류"), ("l2", "중분류"), ("l3", "소분류"), ("name", "사업"), ("dept", "주관 부서"), ("period", "기간"),
           ("budget", "예산"), ("heads", "총괄 책임자"), ("leads", "사업 담당자"), ("projects", "진행 프로젝트"), ("archived", "보관 묶음"))
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
                    "budget": str(e.get("budget") or ""), "terms": terms, "heads": [], "leads": [], "hidden": False})
    return out


def merged_rows(data_dir=None, ledger: dict | None = None, store: dict | None = None) -> list[dict]:
    """장부 줄 + 관리 화면에서 고친 값 + 더한 줄. 줄마다 ids(이 사업으로 보는 projects.program 열쇠)를 단다."""
    ledger = load_ledger(data_dir) if ledger is None else ledger
    store = load_store(data_dir) if store is None else store
    rows = base_rows(ledger)
    for key, a in store["added"].items():
        rows.append({"key": key, "source": "추가", "l1": "", "l2": "", "l3": "", "name": "", "dept": "", "period": "",
                     "budget": "", "terms": [], "heads": [], "leads": [], "hidden": False, **a})
    for r in rows:
        o = store["rows"].get(r["key"]) or {}
        for f in TEXT_FIELDS:
            if f in o:
                r[f] = o[f]
        for f in ("heads", "leads"):
            if f in o:
                r[f] = list(o[f] or [])
        r["hidden"] = bool(o.get("hidden", r.get("hidden")))
        r["ids"] = {r["key"], *("program:" + k for t in [r["name"], *r["terms"]] if (k := _norm(t)))}
        r["depts"] = [d for d in _SPLIT.split(r.get("dept") or "") if d]
    return rows


def link_projects(db, rows: list[dict]) -> None:
    """줄마다 이어진 프로젝트 수 — 진행(projects, archived=0)과 보관 묶음(archived=1). 열쇠가 바로 맞지 않으면 그래프 사업 노드의
    이름을 줄 이름·별칭으로 찾는다(program_tree.Ledger 와 같은 이름 규칙)."""
    for r in rows:
        r["projects"], r["archived"], r["project_ids"] = 0, 0, []
    if db is None:
        return
    by_id = {i: r for r in rows for i in r["ids"]}
    with db._conn() as conn:
        found = conn.execute("SELECT id, program, archived FROM projects WHERE program <> ''").fetchall()
        labels: dict[str, str] = {}
        try:
            labels = {str(a): str(b or "") for a, b in conn.execute("SELECT id, label FROM kg_nodes WHERE type = 'program'")}
        except Exception:
            pass
    lg = Ledger({"programs": [{"name": r["name"], "terms": r["terms"], "_row": r} for r in rows]})
    for pid, program, archived in found:
        key = str(program or "").split("|", 1)[0]
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


_CACHE: dict = {}


def roles_for_project(project: dict | None, data_dir=None) -> dict:
    """프로젝트가 이어진 사업의 지정 — {"heads": [...], "leads": [...]}. 사업에 이어지지 않았거나 지정이 없으면 빈 dict.
    장부·관리 값은 파일이 바뀔 때만 다시 읽는다."""
    key = str((project or {}).get("program") or "").split("|", 1)[0]
    if not key:
        return {}
    d = _dir(data_dir)
    stamp = tuple((p.stat().st_mtime if p.is_file() else 0) for p in (d / STORE, d / "kg_external.json"))
    if _CACHE.get("at") != (str(d), stamp):
        store = load_store(d)
        idx = {}
        if any(o.get("heads") or o.get("leads") for o in store["rows"].values()):
            for r in merged_rows(d, store=store):
                if r["heads"] or r["leads"]:
                    idx.update({i: {"heads": list(r["heads"]), "leads": list(r["leads"])} for i in r["ids"]})
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
        if scope == "dept" and ((mine and mine in r["depts"]) or user in r["heads"] or user in r["leads"]):
            out.append(r)
        elif scope == "own" and (user in r["leads"] or user in r["heads"]):
            out.append(r)
    return out


def _choices(accounts: dict, scope: str, acct: dict) -> tuple[list[dict], list[dict]]:
    """(총괄 책임자 후보, 사업 담당자 후보) — 사용 중인 교직원 가운데 총괄 책임자은 총괄 책임자 등급, 사업 담당자는 사업 담당자 이상.
    총괄 책임자이 고르는 사업 담당자는 자기 부서 사람만."""
    heads, leads = [], []
    mine = str(acct.get("dept") or "").strip()
    for u, a in sorted(accounts.items()):
        if not a.get("active", True) or a.get("role") not in grades.STAFF:
            continue
        g = grades.grade_of(a)
        item = {"user": u, "label": (a.get("name") or "").strip() or u, "dept": a.get("dept") or "", "grade": grades.label(g)}
        if g == "head":
            heads.append(item)
        if grades.at_least(g, "lead") and (scope == "all" or (mine and (a.get("dept") or "").strip() == mine)):
            leads.append(item)
    return heads, leads


def _back(msg: str, ok: bool = True, anchor: str = "") -> RedirectResponse:
    return RedirectResponse(f"/programs/admin?{'ok' if ok else 'err'}={quote(msg)}{anchor}", status_code=303)


def _scope_or_403(request: Request) -> tuple[str, str, dict]:
    user, acct = _me(request)
    scope = grades.program_scope(acct)
    if not scope:
        raise HTTPException(403, "사업 관리는 사업 담당자 이상만 볼 수 있습니다")
    return scope, user, acct


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

@router.get("/programs/admin", response_class=HTMLResponse)
def programs_admin(request: Request, ok: str = "", err: str = "", hidden: str = ""):
    scope, user, acct = _scope_or_403(request)
    st = request.app.state
    accounts = getattr(st, "accounts", None) or {}
    rows = merged_rows(st.data_dir)
    link_projects(st.db, rows)
    shown = visible_rows(rows, scope, user, acct, show_hidden=bool(hidden) and scope == "all")
    heads, leads = _choices(accounts, scope, acct)
    name_of = {u: ((a.get("name") or "").strip() or u) for u, a in accounts.items()}
    for r in shown:
        r["edit"] = {k: r.get(k) for k in ("key", *TEXT_FIELDS, "heads", "leads", "source")}
    from zzaimy.app.program_tree import ORDER
    l1s = [x for x in ORDER if any(r["l1"] == x for r in shown)] + sorted({r["l1"] for r in shown if r["l1"] and r["l1"] not in ORDER})
    depts = sorted({d for r in shown for d in r["depts"]})
    return st.templates.TemplateResponse(request, "programs_admin.html", st.page_ctx(request, {
        "rows": shown, "scope": scope, "can_edit": scope == "all", "can_assign": scope in ("all", "dept"),
        "head_choices": heads, "lead_choices": leads, "name_of": name_of, "l1s": l1s, "depts": depts,
        "show_hidden": bool(hidden), "n_hidden": sum(1 for r in rows if r["hidden"]) if scope == "all" else 0,
        "ok": ok, "err": err, "me_dept": acct.get("dept") or ""}))


@router.get("/programs/admin.csv")
def programs_admin_csv(request: Request, q: str = "", dept: str = "", l1: str = "", hidden: str = ""):
    """보이는 줄을 CSV 로(엑셀에서 바로 열리게 BOM 포함). 화면과 같은 권한·거르기."""
    scope, user, acct = _scope_or_403(request)
    st = request.app.state
    accounts = getattr(st, "accounts", None) or {}
    rows = merged_rows(st.data_dir)
    link_projects(st.db, rows)
    rows = _filter(visible_rows(rows, scope, user, acct, show_hidden=bool(hidden) and scope == "all"), q, dept, l1)
    name_of = lambda u: f"{(accounts.get(u) or {}).get('name') or u}({u})"
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _, label in COLUMNS] + (["숨김"] if scope == "all" else []))
    for r in rows:
        vals = [", ".join(name_of(u) for u in r[k]) if k in ("heads", "leads") else r.get(k, "") for k, _ in COLUMNS]
        w.writerow(vals + (["숨김" if r["hidden"] else ""] if scope == "all" else []))
    fname = quote(f"사업 관리 {time.strftime('%Y-%m-%d')}.csv")
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


@router.post("/programs/admin/save")
async def programs_admin_save(request: Request):
    """줄 저장 — 관리자는 모든 칸과 새 줄(key 비움), 총괄 책임자은 보이는 줄의 사업 담당자만. 장부 값과 같은 칸은 따로 적지 않는다."""
    scope, user, acct = _scope_or_403(request)
    if scope not in ("all", "dept"):
        raise HTTPException(403, "사업 담당자 지정은 총괄 책임자과 관리자만 합니다")
    st = request.app.state
    accounts = getattr(st, "accounts", None) or {}
    form = await request.form()
    key = str(form.get("key") or "").strip()
    store = load_store(st.data_dir)
    rows = merged_rows(st.data_dir, store=store)
    by_key = {r["key"]: r for r in rows}
    heads_ok, leads_ok = _choices(accounts, scope, acct)
    heads_ok, leads_ok = {c["user"] for c in heads_ok}, {c["user"] for c in leads_ok}
    want_leads = list(dict.fromkeys(str(x) for x in form.getlist("leads") if str(x)))
    if scope == "dept":
        r = by_key.get(key)
        if r is None or r not in visible_rows(rows, scope, user, acct):
            raise HTTPException(404, "사업을 찾을 수 없습니다")
        keep = [u for u in r["leads"] if u not in leads_ok]          # 다른 부서 사람이 이미 지정돼 있으면 그대로 둔다
        bad = [u for u in want_leads if u not in leads_ok and u not in r["leads"]]
        if bad:
            return _back("사업 담당자는 같은 부서의 사업 담당자 이상 계정에서 고릅니다", ok=False)
        store["rows"].setdefault(key, {})["leads"] = list(dict.fromkeys(keep + want_leads))
        save_store(store, st.data_dir)
        return _back(f"「{r['name']}」 사업 담당자를 저장했습니다")
    vals = {f: " ".join(str(form.get(f) or "").split())[:120] for f in TEXT_FIELDS}
    if len(vals["name"]) < 2:
        return _back("사업 이름을 적어 주세요", ok=False)
    want_heads = list(dict.fromkeys(str(x) for x in form.getlist("heads") if str(x)))
    old = by_key.get(key)
    bad = [u for u in want_heads if u not in heads_ok and u not in (old or {}).get("heads", [])] + \
          [u for u in want_leads if u not in leads_ok and u not in (old or {}).get("leads", [])]
    if bad:
        return _back(f"지정할 수 없는 계정입니다: {', '.join(bad[:5])} — 총괄 책임자은 총괄 책임자 등급, 사업 담당자는 사업 담당자 이상 교직원", ok=False)
    if not key:
        key = slug(vals["name"])
        if key in by_key:
            return _back(f"「{vals['name']}」 사업은 이미 있습니다", ok=False)
        store["added"][key] = {**vals, "added_by": user, "added_at": time.strftime("%Y-%m-%d")}
        store["rows"][key] = {"heads": want_heads, "leads": want_leads}
        save_store(store, st.data_dir)
        return _back(f"「{vals['name']}」 줄을 더했습니다")
    if old is None:
        raise HTTPException(404, "사업을 찾을 수 없습니다")
    base = next((r for r in base_rows(load_ledger(st.data_dir)) if r["key"] == key), None)
    o = {k: v for k, v in (store["rows"].get(key) or {}).items() if k in ("hidden",)}
    if old["source"] == "추가":
        store["added"][key] = {**store["added"].get(key, {}), **vals}
    else:
        o.update({f: v for f, v in vals.items() if base is None or v != base.get(f, "")})
    o["heads"], o["leads"] = want_heads, want_leads
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
