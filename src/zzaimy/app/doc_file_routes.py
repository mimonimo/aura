"""문서 파일 열기·내려받기 — 구글에서 열기(/doc/{id}/view, 뒤 작업·기다림 화면), 원본 열기·미리보기(/doc/{id}/original, PDF 변환·DGX 원본
받기 뒤 작업)·원본 내려받기, 복원 뷰 쪽 그림·복원 PDF·추출 그림 묶음·인식 영역, 추출 그림·표 CSV, 내보내기(export.pdf·docx·md)와
초안 내려받기(/doc/{id}/draft.{fmt}).

- 문서 화면(/doc/{id})과 문서 고치기·판정·지우기는 app/doc_routes, 문서함 구글 열람 JSON(/api/doc/{id}/google)은 main 에 있다.
- DB·화면 틀과 열람 범위(_visible·_doc_scope)·계정표는 main.create_app 이 앱마다 만들어 문서 파일 묶음(app.state.doc_file —
  make_doc_file_helpers)에 넘긴다.
- 뒤 작업 표(구글 열람본 _VIEW_JOBS·원본 미리보기 _PREVIEW_JOBS)와 원본 받기·미리보기 만들기·생성 파일 보관 도우미는
  make_doc_file_helpers 안에 있다 — 앱마다 하나라 시험 앱끼리 작업 상태를 나누지 않는다.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote as _q

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from zzaimy.app import korean_text as _ko
from zzaimy.app import original_preview as _op
from zzaimy.app import paths as _paths
from zzaimy.app import storage

router = APIRouter()


def make_doc_file_helpers(
    *,
    db, db_path, templates, accounts, password, _visible,
    _doc_scope,
) -> SimpleNamespace:
    """앱마다 하나 — main.create_app 이 넘긴 값과 문서 파일 전용 도우미를 한 묶음(app.state.doc_file)으로."""
    deps = dict(locals())

    _VIEW_JOBS = _op.Jobs()                                # 문서 → 구글 열람본 만들기(뒤 작업)
    _PREVIEW_JOBS = _op.Jobs()                             # 문서 → 원본 열기 미리보기(PDF 변환·DGX 원본 받기)

    def _wait_page(request: Request, doc: dict, jobs, *, title: str, note: str, steps, open_url: str,
                   status_url: str, download_url: str = ""):
        """기다림 화면 — 구글 열람본과 원본 미리보기가 같이 쓴다. 상태 주소를 물어 단계·지난 시간·실패 사유를 그린다."""
        st = jobs.status(int(doc["id"]))
        return templates.TemplateResponse(request, "_wait.html", {
            "title": title, "filename": doc.get("filename") or "문서", "note": note,
            "steps": steps, "step": st["step"] or steps[0][0], "elapsed": st["elapsed"],
            "error": st["msg"] if st["state"] == "error" else "",
            "status_url": status_url, "retry_url": open_url, "back_url": f"/doc/{doc['id']}",
            "download_url": download_url,
        })

    def _original_named(doc: dict) -> tuple[Path, bool]:
        """_original_file 과 같되, 받아 온 임시 파일에는 원래 확장자를 붙인다 — 변환기는 확장자로 형식을 고른다."""
        path, is_temp = _original_file(doc)
        ext = storage.original_ext(doc)                    # 실제 확장자 — 표시 이름(본문 제목)에서 고르지 않는다
        if is_temp and ext and path.suffix.lower() != ext:
            named = path.with_name(path.name + ext)
            path.replace(named)
            path = named
        return path, is_temp

    def _make_google_view(doc: dict, email: str, folder: str, progress=None) -> dict:
        from zzaimy.ingest import gdrive_files

        temp = None
        try:
            src = doc
            if not (db.get_setting(f"doc_google:{doc['id']}", "") or ""):      # 이미 만든 열람본이면 원본을 받을 필요가 없다
                path, is_temp = _original_named(doc)
                temp = path if is_temp else None
                src = dict(doc, stored_path=str(path))
            return gdrive_files.google_copy(db, src, email, folder, progress=progress)
        finally:
            if temp is not None:
                Path(temp).unlink(missing_ok=True)

    def _keep_generated(doc: dict, payload, kind: str, ext: str) -> None:
        """내보낸 파일의 사본을 생성 폴더에 두고 장부에 적는다(ADR-0030). 실패해도 내려받기는 그대로."""
        try:
            data = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
            storage.save_generated(db, data, ref=doc.get("receipt_no") or f"문서-{doc['id']}", kind=kind, ext=ext,
                                   doc_id=int(doc["id"]))
        except Exception:
            import logging

            logging.getLogger("zzaimy.app.main").warning("생성 파일 보관 실패 doc=%s kind=%s", doc.get("id"), kind)

    def _archive_row(doc: dict) -> dict | None:
        """DGX 원본(dgx://) 문서의 보관소 대장 줄 — rel·size·mtime. 없으면 None."""
        sp = str(doc.get("stored_path") or "")
        if not sp.startswith("dgx://"):
            return None
        from zzaimy.app import archive

        archive.ensure(db)
        with db._conn() as conn:
            row = conn.execute("SELECT rel, size, mtime FROM archive_files WHERE rel = ? AND removed_at = ''",
                               (sp[len("dgx://"):],)).fetchone()
        return dict(zip(("rel", "size", "mtime"), row)) if row else None

    def _original_file(doc: dict) -> tuple[Path, bool]:
        """문서의 원본 파일 — (경로, 임시 파일인가). 플랫폼에 있으면 그것, DGX 원본(dgx://)이면 읽기 전용 연결로 한 개만 받아 온다.
        DGX 문서는 원본이 VM 에 없으므로 「원본 열기」·「구글에서 열기」가 이 함수로 받아 온다."""
        sp = str(doc.get("stored_path") or "")
        if sp and not sp.startswith("dgx://") and Path(sp).exists():
            return Path(sp), False
        row = _archive_row(doc)
        if row:
            from zzaimy.app import archive_original

            return archive_original.fetch(row), True
        raise FileNotFoundError("원본 파일을 찾을 수 없습니다")

    def _preview_target(doc: dict) -> tuple[Path | None, str]:
        """원본 열기에서 보여 줄 파일이 놓일 자리 — (경로, 종류). 플랫폼 사무 문서는 문서 폴더의 열람 PDF(반입 때 만드는 것과 같은 파일),
        DGX 원본은 캐시(사무 문서는 PDF, PDF·그림은 받아 온 원본 사본). 보여 줄 수 없는 형식이면 (None, 'download')."""
        from zzaimy.app import office_pdf

        kind = _op.kind_of(doc)                                # 형식은 실제 파일에서(storage.original_ext) — 제목에는 확장자가 없다
        sp = str(doc.get("stored_path") or "")
        if kind == "download":
            return None, kind
        if not sp.startswith("dgx://"):
            if kind == "office" and Path(sp).exists():
                return office_pdf.view_path(doc), kind
            return (Path(sp) if Path(sp).exists() else None), kind
        row = _archive_row(doc)
        if row is None:
            return None, kind
        ext = ".pdf" if kind == "office" else storage.original_ext(doc)
        version = f"{row['rel']}|{row['size']}|{row['mtime']}"
        return _op.cache_path(Path(db_path).parent, int(doc["id"]), version, ext), kind

    def _serve_preview(doc: dict, path: Path, kind: str):
        from fastapi.responses import FileResponse

        name = storage.original_name(doc)
        if kind == "office":                                   # 변환본은 「원래 이름.pdf」로 보인다
            ext = storage.original_ext(doc)
            return FileResponse(path, media_type="application/pdf", filename=(name[: -len(ext)] if ext else name) + ".pdf",
                                content_disposition_type="inline")
        return FileResponse(path, media_type=_op.media_type(doc), filename=name, content_disposition_type="inline")

    def _build_preview(doc: dict, target: Path, kind: str, set_step) -> None:
        """뒤 작업 — 원본 받기(DGX면) → 변환(사무 문서면) → 미리보기 자리에 둔다."""
        import shutil as _sh

        from zzaimy.app import office_pdf

        if kind == "office" and not office_pdf.soffice():          # 변환기가 없으면 원본을 받거나 바꾸기 전에 바로 알린다
            raise _op.ConvertFailed()
        set_step("fetch")
        sp = str(doc.get("stored_path") or "")
        if kind == "office" and not sp.startswith("dgx://"):
            if office_pdf.render(db, doc, progress=set_step) is None:
                raise _op.ConvertFailed()
            return
        path, is_temp = _original_named(doc)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if kind == "office":
                part = target.with_name(target.name + ".part")
                if office_pdf.convert_file(path, part, progress=set_step) is None:
                    raise _op.ConvertFailed()
                part.replace(target)
            else:
                set_step("render")
                part = target.with_name(target.name + ".part")
                if is_temp:
                    _sh.move(str(path), str(part))
                else:
                    _sh.copyfile(path, part)
                part.replace(target)
            _op.prune(target.parent, keep=target)
        finally:
            if is_temp:
                Path(path).unlink(missing_ok=True)

    return SimpleNamespace(
        **deps,
        _VIEW_JOBS=_VIEW_JOBS, _PREVIEW_JOBS=_PREVIEW_JOBS, _wait_page=_wait_page, _original_named=_original_named,
        _make_google_view=_make_google_view, _keep_generated=_keep_generated, _archive_row=_archive_row, _original_file=_original_file,
        _preview_target=_preview_target, _serve_preview=_serve_preview, _build_preview=_build_preview,
    )


@router.get("/doc/{doc_id}/view")
def doc_view(request: Request, doc_id: int):
    """문서를 구글에서 연다 — 엑셀·PPT·워드는 시트·슬라이드·독스, 한글은 복원 docx→독스, PDF·그림은 드라이브 미리보기.
        열람본이 있으면 바로 넘기고, 없으면 뒤에서 만들며 기다림 화면을 준다 — 큰 한글 파일은 원본 받기·변환·올리기에 몇 분이 걸려
        한 요청 안에서 만들면 브라우저가 끝없이 도는 것처럼 보인다. 허용 계정이 없으면 플랫폼 문서 화면으로."""
    h = request.app.state.doc_file
    db, accounts, password, _visible = h.db, h.accounts, h.password, h._visible
    _doc_scope, _VIEW_JOBS, _wait_page, _make_google_view = h._doc_scope, h._VIEW_JOBS, h._wait_page, h._make_google_view
    import json as _vj

    from zzaimy.ingest import gdrive_files

    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    owner = getattr(request.state, "user", "zzaimy")
    acct_ = accounts.get(owner, {}) if password is not None else {}
    email = gdrive_files.doc_account_for(db, owner, acct_.get("dept") or None)
    if not email or not gdrive_files.has_file_scope(email):
        return RedirectResponse(f"/doc/{doc_id}?view=local", status_code=303)
    if not _visible(doc, **_doc_scope(request)):
        raise HTTPException(404)
    saved = db.get_setting(f"doc_google:{doc_id}", "") or ""
    if saved:
        try:
            url = _vj.loads(saved).get("url")
            if url:
                return RedirectResponse(url, status_code=303)
        except ValueError:
            pass
    project = db.get_project(int(doc["project_id"])) if doc.get("project_id") else None

    def work(set_step):
        set_step("fetch")
        folder = gdrive_files.project_folder_for(db, email, project, acct_.get("dept") or None, sub="첨부")
        return _make_google_view(doc, email, folder, progress=set_step)["url"]

    def fail(e: Exception) -> str:
        if isinstance(e, (FileNotFoundError, RuntimeError, ValueError)):
            return f"구글 열람본을 만들지 못했습니다 — {e}"
        return f"구글 열람본을 만들지 못했습니다({_ko.reason(e)})"

    _VIEW_JOBS.start(doc_id, work, _op.STEPS_GOOGLE, fail)
    return _wait_page(request, doc, _VIEW_JOBS, title="구글에서 열 준비를 하고 있습니다",
                      note="원본을 받아 구글 문서로 바꾸는 중입니다. 큰 한글 파일은 몇 분 걸릴 수 있습니다. 이 창을 닫아도 작업은 계속됩니다.",
                      steps=_op.STEPS_GOOGLE, open_url=f"/doc/{doc_id}/view",
                      status_url=f"/doc/{doc_id}/view/status")


@router.get("/doc/{doc_id}/view/status")
def doc_view_status(request: Request, doc_id: int):
    h = request.app.state.doc_file
    db, _visible, _doc_scope, _VIEW_JOBS = h.db, h._visible, h._doc_scope, h._VIEW_JOBS
    doc = db.get_document(doc_id)
    if doc is None or not _visible(doc, **_doc_scope(request)):
        raise HTTPException(404)
    return _VIEW_JOBS.status(doc_id)


@router.get("/doc/{doc_id}/draft.{fmt}")
def draft_export(request: Request, doc_id: int, fmt: str):
    """초안 내보내기 — docx(한글 호환)·pdf·md."""
    h = request.app.state.doc_file
    db, _keep_generated = h.db, h._keep_generated
    from urllib.parse import quote as _q

    from fastapi.responses import Response

    doc = db.get_document(doc_id)
    if doc is None or not doc.get("draft"):
        raise HTTPException(404, "초안이 없습니다")
    stem = storage.title_of(doc["filename"] or "") or "draft"
    title = f"{stem} 계획서 초안"
    # 자료 문서의 추출 그림을 붙임으로 — 표·도표 근거를 파일에 동봉한다
    images = [
        a["path"] for a in db.list_doc_assets(doc_id)
        if a["kind"] == "image" and Path(a["path"]).exists()
    ][:6]
    if fmt == "md":
        payload: bytes | None = (
            f"# {title}\n\n{doc['draft']}\n"
        ).encode("utf-8")
        media = "text/markdown; charset=utf-8"
    elif fmt == "docx":
        from zzaimy.app.draft_export import build_draft_docx

        payload = build_draft_docx(title, doc["draft"], images=images)
        media = ("application/vnd.openxmlformats-officedocument"
                 ".wordprocessingml.document")
    elif fmt == "hwpx":
        from zzaimy.app.draft_export import build_draft_hwpx

        payload = build_draft_hwpx(title, doc["draft"], images=images)
        media = "application/hwp+zip"
    elif fmt == "pdf":
        from zzaimy.app.draft_export import build_draft_pdf

        payload = build_draft_pdf(title, doc["draft"], images=images)
        media = "application/pdf"
    else:
        raise HTTPException(404)
    if payload is None:
        raise HTTPException(500, "내보내기에 실패했습니다")
    _keep_generated(doc, payload, "초안", fmt)
    return Response(payload, media_type=media, headers={
        "Content-Disposition":
        "attachment; filename*=UTF-8''"
        + _q(f"{stem}_초안.{fmt}"),
    })


@router.get("/doc/{doc_id}/original")
def doc_original(request: Request, doc_id: int):
    """원본 열기 — 브라우저에서 바로 본다. PDF·그림·글은 원본 그대로, 한글·워드·엑셀·PPT 는 열람 PDF 로 바꿔서.
        변환·DGX 원본 받기는 뒤에서 하고 기다림 화면을 준다(큰 한글 파일은 몇 분). 만든 것은 남겨 두 번째부터 바로 열린다.
        보여 줄 수 없는 형식은 내려받기로 넘긴다. 파일은 통째로 메모리에 올리지 않고 흘려 보낸다(FileResponse)."""
    h = request.app.state.doc_file
    db, _visible, _doc_scope, _PREVIEW_JOBS = h.db, h._visible, h._doc_scope, h._PREVIEW_JOBS
    _wait_page, _preview_target, _serve_preview, _build_preview = h._wait_page, h._preview_target, h._serve_preview, h._build_preview
    doc = db.get_document(doc_id)
    if doc is None or not _visible(doc, **_doc_scope(request)):        # 원본도 열람 권한을 확인한다
        raise HTTPException(404)
    try:
        target, kind = _preview_target(doc)
    except Exception:
        target, kind = None, "download"
    if target is not None and target.is_file() and target.stat().st_size > 0:
        return _serve_preview(doc, target, kind)
    if target is None:
        return RedirectResponse(f"/doc/{doc_id}/original/download", status_code=303)
    from zzaimy.app import office_pdf

    def fail(e: Exception) -> str:
        if isinstance(e, _op.ConvertFailed):
            if not office_pdf.soffice():
                return "이 서버에 문서 변환기(LibreOffice)가 없어 미리보기를 만들 수 없습니다. 원본을 내려받아 열어 주세요."
            return "미리보기 PDF 를 만들지 못했습니다. 원본을 내려받아 열어 주세요."
        if isinstance(e, (FileNotFoundError, RuntimeError, ValueError)):
            return f"원본을 열 수 없습니다 — {e}"
        return f"미리보기를 만들지 못했습니다({_ko.reason(e)})"

    def work(set_step):
        _build_preview(doc, target, kind, set_step)
        return f"/doc/{doc_id}/original"                   # 다 만들면 같은 주소가 이제 미리보기를 바로 준다

    steps = _op.STEPS_PREVIEW if kind == "office" else _op.STEPS_FETCH
    _PREVIEW_JOBS.start(doc_id, work, steps, fail)
    note = ("원본을 PDF 로 바꿔 브라우저에서 볼 수 있게 만드는 중입니다. 큰 한글 파일은 몇 분 걸릴 수 있으며, 한 번 만들면 다음부터는 바로 열립니다."
            if kind == "office" else "원본 보관 서버에서 파일을 받아 오는 중입니다. 한 번 받으면 다음부터는 바로 열립니다.")
    return _wait_page(request, doc, _PREVIEW_JOBS, title="원본 미리보기를 준비하고 있습니다", note=note,
                      steps=steps, open_url=f"/doc/{doc_id}/original",
                      status_url=f"/doc/{doc_id}/original/status", download_url=f"/doc/{doc_id}/original/download")


@router.get("/doc/{doc_id}/original/status")
def doc_original_status(request: Request, doc_id: int):
    h = request.app.state.doc_file
    db, _visible, _doc_scope, _PREVIEW_JOBS = h.db, h._visible, h._doc_scope, h._PREVIEW_JOBS
    doc = db.get_document(doc_id)
    if doc is None or not _visible(doc, **_doc_scope(request)):
        raise HTTPException(404)
    return _PREVIEW_JOBS.status(doc_id)


@router.get("/doc/{doc_id}/original/download")
def doc_original_download(request: Request, doc_id: int):
    """원본 파일 그대로 내려받기. DGX 원본은 미리보기 때 받아 둔 사본이 있으면 그것을, 없으면 받아서 보낸 뒤 지운다."""
    h = request.app.state.doc_file
    db, _visible, _doc_scope, _original_file = h.db, h._visible, h._doc_scope, h._original_file
    _preview_target = h._preview_target
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask

    doc = db.get_document(doc_id)
    if doc is None or not _visible(doc, **_doc_scope(request)):
        raise HTTPException(404)
    name = storage.original_name(doc)                                 # 제목 + 실제 확장자
    try:
        cached, kind = _preview_target(doc)
    except Exception:
        cached, kind = None, "download"
    if (str(doc.get("stored_path") or "").startswith("dgx://") and kind == "inline"
            and cached is not None and cached.is_file()):
        return FileResponse(cached, filename=name, content_disposition_type="attachment")
    try:
        path, temp = _original_file(doc)
    except (FileNotFoundError, ValueError) as e:
        return RedirectResponse(f"/doc/{doc_id}?msg=" + _q(f"원본을 열 수 없습니다 — {e}"), status_code=303)
    except RuntimeError as e:                                          # DGX 연결 문제·동시 요청
        return RedirectResponse(f"/doc/{doc_id}?msg=" + _q(str(e)), status_code=303)
    return FileResponse(path, filename=name, content_disposition_type="attachment",
                        background=BackgroundTask(path.unlink, missing_ok=True) if temp else None)


@router.get("/doc/{doc_id}/page/{page_no}.png")
def doc_page_image(request: Request, doc_id: int, page_no: int):
    """원본 PDF 페이지 렌더 — 복원 뷰(원본 배치)의 배경. 디스크 캐시."""
    h = request.app.state.doc_file
    db, db_path = h.db, h.db_path
    from fastapi.responses import FileResponse

    doc = db.get_document(doc_id)
    if (
        doc is None
        or not Path(doc["stored_path"]).exists()
        or Path(doc["stored_path"]).suffix.lower() != ".pdf"
        or not (1 <= page_no <= 500)
    ):
        raise HTTPException(404)
    cache_dir = _paths.pagecache_dir(Path(db_path).parent)
    cache_dir.mkdir(exist_ok=True)
    out = cache_dir / f"{doc_id}-{page_no}.png"
    if not out.exists():
        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(doc["stored_path"])
            try:
                if page_no > len(pdf):
                    raise HTTPException(404)
                page = pdf[page_no - 1]
                scale = min(1520.0 / max(page.get_width(), 1.0), 2.2)
                page.render(scale=scale).to_pil().save(out)
            finally:
                pdf.close()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(404) from exc
    return FileResponse(out, media_type="image/png")


@router.get("/doc/{doc_id}/restored.pdf")
def doc_restored_pdf(request: Request, doc_id: int):
    """복원 문서 — OCR 레이어가 입혀진 PDF를 크롬 내장 뷰어로 바로 연다.

        디지털 PDF는 원본 그대로(이미 완전한 텍스트 레이어), 스캔 PDF는
        보정 페이지 + OCR 줄 레이어로 재조립, 사진은 보정 스캔 + 레이어.
        """
    h = request.app.state.doc_file
    db, db_path = h.db, h.db_path
    import json as _rj

    from fastapi.responses import FileResponse

    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    src = Path(doc["stored_path"])
    if not src.exists():
        raise HTTPException(404)
    suffix = src.suffix.lower()
    from zzaimy.app import office_pdf

    if office_pdf.is_office(src):
        out = office_pdf.render(db, doc)        # 반입 때 만든 열람 PDF, 없으면 지금 만든다
        if out is None:
            raise HTTPException(404, "열람 PDF 를 만들지 못했습니다")
        return FileResponse(out, media_type="application/pdf", content_disposition_type="inline")

    if suffix == ".pdf":
        try:
            from zzaimy.app.pipeline import DocumentProcessor

            if DocumentProcessor._pdf_has_text_layer(src):
                return FileResponse(  # 원본이 이미 완전한 전자 문서다
                    src, media_type="application/pdf",
                    content_disposition_type="inline",
                )
        except Exception:
            pass

    cache_dir = _paths.restored_dir(Path(db_path).parent)
    cache_dir.mkdir(exist_ok=True)
    cache = cache_dir / f"{doc_id}.pdf"
    if not cache.exists():
        payload: bytes | None = None
        lines_file = _paths.lines_dir(Path(db_path).parent) / f"{doc_id}.json"
        lines_payload = (
            _rj.loads(lines_file.read_text()) if lines_file.exists() else None
        )
        if suffix == ".pdf":
            from zzaimy.app.pdf_layer import (
                build_restored_scan_pdf,
                build_searchable_pdf,
            )

            if lines_payload:
                from pypdf import PdfReader

                from zzaimy.app.pdf_lines import scale_ocr_lines

                sizes = {
                    i: (float(p.mediabox.width), float(p.mediabox.height))
                    for i, p in enumerate(PdfReader(str(src)).pages, start=1)
                }
                payload = build_restored_scan_pdf(
                    src, scale_ocr_lines(lines_payload, sizes), sizes
                )
            if payload is None:
                payload = build_searchable_pdf(src, db.list_doc_chunks(doc_id))
        elif suffix in (".png", ".jpg", ".jpeg"):
            from zzaimy.app.pdf_layer import build_restored_photo_pdf, build_scan_pdf

            scan = next(
                (a for a in db.list_doc_assets(doc_id)
                 if a["kind"] == "scan" and Path(a["path"]).exists()),
                None,
            )
            img = Path(scan["path"]) if scan else src
            if lines_payload:
                full_text = "\n".join(
                    c["content"] for c in db.list_doc_chunks(doc_id)
                    if c["kind"] in ("text", "heading")
                )
                payload = build_restored_photo_pdf(
                    img, lines_payload, full_text=full_text
                )
            if payload is None:
                payload = build_scan_pdf(img, db.list_doc_chunks(doc_id))
        if payload is None:
            raise HTTPException(400, "복원 PDF를 만들 수 없는 형식입니다")
        cache.write_bytes(payload)
    # FileResponse — Range 요청 지원으로 뷰어가 필요한 부분만 스트리밍
    return FileResponse(
        cache, media_type="application/pdf",
        content_disposition_type="inline", filename="restored.pdf",
    )


@router.get("/doc/{doc_id}/images.zip")
def doc_images_zip(request: Request, doc_id: int):
    """추출 그림 일괄 내려받기 — 문서 속 사진·도표 이미지를 zip 하나로."""
    h = request.app.state.doc_file
    db = h.db
    import io as _io
    import zipfile
    from urllib.parse import quote as _q

    from fastapi.responses import Response

    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    assets = [
        a for a in db.list_doc_assets(doc_id)
        if a["kind"] != "scan" and Path(a["path"]).exists()
    ]
    if not assets:
        raise HTTPException(404, "추출된 그림이 없습니다")
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, a in enumerate(assets, start=1):
            p = Path(a["path"])
            zf.write(p, f"p{a.get('page_no') or 0:03d}_{i:02d}{p.suffix}")
    stem = storage.title_of(doc["filename"] or "") or "document"
    return Response(
        buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{_q(stem)}_images.zip"},
    )


@router.get("/doc/{doc_id}/asset/{asset_id}")
def doc_asset(request: Request, doc_id: int, asset_id: int, dl: int = 0):
    h = request.app.state.doc_file
    db = h.db
    from fastapi.responses import FileResponse

    asset = next(
        (a for a in db.list_doc_assets(doc_id) if a["id"] == asset_id), None
    )
    if asset is None or not Path(asset["path"]).exists():
        raise HTTPException(404)
    if dl:
        doc = db.get_document(doc_id) or {}
        stem = storage.title_of(doc.get("filename") or "") or "문서"
        name = f"{stem}_그림{asset_id}{Path(asset['path']).suffix}"
        return FileResponse(asset["path"], filename=name)
    return FileResponse(asset["path"])


@router.get("/doc/{doc_id}/table/{chunk_id}.csv")
def doc_table_csv(request: Request, doc_id: int, chunk_id: int):
    h = request.app.state.doc_file
    db = h.db
    from fastapi.responses import Response

    from zzaimy.app.render import table_csv

    chunk = next(
        (c for c in db.list_doc_chunks(doc_id)
         if c["id"] == chunk_id and c["kind"] == "table"),
        None,
    )
    if chunk is None:
        raise HTTPException(404)
    try:
        csv_text = table_csv(chunk["content"])
    except Exception:
        raise HTTPException(404, "표 구조를 읽을 수 없습니다") from None
    return Response(
        "\ufeff" + csv_text,  # BOM — 엑셀에서 한글 깨짐 방지
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="table_{doc_id}_{chunk_id}.csv"'},
    )


@router.get("/doc/{doc_id}/export.pdf")
def doc_export_pdf(request: Request, doc_id: int):
    h = request.app.state.doc_file
    db, db_path, _keep_generated = h.db, h.db_path, h._keep_generated
    from urllib.parse import quote

    from fastapi.responses import Response

    from zzaimy.app.pdf_layer import build_scan_pdf, build_searchable_pdf

    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    chunks = db.list_doc_chunks(doc_id)
    if not chunks:
        raise HTTPException(404, "추출 조각이 없습니다")
    src = Path(doc["stored_path"])
    suffix = src.suffix.lower()
    payload: bytes | None = None
    if suffix == ".pdf" and src.exists():
        # 줄 단위 좌표가 있으면 그것으로 — 선택·검색 위치가 정확하다
        line_items: list[dict] = []
        try:
            from zzaimy.app.pdf_lines import pdf_line_boxes, scale_ocr_lines
            from zzaimy.app.pipeline import DocumentProcessor

            if DocumentProcessor._pdf_has_text_layer(src):
                lp = pdf_line_boxes(src)
                line_items = [
                    ln for pg in sorted(lp) for ln in lp[pg]
                ]
            else:
                lf = _paths.lines_dir(Path(db_path).parent) / f"{doc_id}.json"
                if lf.exists():
                    import json as _ej

                    from pypdf import PdfReader

                    sizes = {
                        i: (float(p.mediabox.width), float(p.mediabox.height))
                        for i, p in enumerate(
                            PdfReader(str(src)).pages, start=1
                        )
                    }
                    line_items = scale_ocr_lines(
                        _ej.loads(lf.read_text()), sizes
                    )
        except Exception:
            line_items = []
        payload = build_searchable_pdf(
            src, line_items if len(line_items) >= 4 else chunks
        )
    elif suffix in (".png", ".jpg", ".jpeg") and src.exists():
        scan = next(
            (a for a in db.list_doc_assets(doc_id)
             if a["kind"] == "scan" and Path(a["path"]).exists()),
            None,
        )
        payload = build_scan_pdf(
            Path(scan["path"]) if scan else src, chunks
        )
    if payload is None:
        raise HTTPException(400, "이 문서 형식은 PDF 레이어를 만들 수 없습니다")
    stem = storage.title_of(doc["filename"] or "") or src.stem
    fname = quote(f"{stem}_OCR.pdf")
    _keep_generated(doc, payload, "OCR", "pdf")
    return Response(
        payload, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )


@router.get("/doc/{doc_id}/export.docx")
def doc_export_docx(request: Request, doc_id: int):
    h = request.app.state.doc_file
    db, _keep_generated = h.db, h._keep_generated
    from urllib.parse import quote

    from fastapi.responses import Response

    from zzaimy.app.render import build_docx

    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    chunks = db.list_doc_chunks(doc_id)
    if not chunks:
        raise HTTPException(404, "추출 조각이 없습니다")
    asset_paths = {
        Path(a["path"]).name: a["path"]
        for a in db.list_doc_assets(doc_id)
        if Path(a["path"]).exists()
    }
    payload = build_docx(
        doc["filename"], chunks, asset_paths,
        extra_images=list(asset_paths.values()),
    )
    fname = quote(f"{storage.title_of(doc['filename'] or '') or '문서'}_복원.docx")
    _keep_generated(doc, payload, "복원", "docx")
    return Response(
        payload,
        media_type=("application/vnd.openxmlformats-officedocument"
                    ".wordprocessingml.document"),
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )


@router.get("/doc/{doc_id}/export.md")
def doc_export_md(request: Request, doc_id: int):
    h = request.app.state.doc_file
    db, _keep_generated = h.db, h._keep_generated
    from urllib.parse import quote

    from fastapi.responses import Response

    from zzaimy.app.render import export_markdown

    doc = db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404)
    chunks = db.list_doc_chunks(doc_id)
    if not chunks:
        raise HTTPException(404, "추출 조각이 없습니다")
    md = export_markdown(doc["filename"], chunks)
    fname = quote(f"{storage.title_of(doc['filename'] or '') or '문서'}_추출결과.md")
    _keep_generated(doc, md, "추출결과", "md")
    return Response(
        md, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{fname}"},
    )
