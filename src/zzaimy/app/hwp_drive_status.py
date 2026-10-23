"""문서함(/hwp) 왼쪽 「구글 드라이브」 의 연결 상태 — 학교 구글 계정을 이었는지만 알려 준다.

계정이 없는 것은 오류가 아니라 상태다. 드라이브 목록(/api/hwp/drive/browse)은 계정이 없으면 409 로 답해
브라우저 콘솔에 실패가 남으므로, 화면은 먼저 이 상태를 물어(늘 200) 이어졌을 때만 목록을 부른다.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/api/hwp/drive/status")
def hwp_drive_status(request: Request) -> dict:
    """{"status": "ok" | "no_account", "account": 이은 계정(없으면 "")}. 교직원·관리자만(문서함과 같은 조건)."""
    from zzaimy.app.webhwp_routes import _need_staff
    from zzaimy.ingest import gdrive_files

    _need_staff(request)
    user = getattr(request.state, "user", "")
    email = gdrive_files.account_for(request.app.state.db, user, getattr(request.state, "dept", "") or None) or ""
    return {"status": "ok" if email else "no_account", "account": email}
