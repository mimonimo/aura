"""읽지 못한 문서 다시 하기 — 문서 화면·목록의 단추가 부르는 길(사유 갈래와 단추는 app/read_failure.info).

- /doc/{id}/reread   다시 읽기(같은 파일로 한 번 더)
- /doc/{id}/replace  파일 바꾸기(암호를 푼 파일·다른 파일) — 같은 문서 자리에서 다시 읽는다
- /doc/{id}/ocr      글자 읽기 맡기기(스캔본 → 글자 읽기 대기열)
- /doc/{id}/resave   기안기로 열어 다시 저장(한글) — 기안기 서버로 hwpx 로 다시 저장한 파일로 바꾸고 다시 읽는다
- /doc/{id}/discard  지우기 — 읽지 못한 문서만, 올린 사람도(읽은 문서 지우기는 관리자만인 규칙과 따로)
권한(볼 수 있는가·바꿀 수 있는가)은 앱 인증 단계의 /doc/{id} 경로 검사가 한 번에 맡는다.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse

from zzaimy.app import read_failure

router = APIRouter()
log = logging.getLogger(__name__)


def _failed(request: Request, doc_id: int) -> dict:
    doc = request.app.state.db.get_document(doc_id)
    if doc is None:
        raise HTTPException(404, "문서를 찾을 수 없습니다")
    if doc.get("status") != "failed":
        raise HTTPException(409, "이미 읽은 문서입니다")
    return doc


def _back(doc_id: int, back: str = "", **msg) -> RedirectResponse:
    dest = back if back.startswith("/") and not back.startswith("//") else f"/doc/{doc_id}"
    if msg:
        dest += ("&" if "?" in dest else "?") + urlencode(msg)
    return RedirectResponse(dest, status_code=303)


def _process(db, processor, doc_id: int, path: Path) -> None:
    """다시 읽기 — DGX 원본(dgx://)은 학교 원본에서 하나 받아 읽고 원본 자리 표시를 되돌린다."""
    doc = db.get_document(doc_id) or {}
    sp = str(doc.get("stored_path") or "")
    if not sp.startswith("dgx://"):
        processor.process(db, doc_id, path)
        return
    import shutil
    import tempfile
    from zzaimy.app import archive, archive_original

    archive.ensure(db)
    with db._conn() as conn:
        row = conn.execute("SELECT rel, size, mtime FROM archive_files WHERE rel = ? AND removed_at = ''", (sp[6:],)).fetchone()
    tmp = Path(tempfile.mkdtemp(prefix="zz-reread-"))
    try:
        if not row:
            raise FileNotFoundError("학교 원본에서 파일을 찾지 못했습니다")
        local = tmp / Path(sp[6:]).name
        shutil.move(str(archive_original.fetch(dict(zip(("rel", "size", "mtime"), row)))), str(local))
        processor.process(db, doc_id, local)
    except Exception as e:
        db.update_document(doc_id, status="failed", error=f"{type(e).__name__}: {e}")
    finally:
        db.set_stored_path(doc_id, sp)
        shutil.rmtree(tmp, ignore_errors=True)


@router.post("/doc/{doc_id}/reread")
def reread(request: Request, background: BackgroundTasks, doc_id: int, back: str = Form("")):
    doc = _failed(request, doc_id)
    st = request.app.state
    read_failure.dequeue(st.db, doc_id)
    st.db.update_document(doc_id, status="processing")
    background.add_task(_process, st.db, st.processor, doc_id, Path(str(doc.get("stored_path") or "")))
    return _back(doc_id, back)


@router.post("/doc/{doc_id}/replace")
def replace(request: Request, background: BackgroundTasks, doc_id: int, file: UploadFile = File(...), back: str = Form("")):
    from zzaimy.app.main import ALLOWED_EXTENSIONS

    doc = _failed(request, doc_id)
    if str(doc.get("stored_path") or "").startswith("dgx://"):
        raise HTTPException(400, "학교 원본 문서는 파일을 바꾸지 않습니다")
    name = Path(file.filename or "").name
    if not name or Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        return _back(doc_id, back, fix_err=f"올릴 수 없는 형식입니다{(' — ' + Path(name).suffix) if Path(name).suffix else ''}")
    st = request.app.state
    path = read_failure.replace_file(st.db, doc, name, file.file)
    st.db.update_document(doc_id, status="processing")
    background.add_task(st.processor.process, st.db, doc_id, path)
    return _back(doc_id, back)


@router.post("/doc/{doc_id}/ocr")
def ocr(request: Request, doc_id: int, back: str = Form("")):
    _failed(request, doc_id)
    st = request.app.state
    read_failure.start_worker(st.db, st.processor)
    read_failure.queue_ocr(st.db, doc_id, by=getattr(request.state, "user", "") or "")
    return _back(doc_id, back)


def _resave_then_read(db, processor, doc_id: int) -> None:
    doc = db.get_document(doc_id) or {}
    old_err = str(doc.get("error") or "")
    try:
        out = read_failure.resave_hwp(Path(str(doc.get("stored_path") or "")))
    except Exception as e:
        db.update_document(doc_id, status="failed", error=f"{old_err} · 기안기로 다시 저장하지 못했습니다({e})"[:600])
        return
    name = Path(str(doc.get("filename") or "문서")).stem or "문서"
    with out.open("rb") as fh:
        path = read_failure.replace_file(db, doc, name + ".hwpx", fh)
    out.unlink(missing_ok=True)
    processor.process(db, doc_id, path)


@router.post("/doc/{doc_id}/resave")
def resave(request: Request, background: BackgroundTasks, doc_id: int, back: str = Form("")):
    from zzaimy.app import storage

    doc = _failed(request, doc_id)
    if storage.original_ext(doc) not in read_failure.HWP_EXT or str(doc.get("stored_path") or "").startswith("dgx://"):
        raise HTTPException(400, "한글 파일(hwp·hwpx)만 기안기로 다시 저장합니다")
    st = request.app.state
    read_failure.dequeue(st.db, doc_id)
    st.db.update_document(doc_id, status="processing")
    background.add_task(_resave_then_read, st.db, st.processor, doc_id)
    return _back(doc_id, back)


@router.post("/doc/{doc_id}/discard")
def discard(request: Request, doc_id: int, back: str = Form("")):
    doc = _failed(request, doc_id)
    if str(doc.get("stored_path") or "").startswith("dgx://"):
        raise HTTPException(400, "학교 원본 문서는 관리자가 원본 관리에서 다룹니다")
    st = request.app.state
    st.doc_page._drop_doc(doc)
    read_failure.dequeue(st.db, doc_id)
    dest = back if back.startswith("/") and not back.startswith("//") and not back.startswith(f"/doc/{doc_id}") else "/?type=all"
    return RedirectResponse(dest + ("&" if "?" in dest else "?") + "ok=" + quote("읽지 못한 문서를 지웠습니다"), status_code=303)
