"""공동 편집 시연(ADR-0071) — 관리자 화면 하나에 같은 기안기 문서를 세 칸으로 열고, 칸마다 다른 시연 계정으로 쓴다.

한 주소(한 쿠키)에서 세 사람을 흉내 내야 해서 로그인 세션은 관리자 그대로 두고, 칸 표시(요청 머리 X-Aura-Demo,
알림 흐름·sendBeacon 처럼 머리를 못 붙이는 요청은 주소의 demo=)를 단 요청만 그 요청 동안 시연 계정으로 판정한다.
바꾸는 조건은 셋 모두 — 실제 로그인한 사람이 관리자, 칸 표시가 시연 계정(장부에 demo 표시가 있는 계정), 주소가 기안기 편집·공동 편집 경로.
그 밖의 요청은 표시를 무시한다. 시연 계정은 비활성·비밀번호 없음이라 로그인할 수 없고, 열 수 있는 곳은 시연 폴더(ROOT) 하나다
(webhwp_routes._depts). 그래서 표시는 권한을 좁히기만 한다.

화면: GET /dev/coedit-demo — 한 문서(제목만 있는 빈 문서 → 「AURA 양식 만들기」 로 27B 가 시연 시나리오(scenario)의 가상 사업에 맞는 짧은
사업계획서 양식을 설계해 넣고 요약 칸을 채움 → 「AURA 이어 쓰기」 로 빈 본문 절을 1절부터 차례로 세 개 — DemoRun). 칸 밖 문단도 칸으로 감싸 제목까지 함께 고친다
(webhwp_fields.wrap_rest). 시연 문서가 없으면 열 때 만든다. 「새 빈 문서」 POST /dev/coedit-demo/reset, 「AURA 양식 만들기 / 이어 쓰기」 POST /dev/coedit-demo/aura(단계·결과는 GET /dev/coedit-demo/state),
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
    with webhwp.preloaded_meta(db):                      # 한글 파일 수천 개의 meta 를 하나씩 읽던 것(시연 화면 15초) — 읽기만 하는 훑기라 한 번에
        for r in db.list_files(kind="webhwp", limit=5000):
            src = str(webhwp.meta(db, r["id"]).get("source", ""))
            if src.startswith(TEMPLATE) and webhwp.locked(db, r["id"]) and Path(r["path"]).exists():
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
    이 값은 재료일 뿐 칸 이름에 묶지 않는다 — 어느 칸·어느 글에 넣을지는 27B 가 양식을 보고 정한다(form_request·DemoRun).
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
        # 일정·예산 절은 날짜·금액 없이는 쓸 수 없다 — 설정에 없으면 관문이 막아 그 절을 못 썼다(10-27 운영: 「사업 추진 일정」 의 11·12·30)
        "추진 일정(예시)": f"{y}. 3. 착수·교과 개설, {y}. 6. 1학기 교과 운영·중간 점검, {y}. 9. ~ {y}. 11. 기업 협력 프로젝트, "
                        f"{y}. 12. 성과 발표회, {y + 1}. 2. 결과 보고",
        "예산 구성(예시)": "인건비 1억 5천만 원, 교과·실습 운영비 2억 원, 기업 협력 프로젝트비 1억 원, 교원 연수비 5천만 원",
    }


def scenario_text(today=None) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in scenario(today).items())


def demo_values(fields: list[dict]) -> dict[str, str]:
    """만들기 길(make_doc — 화면 단추 없는 장문 시연 문서)의 머리 칸 값 — 칸 이름 낱말로만 고른다. 본문(절)은 시연 때 쓰도록 비워 둔다.
    화면 시연(빈 문서 → 양식 만들기)은 이 규칙을 쓰지 않고 27B 가 시나리오(scenario)를 보고 칸을 고른다(DemoRun.fill_summary)."""
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


BLANK = "blank2"           # 2 = 제목만 있는 빈 문서(AURA 가 양식을 만들어 넣는 시연, 10-26·10-27). 옛 「blank」(사람별 절) 문서는 열 때 휴지통으로 — 「세 사람 동시 작성」 — 양식 사본이 아닌 빈 문서(시연 계정 수만큼 번호 붙은 절, 안내 글·미리 넣은 값 없음)


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
    """시연 머리 단추의 단계 — 양식을 넣었는지, AURA 가 아직 일하는지와 지금 단계(「2. 추진 전략 쓰는 중 (2/3)」), 마친 뒤 결과 한 줄."""
    _need_login_mode(request)
    r = run_state(request.app.state.db, id)
    out = {"loaded": form_loaded(request, id), "busy": bool(r.get("busy"))}
    if r.get("step"):
        out["step"] = str(r["step"])[:120]
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


def form_request(today=None) -> str:
    """「AURA 양식 만들기」 요청 — 27B 가 이 가상 사업의 짧은 사업계획서 양식을 설계한다(설계 길은 webhwp_form_create 와 같다).
    시연 중 기다림을 줄이려고 짧게(1단계 절 몇 개, 표·도식 자리 없이) 청한다 — 절 이름·차례·작성방법은 27B 가 정한다."""
    sc = scenario(today)
    return (f"시연용 가상 사업 「{sc['사업명']}」 의 사업계획서 양식을 만들어 줘. 두세 쪽 분량으로 짧게 — 1단계 절 5~6개(아래 절 없이), "
            "절마다 무엇을 쓸지 작성방법 한두 문장, 표지 없이 문서 머리에 요약 표(사업명·기간 같은 짧은 칸 4~6개). 절 안 표·도식 자리는 두지 않는다.\n"
            "가상 사업 설정:\n" + scenario_text(today))


def write_request(today=None) -> str:
    """「AURA 이어 쓰기」 요청(대화 기록) — 빈 본문 절을 1절부터 차례로 세 개."""
    return f"「{scenario(today)['사업명']}」 사업계획서의 빈 절을 1절부터 차례로 세 개 써 줘."


SECTION_SYSTEM = """당신은 대학 사업계획서의 한 절을 쓰는 AURA 다. 주어진 가상 사업 설정과 그 절의 작성방법을 보고 실제 사업계획서 품질의 본문만 쓴다.
- 개조식 번호 문단: 「1)」 소제목 두세 개, 그 아래 「가)」「나)」 항목. 항목마다 무엇을·왜·어떻게 하는지 한 문장. 문장 끝은 「~함」「~임」.
- 가상 사업의 실제 계획 내용(목표·전략·추진 체계·일정·기대 효과 등 그 절이 묻는 것)을 쓴다. 작성방법 안내를 풀어 옮기지 않는다.
- 400~700자. 표·도식·마크다운 기호(#, **, |) 없이 글로만.
- 숫자·날짜·기관·사람 이름은 설정에 있는 것만 그대로. 설정에 「예시」 가 붙은 수치는 「(예시)」 를 붙인다. 설정에 없는 숫자는 쓰지 않는다.
- 절 제목은 다시 쓰지 않는다. 본문만 답한다."""

FILL_SYSTEM = """문서 머리 요약 표의 칸에 넣을 값을 가상 사업 설정에서 고른다. JSON 하나만 답한다: {"칸 이름 그대로": "값", ...}.
설정에 맞는 값이 없는 칸은 넣지 않는다. 값은 짧게(설정 글 그대로나 그 일부)."""

RUN_KEY = "coedit_demo_run:{fid}"
RUN_STALE = 900.0          # 이보다 오래 소식이 없으면 끊긴 실행으로 본다(배포 재시작 등)
make_client = None         # 시험에서 가짜 연결로 바꾼다 — 기본은 서빙 27B(VllmClient)


def run_state(db, fid: int) -> dict:
    import json as _json
    import time as _time
    try:
        v = _json.loads(db.get_setting(RUN_KEY.format(fid=int(fid)), "{}") or "{}")
    except ValueError:
        v = {}
    if v.get("busy") and _time.time() - float(v.get("at") or 0) > RUN_STALE:
        v["busy"] = False
    return v


class DemoRun:
    """시연 실행 — 27B 가 양식을 설계하고(webhwp_form_create 설계 길), 빈 본문 절을 1절부터 차례로 쓴다. 쓰기는 문서 작업 도구(HwpTools)의
    같은 관문(자리·수치·예시 글자)과 흘려 쓰기 길을 지난다. 단계·걸린 초는 RUN_KEY 에 남겨 시연 머리줄과 보고가 읽는다.
    판단(양식 짜임·절 이름·작성방법·칸 값·본문)은 27B, 차례(만들기 → 1·2·3절)와 기록은 코드."""

    def __init__(self, app, fid: int, sid: int):
        from zzaimy.app import webhwp_agent
        self.app, self.db, self.fid, self.sid = app, app.state.db, int(fid), int(sid)
        self.base = Path(app.state.data_dir)
        self.client = (make_client or _default_client)()
        hw = webhwp_agent.HwpTools(self.db, self.base, self.sid, SLOTS["a"][0], ROOT, None, lambda: [])
        hw.client = self.client
        hw.evidence = lambda: [scenario_text(), *[str(m.get("content") or "") for m in self.db.list_chats(self.sid, limit=200) if m.get("role") == "user"]]
        webhwp_agent.attach(self.db, self.sid, self.fid)
        self.hw = hw
        self.log: list[dict] = []
        self.t0 = self.t_step = 0.0

    def step(self, label: str, busy: bool = True) -> None:
        import json as _json
        import time as _time
        now = _time.time()
        if self.log:
            self.log[-1]["sec"] = round(now - self.t_step, 1)
        self.t_step = now
        if busy:
            self.log.append({"step": label})
        self.db.set_setting(RUN_KEY.format(fid=self.fid), _json.dumps({"busy": busy, "step": label, "at": now, "log": self.log,
                                                                       "total": round(now - self.t0, 1)}, ensure_ascii=False))

    def ask(self, system: str, user: str, out_tokens: int = 1400, temperature: float = 0.4) -> str:
        from zzaimy.app import llm_budget
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        resp = llm_budget.create_fitted(self.client, msgs, out_tokens, [llm_budget.Cut(msg=1, text=user), llm_budget.OUTPUT], floor=400,
                                        temperature=temperature, extra_body=getattr(self.client, "_extra", {}))
        return re.sub(r"<think>.*?</think>", "", resp.choices[0].message.content or "", flags=re.S).strip()

    # ── 양식 만들기 ──
    def make_form(self) -> str:
        import json as _json
        import time as _time
        from zzaimy.app import webhwp, webhwp_form_create as fc
        from zzaimy.app import webhwp_write as ww
        sc = scenario()
        req = form_request()
        self.db.add_chat(self.sid, "user", req)
        self.step("양식 설계 중 — 절 차례·작성방법")
        creator = fc.FormCreator(self.client, self.db, self.sid, self.hw, req, refs=False, review=False)   # 시연은 빠르게 — 참고 양식·자기 검토 없이 한 번
        clean, probs, _tries = creator.design({"purpose": f"시연용 가상 사업 「{sc['사업명']}」 의 짧은 사업계획서", "kind": "사업계획서",
                                               "title": sc["사업명"], "cover": False})
        if clean is None:
            return "양식을 만들지 못했습니다 — 설계가 관문을 지나지 못했습니다: " + "; ".join(probs[:3])
        heads = [x["heading"] for x in clean["sections"]]
        self.step(f"양식 넣는 중 — 절 {len(heads)}개")
        data = _bigger_text(fc.build(clean), BIG)
        fields = ww.fields(data)
        self.hw._load_into(self.fid, data, fields, clean["title"], "양식생성")
        m = webhwp.meta(self.db, self.fid)
        m["form_design"] = {**clean, "shape": fc.shape(clean), "at": _time.time()}
        self.db.set_setting(f"webhwp_meta:{self.fid}", _json.dumps(m, ensure_ascii=False))
        filled = self.fill_summary(fields)
        return (f"양식 「{clean['title']}」 을 만들어 이 문서에 넣었습니다 — 절 {len(heads)}개: " + " / ".join(heads)
                + (f". 요약 칸 {len(filled)}개를 가상 사업 값으로 채웠습니다({', '.join(f[2:] for f in filled)})." if filled else ". 요약 칸은 비워 두었습니다."))

    def fill_summary(self, fields: list[dict]) -> list[str]:
        """요약 표 칸 — 어느 칸에 무엇을 넣을지는 27B 가 설정에서 고르고, 넣기는 칸 공동 편집 길(hwp_fill)로."""
        from zzaimy.app import webhwp_form_create as fc
        cells = [x["name"] for x in fields if x["kind"] == "칸" and not x["text"]]
        if not cells:
            return []
        self.step("요약 칸 채우는 중")
        # 27B 에는 보이는 이름(「사업명」)으로 묻고, 답의 이름은 앞머리(「칸:」)가 있든 없든·띄어쓰기가 달라도 그 칸으로 맞춘다 —
        # 칸 이름 그대로만 받으니 27B 가 「사업명」 으로 답한 값이 모두 버려져 요약 표가 빈 채로 남았다(10-27 운영)
        shown = {c.split(":", 1)[-1].strip(): c for c in cells}
        key = lambda t: re.sub(r"\s+", "", str(t or "")).split(":", 1)[-1]
        by_key = {key(c): c for c in cells}
        raw = self.ask(FILL_SYSTEM, "칸: " + ", ".join(shown) + "\n가상 사업 설정:\n" + scenario_text(), out_tokens=600, temperature=0.1)
        got = fc.parse_design(raw) or {}
        vals = {by_key[key(k)]: str(v) for k, v in got.items() if key(k) in by_key and str(v or "").strip()}
        if not vals:
            return []
        out = self.hw.fill({"values": vals, "from_user": True})
        return [k for k in vals if k in out]

    # ── 절 쓰기 ──
    def targets(self, n: int) -> list[str]:
        """빈 본문 절 — 문서 차례대로, 안내 상자·짧은 값 칸은 빼고."""
        rooms = self.hw.rooms()
        out = []
        for x in self.hw._fields():
            r = rooms.get(x["name"]) or {}
            if x["kind"] == "절" and not x["text"] and not r.get("guide") and r.get("budget") is None:
                out.append(x["name"])
        return out[:n]

    def draft(self, field: str, done: list[tuple[str, str]], why: str = "") -> str:
        from zzaimy.app import webhwp_agent as wa
        from zzaimy.app import webhwp_write as ww
        data = Path(webhwp_get(self.db, self.fid)["path"]).read_bytes()
        guide = wa.guide_of(ww.section_context(data, field)) or "(작성방법 없음 — 절 제목에 맞게)"
        order = " / ".join(x["name"][2:] for x in self.hw._fields() if x["kind"] == "절")
        prev = "\n".join("- " + f[2:] + ": " + " ".join(t.split())[:160] for f, t in done[-3:]) or "(없음)"
        user = (f"[가상 사업 설정]\n{scenario_text()}\n\n[문서 차례] {order}\n\n[앞 절 요지]\n{prev}\n\n"
                f"[쓸 절] {field[2:]}\n[작성방법] {guide[:600]}" + (f"\n\n[앞서 쓴 글이 거절된 까닭 — 고쳐 쓴다] {why}" if why else ""))
        return self.ask(SECTION_SYSTEM, user)

    def write_sections(self, n: int = 3) -> str:
        from concurrent.futures import ThreadPoolExecutor
        self.db.add_chat(self.sid, "user", write_request())
        todo = self.targets(n)
        if not todo:
            return "쓸 빈 본문 절이 없습니다."
        done: list[tuple[str, str]] = []
        failed: list[str] = []
        with ThreadPoolExecutor(max_workers=1) as pool:          # 한 절을 흘려 쓰는 동안 다음 절 초안을 미리 만든다(기다림 줄이기)
            self.step(f"{todo[0][2:]} — 초안 만드는 중")
            fut = pool.submit(self.draft, todo[0], [])
            for i, field in enumerate(todo):
                text = fut.result()
                self.step(f"{field[2:]} 쓰는 중 ({i + 1}/{len(todo)})")
                if i + 1 < len(todo):
                    fut = pool.submit(self.draft, todo[i + 1], done + [(field, text)])
                res = self.hw.write({"field": field, "text": text})
                if res.startswith("쓰지 않았다"):
                    self.step(f"{field[2:]} 고쳐 쓰는 중 ({i + 1}/{len(todo)})")
                    text = self.draft(field, done, why=res[:400])
                    res = self.hw.write({"field": field, "text": text})
                if res.startswith("쓰지 않았다"):
                    failed.append(f"{field[2:]}({res[7:80]})")
                else:
                    done.append((field, text))
        msg = (f"{len(done)}개 절을 썼습니다: " + ", ".join(f[2:] for f, _ in done) if done else "절을 쓰지 못했습니다")
        return msg + (". 못 쓴 절: " + "; ".join(failed) if failed else "") + "."

    def run(self, what: str) -> None:
        import time as _time
        self.t0 = self.t_step = _time.time()
        try:
            msg = self.make_form() if what == "form" else self.write_sections()
        except Exception as e:                                   # 연결 끊김 등 — 화면에 까닭을 남긴다(오류 글은 옮기지 않는다)
            msg = f"AURA 가 멈췄습니다({type(e).__name__}) — 다시 눌러 주세요."
        self.db.add_chat(self.sid, "assistant", msg)
        self.step(msg[:120], busy=False)


def webhwp_get(db, fid: int):
    from zzaimy.app import webhwp
    return webhwp.get(db, fid)


def _default_client():
    from zzaimy.generate.client import VllmClient
    return VllmClient()


def form_loaded_id(app, fid: int) -> bool:
    from zzaimy.app import webhwp
    return bool(webhwp.meta(app.state.db, fid).get("form_loaded"))


def form_loaded(request: Request, fid: int) -> bool:
    return form_loaded_id(request.app, fid)


@router.post("/dev/coedit-demo/aura")
def coedit_demo_aura(request: Request, background: BackgroundTasks, id: int = Form(...)):
    """「AURA 양식 만들기 / 이어 쓰기」 — 빈 문서면 27B 가 가상 사업의 짧은 사업계획서 양식을 설계해 넣고 요약 칸을 채운다,
    넣은 뒤면 빈 본문 절을 1절부터 차례로 세 개 쓴다(DemoRun). 기록은 시연 계정 김가람의 대화(문서에 이어 둔다)에 남는다."""
    import json as _json
    import time as _time
    _need_login_mode(request)
    from zzaimy.app import webhwp
    st = request.app.state
    db = st.db
    if next((r for r in docs(request) if r["id"] == id), None) is None:
        raise HTTPException(404, "시연 문서를 찾을 수 없습니다")
    ensure_accounts(request.app)
    if run_state(db, id).get("busy"):
        return RedirectResponse(f"/dev/coedit-demo?id={id}&aura=busy", status_code=303)
    uid = SLOTS["a"][0]
    m = webhwp.meta(db, id)
    sid = int(m.get("demo_session") or 0)
    if not sid or not db.get_chat_session(sid):
        sid = db.create_chat_session("시연 · AURA 쓰기", owner=uid)
        m = webhwp.meta(db, id)
        m["demo_session"] = sid
        db.set_setting(f"webhwp_meta:{int(id)}", _json.dumps(m, ensure_ascii=False))
    what = "write" if form_loaded(request, id) else "form"
    db.set_setting(RUN_KEY.format(fid=int(id)), _json.dumps({"busy": True, "step": "AURA 시작", "at": _time.time(), "log": []}, ensure_ascii=False))
    background.add_task(lambda: DemoRun(request.app, id, sid).run(what))
    return RedirectResponse(f"/dev/coedit-demo?id={id}&aura=1", status_code=303)
