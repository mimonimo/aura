"""문서 화면(/doc/{id})과 문서 고치기 — 처리 상태(/doc/{id}/status), 맥락 분석·초안 만들기·말로 고치기, 부서·열람 등급·학생 공개,
갈래·사업 정보 다시 정하기, 품질 신고, 폴더 담기, 판정·쪽 판독·접수번호·검토 의견, 지우기, 라이브러리에서 고른 여러 문서
지우기·검색 범위·폴더(/docs/delete·/docs/scope·/docs/folder).

- 원본 열기·미리보기·구글 열람본·내려받기·내보내기(/doc/{id}/original·view·export.* 등)는 app/doc_file_routes 에 있다.
  문서함 구글 열람 JSON(/api/doc/{id}/google)·라이브러리(/)·기준 문서(/criteria)는 main 에 그대로 있다.
- DB·화면 틀·화면 공통 맥락(ctx)·처리기·초안기와 대화·라이브러리 화면이 함께 쓰는 도우미(열람 범위 _visible·고를 수 있는 검색 범위
  _scope_choices·부서 목록 _all_depts·고칠 수 있는가 _can_edit_doc·갈래 다시 정하기 _auto_route·사업 정보 _identify_document 등)는
  main.create_app 이 앱마다 만들어 문서 화면 묶음(app.state.doc_page — make_doc_page_helpers)에 넘긴다. 대화 묶음(app.state.chat)이
  받는 것과 같은 객체다.
- 문서 화면에서만 쓰는 도우미(폴더 고르기·폴더 담기·인식 영역 쪽 목록·문서 지우기·접수번호 꼴·품질 신고 갈래)는
  make_doc_page_helpers 안에 있다 — 앱마다 하나.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from zzaimy.app import paths as _paths
from zzaimy.app import storage

router = APIRouter()


def make_doc_page_helpers(
    *,
    db, db_path, processor, drafter, templates, ctx,
    CRITERIA_LIST, _req_viewer, _visible, _scope_choices, _all_depts, _local_back,
    _auto_route, _identify_document, _can_edit_doc,
) -> SimpleNamespace:
    """앱마다 하나 — main.create_app 이 넘긴 값과 문서 화면 전용 도우미를 한 묶음(app.state.doc_page)으로."""
    deps = dict(locals())
    from zzaimy.app.main import QUALITY_KIND_LABELS   # 품질 신고 갈래 — 정본은 main(같은 표)

    # ---- 추출 품질 신고 루프 (품질 체계 5계층, docs/quality-system.md) ----

    _QUALITY_KINDS = set(QUALITY_KIND_LABELS)

    def _doc_folder_ctx(doc_id: int, request: Request) -> dict:
        """문서 화면의 폴더 고르기 — 만든 폴더 목록(이 계정의 범위 안)과 지금 담긴 폴더."""
        from zzaimy.app import library_folders as lf
        made = [f for f in lf.folders(db, Path(db_path).parent, _req_viewer(request)) if f["kind"] == "만든"]
        with db._conn() as conn:
            row = conn.execute("SELECT folder_id FROM doc_folders WHERE doc_id = ?", (doc_id,)).fetchone()
        return {"made_folders": made, "doc_folder": f"{lf.MADE}{row[0]}" if row else ""}

    def move_to_folder(request: Request, ids: list[int], folder: str) -> int | None:
        """문서들을 만든 폴더에 담는다(빈 값 = 폴더에서 뺌). 폴더의 검색 범위가 이 계정이 고를 수 없는 부서면 None."""
        from zzaimy.app import library_folders as lf, rag_spaces
        if not folder:
            with db._conn() as conn:
                for d in ids:
                    conn.execute("DELETE FROM doc_folders WHERE doc_id = ?", (int(d),))
            return len(ids)
        f = next((x for x in lf.folders(db, Path(db_path).parent, _req_viewer(request)) if x["key"] == folder and x["kind"] == "만든"), None)
        if f is None or f["dept"] not in _scope_choices(request):
            return None
        lf.put(db, folder, ids)
        rag_spaces.move_docs(db, ids, f["dept"])
        return len(ids)

    def _box_page_list(doc_id: int) -> list[int]:
        """인식 영역이 있는 페이지 번호 목록. 없으면 빈 목록."""
        import json as _bj

        lines_file = _paths.lines_dir(Path(db_path).parent) / f"{doc_id}.json"
        if not lines_file.exists():
            return []
        try:
            payload = _bj.loads(lines_file.read_text())
        except (ValueError, OSError):
            return []
        return sorted((int(p) for p in (payload.get("page_sizes") or {})), key=int)

    def _drop_doc(doc: dict) -> None:
        db.delete_document(doc["id"])
        storage.remove_intake_dir(db, doc)          # 원본·추출 그림이 든 문서 폴더째 지운다

    _RECEIPT_RE = re.compile(r"^\d{4}-[가-힣A-Za-z]{1,8}-\d{3,5}$")

    return SimpleNamespace(
        **deps,
        _QUALITY_KINDS=_QUALITY_KINDS, _doc_folder_ctx=_doc_folder_ctx, move_to_folder=move_to_folder, _box_page_list=_box_page_list,
        _drop_doc=_drop_doc, _RECEIPT_RE=_RECEIPT_RE,
    )


@router.get("/doc/{doc_id}/status")
def doc_status(request: Request, doc_id: int):
    h = request.app.state.doc_page
    db = h.db
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    return {
        "processing": doc["status"] in ("received", "processing"),
        "drafting": (doc.get("coverage") or "").startswith(("초안 작성 중", "분석 중")),
    }


@router.post("/doc/{doc_id}/analyze")
def doc_analyze(request: Request, background: BackgroundTasks, doc_id: int):
    h = request.app.state.doc_page
    db, processor = h.db, h.processor
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    if doc["doc_type"] not in ("ocr", "regulation"):
        raise HTTPException(400, "맥락 분석은 문서 추출·기준 문서에서만 지원합니다")
    db.update_document(doc_id, coverage="분석 중입니다 (30초~1분)")
    background.add_task(processor.analyze, db, doc_id)
    return RedirectResponse(f"/doc/{doc_id}", status_code=303)


@router.post("/doc/{doc_id}/revise")
def doc_revise(request: Request, background: BackgroundTasks, doc_id: int, want: str = Form("")):
    """말한 대로 초안을 고친다 — 요청을 남기고 다시 만든다."""
    h = request.app.state.doc_page
    db, drafter = h.db, h.drafter
    from urllib.parse import quote as _q

    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    want = want.strip()
    if len(want) < 4:
        return RedirectResponse(
            f"/doc/{doc_id}?err=" + _q("어떻게 고칠지 한 문장으로 적어 주십시오"),
            status_code=303)
    db.add_review(doc_id, want)
    db.update_document(doc_id, coverage="초안을 고치는 중입니다")
    background.add_task(drafter.generate, db, doc_id)
    return RedirectResponse(
        f"/doc/{doc_id}?ok=" + _q("요청을 반영해 초안을 다시 만들고 있습니다"),
        status_code=303)


@router.post("/doc/{doc_id}/scope")
def doc_set_scope(request: Request, doc_id: int, dept: str = Form(""), access_level: str = Form(""),
                  back: str = Form("")):
    """문서의 부서·열람 등급 바꾸기 — 조각에도 즉시 옮겨져 검색 범위가 따라간다(문서 화면이 부른다).
        관리자, 올린 사람, 또는 그 문서 부서의 교직원."""
    h = request.app.state.doc_page
    db, _scope_choices, _all_depts, _local_back = h.db, h._scope_choices, h._all_depts, h._local_back
    from zzaimy.app.access_policy import LEVELS

    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    if not request.app.state.may_edit_doc(request, doc):
        raise HTTPException(403, "문서를 올린 담당자, 그 부서 교직원, 관리자만 바꿀 수 있습니다")
    d = dept.strip()
    if d:                                            # 검색 범위 — 공통 RAG(전체 공개) 또는 부서 RAG(부서 제한)
        from zzaimy.app import rag_spaces
        if d != "공통" and d not in _all_depts():
            raise HTTPException(400, f"없는 부서 RAG: {d}")
        if d not in _scope_choices(request):           # 교직원은 공통·소속·추가로 받은 부서만 — 그 밖의 부서 RAG 로는 관리자만
            raise HTTPException(403, f"이 계정은 「{d}」 부서 RAG 로 옮길 수 없습니다")
        rag_spaces.move_docs(db, [doc_id], d)
    if access_level in LEVELS:
        db.set_document_scope(doc_id, access_level=access_level)
    dest = _local_back(back, f"/doc/{doc_id}")
    return RedirectResponse(dest, status_code=303)


@router.post("/doc/{doc_id}/audience")
def doc_set_audience(request: Request, doc_id: int, audience: str = Form("staff"), back: str = Form("")):
    """규정 문서의 학생 공개 켜기·끄기(ADR-0052) — 관리자, 또는 그 문서를 올린 사람만. 규정이 아니면 학생 공개가 되지 않는다.
    부서 교직원의 문서 작업 권한(can_edit)에는 넣지 않는다 — 학생 공개는 열람 범위를 학생 전체로 넓히는 일이라(10-21 보안 검토)."""
    h = request.app.state.doc_page
    db, _local_back = h.db, h._local_back
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    me = "" if getattr(request.state, "view_as", None) else getattr(request.state, "user", "")
    if getattr(request.state, "role", "") != "dev" and not (me and doc.get("owner") == me):
        raise HTTPException(403, "학생 공개는 문서를 올린 담당자나 관리자만 바꿀 수 있습니다")
    if audience not in ("staff", "student") or (audience == "student" and doc.get("doc_type") != "regulation"):
        raise HTTPException(400, "학생 공개는 규정 문서만 할 수 있습니다")
    db.set_document_audience(doc_id, audience)
    dest = _local_back(back, f"/doc/{doc_id}")
    return RedirectResponse(dest, status_code=303)


@router.post("/doc/{doc_id}/route")
def doc_route(request: Request, doc_id: int):
    """이 문서의 갈래와 영역을 다시 정한다."""
    h = request.app.state.doc_page
    db, _auto_route = h.db, h._auto_route
    from urllib.parse import quote as _q

    if db.get_document(doc_id) is None:
        raise HTTPException(404)
    line = _auto_route(db, doc_id, user_chose_type=False)
    return RedirectResponse(
        f"/doc/{doc_id}?ok=" + _q(line or "지금 값이 알맞아 그대로 두었습니다"),
        status_code=303,
    )


@router.post("/doc/{doc_id}/identity")
def doc_identity(request: Request, doc_id: int):
    """이 문서가 어떤 사업에 관한 것인지 본문에서 인출한다.

        값은 모두 본문과 대조해 확인된 것만 남긴다. 확인되지 않은 값은 버리고
        버린 이유를 함께 돌려준다 — 근거 없는 값이 화면에 남으면 안 되기 때문이다.
        """
    h = request.app.state.doc_page
    db, _identify_document = h.db, h._identify_document
    from urllib.parse import quote as _q

    if db.get_document(doc_id) is None:
        raise HTTPException(404)
    r = _identify_document(db, doc_id)
    return RedirectResponse(
        f"/doc/{doc_id}?" + ("ok=" if r["ok"] else "err=")
        + _q(("사업 정보를 정리했습니다 — " + " · ".join(r["identity"].values()))
             if r["ok"] else r["error"]),
        status_code=303,
    )


@router.post("/docs/delete")
async def docs_delete(request: Request):
    """여러 문서 삭제(JSON {ids}) — 라이브러리 목록에서 고른 문서를 한 번에. 관리자만."""
    h = request.app.state.doc_page
    db, _drop_doc = h.db, h._drop_doc
    if request.state.role != "dev":
        raise HTTPException(403, "문서 삭제는 관리자만 할 수 있습니다")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "잘못된 요청")
    n = 0
    for i in [int(x) for x in (body.get("ids") or []) if str(x).isdigit()][:500]:
        doc = db.get_document(i)
        if doc is not None:
            _drop_doc(doc)
            n += 1
    return {"moved": n}


@router.post("/docs/scope")
async def docs_set_scope(request: Request):
    """여러 문서의 검색 범위 — 공통 RAG 또는 부서 RAG(JSON {ids, dept}). 관리자만."""
    h = request.app.state.doc_page
    db, _all_depts = h.db, h._all_depts
    if request.state.role != "dev":
        raise HTTPException(403, "관리자만 바꿀 수 있습니다")
    from zzaimy.app import rag_spaces
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "잘못된 요청")
    dept = str(body.get("dept") or "공통").strip()
    if dept != "공통" and dept not in _all_depts():
        raise HTTPException(400, f"없는 부서 RAG: {dept}")
    ids = [int(x) for x in (body.get("ids") or []) if str(x).isdigit()][:5000]
    return {"moved": rag_spaces.move_docs(db, ids, dept), "dept": dept}


@router.post("/doc/{doc_id}/quality-report")
def doc_quality_report(
    request: Request, doc_id: int,
    kind: str = Form(...), note: str = Form(""),
):
    h = request.app.state.doc_page
    db, _QUALITY_KINDS = h.db, h._QUALITY_KINDS
    if db.get_document(doc_id) is None:
        raise HTTPException(404, "없는 문서입니다")
    if kind not in _QUALITY_KINDS:
        raise HTTPException(400, "알 수 없는 신고 유형입니다")
    db.add_quality_report(
        doc_id, kind, note.strip(), reporter=request.state.user
    )
    return RedirectResponse(f"/doc/{doc_id}?reported=1", status_code=303)


@router.post("/doc/{doc_id}/folder")
def doc_set_folder(request: Request, doc_id: int, folder: str = Form("")):
    """문서를 만든 폴더에 담거나 뺀다 — 폴더의 검색 범위로 함께 옮긴다(폴더 없음이면 범위는 그대로)."""
    h = request.app.state.doc_page
    db, _can_edit_doc, move_to_folder = h.db, h._can_edit_doc, h.move_to_folder
    from zzaimy.app import library_folders as lf, rag_spaces
    doc = db.get_document(doc_id)
    if not _can_edit_doc(request, doc):
        raise HTTPException(403, "문서를 올린 담당자, 그 부서 교직원, 관리자만 바꿀 수 있습니다")
    n = move_to_folder(request, [doc_id], folder)
    if n is None:
        raise HTTPException(400, "없는 폴더이거나 이 계정이 쓸 수 없는 범위의 폴더입니다")
    return RedirectResponse(f"/doc/{doc_id}", status_code=303)


@router.post("/docs/folder")
async def docs_set_folder(request: Request):
    """라이브러리에서 고른 여러 문서를 폴더에 담는다(JSON {ids, folder}). 관리자, 또는 자기가 올린 문서만."""
    h = request.app.state.doc_page
    db, _can_edit_doc, move_to_folder = h.db, h._can_edit_doc, h.move_to_folder
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "잘못된 요청")
    ids = [int(x) for x in (body.get("ids") or []) if str(x).isdigit()][:2000]
    if request.state.role != "dev":
        ids = [d for d in ids if _can_edit_doc(request, db.get_document(d))]
    n = move_to_folder(request, ids, str(body.get("folder") or ""))
    if n is None:
        raise HTTPException(400, "없는 폴더이거나 이 계정이 쓸 수 없는 범위의 폴더입니다")
    return {"moved": n}


@router.post("/doc/{doc_id}/draft")
def make_draft(
    request: Request, background: BackgroundTasks, doc_id: int,
    sections: str = Form(""),
):
    h = request.app.state.doc_page
    db, drafter = h.db, h.drafter
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    if doc["doc_type"] != "grant":
        # 목적별 플로우: 초안 작성은 국고사업 계열, 나머지는 검토·판정
        raise HTTPException(400, "초안 작성은 국고사업 문서에서만 지원합니다")
    # 담당자가 요구한 작성 항목(한 줄에 하나) — 공고 목차보다 우선한다
    db.update_document(doc_id, draft_spec=sections.strip() or None)
    # 진행 표시를 먼저 남긴다 — 생성이 끝나면 drafter가 결과로 덮어쓴다
    db.update_document(doc_id, coverage="초안 작성 중입니다 (1~2분 걸립니다)")
    background.add_task(drafter.generate, db, doc_id)
    return RedirectResponse(f"/doc/{doc_id}", status_code=303)


@router.get("/doc/{doc_id}", response_class=HTMLResponse)
def detail(request: Request, doc_id: int):
    h = request.app.state.doc_page
    db, db_path, templates, ctx = h.db, h.db_path, h.templates, h.ctx
    _req_viewer, _visible, _scope_choices, _doc_folder_ctx = h._req_viewer, h._visible, h._scope_choices, h._doc_folder_ctx
    _box_page_list = h._box_page_list
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    _dv = _req_viewer(request)

    def _sees(did) -> bool:
        """문서 화면에 걸리는 다른 문서(연결 기준·추천·같은 사업·참조)도 이 계정이 볼 수 있는 것만 — 제목조차 드러내지 않는다."""
        try:
            return _visible(db.get_document(int(did)) or {}, **_dv)
        except (TypeError, ValueError):
            return False

    related = None
    if doc.get("related_criteria_id") and _sees(doc["related_criteria_id"]):
        related = db.get_document(doc["related_criteria_id"])
    chunks = db.list_doc_chunks(doc_id)
    from zzaimy.app.render import chunk_blocks

    src_path = Path(doc["stored_path"])
    suffix = src_path.suffix.lower()
    original_kind = None
    if src_path.exists():
        from zzaimy.app import office_pdf

        original_kind = (
            "pdf" if suffix == ".pdf"
            else "image" if suffix in (".png", ".jpg", ".jpeg")
            else "office" if office_pdf.is_office(src_path) and office_pdf.view_path(doc).is_file() and office_pdf.view_path(doc).stat().st_size > 0 else None
        )

    all_assets = db.list_doc_assets(doc_id)
    scan_asset = next((a for a in all_assets if a["kind"] == "scan"), None)
    assets = [a for a in all_assets if a["kind"] != "scan"]
    asset_by_name = {Path(a["path"]).name: a["id"] for a in assets}
    blocks = chunk_blocks(chunks, doc_id, asset_by_name) if chunks else None
    if blocks is not None and assets and not any(
        c["kind"] == "image" for c in chunks
    ):
        from zzaimy.app.render import trailing_image_blocks

        blocks = blocks + trailing_image_blocks(doc_id, assets)
    from zzaimy.app.render import layout_pages

    page_sizes: dict[int, tuple[float, float]] = {}
    if chunks and Path(doc["stored_path"]).suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader

            for i, pg in enumerate(PdfReader(doc["stored_path"]).pages, start=1):
                page_sizes[i] = (
                    float(pg.mediabox.width), float(pg.mediabox.height)
                )
        except Exception:
            page_sizes = {}
    layout = None
    if page_sizes:
        # 디지털 PDF는 글자 좌표를 원본에서 직독 — 잘림·중복·위치 오차 없음
        try:
            from zzaimy.app.pdf_lines import pdf_line_boxes
            from zzaimy.app.pipeline import DocumentProcessor

            if DocumentProcessor._pdf_has_text_layer(Path(doc["stored_path"])):
                line_pages = pdf_line_boxes(Path(doc["stored_path"]))
                if line_pages:
                    precise = [
                        ln for pg in sorted(line_pages) for ln in line_pages[pg]
                    ]
                    layout = layout_pages(
                        precise, doc_id, asset_by_name, page_sizes,
                        page_image_url=(
                            lambda pg: f"/doc/{doc_id}/page/{pg}.png"
                        ),
                    )
        except Exception:
            layout = None
    if layout is None and page_sizes:
        # 스캔 PDF — 파이프라인이 저장한 줄 단위 OCR 좌표로 투명 레이어
        lines_file = _paths.lines_dir(Path(db_path).parent) / f"{doc_id}.json"
        if lines_file.exists():
            try:
                import json as _ljson

                from zzaimy.app.pdf_lines import scale_ocr_lines

                items = scale_ocr_lines(
                    _ljson.loads(lines_file.read_text()), page_sizes
                )
                if len(items) >= 4:
                    layout = layout_pages(
                        items, doc_id, asset_by_name, page_sizes,
                        page_image_url=(
                            lambda pg: f"/doc/{doc_id}/page/{pg}.png"
                        ),
                    )
            except Exception:
                layout = None
    if layout is None and not page_sizes:
        # 사진 문서 — 보정 스캔본을 배경으로, OCR 줄 좌표를 투명 레이어로
        lines_file = _paths.lines_dir(Path(db_path).parent) / f"{doc_id}.json"
        if lines_file.exists() and Path(doc["stored_path"]).suffix.lower() in (
            ".png", ".jpg", ".jpeg",
        ):
            try:
                import json as _pj

                from zzaimy.app.pdf_lines import image_layout_from_lines

                items, img_sizes = image_layout_from_lines(
                    _pj.loads(lines_file.read_text())
                )
                bg_url = (
                    f"/doc/{doc_id}/asset/{scan_asset['id']}"
                    if scan_asset else f"/doc/{doc_id}/original"
                )
                if len(items) >= 2:
                    layout = layout_pages(
                        items, doc_id, asset_by_name, img_sizes,
                        page_image_url=lambda pg: bg_url,
                    )
            except Exception:
                layout = None
    if layout is None and chunks:
        layout = layout_pages(
            chunks, doc_id, asset_by_name, page_sizes or None,
            page_image_url=(
                (lambda pg: f"/doc/{doc_id}/page/{pg}.png")
                if page_sizes else None
            ),
        )
    import json as _json

    try:
        suggested = _json.loads(doc.get("suggested_criteria") or "[]")
    except _json.JSONDecodeError:
        suggested = []
    from zzaimy.app.regulations import criteria_link_evidence, evidenced_suggestions

    # 검색 유사도로 고른 추천은 같은 프로젝트·같은 사업 근거가 있을 때만 「자동 연결」로 보인다
    suggested = [x for x in evidenced_suggestions(db, doc, suggested) if _sees(x.get("id"))]
    referencing = [
        r for r in (db.referencing_documents(doc_id) if doc["doc_type"] == "regulation" else [])
        if (r.get("related_criteria_id") == doc_id or criteria_link_evidence(db, r, doc_id)) and _visible(r, **_dv)
    ]
    from zzaimy.app import doc_origin

    # 돌아갈 곳 — 진행 중 프로젝트 › 들어온 곳(from= · Referer) › 보관 묶음 · 기준 문서 · 라이브러리
    back = doc_origin.back_link(
        doc, db.get_project(int(doc["project_id"])) if doc.get("project_id") else None,
        request.query_params.get("from", ""), request.headers.get("referer", ""), request.url.netloc,
    )
    dgx_rel = (doc.get("stored_path") or "")[6:] if (doc.get("stored_path") or "").startswith("dgx://") else ""
    origin_place = None
    if dgx_rel:
        from zzaimy.app import archive as _archive

        _archive.ensure(db)
        with db._conn() as conn:
            row = conn.execute("SELECT program, program_name, year FROM archive_files WHERE rel = ?",
                               (dgx_rel,)).fetchone()
        origin_place = doc_origin.archive_location(
            dgx_rel, dict(zip(("program", "program_name", "year"), row)) if row else None)
    return templates.TemplateResponse(
        request,
        "doc.html",
        ctx(request, {
            "doc": doc, "reviews": db.get_reviews(doc_id), "related": related, "scope_choices": _scope_choices(request),
            # 한글 양식이면 「기안기로 작업」 — 교직원·관리자, 기안기 서버가 떠 있을 때
            "hwp_work": (storage.original_ext(doc) in (".hwp", ".hwpx") and request.state.role in ("dev", "staff", "professor", "head")
                         and __import__("zzaimy.app.webhwp", fromlist=["status"]).status()["ok"]),
            **_doc_folder_ctx(doc_id, request),
            "assets": assets,
            "extract_blocks": blocks, "layout_pages": layout,
            "restored_pdf": Path(doc["stored_path"]).suffix.lower()
            in (".pdf", ".png", ".jpg", ".jpeg"),
            "has_lines": (
                _paths.lines_dir(Path(db_path).parent) / f"{doc_id}.json"
            ).exists(),
            "box_pages": _box_page_list(doc_id),
            "scan_asset": scan_asset,
            "original_kind": original_kind,
            "original_ext": storage.original_ext(doc),
            # DGX 원본 보관소 문서(가벼운 처리) — 원본은 DGX 에 있고 글·조각만 여기에(ADR-0047)
            "dgx_origin": dgx_rel, "origin_place": origin_place, "back": back,
            "suggested_criteria": suggested,
            "referencing": referencing,
            "identity": db.get_doc_identity(doc_id),
            "same_program": [x for x in db.docs_sharing_program(doc_id) if _sees(x["id"])],
            "n_text_chunks": sum(1 for c in chunks if c["kind"] == "text"),
            "n_table_chunks": sum(1 for c in chunks if c["kind"] == "table"),
            "n_figtext_chunks": sum(1 for c in chunks if c["kind"] == "image_text"),
        }),
    )


@router.post("/doc/{doc_id}/decision")
def decide(request: Request, background: BackgroundTasks, doc_id: int, decision: str = Form(...)):
    h = request.app.state.doc_page
    db, processor = h.db, h.processor
    if db.get_document(doc_id) is None:
        raise HTTPException(404)
    if decision not in ("approved", "rejected", "rework"):
        raise HTTPException(400, f"알 수 없는 판정입니다: {decision}")
    db.update_document(doc_id, decision=decision)
    if decision == "rework":
        db.update_document(doc_id, status="processing")
        background.add_task(processor.reprocess, db, doc_id)
    return RedirectResponse(f"/doc/{doc_id}", status_code=303)


@router.post("/doc/{doc_id}/read-pages")
def read_pages(request: Request, background: BackgroundTasks, doc_id: int):
    """그림 쪽(글자층 없는 쪽) 판독 — 요청 시에만. 끝나면 조각이 더해지고 처리 기록에 남는다."""
    h = request.app.state.doc_page
    db, processor = h.db, h.processor
    if db.get_document(doc_id) is None:
        raise HTTPException(404)
    fn = getattr(processor, "read_image_pages", None)
    if fn:
        background.add_task(fn, db, doc_id)
    return RedirectResponse(f"/doc/{doc_id}?reading=1", status_code=303)


@router.post("/doc/{doc_id}/delete")
def delete_doc(request: Request, doc_id: int):
    h = request.app.state.doc_page
    db, CRITERIA_LIST, _drop_doc = h.db, h.CRITERIA_LIST, h._drop_doc
    # 라이브러리 문서는 여럿이 같이 쓰는 자료라 지우기는 관리자만(사용자 요청 10-17)
    if request.state.role != "dev":
        raise HTTPException(403, "문서 삭제는 관리자만 할 수 있습니다")
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    _drop_doc(doc)
    dest = CRITERIA_LIST if doc["doc_type"] == "regulation" else "/?type=all"
    return RedirectResponse(dest, status_code=303)


@router.post("/doc/{doc_id}/receipt")
def set_receipt(request: Request, doc_id: int, receipt_no: str = Form(...)):
    """접수번호 변경 — 연도-유형-일련(예 2026-국고-0004). 폴더 이름·파일 장부도 따라간다."""
    h = request.app.state.doc_page
    db, _RECEIPT_RE = h.db, h._RECEIPT_RE
    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    new = (receipt_no or "").strip()
    if not _RECEIPT_RE.match(new):
        return RedirectResponse(f"/doc/{doc_id}?err=receipt", status_code=303)
    try:
        db.set_receipt_no(doc_id, new)
    except ValueError:
        return RedirectResponse(f"/doc/{doc_id}?err=receipt_dup", status_code=303)
    try:
        storage.rename_intake_dir(db, doc_id)
    except Exception:
        logging.getLogger("zzaimy.app.main").warning("접수번호 변경 뒤 폴더 이름 바꾸기 실패 doc=%s", doc_id)
    return RedirectResponse(f"/doc/{doc_id}", status_code=303)


@router.post("/doc/{doc_id}/review")
def add_review(request: Request, doc_id: int, opinion: str = Form(...)):
    h = request.app.state.doc_page
    db = h.db
    if db.get_document(doc_id) is None:
        raise HTTPException(404)
    db.add_review(doc_id, opinion.strip())
    return RedirectResponse(f"/doc/{doc_id}", status_code=303)
