"""구글 드라이브 연결 — 관리자가 클라이언트 ID·비밀을 넣고(/team/google/client), 담당자가 자기 계정으로 한 번 허용한다
(되돌아오는 주소 /dev/gdrive/callback, 읽기 전용, ADR-0028).

- 앱 등록은 화면(조직 관리)을 따라 /team/google/client — /dev 앞부분 막기를 받지 않으니 user_admin.need_admin 으로 관리자만.
- 되돌아오는 주소(/dev/gdrive/callback)는 구글 콘솔에 등록한 값이라 그대로 둔다 — main.check_auth 의 /dev 막기에서 예외로 모든 계정이 온다.
  옛 /dev/gdrive/client·/dev/gdrive/revoke 는 10-27 에 지웠다(연결 삭제는 user_admin 의 /team/google/revoke 한 곳).
- DB·계정 표(accounts)·서비스 안 경로 판정식(local_path)은 main.create_app 이 app.state 에 올려 둔 것을 읽는다.
- 앱 등록과 계정 목록은 조직 관리의 학교 계정 연결 현황(/team#google-links) — 관리자 동작은 그리로 되돌아간다.
"""
from __future__ import annotations

from urllib.parse import quote as _q

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from zzaimy.app.user_admin import need_admin
from zzaimy.app.user_admin import redirect_uri as _gdrive_redirect_uri

router = APIRouter()


def _users_redirect(msg: str, ok: bool = True) -> RedirectResponse:
    """관리자 동작 뒤 사용자·계정 화면의 「학교 계정 연결 현황」 으로."""
    return RedirectResponse(f"/team?{'ok' if ok else 'err'}={_q(msg)}#google-links", status_code=303)


@router.post("/team/google/client")
def team_google_client(request: Request, client_id: str = Form(""), client_secret: str = Form("")):
    need_admin(request)
    from zzaimy.ingest import gdrive

    try:
        gdrive.set_client(client_id, client_secret)
    except ValueError as e:
        return _users_redirect(str(e), ok=False)
    return _users_redirect("Google 앱 등록을 저장했습니다 — 사용자는 설정에서 학교 계정을 연결합니다")


@router.get("/dev/gdrive/callback")
def dev_gdrive_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    from zzaimy.ingest import gdrive

    back = request.cookies.get("zz_google_back", "")
    back = back if request.app.state.local_path.fullmatch(back or "") else ""          # 쿠키 값도 서비스 안 경로만
    if getattr(request.state, "role", "") != "dev" and not back:
        back = "/settings"

    def done(msg: str, ok: bool = True):
        if not back:
            return _users_redirect(msg, ok=ok)
        sep = "&" if "?" in back else "?"
        resp = RedirectResponse(f"{back}{sep}{'ok' if ok else 'err'}={_q(msg)}", status_code=303)
        resp.delete_cookie("zz_google_back")
        return resp

    if error or not code:
        return done(f"구글 허용이 취소됐습니다({error or '코드 없음'})", ok=False)
    purpose = gdrive.state_purpose(state)
    if purpose.startswith("dept:"):
        # 부서 드라이브 계정 — 로그인한 사람 본인 계정에 묶지 않고 부서에 잇는다(dept_drive_routes)
        from zzaimy.app.dept_drive_routes import finish_connect
        msg, good = finish_connect(request, purpose[5:], code, state, _gdrive_redirect_uri(request))
        return done(msg, ok=good)
    try:
        email = gdrive.exchange_code(code, state, _gdrive_redirect_uri(request), user=getattr(request.state, "user", ""), purpose="")
    except ValueError as e:
        return done(str(e), ok=False)
    from zzaimy.ingest import gdrive_files

    gdrive_files.bind_account(request.app.state.db, getattr(request.state, "user", ""), email)   # 허용을 누른 계정에 묶인다
    return done(f"구글 계정 {email} 을 연결했습니다 — 이 플랫폼 계정의 문서는 그 드라이브에 만들어집니다")
