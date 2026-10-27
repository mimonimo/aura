"""문답 검수(/review) — 검수자와 관리자가 쓰는 화면 하나(10-27).

- 누가: 관리자와 「SFT 문답 검수」 권한(grades.PERMS sft_review)을 받은 교직원. 권한은 관리자가 조직 관리에서 준다(ADR-0066 10-27 보충).
  검수자는 /dev 를 열지 못한다(main.check_auth 가 /dev 를 관리자만으로 막는 규칙 그대로).
- 무엇을: 라벨 스튜디오 검수 프로젝트 열기, 내 몫(할 일 data.assignee == 내 아이디)과 진행, 관리자는 검수자별 진행 표,
  그리고 SFT 학습·검수 매뉴얼(docs/guides/sft-review-guide.md — 원본은 이 파일 하나, /dev/train/review-guide 는 여기로 보낸다).
- 진행 수치는 라벨 스튜디오에서 읽는다(검수자마다 두 번 묻기 — 맡은 수·제출한 수). 45초 캐시라 화면을 열 때마다 두드리지 않는다.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from zzaimy.app import grades

router = APIRouter()

PROJECT_SETTING = "sft_review_project"           # 검수 프로젝트 제목(설정) — 없으면 기본 제목
DEFAULT_PROJECT = "ZZAIMY SFT 5000 검수"
PROGRESS_TTL = 45.0

# ---- 매뉴얼 — 문서 보기와 같은 마크다운 그리기, 그림은 그 폴더에서만 ----
_DOCS = Path(__file__).resolve().parents[3] / "docs"
GUIDE_PATH = _DOCS / "guides" / "sft-review-guide.md"
GUIDE_IMG_DIR = _DOCS / "images" / "guides" / "sft-review"
IMG_ROUTE = "/review/guide/img/"
_IMG_NAME = re.compile(r"[A-Za-z0-9가-힣_][A-Za-z0-9가-힣_.-]*\.(?:png|jpe?g|webp|gif)", re.I)
_IMG_MD = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def _need_reviewer(request: Request) -> None:
    if not grades.request_can_review(request):
        raise HTTPException(403, "문답 검수 권한이 있는 계정만 여는 화면입니다")


def _guide_img(src: str) -> str:
    """매뉴얼의 그림 경로 → 앱 주소. 그림 폴더 바로 안의 그림 파일만, 아니면 빈 글(그리지 않는다)."""
    from html import unescape
    name = Path(unescape(src)).name
    if not _IMG_NAME.fullmatch(name):
        return ""
    try:
        target = (GUIDE_PATH.parent / unescape(src)).resolve()
    except (OSError, ValueError):
        return ""
    if target.parent != GUIDE_IMG_DIR.resolve() or not target.is_file():
        return ""
    return IMG_ROUTE + name


def render_guide(st) -> dict | None:
    """매뉴얼을 화면용으로 — {title, html, toc}. 글은 문서 보기(dev_doc_view)가 이스케이프하고, 그림만 허용한 폴더에서 되살린다."""
    from markupsafe import Markup

    if not GUIDE_PATH.is_file():
        return None
    doc = st.docs.dev_doc_view(GUIDE_PATH.read_text(encoding="utf-8"), GUIDE_PATH.name)

    def img(m):
        url = _guide_img(m.group(2))
        alt = re.sub(r"<[^>]+>", "", m.group(1)).replace('"', "&quot;")    # 이미 이스케이프된 글 — 되살린 굵게·기울임만 걷는다
        return f'<img class="guide-img" src="{url}" alt="{alt}" loading="lazy">' if url else m.group(0)
    doc["html"] = Markup(_IMG_MD.sub(img, str(doc["html"])))
    return doc


# ---- 진행 — 라벨 스튜디오의 data.assignee 로 센다 ----

def _assignee(uid: str) -> dict:
    return {"filter": "filter:tasks:data.assignee", "operator": "equal", "type": "String", "value": uid}


_ANNOTATED = {"filter": "filter:tasks:total_annotations", "operator": "greater", "type": "Number", "value": 0}


def reviewer_progress(client, project_id: int, uids: list[str], timeout: float = 4.0) -> dict:
    """검수자마다 맡은 수·제출한 수 — {ok, error, rows: [{uid, assigned, done, pct}]}. 닿지 않으면 ok=False(행 없음)."""
    from zzaimy.dataset.ls_client import LabelStudioError

    rows = []
    try:
        for uid in uids:
            assigned = client.count_tasks(project_id, [_assignee(uid)], timeout=timeout)
            done = client.count_tasks(project_id, [_assignee(uid), _ANNOTATED], timeout=timeout) if assigned else 0
            rows.append({"uid": uid, "assigned": assigned, "done": done,
                         "pct": round(100 * done / assigned) if assigned else 0})
    except LabelStudioError as e:
        return {"ok": False, "error": {"unreachable": "라벨 스튜디오 응답 없음", "auth": "라벨 스튜디오 토큰 불일치",
                                       "config": "라벨 스튜디오 설정 없음"}.get(e.kind, "라벨 스튜디오 응답 오류"), "rows": []}
    return {"ok": True, "error": "", "rows": rows}


def _cached_progress(st, project_id: int, uids: list[str]) -> dict:
    cache = getattr(st, "review_progress_cache", None)
    if cache is None:
        cache = st.review_progress_cache = {}
    key = (int(project_id), tuple(uids))
    hit = cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < PROGRESS_TTL:
        return hit[1]
    try:
        client = st.ls_client()
    except Exception:
        return {"ok": False, "error": "라벨 스튜디오 설정 없음", "rows": []}
    value = reviewer_progress(client, project_id, uids)
    if value["ok"]:
        cache[key] = (now, value)
    return value


def reviewers(accounts: dict) -> list[str]:
    """검수 권한을 받은 사용 중 계정(관리자 빼고) — 관리자 진행 표의 줄."""
    return sorted(u for u, a in accounts.items()
                  if a.get("active", True) and a.get("role") != "dev" and grades.can_sft_review(a))


def _project(st) -> dict:
    """검수 프로젝트 — 제목(설정), id·완료/전체(라벨 스튜디오 목록 캐시), 여는 주소."""
    url = (st.db.get_setting("labelstudio_url") or "").rstrip("/")
    title = (st.db.get_setting(PROJECT_SETTING, "") or "").strip() or DEFAULT_PROJECT
    ls = st.ls_projects() if url else {"ok": False, "projects": []}
    p = next((x for x in ls.get("projects") or [] if x.get("title") == title), None)
    href = f"{url}/projects/{p['id']}/data" if (url and p) else (f"{url}/projects" if url else "")
    return {"title": title, "id": int(p["id"]) if p else None, "total": int((p or {}).get("total") or 0),
            "done": int((p or {}).get("done") or 0), "href": href, "url": url, "ok": bool(ls.get("ok"))}


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request):
    _need_reviewer(request)
    st = request.app.state
    me = getattr(request.state, "user", "") or ""
    is_admin = getattr(request.state, "role", "") == "dev"
    proj = _project(st)
    mine, table = None, None
    if proj["id"] is not None:
        uids = reviewers(getattr(st, "accounts", None) or {}) if is_admin else [me]
        prog = _cached_progress(st, proj["id"], uids) if uids else {"ok": True, "error": "", "rows": []}
        if is_admin:
            table = prog
        else:
            mine = {"ok": prog["ok"], "error": prog["error"],
                    **(prog["rows"][0] if prog["rows"] else {"uid": me, "assigned": 0, "done": 0, "pct": 0})}
    return st.templates.TemplateResponse(request, "review.html", st.page_ctx(request, {
        "doc": render_guide(st), "proj": proj, "mine": mine, "table": table, "is_admin": is_admin, "me": me,
    }))


@router.get("/review/guide/img/{name}")
def review_guide_img(request: Request, name: str):
    _need_reviewer(request)
    if not _IMG_NAME.fullmatch(name):
        raise HTTPException(404)
    p = GUIDE_IMG_DIR / name
    if not p.is_file() or p.resolve().parent != GUIDE_IMG_DIR.resolve():
        raise HTTPException(404)
    return FileResponse(p)


@router.get("/dev/train/review-guide")
def dev_review_guide_moved():
    """예전 주소 — 매뉴얼은 문답 검수 화면 한 곳에 있다."""
    return RedirectResponse("/review#manual", status_code=303)
