"""열린 기안기와 주고받는 명령 줄 — 에이전트가 넣은 기안기 명령을 편집 화면이 가져가 실행하고 결과를 돌려준다(webhwp_edit_ops B).

- GET  /hwp/{id}/cmd           편집 화면이 1~2초마다 — 새 명령(허용 단계만)을 가져간다. 묻는 시각이 「기안기 열림」 표시가 된다.
- POST /hwp/{id}/cmd/{cid}     실행 결과 {ok, message}.
- GET  /hwp/{id}/page-setup    지금 페이지 설정(mm) — 「페이지 설정」 창의 처음 값(파일 기준).
권한은 편집 화면과 같다(교직원·관리자, 열 수 있는 부서의 파일, 공통 양식은 관리자만).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from zzaimy.app import webhwp
from zzaimy.app import webhwp_edit_ops as eo
from zzaimy.app.webhwp_routes import _file, _need_staff

router = APIRouter()


def _editable(request: Request, fid: int) -> dict:
    _need_staff(request)
    f = _file(request, fid)
    if webhwp.locked(request.app.state.db, fid) and getattr(request.state, "role", "") != "dev":
        raise HTTPException(403, "공통 양식은 관리자만 고칠 수 있습니다")
    return f


@router.get("/hwp/{fid}/cmd")
def hwp_cmd_take(request: Request, fid: int):
    _editable(request, fid)
    db = request.app.state.db
    cmds = eo.take(db, fid, getattr(request.state, "user", ""))
    if cmds:                                   # 실행 전 파일을 판으로 — hwp_undo 로 되돌릴 수 있게
        eo.remember(db, Path(request.app.state.data_dir), fid)
    return {"cmds": cmds}


@router.post("/hwp/{fid}/cmd/{cid}")
async def hwp_cmd_report(request: Request, fid: int, cid: int):
    _editable(request, fid)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ok = eo.report(request.app.state.db, fid, cid, bool(body.get("ok")), str(body.get("message") or ""))
    return {"ok": ok}


@router.get("/hwp/{fid}/page-setup")
def hwp_page_setup(request: Request, fid: int):
    f = _editable(request, fid)
    try:
        cur = eo.current_page(Path(f["path"]).read_bytes())
    except (eo.EditError, OSError):
        cur = {}
    return {"current": cur, "papers": {k: list(v) for k, v in eo.PAPERS.items()}, "presets": eo.MARGIN_PRESETS}
