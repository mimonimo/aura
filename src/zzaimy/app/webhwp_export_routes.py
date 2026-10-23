"""기안기 편집 화면의 내려받기(여러 형식)·다른 이름으로 저장(부서 폴더·구글 드라이브)·이름 바꾸기 경로.

권한 검사는 webhwp_routes 의 것을 그대로 쓴다(교직원·관리자, 볼 수 있는 부서 문서만). 규칙은 app/webhwp_export.
"""
from __future__ import annotations

from urllib.parse import quote as _q

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from zzaimy.app import webhwp, webhwp_export
from zzaimy.app.webhwp_routes import EDIT_PAGE, _base, _body, _check_dept, _file, _google, _need_staff, _writable

router = APIRouter()


@router.get("/hwp/{fid}/export")
def hwp_export(request: Request, fid: int, fmt: str = "hwpx"):
    """저장된 판을 고른 형식으로 내려받기 — hwpx·hwp·pdf·docx(기안기 필터 서버로 바꾼다)."""
    _need_staff(request)
    f = _file(request, fid)
    from pathlib import Path
    from zzaimy.app import webhwp_coedit
    webhwp_coedit.fresh(request.app.state.db, Path(request.app.state.data_dir), fid)     # 공동 편집 칸 글을 먼저 파일에
    p = Path(f["path"])
    if not p.exists():
        raise HTTPException(404, "파일이 없습니다")
    fmt = (fmt or "").lower()
    if fmt not in webhwp_export.FORMATS:
        raise HTTPException(400, "내려받을 수 없는 형식입니다")
    try:
        data, name, mime = webhwp_export.export(p, fmt)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return Response(data, media_type=mime,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_q(name)}", "Cache-Control": "no-store"})


def _hwpx_body(data: bytes) -> bytes:
    if data[:2] != b"PK":
        raise HTTPException(400, "한글(HWPX) 파일이 아닙니다")
    return data


@router.post("/hwp/save-as")
async def hwp_save_as(request: Request, dept: str = "", folder: str = "", name: str = "", src: int | None = None):
    """다른 이름으로 저장(새 문서 만들기도) — 편집 중인 문서(HWPX 본문)를 부서 폴더에 새 파일로 들이고 그 주소를 돌려준다."""
    _need_staff(request)
    folder = _check_dept(request, dept, folder)
    if src is not None:
        _file(request, src)                          # 볼 수 있는 문서에서만 사본을 만든다
    data = _hwpx_body(await _body(request))
    try:
        stem = webhwp_export.clean_title(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    fid = webhwp.add(request.app.state.db, _base(request), dept, f"{stem}.hwpx", data,
                     owner=getattr(request.state, "user", ""), source=f"다른 이름:{src}" if src is not None else "new", folder=folder)
    return {"ok": True, "id": fid, "url": f"{EDIT_PAGE}?id={fid}", "name": webhwp.get(request.app.state.db, fid)["name"]}


@router.post("/hwp/save-as/drive")
async def hwp_save_as_drive(request: Request, name: str = "", folder: str = "", drive: str = "",
                            keep: int = 0, dept: str = "", dept_folder: str = ""):
    """다른 이름으로 저장 — 내 구글 드라이브(고른 폴더·공유 드라이브)에 hwpx 로 올린다.
    keep=1(새 문서 만들기)이면 부서 폴더(dept·dept_folder)에 작업본도 만들어 드라이브 자리를 이어 두고 그 주소를 돌려준다(자동 저장이 이어지게)."""
    _need_staff(request)
    from zzaimy.app import webhwp_drive
    from zzaimy.ingest import gdrive_files
    for v in (folder, drive):
        if v and not webhwp_drive._ID.fullmatch(v):
            raise HTTPException(400, "드라이브 id 가 올바르지 않습니다")
    if keep:
        dept_folder = _check_dept(request, dept, dept_folder)
    try:
        stem = webhwp_export.clean_title(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    data = _hwpx_body(await _body(request))
    email = _google(request)
    parent = folder or drive or ""
    try:
        up = gdrive_files.upload_file(email, data, f"{stem}.hwpx", webhwp_drive.HWP_MIME[".hwpx"], parent or None,
                                      http=webhwp_drive._http(), reuse=False)
    except (RuntimeError, PermissionError) as e:
        raise HTTPException(502, str(e))
    out = {"ok": True, "drive_url": up["url"], "name": up.get("name", f"{stem}.hwpx")}
    if keep:
        st = request.app.state
        fid = webhwp.add(st.db, _base(request), dept, f"{stem}.hwpx", data, owner=getattr(request.state, "user", ""),
                         source=f"drive:{up['id']}", folder=dept_folder)
        webhwp_export.link_drive(st.db, fid, up, parent, email)
        out.update(id=fid, url=f"{EDIT_PAGE}?id={fid}")
    return out


@router.post("/hwp/{fid}/rename")
async def hwp_rename(request: Request, fid: int):
    """이름 바꾸기 — 같은 폴더 안에서. 확장자는 그대로, 같은 이름이 있으면 「(2)」."""
    _need_staff(request)
    _file(request, fid)
    _writable(request, fid)
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        p = webhwp_export.rename(request.app.state.db, fid, str(body.get("name") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "name": p.name}
