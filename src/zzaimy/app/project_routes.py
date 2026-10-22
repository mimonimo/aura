"""프로젝트(/project*·/projects*) — 프로젝트 만들기(이름만·자료 묶음으로), 프로젝트 화면(/project/{id}),
자료 더 넣기(/project/{id}/bundle — 끌어 놓기·파일 고르기), 문서함·드라이브에서 가져오기(/project/{id}/documents/import),
기준 문서 고르기·한 건 잇기·빼기, 관련 보관 사업 잇기·풀기, 프로젝트 검색(/projects/archived),
보관·불러오기, 작업 폴더 부서 공유, 작성 원칙, 아이콘 색·사업 담당자, 이름·기한·사업단·사업 바꾸기, 지우기.

프로젝트 화면은 탭 하나(「대화·문서」)에 대화·작업 문서(왼쪽)와 프로젝트 문서·작성 원칙·관련 지난 사업(오른쪽)을 함께 둔다.
옛 「작성 원칙·기준 문서」 탭 주소(?tab=context)는 같은 화면의 작성 원칙 자리(#projectContext)로 이어진다.

- 프로젝트 찾기 JSON(/api/projects/browse)은 main, 프로젝트 검색 API(/api/projects/search)는 app/project_search 에 그대로 있다.
- DB·화면 틀·화면 공통 맥락(ctx)과 묶음 반입(_intake_bundle — 배경 판독·뒤 단계 줄)·지난 사업 자동 참조(_link_past_projects)·
  문서 세 갈래(_doc_groups)·부서 공유 맞추기(_share_sync) 같은 대화·문서 화면과 함께 쓰는 도우미는 main.create_app 이 앱마다 만들어
  프로젝트 묶음(app.state.project — make_project_helpers)에 넘긴다. 대화 묶음(app.state.chat)이 받는 것과 같은 객체다.
- 프로젝트 화면에서만 쓰는 도우미(보관 사업 후보 이름·지침·기준 탭의 관련 보관 사업)는 make_project_helpers 안에 있다 — 앱마다 하나.
"""
from __future__ import annotations

import io
import json as _aj
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from zzaimy.app import storage

router = APIRouter()


def make_project_helpers(
    *,
    db, templates, ctx, _req_viewer, _visible, _dept_peers,
    _ref_scope, _doc_groups, _business_units, _program_options, _project_suggestions, _share_sync,
    _can_read_chat, _intake_bundle, _link_past_projects, _title_from_bundle,
) -> SimpleNamespace:
    """앱마다 하나 — main.create_app 이 넘긴 값과 프로젝트 화면 전용 도우미를 한 묶음(app.state.project)으로."""
    deps = dict(locals())

    def _ref_names(doc_groups: dict) -> list[str]:
        """보관 사업 후보를 찾을 이름 — 프로젝트 기준 문서·첨부 문서의 제목과, 그 문서들에서 읽은 사업명(정체)."""
        names: list[str] = []
        docs_ = doc_groups.get("rules", []) + doc_groups.get("attached", [])
        for d in docs_:                                   # 문서에서 읽은 사업명이 가장 곧은 단서 — 먼저
            program = (db.get_doc_identity(int(d["id"])) or {}).get("program") or ""
            if program.strip():
                names.append(program)
        for d in docs_:                                   # 기준·첨부 문서 제목(「… 부트캠프 사업계획서 제출 안내」)
            names.append(storage.title_of(d.get("filename") or ""))
        return list(dict.fromkeys(n for n in names if n.strip()))[:40]

    def _project_ref_ctx(request: Request, proj: dict, refq: str, names: list[str] | None = None) -> dict:
        """지침·기준 탭의 관련 보관 사업 — 연결된 것과 후보(검색어, 또는 이름·기준 제목·사업명 겹침). 열람 범위의 문서만 센다."""
        from zzaimy.app import project_refs
        try:
            scope = _ref_scope(request)
            return {"project_refs": project_refs.list_refs(db, int(proj["id"]), scope),
                    "ref_candidates": [] if proj.get("archived") else project_refs.candidates(db, proj, refq, scope, names=names),
                    "refq": refq, "business_units": _business_units()}
        except Exception:
            logging.getLogger("zzaimy.app.web").exception("관련 보관 사업 조회 실패")
            return {"project_refs": [], "ref_candidates": [], "refq": refq}

    # 프로젝트 아이콘 색 — 프로젝트 목록·사이드바가 같은 색으로 그리게 템플릿 어디서나 project_color(프로젝트 id) 로 부른다
    from zzaimy.app import project_access as _pa
    try:
        templates.env.globals["project_color"] = lambda pid: _pa.color(db, int(pid)) if pid else _pa.PALETTE[_pa.DEFAULT_COLOR][0]
        # 새 프로젝트 만들기 단추 — 사업 담당자 이상에게만(grades.can_create_project). 화면은 can_create_project(request) 로 묻는다
        templates.env.globals["can_create_project"] = _may_create
    except AttributeError:                                # 테스트용 가짜 템플릿
        pass
    return SimpleNamespace(**deps, _ref_names=_ref_names, _project_ref_ctx=_project_ref_ctx)


IMPORT_LIMIT = 20                                         # 문서함·드라이브에서 한 번에 가져오는 문서 수
_DRIVE_ID = re.compile(r"[A-Za-z0-9_-]{10,128}")
_DRIVE_MAX = 100 * 1024 * 1024


def _account(request: Request) -> dict:
    """이 요청의 계정 — 계정 장부 값에 지금 역할을 채운다(인증 없는 로컬 개발 모드는 장부가 비어 관리자)."""
    user = getattr(request.state, "user", "") or ""
    acc = dict((getattr(request.app.state, "accounts", None) or {}).get(user) or {})
    acc.setdefault("role", getattr(request.state, "role", "") or "")
    return acc


def _may_create(request: Request) -> bool:
    from zzaimy.app import grades
    return grades.can_create_project(_account(request))


def _require_create(request: Request) -> None:
    if not _may_create(request):
        raise HTTPException(403, "사업 담당자 이상만 프로젝트를 만들 수 있습니다")


def _wants_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("accept") or "")


def _require_write(request: Request, proj: dict) -> None:
    """대화 시작·문서 추가·원칙 수정 같은 쓰기 — 소유자·관리자·함께 작업만. 참조(읽기 전용)는 403."""
    from zzaimy.app import project_access as pa
    if proj.get("archived"):
        raise HTTPException(403, "지난 사업은 보관 해제 뒤에 고칩니다")
    if not pa.can_write(request.app.state.project_access(request, proj)):
        raise HTTPException(403, "참조로 공유된 프로젝트는 읽기만 할 수 있습니다")


def _require_owner(request: Request, proj: dict) -> None:
    """프로젝트 설정(색·담당자·공유) — 소유자와 관리자만."""
    if getattr(request.state, "role", "") != "dev" and proj.get("owner") != getattr(request.state, "user", ""):
        raise HTTPException(403, "프로젝트 설정은 소유자만 바꿉니다")


def _share_of(request: Request, proj: dict) -> dict:
    from zzaimy.app import project_access as pa
    accounts = request.app.state.accounts
    owner_dept = ((accounts.get(proj.get("owner") or "", {}) or {}).get("dept") or "").strip()
    return pa.shares(request.app.state.db, proj, owner_dept)


def _drive_fetch(email: str, drive_id: str, http=None) -> tuple[bytes, str]:
    """드라이브 파일 하나를 내려받는다 — 구글 문서·시트·슬라이드는 오피스 형식으로 내보낸다(gdrive_import._download 그대로)."""
    from zzaimy.app import gdrive_import, webhwp_drive
    from zzaimy.ingest import gdrive, gdrive_files
    http = http or webhwp_drive._http()
    m = http.get(f"{gdrive.API}/files/{drive_id}", headers=gdrive_files._headers(email, http),
                 params={"fields": "id,name,mimeType,size", "supportsAllDrives": "true"})
    if m.status_code != 200:
        raise RuntimeError(f"드라이브 파일을 찾지 못했습니다({m.status_code})")
    meta = m.json()
    if not webhwp_drive.accepts(meta, "docs"):
        raise ValueError("프로젝트 문서로 들일 수 없는 형식입니다")
    if int(meta.get("size") or 0) > _DRIVE_MAX:
        raise ValueError("100MB 보다 큰 파일은 가져올 수 없습니다")
    return gdrive_import._download(email, meta, http)


@router.post("/project/{project_id}/criteria/unlink")
def project_criteria_unlink(request: Request, project_id: int, criteria_doc_id: int = Form(...)):
    h = request.app.state.project
    db = h.db
    proj = db.get_project(project_id)
    if proj is None:
        raise HTTPException(404)
    _require_write(request, proj)                       # 참조(읽기 전용)는 기준 문서를 빼지 못한다
    db.remove_project_criterion(project_id, criteria_doc_id)
    return RedirectResponse(f"/project/{project_id}#projectDocuments", status_code=303)


@router.post("/project/{project_id}/criteria/link")
def project_criteria_link(request: Request, project_id: int, criteria_doc_id: int = Form(...)):
    """문서 한 건을 기준 문서로 — 프로젝트 문서 줄의 「기준으로 쓰기」. 볼 수 있는 문서만."""
    h = request.app.state.project
    db = h.db
    proj = db.get_project(project_id)
    if proj is None:
        raise HTTPException(404)
    _require_write(request, proj)
    doc = db.get_document(criteria_doc_id)
    if doc is None or not h._visible(doc, **h._req_viewer(request)):
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    db.add_project_criteria(project_id, [criteria_doc_id])
    return RedirectResponse(f"/project/{project_id}#projectDocuments", status_code=303)


@router.post("/project/{project_id}/color")
def project_set_color(request: Request, project_id: int, color: str = Form(...)):
    """머리 아이콘 색 — 팔레트 열쇠 하나. 목록·사이드바도 같은 색(project_color)."""
    from zzaimy.app import project_access as pa
    db = request.app.state.db
    proj = db.get_project(project_id)
    if proj is None:
        raise HTTPException(404)
    _require_owner(request, proj)
    try:
        key = pa.set_color(db, project_id, color.strip())
    except ValueError as e:
        raise HTTPException(400, str(e))
    if _wants_json(request):
        return {"color": key, "hex": pa.PALETTE[key][0], "bg": pa.PALETTE[key][1]}
    return RedirectResponse(f"/project/{project_id}", status_code=303)


@router.post("/project/{project_id}/lead")
def project_set_lead(request: Request, project_id: int, user: str = Form(...)):
    """사업 담당자(메인 담당자) — 만든 사람과 「함께 작업」으로 받는 사람 가운데서."""
    from zzaimy.app import project_access as pa
    db = request.app.state.db
    proj = db.get_project(project_id)
    if proj is None:
        raise HTTPException(404)
    _require_owner(request, proj)
    try:
        pa.set_lead(db, proj, user.strip(), pa.lead_choices(proj, _share_of(request, proj), request.app.state.accounts or {}))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return RedirectResponse(f"/project/{project_id}", status_code=303)


@router.post("/projects")
def create_project(
    request: Request,
    sector: str = Form(...), name: str = Form(...), due_date: str = Form(""), unit: str = Form(""),
):
    h = request.app.state.project
    db, _business_units, _link_past_projects = h.db, h._business_units, h._link_past_projects
    _require_create(request)
    from zzaimy.app.main import INBOX_TYPES
    if sector not in INBOX_TYPES:
        raise HTTPException(400, f"알 수 없는 업무 영역입니다: {sector}")
    if not name.strip():
        raise HTTPException(400, "프로젝트 이름을 입력하세요")
    pid = db.create_project(
        sector, name.strip(), due_date=due_date.strip(),
        owner=getattr(request.state, "user", "zzaimy"), unit=unit.strip() if unit.strip() in _business_units() else "",
    )
    _link_past_projects(request, db.get_project(pid) or {"id": pid, "name": name.strip()})
    return RedirectResponse(f"/project/{pid}", status_code=303)


@router.post("/projects/bundle")
def create_project_bundle(request: Request, background: BackgroundTasks, sector: str = Form("grant"),
                          name: str = Form(""), due_date: str = Form(""), unit: str = Form(""),
                          file: list[UploadFile] = File([])):
    """프로젝트 만들기 한 곳 — 자료(공고·기본계획·양식·계획서 …)를 고르면 그 묶음으로 만들고(이름은 비우면 묶음에서 뽑는다),
    고르지 않으면 이름만으로 만든다(「만들기」·「묶음으로 만들기」를 한 버튼으로)."""
    h = request.app.state.project
    db, _business_units, _intake_bundle, _link_past_projects = h.db, h._business_units, h._intake_bundle, h._link_past_projects
    _title_from_bundle = h._title_from_bundle
    _require_create(request)
    from zzaimy.app.main import INBOX_TYPES
    if sector not in INBOX_TYPES:
        raise HTTPException(400, f"알 수 없는 업무 영역입니다: {sector}")
    files = [f for f in file if f and f.filename]
    if not files and not name.strip():
        raise HTTPException(400, "프로젝트 이름을 적거나 자료를 하나 이상 골라 주세요")
    title = name.strip() or _title_from_bundle(files)
    pid = db.create_project(sector, title, due_date=due_date.strip(), owner=getattr(request.state, "user", "zzaimy"),
                            unit=unit.strip() if unit.strip() in _business_units() else "")
    if not files:
        _link_past_projects(request, db.get_project(pid) or {"id": pid, "name": title})
        return RedirectResponse(f"/project/{pid}", status_code=303)
    project = db.get_project(pid) or {"id": pid, "sector": sector, "name": title}
    made = _intake_bundle(request, background, project, files)
    _link_past_projects(request, project)
    return RedirectResponse(f"/project/{pid}?bundle={len(made['criteria'])}+{len(made['intake'])}", status_code=303)


@router.post("/project/{project_id}/bundle")
def project_bundle(request: Request, background: BackgroundTasks, project_id: int, file: list[UploadFile] = File([])):
    h = request.app.state.project
    db, _intake_bundle = h.db, h._intake_bundle
    project = db.get_project(project_id)
    # 소유자·관리자·함께 작업은 올릴 수 있고 참조(읽기 전용)는 403, 못 보는 프로젝트는 404. 올린 문서의 owner 는 문서함·드라이브에서
    # 가져오기(/documents/import)와 같이 올린 사람이다(_intake_bundle — 부서·등급도 올린 사람 기준)
    if project is None or not request.app.state.project_access(request, project):
        raise HTTPException(404)
    _require_write(request, project)
    files = [f for f in file if f and f.filename]
    if not files:
        raise HTTPException(400, "파일을 하나 이상 골라 주세요")
    made = _intake_bundle(request, background, project, files)
    if _wants_json(request):                              # 끌어 놓기·진행 표시 — 화면이 결과를 받아 새로 그린다
        return {"criteria": len(made["criteria"]), "intake": len(made["intake"]), "skipped": made["skipped"]}
    return RedirectResponse(f"/project/{project_id}?bundle={len(made['criteria'])}+{len(made['intake'])}", status_code=303)


@router.post("/project/{project_id}/documents/import")
def project_documents_import(request: Request, background: BackgroundTasks, project_id: int, payload: dict = Body(...)):
    """문서함·구글 드라이브에서 고른 문서를 프로젝트 문서로 — 공용 디렉터리 창(AuraFinder)이 고른 항목을 받는다.
    {items: [{source:"dept", id} | {source:"drive", drive_id}]}. 문서함 파일은 열 수 있는 부서의 것만(webhwp.may_open),
    드라이브 파일은 이 사람이 연결한 학교 계정으로 내려받는다(구글 문서는 오피스 형식으로 내보냄).
    들이는 길은 「문서 추가」 업로드와 같은 반입(_intake_bundle — 원본 보관 adopt_original·기준/첨부 나누기·배경 판독)."""
    h = request.app.state.project
    db = h.db
    project = db.get_project(project_id)
    if project is None:
        raise HTTPException(404)
    _require_write(request, project)
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "가져올 문서를 하나 이상 골라 주세요")
    if len(items) > IMPORT_LIMIT:
        raise HTTPException(400, f"한 번에 {IMPORT_LIMIT}개까지 가져올 수 있습니다")
    from zzaimy.app import webhwp
    from zzaimy.app.webhwp_routes import _depts
    from zzaimy.ingest import gdrive_files
    st = request.app.state
    base = Path(st.data_dir) if getattr(st, "data_dir", None) else None
    depts = _depts(request) if base is not None else []
    email = None
    files, rejected, opened, seen = [], [], [], set()
    try:
        for it in items:
            it = it if isinstance(it, dict) else {}
            name = str(it.get("name") or "")[:160]
            src = it.get("source")
            if src == "dept":
                try:
                    fid = int(it.get("id"))
                except (TypeError, ValueError):
                    rejected.append({"name": name, "why": "없는 문서입니다"})
                    continue
                if ("dept", fid) in seen:
                    continue
                seen.add(("dept", fid))
                f = webhwp.get(db, fid)
                if f is None or base is None or not webhwp.may_open(base, f, depts) or not Path(f["path"]).is_file():
                    rejected.append({"name": name, "why": "열 수 없는 문서입니다"})
                    continue
                fh = open(f["path"], "rb")
                opened.append(fh)
                files.append(SimpleNamespace(filename=f["name"], file=fh))
            elif src == "drive":
                did = str(it.get("drive_id") or "")
                if not _DRIVE_ID.fullmatch(did):
                    rejected.append({"name": name, "why": "드라이브 파일 id 가 올바르지 않습니다"})
                    continue
                if ("drive", did) in seen:
                    continue
                seen.add(("drive", did))
                if email is None:
                    email = gdrive_files.account_for(db, getattr(request.state, "user", ""),
                                                     getattr(request.state, "dept", "") or None) or ""
                if not email:
                    rejected.append({"name": name, "why": "학교 구글 계정이 연결되어 있지 않습니다"})
                    continue
                try:
                    data, fname = _drive_fetch(email, did)
                except (RuntimeError, ValueError) as e:
                    rejected.append({"name": name, "why": str(e)})
                    continue
                files.append(SimpleNamespace(filename=fname, file=io.BytesIO(data)))
            else:
                rejected.append({"name": name, "why": "알 수 없는 항목입니다"})
        made = h._intake_bundle(request, background, project, files) if files else {"criteria": [], "intake": [], "skipped": []}
    finally:
        for fh in opened:
            fh.close()
    body = {"criteria": len(made["criteria"]), "intake": len(made["intake"]), "skipped": made["skipped"], "rejected": rejected}
    return JSONResponse(body, status_code=403 if rejected and not files else 200)


@router.get("/project/{project_id}", response_class=HTMLResponse)
def project_page(request: Request, project_id: int, refq: str = "", tab: str = ""):
    if tab:                                               # 옛 탭 주소 — 탭은 「대화·문서」 하나. 작성 원칙·기준 문서는 같은 화면 오른쪽
        rest = [(k, v) for k, v in request.query_params.multi_items() if k != "tab"]
        hint = "#projectContext" if tab in ("context", "guide", "criteria", "refs") else ""
        return RedirectResponse(f"/project/{project_id}" + (f"?{urlencode(rest)}" if rest else "") + hint, status_code=307)
    h = request.app.state.project
    db, templates, ctx, _req_viewer = h.db, h.templates, h.ctx, h._req_viewer
    _visible, _doc_groups, _program_options, _project_suggestions = h._visible, h._doc_groups, h._program_options, h._project_suggestions
    _share_sync, _can_read_chat = h._share_sync, h._can_read_chat
    proj = db.get_project(project_id)
    if proj is None:
        raise HTTPException(404)
    proj_sector = proj["sector"]
    # 문서 목록은 대화 문서함과 같은 세 갈래(project_documents)로 모은다
    doc_groups = _doc_groups(request, proj)
    docs = doc_groups["attached"]
    linked = set(db.get_project_criteria_ids(project_id))
    _pv = _req_viewer(request)
    sector_criteria = [
        d for d in db.list_documents("regulation")
        if d["status"] == "reviewed" and d["sector"] in (proj_sector, "common") and _visible(d, **_pv)
    ]
    # 이 섹터 전용 기준을 공통보다 위에 보여준다
    sector_criteria.sort(key=lambda d: d["sector"] != proj_sector)
    from zzaimy.app.doc_routing import KINDS
    criteria_kinds = {}
    for criterion in sector_criteria:
        kind = criterion.get("kind") or ""
        if kind not in KINDS:
            kind = ""
        criterion["picker_kind"] = kind
        criterion["kind_label"] = KINDS.get(kind, "미분류")
        criteria_kinds[kind] = criteria_kinds.get(kind, 0) + 1
    # 구버전 단일 지침·메모는 노트로 한 번만 이관한다 (지침·메모 통합)
    legacy_memo = (proj.get("memo") or "").strip()
    legacy_inst = (proj.get("instructions") or "").strip()
    if legacy_memo or legacy_inst:
        if legacy_inst:
            db.add_project_note(project_id, legacy_inst)
        if legacy_memo:
            db.add_project_note(project_id, legacy_memo)
        db.update_project_meta(project_id, instructions="", memo="")
        proj = db.get_project(project_id) or proj
    # 작업 문서 — 이 프로젝트 대화들이 만든 구글 문서(작업본). 고치는 곳은 그 대화의 문서 패널이다
    me = "" if getattr(request.state, "view_as", None) else getattr(request.state, "user", "")
    can_edit = request.state.role == "dev" or (bool(me) and proj.get("owner") == me)
    # 대화는 만든 사람만 — 담당자가 아닌 사람(같은 부서 동료·지난 사업 묶음 열람)에게는 대화·작업 문서 목록을 보이지 않는다
    from zzaimy.ingest import drive_share as _ds
    share_on = _ds.enabled(db, project_id)
    if can_edit and proj.get("owner") == me and share_on and not proj.get("archived") and _ds.due(db, project_id):
        _share_sync(proj)                                 # 담당자가 열 때 10분에 한 번 — 부서에 새로 들어온 동료도 받는다
    project_chats = db.list_project_chat_sessions(project_id)
    if not can_edit:                                # 동료는 공개된 대화만 읽기 전용으로(작업 문서는 담당자 드라이브라 빼고)
        project_chats = [cs for cs in project_chats if _can_read_chat(request, cs)]
    work_docs: list[dict] = []
    work_sessions: list[int] = []                 # 구글 문서가 연결된 대화 — 화면이 문서 패널과 같은 목록(드라이브 폴더)을 불러온다
    seen_urls: set[str] = set()
    for cs in (project_chats if (can_edit or share_on) else []):    # 동료는 부서 공유가 켜졌을 때만(구글에서 열 수 있을 때)
        for f in db.list_files(kind="google", session_id=int(cs["id"]), limit=20):
            url = f.get("path") or ""
            if url.startswith("https://") and url not in seen_urls:
                seen_urls.add(url)
                work_docs.append({"kind": "gdoc", "name": f.get("name") or "작업 문서", "url": url, "chat_id": cs["id"],
                                  "chat_title": cs.get("title") or "", "created_at": f.get("created_at") or ""})
        # 양식으로 만든 문서는 파일 기록 없이 대화 연결(chat_google_doc)만 있다 — 연결된 문서도 넣는다
        link = _aj.loads(db.get_setting(f"chat_google_doc:{cs['id']}", "") or "{}")
        if re.fullmatch(r"[A-Za-z0-9_-]+", str(link.get("doc") or "")):
            if can_edit:                                  # 문서 패널 목록(/api/chat-documents)은 대화 주인만 — 동료는 서버 기록 그대로
                work_sessions.append(int(cs["id"]))
            url = f"https://docs.google.com/document/d/{link['doc']}/edit"
            if not any(link["doc"] in u for u in seen_urls):
                seen_urls.add(url)
                work_docs.append({"kind": "gdoc", "name": f"{cs.get('title') or '대화'}의 작업 문서", "url": url, "chat_id": cs["id"],
                                  "chat_title": cs.get("title") or "", "created_at": cs.get("created_at") or ""})
    # 한글 작업 문서 — 대화에 이은 한글 파일과 부서 한글 문서함의 「작업/<프로젝트>」 폴더. 이 사람이 열 수 있는 부서의 것만
    work_docs += _hwp_work_docs(request, proj, project_chats, owner_view=can_edit)
    work_docs.sort(key=lambda w: (w["created_at"] or "").replace("T", " "), reverse=True)
    from zzaimy.app import project_access as pa
    accounts = request.app.state.accounts or {}
    lead = pa.lead(db, proj, accounts)
    _nm = lambda u: ((accounts.get(u) or {}).get("name") or "").strip() or u
    from zzaimy.app import grades
    owner_dept = ((accounts.get(proj.get("owner") or "", {}) or {}).get("dept") or "").strip()
    show_activity = grades.can_view_activity(proj, user=me or getattr(request.state, "user", ""), account=_account(request),
                                             lead=lead, project_dept=owner_dept)
    lead_choices = [{"user": u, "label": _nm(u)} for u in pa.lead_choices(proj, _share_of(request, proj), accounts)] if can_edit else []
    return templates.TemplateResponse(
        request,
        "project.html",
        ctx(request, {
            "project": proj, "documents": docs, "doc_groups": doc_groups, "active_tab": proj["sector"],
            "program_options": _program_options() if not proj.get("archived") else [],
            "project_program": (proj.get("program") or "").split("|", 1)[0], "project_program_year": ((proj.get("program") or "") + "|").split("|")[1],
            "ministries": sorted({str(e.get("ministry") or "").split(",")[0].strip()
                                  for e in __import__("zzaimy.app.program_names", fromlist=["load_ledger"]).load_ledger().get("programs", [])} - {""}),
            "suggestions": _project_suggestions(proj),
            "linked_criteria": linked, "sector_criteria": sector_criteria,
            "criteria_kinds": criteria_kinds, "kind_labels": KINDS,
            "project_chats": project_chats, "can_edit": can_edit,
            "drive_share": {**_ds.state(db, project_id)},
            **_share_ctx(request, proj),
            "owner_label": proj.get("owner") or "",
            "work_docs": work_docs, "work_sessions": work_sessions,
            "project_notes": db.list_project_notes(project_id),
            **h._project_ref_ctx(request, proj, refq, h._ref_names(doc_groups)),
            "palette": pa.PALETTE, "project_color_key": pa.color_key(db, project_id),
            "lead": lead, "lead_label": _nm(lead), "lead_choices": lead_choices, "show_activity": show_activity,
        }),
    )


@router.post("/project/{project_id}/refs")
def project_ref_link(request: Request, project_id: int, ref_project_id: int = Form(...), reason: str = Form("")):
    """관련 보관 사업 연결 — 참조 관계만 둔다(보관 해제·소유권 변경·문서 이동 없음)."""
    h = request.app.state.project
    db, _ref_scope = h.db, h._ref_scope
    from zzaimy.app import project_refs
    proj, ref = db.get_project(project_id), db.get_project(ref_project_id)
    if proj is None or ref is None or not ref.get("archived") or ref_project_id == project_id:
        raise HTTPException(404)
    scope = _ref_scope(request)
    if proj.get("archived") or (proj.get("owner") != scope["user"] and scope["role"] != "dev"):
        raise HTTPException(404)
    if not project_refs._visible_counts(db, [ref_project_id], scope).get(ref_project_id):
        raise HTTPException(404)
    project_refs.link(db, project_id, ref_project_id, reason, getattr(request.state, "user", "") or "")
    return RedirectResponse(f"/project/{project_id}#projectContext", status_code=303)


@router.post("/project/{project_id}/refs/{ref_project_id}/delete")
def project_ref_unlink(request: Request, project_id: int, ref_project_id: int):
    h = request.app.state.project
    db = h.db
    from zzaimy.app import project_refs
    if db.get_project(project_id) is None:
        raise HTTPException(404)
    project_refs.unlink(db, project_id, ref_project_id)
    return RedirectResponse(f"/project/{project_id}#projectContext", status_code=303)


@router.get("/projects/archived", response_class=HTMLResponse)
def projects_archived(request: Request, q: str = "", status: str = "all", year: str = "", unit: str = "",
                      for_project: int | None = None, sort: str = ""):
    """프로젝트 검색(사업 단위 찾기) — 권한 범위의 진행 중 프로젝트·보관된 사업 묶음을 사업명·수행 연도·사업단으로. 기본은 전체.
    결과는 같은 사업의 연도별 묶음을 한 카드로 모아 보인다(project_refs.group_items)."""
    h = request.app.state.project
    db, templates, ctx, _dept_peers = h.db, h.templates, h.ctx, h._dept_peers
    _ref_scope, _doc_groups, _ref_names = h._ref_scope, h._doc_groups, h._ref_names
    from zzaimy.app import project_refs
    if for_project is not None:
        fp = db.get_project(for_project)
        if fp is None or fp.get("archived") or (fp.get("owner") != request.state.user and request.state.role != "dev"):
            raise HTTPException(404)
    status = status if status in ("archived", "active", "all") else "all"
    q = (q or "").strip()[:200]
    names = _ref_names(_doc_groups(request, fp)) if for_project is not None else None
    data = project_refs.browse(db, _ref_scope(request), q, status, year, unit, for_project, sort=sort, names=names,
                               shared=request.app.state.shared_project_ids(request))
    y, r, none = project_refs.year_option(year)
    year = "none" if none else (f"r{r}" if r is not None else y)
    year_label = "연도 미상" if none else (f"{r}차년도" if r is not None else y)
    groups = project_refs.group_items(data["items"])
    return templates.TemplateResponse(request, "projects_archived.html", ctx(request, {
        "projects": data["items"], "groups": groups, "total": data["total"], "units": data["units"], "years": data["years"],
        "rounds": data["rounds"], "has_unknown": data["has_unknown"], "sorts": project_refs.SORTS, "sort": data["sort"],
        "n_docs_total": sum(it["n_docs"] for it in data["items"]), "year_label": year_label,
        "q": q, "status": status, "year": year, "unit": unit, "for_project": for_project}))


@router.post("/project/{project_id}/unarchive")
def project_unarchive(request: Request, project_id: int):
    """불러오기 — 보관을 풀고, 과거 사업 묶음이면 불러온 담당자의 프로젝트로 가져온다."""
    h = request.app.state.project
    db = h.db
    proj = db.get_project(project_id)
    if proj is None:
        raise HTTPException(404)
    user = getattr(request.state, "user", None)
    db.set_project_archived(project_id, False)
    if user and proj.get("owner") == "zzdev" and user != "zzdev":
        with db._conn() as conn:
            conn.execute("UPDATE projects SET owner = ? WHERE id = ?", (user, project_id))
    return RedirectResponse(f"/project/{project_id}", status_code=303)


@router.post("/project/{project_id}/archive")
def project_archive(request: Request, project_id: int):
    """보관 — 사이드바·목록에서 빼고 「보관된 사업」으로. 문서·대화·메모는 그대로."""
    h = request.app.state.project
    db = h.db
    if db.get_project(project_id) is None:
        raise HTTPException(404)
    db.set_project_archived(project_id, True)
    return RedirectResponse("/projects/archived", status_code=303)


def _hwp_work_docs(request: Request, proj: dict, chats: list[dict], *, owner_view: bool = False) -> list[dict]:
    """이 프로젝트의 한글 작업 문서 — 대화마다 이은 한글 파일(webhwp_agent.files_of)과 부서 한글 문서함의 「작업/<프로젝트 이름>」 폴더
    (에이전트가 양식으로 작업본을 만드는 자리). 보는 사람이 열 수 있는 부서(webhwp.may_open)의 파일만 싣는다.
    드라이브에 최종본을 올린 파일은 담당자 화면에 구글 드라이브 열기도 함께 준다."""
    from pathlib import Path

    from zzaimy.app import webhwp, webhwp_agent
    from zzaimy.app.webhwp_routes import EDIT_PAGE, _depts
    st = request.app.state
    db = st.db
    depts = _depts(request)
    if not depts or not getattr(st, "data_dir", None):
        return []
    base = Path(st.data_dir)
    try:
        folder = webhwp.clean_folder(f"작업/{storage.safe_name(proj.get('name') or '')[:40]}")
    except ValueError:
        folder = ""
    picked: dict[int, int | None] = {}                    # 파일 id → 이은 대화(없으면 None)
    for cs in chats:
        for fid in webhwp_agent.files_of(db, int(cs["id"])):
            picked.setdefault(int(fid), int(cs["id"]))
    if folder and folder != "작업":
        for r in db.list_files(kind="webhwp", limit=5000):
            sub = webhwp.folder_of(base, r["path"])
            if sub == folder or sub.startswith(folder + "/"):
                picked.setdefault(int(r["id"]), None)
    out = []
    for fid, chat_id in picked.items():
        f = webhwp.get(db, fid)
        if not webhwp.may_open(base, f, depts) or not Path(f["path"]).exists():
            continue
        m = webhwp.meta(db, fid)
        drive = (m.get("drive_out") or {}).get("url") or ""
        out.append({"kind": "hwp", "id": fid, "name": Path(f["name"]).stem or f["name"], "url": f"{EDIT_PAGE}?id={fid}",
                    "chat_id": chat_id, "chat_title": "",
                    "where": " / ".join(x for x in (webhwp.dept_of_path(base, f["path"]), webhwp.folder_of(base, f["path"])) if x),
                    "drive_url": drive if owner_view and drive.startswith("https://") else "",
                    "created_at": m.get("saved") or m.get("created") or f.get("created_at") or ""})
    return out


def _share_ctx(request: Request, proj: dict) -> dict:
    """공유 창 — 지금 범위, 고를 수 있는 부서·사용자, 이 사람의 권한."""
    from zzaimy.app import project_access as pa
    from zzaimy.app.user_admin import known_depts
    st = request.app.state
    accounts = st.accounts
    owner_dept = ((accounts.get(proj.get("owner") or "", {}) or {}).get("dept") or "").strip()
    share = pa.shares(st.db, proj, owner_dept)
    depts = known_depts(st.db, accounts, getattr(st, "data_dir", None))
    users = sorted(u for u, a in accounts.items() if (a or {}).get("role", "staff") in ("staff", "professor", "head")
                   and u != proj.get("owner") and (a or {}).get("active", True))
    # 공유 창의 찾기 목록 — 부서 전체와 사람(이름·부서를 함께 보여 고르기 쉽게)
    choices = [{"to": f"dept:{d}", "label": d, "hint": "부서 전체"} for d in depts]
    for u in users:
        a = accounts.get(u) or {}
        name, dept = (a.get("name") or "").strip(), (a.get("dept") or "").strip()
        choices.append({"to": f"user:{u}", "label": f"{name} ({u})" if name and name != u else u, "hint": dept or "사용자"})
    names = {c["to"]: c["label"] for c in choices}
    return {"share": share, "share_label": pa.button_label(share), "share_title": pa.describe(share),
            "share_levels": pa.LEVEL_LABEL, "share_level_hints": pa.LEVEL_HINT, "share_depts": depts, "share_users": users,
            "share_choices": choices, "share_names": names, "share_own_dept": owner_dept if owner_dept in depts else "",
            "my_access": st.project_access(request, proj)}


@router.post("/project/{project_id}/shares")
async def project_set_shares(request: Request, project_id: int):
    """프로젝트 공유 범위 — 나만 · 공유(부서 일괄·지정 사용자, 각각 보기/함께 작업). 담당자·관리자만(로그인 검사가 남의 프로젝트 POST 를 거른다).
    저장 뒤 작업 문서의 구글 권한을 바로 맞춘다(거두는 일은 기다린다)."""
    h = request.app.state.project
    db, _share_sync = h.db, h._share_sync
    from zzaimy.app import project_access as pa
    from zzaimy.app.user_admin import known_depts
    proj = db.get_project(project_id)
    if proj is None or proj.get("archived"):
        raise HTTPException(404)
    form = await request.form()
    accounts = request.app.state.accounts
    depts = set(known_depts(db, accounts, getattr(request.app.state, "data_dir", None)))
    staff = {u for u, a in accounts.items() if (a or {}).get("role", "staff") in ("staff", "professor", "head")}
    items = []
    for to, lv in zip(form.getlist("to"), form.getlist("level")):
        to = str(to).strip()
        if (to.startswith("dept:") and to[5:] in depts) or (to.startswith("user:") and to[5:] in staff and to[5:] != proj.get("owner")):
            items.append({"to": to, "level": lv if lv in pa.LEVELS else "view"})
    got = pa.set_shares(db, project_id, str(form.get("mode") or ""), items)
    _share_sync(proj, wait=True)
    from zzaimy.ingest import drive_share as _ds
    st = _ds.state(db, project_id)
    msg = f"공유 범위를 「{pa.describe(got)}」로 저장했습니다" + (f" — 작업 문서 권한 {st['shared']}명" if st.get("shared") else "")
    if st.get("missing"):
        msg += f" · 구글 미연결 {st['missing']}명(연결하면 작업 문서도 받음)"
    if st.get("error"):
        msg += f" (구글: {st['error']})"
    return RedirectResponse(f"/project/{project_id}?" + urlencode({"ok": msg}), status_code=303)


@router.post("/project/{project_id}/meta")
def project_meta(
    request: Request, project_id: int, instructions: str = Form(""), memo: str = Form("")
):
    h = request.app.state.project
    db = h.db
    if db.get_project(project_id) is None:
        raise HTTPException(404)
    db.update_project_meta(project_id, instructions=instructions, memo=memo)
    return RedirectResponse(f"/project/{project_id}", status_code=303)


@router.post("/project/{project_id}/notes")
def project_note_add(request: Request, project_id: int, content: str = Form(...)):
    h = request.app.state.project
    db = h.db
    proj = db.get_project(project_id)
    if proj is None:
        raise HTTPException(404)
    _require_write(request, proj)
    if content.strip():
        db.add_project_note(project_id, content.strip()[:300])
    return RedirectResponse(f"/project/{project_id}#projectContext", status_code=303)


@router.post("/project/{project_id}/notes/{note_id}/delete")
def project_note_delete(request: Request, project_id: int, note_id: int):
    h = request.app.state.project
    db = h.db
    proj = db.get_project(project_id)
    if proj is None:
        raise HTTPException(404)
    _require_write(request, proj)
    db.delete_project_note(project_id, note_id)
    return RedirectResponse(f"/project/{project_id}#projectContext", status_code=303)


@router.post("/project/{project_id}/criteria")
def project_criteria(request: Request, project_id: int, criteria: list[int] = Form([])):
    h = request.app.state.project
    db = h.db
    if db.get_project(project_id) is None:
        raise HTTPException(404)
    db.set_project_criteria(project_id, criteria)
    return RedirectResponse(f"/project/{project_id}", status_code=303)


@router.post("/projects/{project_id}/rename")
def rename_project(
    request: Request, project_id: int, name: str = Form(...), due_date: str | None = Form(None), unit: str | None = Form(None),
    program: str = Form("__keep__"), new_program: str = Form(""), new_ministry: str = Form(""), program_year: str = Form(""),
):
    h = request.app.state.project
    db, _business_units = h.db, h._business_units
    proj = db.get_project(project_id)
    if proj is None:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다")
    if request.state.role != "dev" and proj.get("owner") != request.state.user:      # 남의 프로젝트는 고칠 수 없다
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다")
    if not name.strip():
        raise HTTPException(400, "프로젝트 이름을 입력하세요")
    db.rename_project(project_id, name.strip())
    if due_date is not None:
        db.update_project_meta(project_id, due_date=due_date.strip())
    if unit is not None:
        db.set_project_unit(project_id, unit.strip() if unit.strip() in _business_units() else "")
    if program != "__keep__" and not proj.get("archived"):          # 「none」 = 사업 풀기(빈 폼 값은 FastAPI 가 기본값으로 바꾼다)
        # 사업 — 기존 사업 또는 새 사업(장부에 등록). 프로젝트 문서가 라이브러리 사업 목록·사업 그래프에 함께 선다
        key = ""
        if program == "_new":
            from zzaimy.app import paths as _paths, program_register as _pr
            key, _label, err = _pr.register(_paths.platform_dir() / "kg_external.json", new_program, new_ministry,
                                            by=getattr(request.state, "user", ""))
            if err:
                return RedirectResponse(f"/project/{project_id}?" + urlencode({"err": err}), status_code=303)
        elif program.startswith("program:"):
            key = program.split("|", 1)[0]
            from zzaimy.graph import kg_store as _ks
            _ks.ensure(db)
            with db._conn() as conn:
                if not conn.execute("SELECT 1 FROM kg_nodes WHERE id = ? AND type = 'program'", (key,)).fetchone():
                    return RedirectResponse(f"/project/{project_id}?" + urlencode({"err": "없는 사업입니다"}), status_code=303)
        y = program_year.strip()
        db.set_project_program(project_id, (key + (f"|{y}" if key and y.isdigit() and len(y) == 4 else "")) if key else "")
    return RedirectResponse(f"/project/{project_id}", status_code=303)


@router.post("/projects/{project_id}/delete")
def delete_project(request: Request, project_id: int):
    h = request.app.state.project
    db = h.db
    proj = db.get_project(project_id)
    if proj is None or (request.state.role != "dev" and proj.get("owner") != request.state.user):
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다")
    db.delete_project(project_id)
    return RedirectResponse(f"/?type={proj['sector']}", status_code=303)
