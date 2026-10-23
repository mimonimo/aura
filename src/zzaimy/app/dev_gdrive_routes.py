"""구글 드라이브 연결(/dev/gdrive/*) — 관리자가 클라이언트 ID·비밀을 넣고, 담당자가 자기 계정으로 한 번 허용한다(읽기 전용, ADR-0028).

- /dev 경로는 main.check_auth 가 개발자만 들인다. 되돌아오는 주소(/dev/gdrive/callback)만 예외로 모든 계정이 온다.
- DB·계정 표(accounts)·서비스 안 경로 판정식(local_path)은 main.create_app 이 app.state 에 올려 둔 것을 읽는다.
- 문서 반입 화면(app/dev_intake_routes)의 Google Drive 탭 주소 이동·되돌아올 주소 도우미를 함께 쓴다.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from zzaimy.app.dev_intake_routes import _gdrive_redirect_uri, _nas_redirect

router = APIRouter()


@router.post("/dev/gdrive/client")
def dev_gdrive_client(client_id: str = Form(""), client_secret: str = Form("")):
    from zzaimy.ingest import gdrive

    try:
        gdrive.set_client(client_id, client_secret)
    except ValueError as e:
        return _nas_redirect(str(e), ok=False)
    return _nas_redirect("구글 클라이언트를 저장했습니다 — 이제 계정 허용을 누르세요")


@router.get("/dev/gdrive/auth")
def dev_gdrive_auth(request: Request):
    from zzaimy.ingest import gdrive

    try:
        return RedirectResponse(gdrive.auth_url(_gdrive_redirect_uri(request), getattr(request.state, "user", "")), status_code=303)
    except ValueError as e:
        return _nas_redirect(str(e), ok=False)


@router.get("/dev/gdrive/callback")
def dev_gdrive_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    from zzaimy.ingest import gdrive

    from urllib.parse import quote as _q

    back = request.cookies.get("zz_google_back", "")
    back = back if request.app.state.local_path.fullmatch(back or "") else ""          # 쿠키 값도 서비스 안 경로만
    if getattr(request.state, "role", "") != "dev" and not back:
        back = "/settings"

    def done(msg: str, ok: bool = True):
        if not back:
            return _nas_redirect(msg, ok=ok)
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


@router.post("/dev/gdrive/revoke")
def dev_gdrive_revoke(request: Request, email: str = Form("")):
    from zzaimy.app.user_admin import unlink_google

    unlink_google(request.app.state.db, request.app.state.accounts, email.strip())            # 사용자·계정 화면의 연결 삭제와 같은 동작
    return _nas_redirect(f"구글 계정 {email} 연결을 지웠습니다 — 이 계정으로 이어진 사용자·부서 연결도 풀었습니다")
