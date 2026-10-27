"""공동 편집 시연(ADR-0071) — 관리자 화면 하나에 같은 기안기 문서를 세 칸으로 열고, 칸마다 다른 시연 계정으로 쓴다.

한 주소(한 쿠키)에서 세 사람을 흉내 내야 해서 로그인 세션은 관리자 그대로 두고, 칸 표시(요청 머리 X-Aura-Demo,
알림 흐름·sendBeacon 처럼 머리를 못 붙이는 요청은 주소의 demo=)를 단 요청만 그 요청 동안 시연 계정으로 판정한다.
바꾸는 조건은 셋 모두 — 실제 로그인한 사람이 관리자, 칸 표시가 시연 계정(장부에 demo 표시가 있는 계정), 주소가 기안기 편집·공동 편집 경로.
그 밖의 요청은 표시를 무시한다. 시연 계정은 비활성·비밀번호 없음이라 로그인할 수 없고, 열 수 있는 곳은 시연 폴더(ROOT) 하나다
(webhwp_routes._depts). 그래서 표시는 권한을 좁히기만 한다.

화면: GET /dev/coedit-demo — 한 문서(제목만 있는 빈 문서 → AURA 가 양식을 불러와 표지·기본 칸을 시연 시나리오(scenario)의 가상 사업으로 채우면
세 사람과 AURA 가 여러 쪽을 함께 쓴다). 칸 밖 문단도 칸으로 감싸 제목까지 함께 고친다
(webhwp_fields.wrap_rest). 시연 문서가 없으면 열 때 만든다. 「새 빈 문서」 POST /dev/coedit-demo/reset, 「AURA 양식 불러오기 / 이어 쓰기」 POST /dev/coedit-demo/aura(단계는 GET /dev/coedit-demo/state),
POST /dev/coedit-demo/new(kind=long 이면 사업계획서)는 화면 단추 없이 남긴 만들기 길. 창을 열면 칸마다 자기 차례의 쓸 자리로 커서가 간다(hwp_edit.html).
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, APIRouter, Form, HTTPException, Request
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
        raise HTTPException(403, "시연 계정이 없습니다 — 공동 편집 시연 화면을 한 번 열면 만들어집니다")
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


TEMPLATE = "양식:"         # 코드로 짠 공통 양식(webhwp_templates)의 source 앞머리 — 시연은 이 양식만 쓴다
SHORT, LONG = "양식:brief", "양식:plan"   # 「AURA 함께 쓰기」 = 사업계획서(17쪽, 절 36). 1쪽 요약 보고서는 make_doc 기본(만들기 길에만)


def _template_forms(request: Request) -> dict[str, dict]:
    """코드로 짠 공통 양식 — {source: 문서}. 지난 사업 문서(실물에서 모은 양식)는 시연에 쓰지 않는다(이름·내용이 실물 그대로라)."""
    from zzaimy.app import webhwp, webhwp_routes
    db, base = request.app.state.db, _base(request)
    webhwp_routes._ensure_templates(db, base)
    out: dict[str, dict] = {}
    for r in db.list_files(kind="webhwp", limit=5000):
        src = str(webhwp.meta(db, r["id"]).get("source", ""))
        if src.startswith(TEMPLATE) and Path(r["path"]).exists() and webhwp.locked(db, r["id"]):
            out.setdefault(src, r)
    return out


def pick_form(request: Request, prefer: int | None = None, long: bool = False) -> dict:
    """복사할 공통 양식 — 코드로 짠 공통 양식만. prefer(앞서 쓴 양식 id)가 그 가운데 있으면 그것, 아니면 장문이면 사업계획서,
    아니면 1쪽 요약 보고서, 그것도 없으면 칸이 MIN_FIELDS 개 이상인 공통 양식 가운데 칸이 가장 적은 것."""
    from zzaimy.app import webhwp_write as ww
    forms = _template_forms(request)
    if prefer is not None:
        hit = next((r for r in forms.values() if r["id"] == prefer), None)
        if hit:
            return hit
    want = LONG if long else SHORT
    if want in forms:
        return forms[want]
    if long:
        raise HTTPException(409, "공통 양식 「사업계획서」 가 없습니다")
    best = None
    for r in forms.values():
        try:
            n = len(ww.fields(Path(r["path"]).read_bytes()))
        except Exception:
            continue
        if n >= MIN_FIELDS and (best is None or n < best[0]):
            best = (n, r)
    if best is None:
        raise HTTPException(409, f"칸이 {MIN_FIELDS}개 이상인 공통 양식이 없습니다")
    return best[1]


def scenario(today=None) -> dict[str, str]:
    """시연 시나리오 — 시연 문서가 될 가상 사업의 설정. 실제 사업이 아니라는 것이 사업명에 드러나고, 연도·날짜는 오늘에서 셈한다.
    이 값은 재료일 뿐 칸 이름에 묶지 않는다 — 어느 칸·어느 글에 넣을지는 27B 가 양식을 보고 정한다(form_ask·write_ask).
    숫자는 여기 준 것만 근거가 된다(요청 글이 수치 관문의 근거라, 설정 밖 숫자는 본문 관문이 막는다). 꾸민 수치에는 「예시」 를 붙인다."""
    from datetime import date
    d = today or date.today()
    y = d.year
    return {
        "사업명": f"{y} AI 융합 실무인재 양성사업(시연용 가상 사업)",
        "주관 기관": "영남이공대학교",
        "주관 부서": "기획팀",
        "사업 책임자": SLOTS["a"][1],
        "참여 인력": f"{SLOTS['b'][1]}(교육 운영), {SLOTS['c'][1]}(산학 협력)",
        "작성일": d.strftime("%Y. %m. %d."),
        "사업 기간": f"{y}. 3. 1. ~ {y + 1}. 2. 28.",
        "총사업비": "5억 원(예시)",
        "참여 대상": "3개 학과 재학생 120명(예시)",
        "목표": "생성형 AI 를 실무에 쓰는 전문 기술 인재 양성, 지역 기업 현장 과제 해결",
        "주요 내용": "AI 기초·응용 교과 개설, 현장 실습 과제, 지역 기업 협력 프로젝트, 교원 연수",
        "성과 지표(예시)": "교과 이수 학생 100명, 기업 협력 과제 10건, 만족도 4.0점(5점 만점)",
    }


def scenario_text(today=None) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in scenario(today).items())


def demo_values(fields: list[dict]) -> dict[str, str]:
    """만들기 길(make_doc — 화면 단추 없는 장문 시연 문서)의 머리 칸 값 — 칸 이름 낱말로만 고른다. 본문(절)은 시연 때 쓰도록 비워 둔다.
    화면 시연(빈 문서 → 양식 불러오기)은 이 규칙을 쓰지 않고 27B 가 시나리오(scenario)를 보고 칸을 고른다."""
    sc = scenario()
    rules = [(("일자", "날짜", "작성일"), sc["작성일"]),
             (("부서", "소속"), sc["주관 부서"]),
             (("보고자", "작성자", "담당자", "책임자", "성명"), sc["사업 책임자"]),
             (("사업명", "과제명", "제목", "프로그램명"), sc["사업명"]),
             (("기관", "대학명", "학교"), sc["주관 기관"])]
    out: dict[str, str] = {}
    for f in fields:
        if f.get("kind") != "칸" or f.get("text"):
            continue
        name = f["name"].split(":", 1)[-1]
        for words, value in rules:
            if any(w in name for w in words):
                out[f["name"]] = value
                break
    return out


def demo_cover(data: bytes) -> bytes:
    """표지·머리의 양식 자리표시(칸 밖 글 「○○○○ 사업」·「○○대학교」·「20○○. ○.」)를 시연용 값으로 — 시연 문서에만.
    양식 자체(공통 양식)는 그대로다. 칸 안 안내 글은 손대지 않는다(paper_hwp.stamp_dept 와 같은 칸 밖 바꾸기)."""
    from datetime import date
    from zzaimy.app import paper_hwp
    today = date.today()
    sc = scenario(today)
    pairs = [("○○○○ 사업", sc["사업명"]), ("○○대학교", sc["주관 기관"]), ("20○○. ○.", f"{today.year}. {today.month}."), ("20○○년", f"{today.year}년")]
    for old, new in pairs:
        data, _ = paper_hwp.stamp_dept(data, [old], new)
    return data


def label(name: str) -> str:
    """목록에 보일 시연 문서 이름 — 「시연 · 」·「 공통 양식」 을 뗀 양식 이름."""
    stem = Path(name).stem
    for cut in ("시연 · ", "시연 "):
        if stem.startswith(cut):
            stem = stem[len(cut):]
    return stem.replace(" 공통 양식", "").strip()


BLANK = "blank2"           # 2 = 제목만 있는 빈 문서(양식 불러오기 시연, 10-26). 옛 「blank」(사람별 절) 문서는 열 때 휴지통으로 — 「세 사람 동시 작성」 — 양식 사본이 아닌 빈 문서(시연 계정 수만큼 번호 붙은 절, 안내 글·미리 넣은 값 없음)


def blank_doc() -> bytes:
    """빈 시연 문서 — 제목 한 줄과 빈 문단뿐. 시연은 여기서 시작해 AURA(27B)가 양식을 찾아 불러오고(webhwp_agent 의 빈 문서 불러오기),
    세 사람과 AURA 가 그 양식 여러 쪽을 함께 쓴다(10-26 「빈 페이지 → 양식 찾아 불러옴 → 작성 → 사용자별 입력·서식 → 따라가기, 몇십 쪽」).
    모양·누름틀은 공통 양식과 같은 빌더(webhwp_templates.build)로."""
    from zzaimy.app import webhwp_templates as wt
    data = wt.build({"id": "demo-blank", "title": "공동 편집 시연",
                     "cover": {"title": "공동 편집 시연", "page": False},
                     "blocks": [{"p": ""}], "page_number": False})
    return _bigger_text(data, BIG)


BIG = 1.6        # 시연 문서 글자 배율 — 세 칸으로 나눈 좁은 화면에서도 앞에서 보이게(시연 문서에만)


def _bigger_text(data: bytes, k: float) -> bytes:
    """hwpx 글자 모양(header.xml charPr height, 1/100pt)을 k 배로 — 모든 글 갈래를 같은 비율로 키운다."""
    import io
    import zipfile
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "Contents/header.xml":
                body = re.sub(rb'(<hh:charPr\b[^>]*?\bheight=")(\d+)(")', lambda m: m.group(1) + str(round(int(m.group(2)) * k)).encode() + m.group(3), body)
            z.writestr(info, body)
    return out.getvalue()


def make_blank(request: Request) -> int:
    """빈 시연 문서 하나 — 앞 빈 시연 문서와 옛 꼴 시연 문서는 시연 폴더 휴지통으로(장문 시연 문서는 남긴다)."""
    from zzaimy.app import webhwp
    db, base = request.app.state.db, _base(request)
    template_ids = {r["id"] for r in _template_forms(request).values()}
    user = getattr(request.state, "user", "")
    for r in docs(request):
        src = str(webhwp.meta(db, r["id"]).get("source", ""))
        rest = src[len(SOURCE):] if src.startswith(SOURCE) else ""
        if not (rest.isdigit() and int(rest) in template_ids):
            webhwp.trash(db, base, r["id"], user=user)
    return webhwp.add(db, base, ROOT, "시연 · 공동 편집.hwpx", blank_doc(), owner=user, source=f"{SOURCE}{BLANK}")


def make_doc(request: Request, prefer: int | None = None, long: bool = False) -> int:
    """공통 양식 사본으로 시연 문서 하나 — 이름은 「시연 · 양식 이름」, 머리 칸은 시연용 값. 같은 양식으로 만든 앞 시연 문서와
    코드로 짠 공통 양식이 아닌 양식으로 만든 옛 시연 문서는 시연 폴더 휴지통으로(목록에 같은 이름이 쌓이지 않게)."""
    from zzaimy.app import webhwp
    from zzaimy.app import webhwp_write as ww
    db, base = request.app.state.db, _base(request)
    form = pick_form(request, prefer, long)
    data = Path(form["path"]).read_bytes()
    try:
        data, _ = ww.set_fields(data, demo_values(ww.fields(data)))
        data = demo_cover(data)
    except Exception:
        pass
    name = f"시연 · {label(form['name'])}{Path(form['name']).suffix or '.hwpx'}"
    template_ids = {r["id"] for r in _template_forms(request).values()}
    user = getattr(request.state, "user", "")
    for r in docs(request):
        src = str(webhwp.meta(db, r["id"]).get("source", ""))
        if src == SOURCE + BLANK:
            continue
        sid = int(src[len(SOURCE):]) if src.startswith(SOURCE) and src[len(SOURCE):].isdigit() else None
        if sid == form["id"] or sid not in template_ids:
            webhwp.trash(db, base, r["id"], user=user)
    return webhwp.add(db, base, ROOT, name, data, owner=user, source=f"{SOURCE}{form['id']}")


def _need_login_mode(request: Request) -> None:
    """관리자 본인 세션만 — 인증 없는 로컬 모드(계정 장부가 비어 있다)에서는 장부 파일을 만들지 않게 막는다."""
    acc = getattr(request.app.state, "accounts", None)
    if getattr(request.state, "real_role", "") != "dev" or not isinstance(acc, dict) or request.state.user not in acc:
        raise HTTPException(403, "로그인한 관리자 계정만 여는 화면입니다")


def is_blank_id(app, fid: int) -> bool:
    from zzaimy.app import webhwp
    return str(webhwp.meta(app.state.db, fid).get("source", "")) == SOURCE + BLANK


def is_blank(request: Request, fid: int) -> bool:
    from zzaimy.app import webhwp
    return str(webhwp.meta(request.app.state.db, fid).get("source", "")) == SOURCE + BLANK


@router.get("/dev/coedit-demo", response_class=HTMLResponse)
def coedit_demo(request: Request, id: int | None = None, aura: str = ""):
    """한 문서에 세 사람과 AURA — 빈 문서에서 시작해 AURA(27B)가 양식을 찾아 불러오고, 세 사람과 AURA 가 그 양식 여러 쪽을 함께 쓴다(10-26).
    갈래를 둘(세 사람·AURA 양식)로 나눴더니 「27B 문서에서도 공동 시연이 되어야」 했다(10-26). 시연 문서가 없으면 바로 만든다."""
    _need_login_mode(request)
    from zzaimy.app import webhwp
    st = request.app.state
    template_ids = {r["id"] for r in _template_forms(request).values()}
    items = []
    for r in docs(request):                              # 옛 꼴 시연 문서(지난 사업 실물 양식 사본)는 시연 폴더 휴지통으로 — 목록에 남지 않게
        src = str(webhwp.meta(st.db, r["id"]).get("source", ""))
        rest = src[len(SOURCE):] if src.startswith(SOURCE) else ""
        if rest == BLANK or (rest.isdigit() and int(rest) in template_ids):
            items.append(r)
        else:
            webhwp.trash(st.db, _base(request), r["id"], user=getattr(request.state, "user", ""))
    blanks = [r for r in items if is_blank(request, r["id"])]
    for r in items:                                      # 한 갈래로 합침(10-26) — 양식 사본 시연 문서는 휴지통으로
        if r not in blanks:
            webhwp.trash(st.db, _base(request), r["id"], user=getattr(request.state, "user", ""))
    ok = blanks
    cur = next((r for r in ok if r["id"] == id), None) or (ok[0] if ok else None)
    if cur is None:
        ensure_accounts(request.app)
        if not accounts_ready(request.app):
            fid = make_blank(request)
            return RedirectResponse(f"/dev/coedit-demo?id={fid}", status_code=303)
    missing = accounts_ready(request.app)
    panes = [{"slot": s, "uid": uid, "name": st.person_name(uid) if uid not in missing else name}
             for s, (uid, name) in SLOTS.items()]
    return st.templates.TemplateResponse(request, "coedit_demo.html", {
        "request": request, "cur": cur, "panes": panes, "missing": missing, "label": label, "loaded": form_loaded(request, cur["id"]) if cur else False,
        "aura_note": {"busy": "AURA가 앞 요청을 하는 중"}.get(aura, "")})


@router.post("/dev/coedit-demo/new")
def coedit_demo_new(request: Request, kind: str = Form("")):
    _need_login_mode(request)
    ensure_accounts(request.app)
    if accounts_ready(request.app):
        raise HTTPException(409, "같은 아이디의 계정이 있어 시연 계정을 만들지 못했습니다: " + ", ".join(accounts_ready(request.app)))
    fid = make_doc(request, long=True) if kind == "long" else make_blank(request)
    return RedirectResponse(f"/dev/coedit-demo?id={fid}", status_code=303)


@router.get("/dev/coedit-demo/state")
def coedit_demo_state(request: Request, id: int):
    """시연 머리 단추의 단계 — 양식을 불러왔는지, AURA 가 아직 일하는지(화면을 다시 열지 않고 단추만 바꾼다)."""
    _need_login_mode(request)
    from zzaimy.app import webhwp
    st = request.app.state
    sid = int(webhwp.meta(st.db, id).get("demo_session") or 0)
    busy = bool(sid and getattr(st, "chat_running", lambda _s: False)(sid))
    out = {"loaded": form_loaded(request, id), "busy": busy}
    if busy:                                             # 지금 AURA 단계(찾는 중·절 쓰는 중 …) — 「빈 절을 쓰고 있어요」 만으로는 무엇을 하는지 안 보였다
        out["step"] = str(getattr(st, "chat_step", lambda _s: "")(sid) or "")[:80]
    return out


@router.post("/dev/coedit-demo/reset")
def coedit_demo_reset(request: Request, id: int = Form(...)):
    """새 빈 문서 — 같은 꼴(빈 문서·같은 양식)로 새로 만들고 앞 것은 시연 폴더 휴지통으로."""
    _need_login_mode(request)
    from zzaimy.app import webhwp
    db = request.app.state.db
    old = next((r for r in docs(request) if r["id"] == id), None)
    if old is None:
        raise HTTPException(404, "시연 문서를 찾을 수 없습니다")
    src = str(webhwp.meta(db, id).get("source", ""))
    prefer = int(src[len(SOURCE):]) if src.startswith(SOURCE) and src[len(SOURCE):].isdigit() else None
    ensure_accounts(request.app)
    new = make_blank(request) if src == SOURCE + BLANK else make_doc(request, prefer)   # 같은 꼴의 앞 사본은 휴지통으로
    if webhwp.get(db, id) is not None:
        webhwp.trash(db, _base(request), id, user=getattr(request.state, "user", ""))
    return RedirectResponse(f"/dev/coedit-demo?id={new}", status_code=303)


def form_ask(today=None) -> str:
    """「AURA 양식 불러오기」 요청 — 양식 고르기·불러오기와, 불러온 문서를 시연 시나리오의 가상 사업 문서로 바꾸기까지.
    어떤 양식을 고르고 어느 칸·어느 글을 고칠지는 27B 가 정한다(양식 칸 이름을 코드에 두지 않는다)."""
    return ("이 빈 문서를 아래 「시연용 가상 사업」 의 사업계획서로 만들려고 해. 차례대로 해 줘.\n"
            "1) hwp_forms 로 공통 양식에서 사업계획서 양식을 찾아. 후보 가운데 양식 이름의 연도가 가장 최근이고 칸 수가 많은(여러 쪽 분량) "
            "사업계획서를 골라 hwp_start 로 이 문서에 불러와. 오래된 연도의 양식은 더 나은 후보가 없을 때만 고른다.\n"
            "2) 불러온 문서를 이 가상 사업의 문서로 바꿔: 표지·머리의 사업명·기관·부서·책임자·작성일·사업 기간 같은 기본 칸은 hwp_fill 로 "
            "아래 값을 넣고(이 값은 내가 알려 주는 값이라 「입력:」 칸도 from_user: true 로 넣어도 된다), 칸 밖 글(표지 제목·연도·날짜 줄)에 "
            "양식의 옛 사업명·옛 연도·옛 날짜·자리표시(○○ 등)가 남아 있으면 hwp_replace 로 아래 값에 맞게 바꿔. 문서 앞부분에 옛 연도·옛 사업명이 "
            "남지 않게 하고, 다 바꿨는지 hwp_replace(replace 없이 find 만)로 옛 연도가 0곳인지 확인해.\n"
            "3) 절 본문은 아직 쓰지 마 — 다음 요청(이어 쓰기)에서 쓴다.\n"
            "시연용 가상 사업 설정(실제 사업이 아니다 — 여기 없는 값은 지어내지 말고 비워 둔다):\n" + scenario_text(today))


def write_ask(today=None) -> str:
    """「AURA 이어 쓰기」 요청 — 빈 절 몇 개를 실제 사업계획서답게(구조가 있는 글) 써 내려가게. 표는 쓰지 않게 해 열린 세 화면에
    흘려 쓰기(칸 공동 편집 길, webhwp_agent._stream_plain)로 보이게 한다. 수치는 시나리오 값만(본문 관문이 그 밖의 숫자를 막는다)."""
    sc = scenario(today)
    return (f"이 문서는 시연용 가상 사업 「{sc['사업명']}」 의 사업계획서야. 아직 비어 있는 사업 내용 절 가운데 앞에서부터 세 개를 차례로 써 줘.\n"
            "- 쓸 절은 사업 내용 절(추진 배경·목표·추진 전략·세부 내용 같은 것)이다. 양식의 제출 서류·규격·작성 요령·제본 같은 안내 부분의 절은 "
            "건너뛴다. 세 개를 쓰면 멈춘다.\n"
            "- 먼저 표지·개요의 기본 칸(사업명·기관·부서·책임자·기간 등)이 비었거나 양식의 옛 값이면 hwp_fill 로 아래 값을 한 번에 넣는다.\n"
            "- 가상 사업이라 찾을 근거 문서가 없다 — 찾기 없이 아래 설정만 보고, 절마다 바로 hwp_write 로 하나씩 직접 쓴다(맡기지 않는다 — "
            "쓰는 대로 열린 화면에 글이 흘러 들어가게).\n"
            "- 실제 사업계획서 품질로: 절마다 그 절의 작성방법 안내를 따르고, 「1)」「2)」 소제목 두세 개와 그 아래 「가)」「나)」 항목을 두며, "
            "항목마다 무엇을·왜·어떻게 하는지 한두 문장으로 풀어 쓴다. 절 하나에 500~800자쯤, 앞 절과 내용이 이어지게.\n"
            "- 표·도식 없이 글과 항목으로만 쓴다(열린 화면에 써 내려가는 것이 보이게).\n"
            "- 숫자·날짜·기관·사람 이름은 아래 설정에 있는 것만 그대로 쓴다. 설정에 「예시」 가 붙은 수치는 본문에도 「(예시)」 를 붙인다. "
            "설정에 없는 숫자는 쓰지 말고 「확인 필요」 로 남긴다.\n"
            "시연용 가상 사업 설정(실제 사업이 아니다):\n" + scenario_text(today))


def form_loaded_id(app, fid: int) -> bool:
    from zzaimy.app import webhwp
    return bool(webhwp.meta(app.state.db, fid).get("form_loaded"))


def form_loaded(request: Request, fid: int) -> bool:
    return form_loaded_id(request.app, fid)


@router.post("/dev/coedit-demo/aura")
def coedit_demo_aura(request: Request, background: BackgroundTasks, id: int = Form(...)):
    """「AURA로 쓰기」 — 실제 27B 문서 작업 에이전트가 이 시연 문서의 빈 절 하나를 골라 쓴다. 채팅과 같은 답 길(app.state.schedule_answer)로,
    시연 계정 김가람의 대화를 이 문서에 잇고 요청을 보낸다. 화면에는 「AURA · 김가람」 으로 보인다. 어느 절을 무엇으로 쓸지는 27B 가 정한다."""
    import json as _json
    _need_login_mode(request)
    from zzaimy.app import webhwp, webhwp_agent
    st = request.app.state
    db = st.db
    if next((r for r in docs(request) if r["id"] == id), None) is None:
        raise HTTPException(404, "시연 문서를 찾을 수 없습니다")
    ensure_accounts(request.app)
    uid = SLOTS["a"][0]
    m = webhwp.meta(db, id)
    sid = int(m.get("demo_session") or 0)
    if not sid or not db.get_chat_session(sid):
        sid = db.create_chat_session("시연 · AURA 쓰기", owner=uid)
        m = webhwp.meta(db, id)
        m["demo_session"] = sid
        db.set_setting(f"webhwp_meta:{int(id)}", _json.dumps(m, ensure_ascii=False))
    webhwp_agent.attach(db, sid, id)
    if getattr(st, "chat_running", lambda _s: False)(sid):
        return RedirectResponse(f"/dev/coedit-demo?id={id}&aura=busy", status_code=303)
    ask = write_ask() if form_loaded(request, id) else form_ask()   # 빈 문서면 양식 불러오기(+ 가상 사업으로 바꾸기), 불러왔으면 이어 쓰기
    db.add_chat(sid, "user", ask)
    st.schedule_answer(background, sid, ask, None, [])
    return RedirectResponse(f"/dev/coedit-demo?id={id}&aura=1", status_code=303)
