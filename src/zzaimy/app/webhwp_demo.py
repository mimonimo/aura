"""공동 편집 시연(ADR-0071) — 관리자 화면 하나에 같은 기안기 문서를 세 칸으로 열고, 칸마다 다른 시연 계정으로 쓴다.

한 주소(한 쿠키)에서 세 사람을 흉내 내야 해서 로그인 세션은 관리자 그대로 두고, 칸 표시(요청 머리 X-Aura-Demo,
알림 흐름·sendBeacon 처럼 머리를 못 붙이는 요청은 주소의 demo=)를 단 요청만 그 요청 동안 시연 계정으로 판정한다.
바꾸는 조건은 셋 모두 — 실제 로그인한 사람이 관리자, 칸 표시가 시연 계정(장부에 demo 표시가 있는 계정), 주소가 기안기 편집·공동 편집 경로.
그 밖의 요청은 표시를 무시한다. 시연 계정은 비활성·비밀번호 없음이라 로그인할 수 없고, 열 수 있는 곳은 시연 폴더(ROOT) 하나다
(webhwp_routes._depts). 그래서 표시는 권한을 좁히기만 한다.

화면: GET /dev/coedit-demo, 새 시연 문서 POST /dev/coedit-demo/new, 문서 비우기 POST /dev/coedit-demo/reset.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()

# 칸 → (계정 아이디, 보일 이름)
SLOTS: dict[str, tuple[str, str]] = {"a": ("demo_a", "김가람"), "b": ("demo_b", "이나래"), "c": ("demo_c", "박다온")}   # 이름 앞에 「시연」 을 붙이면 머리 글자 동그라미가 모두 「시」 가 된다 — 칸 위에만 「시연」
ROOT = "~시연"          # 기안기 문서함의 시연 뿌리 — 「내 폴더」 꼴(~)이라 부서 목록에 끼지 않고, 계정 아이디 규칙([a-z0-9_-]) 밖이라 남의 내 폴더와 겹치지 않는다
HEADER = "x-aura-demo"
SOURCE = "시연:"         # 시연 문서 장부의 source — 뒤에 복사한 공통 양식 id
MIN_FIELDS = 4
_EDITOR = re.compile(r"^/hwp/\d+/(?:file|save|state|presence|live|lock|unlock|coedit|events|ops|lease|leave|conflicts|page-setup"
                     r"|cmd(?:/\d+(?:/asset)?)?)$")


def slot_of(request: Request) -> str:
    v = (request.headers.get(HEADER) or request.query_params.get("demo") or "").strip().lower()
    return v if v in SLOTS else ""


def editor_path(path: str) -> bool:
    from zzaimy.app.webhwp_routes import EDIT_PAGE
    return path == EDIT_PAGE or bool(_EDITOR.match(path))


def apply(request: Request, accounts: dict) -> None:
    """인증 단계에서 부른다 — 조건이 모두 맞을 때만 이 요청을 시연 계정으로 바꾼다(아니면 아무것도 하지 않는다)."""
    slot = slot_of(request)
    if not slot or getattr(request.state, "real_role", "") != "dev" or not editor_path(request.url.path):
        return
    uid = SLOTS[slot][0]
    if not (accounts.get(uid) or {}).get("demo"):
        raise HTTPException(403, "시연 계정이 없습니다 — 공동 편집 시연 화면에서 「새 시연 문서」로 만드세요")
    request.state.demo = slot
    request.state.demo_by = request.state.user
    request.state.user = uid
    request.state.role = request.state.real_role = "staff"
    request.state.dept = ""
    request.state.view_as = None


def ensure_accounts(app) -> None:
    """시연 계정 셋을 장부에 둔다(있으면 그대로) — 비활성·비밀번호 없음이라 로그인 길이 없다. 같은 아이디의 실제 계정은 건드리지 않는다."""
    from zzaimy.app import org_lock
    accounts = app.state.accounts
    with org_lock.LOCK:
        changed = False
        for uid, name in SLOTS.values():
            if uid in accounts:
                if accounts[uid].get("demo") and accounts[uid].get("name") != name:   # 앞판 이름(「시연 …」) 고치기 — 시연 계정만
                    accounts[uid]["name"] = name
                    changed = True
                continue
            accounts[uid] = {"role": "staff", "name": name, "dept": "", "active": False, "demo": True,
                             "created_at": datetime.now().isoformat(timespec="seconds")}
            changed = True
        if changed:
            app.state.save_accounts()


def accounts_ready(app) -> list[str]:
    """쓸 수 없는 칸의 계정 아이디 — 없거나, 같은 아이디의 실제 계정이 있는 것."""
    acc = app.state.accounts
    return [uid for uid, _ in SLOTS.values() if not (acc.get(uid) or {}).get("demo")]


def _base(request: Request) -> Path:
    return Path(request.app.state.data_dir)


def docs(request: Request) -> list[dict]:
    from zzaimy.app import webhwp
    db, base = request.app.state.db, _base(request)
    out = [r for r in db.list_files(kind="webhwp", limit=5000)
           if webhwp.dept_of_path(base, r["path"]) == ROOT and Path(r["path"]).exists()]
    return sorted(out, key=lambda r: r["id"], reverse=True)


def pick_form(request: Request, prefer: int | None = None) -> dict:
    """복사할 공통 양식 — 잠긴 공통 양식 가운데 칸(필드)이 MIN_FIELDS 개 이상인 것. prefer(앞서 쓴 양식)가 맞으면 그것, 아니면 칸이 가장 적은 것."""
    from zzaimy.app import webhwp, webhwp_routes
    from zzaimy.app import webhwp_write as ww
    db, base = request.app.state.db, _base(request)
    webhwp_routes._ensure_templates(db, base)
    best = None
    for r in db.list_files(kind="webhwp", limit=5000):
        p = Path(r["path"])
        if not p.exists() or not webhwp.locked(db, r["id"]):
            continue
        try:
            n = len(ww.fields(p.read_bytes()))
        except Exception:
            continue
        if n < MIN_FIELDS:
            continue
        if prefer is not None and r["id"] == prefer:
            return r
        if "요약 보고서" in r["name"] and (best is None or "요약 보고서" not in best[1]["name"]):   # 보기 쉬운 1쪽 보고서 양식을 먼저
            best = (n, r)
            continue
        if best is not None and "요약 보고서" in best[1]["name"]:
            continue
        if best is None or (n, r["name"]) < (best[0], best[1]["name"]):
            best = (n, r)
    if best is None:
        raise HTTPException(409, f"칸이 {MIN_FIELDS}개 이상인 공통 양식이 없습니다")
    return best[1]


LONG_MIN, LONG_MAX = 100, 320       # 장문 시연 — 절 필드 수 범위(너무 큰 별첨 묶음은 열기만 오래 걸린다)


def pick_long_form(request: Request) -> dict:
    """장문 시연용 양식 — 잠긴 양식 가운데 이름에 「사업계획서」 가 들고 절 필드가 LONG_MIN~LONG_MAX 개인 것 중 절이 가장 많은 것.
    수십 쪽 보고서를 여럿이 서로 다른 장에서 함께 쓰는 장면(멀리 떨어진 쪽의 이름표·옮겨 가기·지연)을 보인다."""
    from zzaimy.app import webhwp
    from zzaimy.app import webhwp_write as ww
    db = request.app.state.db
    best = None
    for r in db.list_files(kind="webhwp", limit=5000):
        p = Path(r["path"])
        if "사업계획서" not in r["name"] or not p.exists() or not webhwp.locked(db, r["id"]):
            continue
        try:
            n = sum(1 for x in ww.fields(p.read_bytes()) if x["kind"] == "절")
        except Exception:
            continue
        if LONG_MIN <= n <= LONG_MAX and (best is None or n > best[0]):
            best = (n, r)
    if best is None:
        raise HTTPException(409, f"절이 {LONG_MIN}~{LONG_MAX}개인 사업계획서 양식이 없습니다")
    return best[1]


def make_doc(request: Request, prefer: int | None = None, long: bool = False) -> int:
    from zzaimy.app import webhwp
    form = pick_long_form(request) if long and prefer is None else pick_form(request, prefer)
    data = Path(form["path"]).read_bytes()
    name = f"시연 {Path(form['name']).stem}{Path(form['name']).suffix or '.hwpx'}"
    return webhwp.add(request.app.state.db, _base(request), ROOT, name, data,
                      owner=getattr(request.state, "user", ""), source=f"{SOURCE}{form['id']}")


def _need_login_mode(request: Request) -> None:
    """관리자 본인 세션만 — 인증 없는 로컬 모드(계정 장부가 비어 있다)에서는 장부 파일을 만들지 않게 막는다."""
    acc = getattr(request.app.state, "accounts", None)
    if getattr(request.state, "real_role", "") != "dev" or not isinstance(acc, dict) or request.state.user not in acc:
        raise HTTPException(403, "로그인한 관리자 계정만 여는 화면입니다")


@router.get("/dev/coedit-demo", response_class=HTMLResponse)
def coedit_demo(request: Request, id: int | None = None):
    _need_login_mode(request)
    st = request.app.state
    items = docs(request)
    cur = next((r for r in items if r["id"] == id), items[0] if items else None)
    missing = accounts_ready(request.app)
    panes = [{"slot": s, "uid": uid, "name": st.person_name(uid) if uid not in missing else name}
             for s, (uid, name) in SLOTS.items()]
    return st.templates.TemplateResponse(request, "coedit_demo.html", {
        "request": request, "docs": items, "cur": cur, "panes": panes, "missing": missing})


@router.post("/dev/coedit-demo/new")
def coedit_demo_new(request: Request, kind: str = Form("")):
    _need_login_mode(request)
    ensure_accounts(request.app)
    if accounts_ready(request.app):
        raise HTTPException(409, "같은 아이디의 계정이 있어 시연 계정을 만들지 못했습니다: " + ", ".join(accounts_ready(request.app)))
    return RedirectResponse(f"/dev/coedit-demo?id={make_doc(request, long=kind == 'long')}", status_code=303)


@router.post("/dev/coedit-demo/reset")
def coedit_demo_reset(request: Request, id: int = Form(...)):
    """문서 비우기 — 같은 양식으로 새 사본을 만들고 앞 사본은 시연 폴더 휴지통으로."""
    _need_login_mode(request)
    from zzaimy.app import webhwp
    db = request.app.state.db
    old = next((r for r in docs(request) if r["id"] == id), None)
    if old is None:
        raise HTTPException(404, "시연 문서를 찾을 수 없습니다")
    src = str(webhwp.meta(db, id).get("source", ""))
    prefer = int(src[len(SOURCE):]) if src.startswith(SOURCE) and src[len(SOURCE):].isdigit() else None
    ensure_accounts(request.app)
    new = make_doc(request, prefer)
    webhwp.trash(db, _base(request), id, user=getattr(request.state, "user", ""))
    return RedirectResponse(f"/dev/coedit-demo?id={new}", status_code=303)
