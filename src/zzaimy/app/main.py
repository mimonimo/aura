"""문서 접수·검토 플랫폼 — FastAPI 앱 (대시보드 v0.1).

보안: 127.0.0.1에만 바인딩하고 원격 접속은 SSH 터널로 한다 (docs/risks.md §8).
실행(Spark):
    .venv/bin/python -m zzaimy.app.main
접속(맥):
    ssh -N -L 8800:localhost:8800 jun@<spark> 후 http://localhost:8800
"""

from __future__ import annotations

import secrets
import shutil
import uuid
from pathlib import Path
from typing import Protocol

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from zzaimy.app.db import Database

ALLOWED_EXTENSIONS = {
    ".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".xls", ".xlsx",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".txt", ".md",
}

_TEMPLATES_DIR = Path(__file__).parent / "templates"

STATUS_LABELS = {
    "received": "접수됨",
    "processing": "처리 중",
    "reviewed": "검토 완료",
    "failed": "실패",
}

# 문서함(검토 대상) 유형 — 기준 문서(regulation)는 별도 공간에서 다룬다
INBOX_TYPES = {
    "auto": "일반 행정",
    "grant": "국고사업",
    "recruit": "채용",
    "admission": "입학",
}
DOC_TYPE_LABELS = {**INBOX_TYPES, "regulation": "기준 문서", "ocr": "문서 추출"}

SECTOR_LABELS = {
    "common": "공통",
    "grant": "국고사업",
    "recruit": "채용",
    "admission": "입학",
    "auto": "일반 행정",
}

DECISION_LABELS = {
    "pending": "판정 대기",
    "approved": "승인",
    "rejected": "반려",
    "rework": "재검토 중",
}


class Processor(Protocol):
    def process(self, db: Database, doc_id: int, file_path: Path) -> None: ...

    def reprocess(self, db: Database, doc_id: int) -> None: ...

    def extract_text(self, file_path: Path) -> str: ...

    def analyze(self, db: Database, doc_id: int) -> None: ...


class Drafter(Protocol):
    def generate(self, db: Database, doc_id: int) -> None: ...


class Responder(Protocol):
    def answer(
        self,
        db: Database,
        question: str,
        attachment_text: str | None = None,
        criteria_ids: list[int] | None = None,
        session_id: int | None = None,
        project: dict | None = None,
    ) -> str: ...


def create_app(
    db_path: Path,
    inbox_dir: Path,
    processor: Processor,
    drafter: Drafter,
    responder: Responder | None = None,
    password: str | None = None,
) -> FastAPI:
    """password를 주면 전 라우트에 HTTP Basic 인증(사용자명 zzaimy)이 걸린다.

    비밀번호 없이 외부 바인딩(0.0.0.0)하는 조합은 main()에서 거부한다.
    """
    dependencies = []
    # 세션 비밀키는 디스크에 영속화 — 앱을 재시작(배포)해도 로그인이 유지된다
    import hashlib
    import hmac as _hmac
    import time as _time

    secret_path = Path(db_path).parent / ".session_secret"
    try:
        session_secret = secret_path.read_text().strip()
        if len(session_secret) < 32:
            raise ValueError
    except (OSError, ValueError):
        session_secret = secrets.token_hex(32)
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret_path.write_text(session_secret)
        secret_path.chmod(0o600)

    def _make_session(days: int = 7) -> str:
        exp = str(int(_time.time()) + days * 86400)
        sig = _hmac.new(
            session_secret.encode(), exp.encode(), hashlib.sha256
        ).hexdigest()
        return f"{exp}.{sig}"

    def _session_valid(token: str) -> bool:
        exp, _, sig = token.partition(".")
        if not exp.isdigit() or not sig:
            return False
        good = _hmac.new(
            session_secret.encode(), exp.encode(), hashlib.sha256
        ).hexdigest()
        return secrets.compare_digest(sig, good) and int(exp) > _time.time()

    if password is not None:
        basic = HTTPBasic(auto_error=False)

        def check_auth(
            request: Request,
            cred: HTTPBasicCredentials | None = Depends(basic),
        ) -> None:
            # 브라우저는 로그인 페이지의 세션 쿠키로, 스크립트·API는 Basic으로
            if request.url.path == "/login" or request.url.path.startswith("/static"):
                return
            if _session_valid(request.cookies.get("zz_session", "")):
                return
            if cred is not None:
                # 바이트 비교 — compare_digest는 비ASCII 문자열을 받지 못한다
                ok = secrets.compare_digest(
                    cred.username.encode(), b"zzaimy"
                ) and secrets.compare_digest(cred.password.encode(), password.encode())
                if ok:
                    return
                raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
            # 인증 정보가 아예 없는 브라우저 요청 — 로그인 페이지로
            raise HTTPException(401, detail="login-required")

        dependencies = [Depends(check_auth)]

    app = FastAPI(title="YNC 행정문서 검토 플랫폼", dependencies=dependencies)

    from fastapi import status as _status
    from fastapi.responses import JSONResponse

    @app.exception_handler(HTTPException)
    async def _auth_redirect(request: Request, exc: HTTPException):
        # 세션 없는 브라우저 접근은 로그인 페이지로 보낸다
        if exc.status_code == 401 and exc.detail == "login-required":
            return RedirectResponse("/login", status_code=_status.HTTP_303_SEE_OTHER)
        return JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).parent / "static")),
        name="static",
    )
    db = Database(db_path)
    app.state.db = db  # 테스트·운영 점검에서 접근할 수 있게 노출
    inbox_dir.mkdir(parents=True, exist_ok=True)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.globals["status_labels"] = STATUS_LABELS
    templates.env.globals["doc_type_labels"] = DOC_TYPE_LABELS
    templates.env.globals["decision_labels"] = DECISION_LABELS
    templates.env.globals["sector_labels"] = SECTOR_LABELS

    import re as _re

    from markupsafe import Markup, escape

    def md_lite(text: str) -> Markup:
        """이스케이프 후 **볼드**만 살리는 최소 마크다운."""
        escaped = str(escape(text))
        return Markup(_re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped))

    templates.env.filters["md_lite"] = md_lite

    def ctx(extra: dict) -> dict:
        pending = db.pending_documents(limit=50)
        by_type: dict[str, int] = {}
        for d in pending:
            by_type[d["doc_type"]] = by_type.get(d["doc_type"], 0) + 1
        failed = db.failed_documents()
        return {
            "chat_sessions": db.list_chat_sessions(),
            "pending_docs": pending[:8],
            "pending_count": len(pending),
            "pending_by_type": by_type,
            "failed_docs": failed,
            "alert_count": len(pending) + len(failed),
            "side_projects": db.list_all_projects(),
            "profile_name": db.get_setting("name"),
            "profile_dept": db.get_setting("dept"),
            **extra,
        }

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        type: str | None = None,
        q: str | None = None,
        project: int | None = None,
        flt: str | None = None,
    ):
        doc_type = type if type in INBOX_TYPES else None
        all_docs = [
            d for d in db.list_documents(doc_type, q=q, project_id=project)
            if d["doc_type"] not in ("regulation", "ocr")
        ]
        stats = {
            "total": len(all_docs),
            "processing": sum(1 for d in all_docs if d["status"] in ("received", "processing")),
            "reviewed": sum(1 for d in all_docs if d["status"] == "reviewed"),
            "pending": sum(
                1 for d in all_docs
                if d["status"] == "reviewed" and d["decision"] == "pending"
            ),
        }
        docs = all_docs
        if flt == "processing":
            docs = [d for d in all_docs if d["status"] in ("received", "processing")]
        elif flt == "reviewed":
            docs = [d for d in all_docs if d["status"] == "reviewed"]
        elif flt == "pending":
            docs = [
                d for d in all_docs
                if d["status"] == "reviewed" and d["decision"] == "pending"
            ]
        else:
            flt = None
        projects = db.list_projects(doc_type) if doc_type else []
        recent = db.recent_activity() if doc_type is None else []
        # 섹터 화면에서는 그 섹터의 기준 문서(공고 등)를 접수 대상 선택지로 제공
        sector_criteria = []
        if doc_type:
            sector_criteria = [
                d for d in db.list_documents("regulation")
                if d["status"] == "reviewed" and d["sector"] == doc_type
            ]
        return templates.TemplateResponse(
            request,
            "index.html",
            ctx({
                "documents": docs, "active_tab": doc_type or "all", "q": q or "",
                "sector_criteria": sector_criteria,
                "projects": projects, "active_project": project,
                "stats": stats, "active_flt": flt, "recent_activity": recent,
            }),
        )

    def _criteria_docs() -> list[dict]:
        counts = db.regulation_chunk_counts()
        return [
            d | {"n_chunks": counts.get(d["id"], 0)}
            for d in db.list_documents("regulation")
            if d["status"] == "reviewed"
        ]

    @app.get("/chat", response_class=HTMLResponse)
    def chat_new(request: Request):
        return templates.TemplateResponse(
            request,
            "chat.html",
            ctx({
                "messages": [], "criteria_docs": _criteria_docs(),
                "waiting": False, "session_id": None,
            }),
        )

    @app.get("/chat/{session_id}", response_class=HTMLResponse)
    def chat_session(request: Request, session_id: int):
        messages = db.list_chats(session_id)
        waiting = bool(messages) and messages[-1]["role"] == "user"
        return templates.TemplateResponse(
            request,
            "chat.html",
            ctx({
                "messages": messages, "criteria_docs": _criteria_docs(),
                "waiting": waiting, "session_id": session_id,
            }),
        )

    @app.get("/doc/{doc_id}/status")
    def doc_status(doc_id: int):
        doc = db.get_document(doc_id)
        if doc is None:
            raise HTTPException(404)
        return {
            "processing": doc["status"] in ("received", "processing"),
            "drafting": (doc.get("coverage") or "").startswith(("초안 작성 중", "분석 중")),
        }

    @app.post("/doc/{doc_id}/analyze")
    def doc_analyze(background: BackgroundTasks, doc_id: int):
        doc = db.get_document(doc_id)
        if doc is None:
            raise HTTPException(404)
        if doc["doc_type"] not in ("ocr", "regulation"):
            raise HTTPException(400, "맥락 분석은 문서 추출·기준 문서에서 지원한다")
        db.update_document(doc_id, coverage="분석 중입니다 (30초~1분)")
        background.add_task(processor.analyze, db, doc_id)
        return RedirectResponse(f"/doc/{doc_id}", status_code=303)

    @app.get("/chat/{session_id}/status")
    def chat_status(session_id: int):
        messages = db.list_chats(session_id)
        return {"waiting": bool(messages) and messages[-1]["role"] == "user"}

    def _answer_task(
        session_id: int, q: str, stored: Path | None, criteria: list[int]
    ) -> None:
        # 전송 직후 화면을 돌려주기 위해 무거운 단계(첨부 파싱·LLM)는 백그라운드에서
        attachment_text = None
        if stored is not None:
            try:
                attachment_text = processor.extract_text(stored)
            except Exception as e:
                log_note = f"(첨부 처리 실패: {type(e).__name__})"
                db.add_chat(session_id, "assistant", f"첨부 문서를 읽지 못했습니다 {log_note}")
                return
        r = responder or _default_responder()
        # 프로젝트에 묶인 세션이면 지침·메모를 맥락으로, 연결 기준을 기본 근거로 쓴다
        session = db.get_chat_session(session_id)
        project = None
        if session and session.get("project_id"):
            project = db.get_project(int(session["project_id"]))
            if project and not criteria:
                criteria = db.get_project_criteria_ids(project["id"])
        try:
            answer = r.answer(
                db, q, attachment_text=attachment_text, criteria_ids=criteria,
                session_id=session_id, project=project,
            )
        except Exception as e:
            answer = f"응답 생성에 실패했습니다: {type(e).__name__}"
        db.add_chat(session_id, "assistant", answer)

    @app.post("/chat/send")
    def chat_send(
        background: BackgroundTasks,
        question: str = Form(...),
        session_id: int | None = Form(None),
        criteria: list[int] = Form([]),
        attachment: UploadFile | None = File(None),
        project_id: int | None = Form(None),
    ):
        q = question.strip()
        if not q:
            return RedirectResponse("/chat", status_code=303)
        if session_id is None:
            title = q
            if project_id and (proj := db.get_project(project_id)):
                title = f"[{proj['name'][:14]}] {q}"
            else:
                project_id = None
            session_id = db.create_chat_session(title=title, project_id=project_id)
        # 응답 대기 중 중복 전송 방지 — 마지막 메시지가 아직 답변 전이면 무시
        last = db.list_chats(session_id, limit=1)
        if last and last[-1]["role"] == "user":
            return RedirectResponse(f"/chat/{session_id}", status_code=303)

        stored: Path | None = None
        shown = q
        if attachment is not None and attachment.filename:
            suffix = Path(attachment.filename).suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                raise HTTPException(400, f"허용되지 않는 파일 형식: {suffix}")
            stored = inbox_dir / f"chat_{uuid.uuid4().hex}{suffix}"
            with stored.open("wb") as out:
                shutil.copyfileobj(attachment.file, out)
            shown = f"📎 {attachment.filename}\n{q}"

        db.add_chat(session_id, "user", shown)
        background.add_task(_answer_task, session_id, q, stored, criteria)
        return RedirectResponse(f"/chat/{session_id}", status_code=303)

    @app.get("/criteria", response_class=HTMLResponse)
    def criteria(request: Request):
        docs = db.list_documents("regulation")
        counts = db.regulation_chunk_counts()
        for d in docs:
            d["n_chunks"] = counts.get(d["id"], 0)
        return templates.TemplateResponse(request, "criteria.html", ctx({"documents": docs}))

    @app.post("/criteria/upload")
    def criteria_upload(
        background: BackgroundTasks,
        file: list[UploadFile] = File(...),
        sector: str = Form("common"),
        link_project_id: int | None = Form(None),
    ):
        if sector not in SECTOR_LABELS:
            raise HTTPException(400, f"알 수 없는 업무 영역: {sector}")
        # 일괄 등록 — 형식 검사를 전부 통과해야 하나라도 저장한다
        for f in file:
            suffix = Path(f.filename or "이름없음").suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                raise HTTPException(400, f"허용되지 않는 파일 형식: {suffix}")
        new_ids: list[int] = []
        for f in file:
            name = f.filename or "이름없음"
            stored = inbox_dir / f"{uuid.uuid4().hex}{Path(name).suffix.lower()}"
            with stored.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            doc_id = db.add_document(
                filename=name, stored_path=str(stored),
                doc_type="regulation", sector=sector,
            )
            new_ids.append(doc_id)
            background.add_task(processor.process, db, doc_id, stored)
        # 프로젝트에서 올린 경우 — 등록과 동시에 그 프로젝트에 연결한다
        if link_project_id and db.get_project(link_project_id):
            db.add_project_criteria(link_project_id, new_ids)
            return RedirectResponse(f"/project/{link_project_id}", status_code=303)
        return RedirectResponse("/criteria", status_code=303)

    @app.post("/project/{project_id}/criteria/unlink")
    def project_criteria_unlink(project_id: int, criteria_doc_id: int = Form(...)):
        if db.get_project(project_id) is None:
            raise HTTPException(404)
        db.remove_project_criterion(project_id, criteria_doc_id)
        return RedirectResponse(f"/project/{project_id}", status_code=303)

    @app.post("/projects")
    def create_project(
        sector: str = Form(...), name: str = Form(...), due_date: str = Form("")
    ):
        if sector not in INBOX_TYPES:
            raise HTTPException(400, f"알 수 없는 업무 영역: {sector}")
        if not name.strip():
            raise HTTPException(400, "프로젝트 이름이 필요하다")
        pid = db.create_project(sector, name.strip(), due_date=due_date.strip())
        return RedirectResponse(f"/project/{pid}", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, err: int = 0):
        if password is None:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request, "login.html", {"err": err, "request": request}
        )

    @app.post("/login")
    def login_submit(username: str = Form(""), pw: str = Form("")):
        if password is None:
            return RedirectResponse("/", status_code=303)
        ok = secrets.compare_digest(username.strip().encode(), b"zzaimy") and (
            secrets.compare_digest(pw.encode(), password.encode())
        )
        if not ok:
            return RedirectResponse("/login?err=1", status_code=303)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            "zz_session", _make_session(), httponly=True, samesite="lax",
            max_age=7 * 24 * 3600,
        )
        return resp

    @app.post("/logout")
    def logout():
        resp = RedirectResponse("/login" if password is not None else "/", status_code=303)
        resp.delete_cookie("zz_session")
        return resp

    @app.get("/ocr", response_class=HTMLResponse)
    def ocr_page(request: Request, err: str | None = None):
        docs = db.list_documents("ocr")
        return templates.TemplateResponse(
            request, "ocr.html",
            ctx({"documents": docs, "active_tab": "all", "err_ext": err}),
        )

    @app.post("/ocr/upload")
    def ocr_upload(
        background: BackgroundTasks,
        file: list[UploadFile] = File(...),
    ):
        for f in file:
            suffix = Path(f.filename or "이름없음").suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                return RedirectResponse(f"/ocr?err={suffix or 'none'}", status_code=303)
        for f in file:
            name = f.filename or "이름없음"
            stored = inbox_dir / f"{uuid.uuid4().hex}{Path(name).suffix.lower()}"
            with stored.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            doc_id = db.add_document(
                filename=name, stored_path=str(stored), doc_type="ocr"
            )
            background.add_task(processor.process, db, doc_id, stored)
        return RedirectResponse("/ocr", status_code=303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return templates.TemplateResponse(
            request, "settings.html", ctx({"s": db.all_settings(), "active_tab": "all"})
        )

    @app.post("/settings")
    def settings_save(
        name: str = Form(""),
        call_me: str = Form(""),
        dept: str = Form(""),
        instructions: str = Form(""),
    ):
        for key, val in (
            ("name", name), ("call_me", call_me),
            ("dept", dept), ("instructions", instructions),
        ):
            db.set_setting(key, val.strip()[:4000])
        return RedirectResponse("/settings", status_code=303)

    @app.get("/project/{project_id}", response_class=HTMLResponse)
    def project_page(request: Request, project_id: int):
        proj = db.get_project(project_id)
        if proj is None:
            raise HTTPException(404)
        proj_sector = proj["sector"]
        docs = [
            d for d in db.list_documents(proj_sector, project_id=project_id)
            if d["doc_type"] != "regulation"
        ]
        linked = set(db.get_project_criteria_ids(project_id))
        sector_criteria = [
            d for d in db.list_documents("regulation")
            if d["status"] == "reviewed" and d["sector"] in (proj_sector, "common")
        ]
        # 이 섹터 전용 기준을 공통보다 위에 보여준다
        sector_criteria.sort(key=lambda d: d["sector"] != proj_sector)
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
        return templates.TemplateResponse(
            request,
            "project.html",
            ctx({
                "project": proj, "documents": docs, "active_tab": proj["sector"],
                "linked_criteria": linked, "sector_criteria": sector_criteria,
                "project_chats": db.list_project_chat_sessions(project_id),
                "project_notes": db.list_project_notes(project_id),
            }),
        )

    @app.post("/project/{project_id}/meta")
    def project_meta(
        project_id: int, instructions: str = Form(""), memo: str = Form("")
    ):
        if db.get_project(project_id) is None:
            raise HTTPException(404)
        db.update_project_meta(project_id, instructions=instructions, memo=memo)
        return RedirectResponse(f"/project/{project_id}", status_code=303)

    @app.post("/project/{project_id}/notes")
    def project_note_add(project_id: int, content: str = Form(...)):
        if db.get_project(project_id) is None:
            raise HTTPException(404)
        if content.strip():
            db.add_project_note(project_id, content.strip())
        return RedirectResponse(f"/project/{project_id}", status_code=303)

    @app.post("/project/{project_id}/notes/{note_id}/delete")
    def project_note_delete(project_id: int, note_id: int):
        if db.get_project(project_id) is None:
            raise HTTPException(404)
        db.delete_project_note(project_id, note_id)
        return RedirectResponse(f"/project/{project_id}", status_code=303)

    @app.post("/project/{project_id}/criteria")
    def project_criteria(project_id: int, criteria: list[int] = Form([])):
        if db.get_project(project_id) is None:
            raise HTTPException(404)
        db.set_project_criteria(project_id, criteria)
        return RedirectResponse(f"/project/{project_id}", status_code=303)

    @app.post("/projects/{project_id}/rename")
    def rename_project(
        project_id: int, name: str = Form(...), due_date: str | None = Form(None)
    ):
        proj = db.get_project(project_id)
        if proj is None:
            raise HTTPException(404, "프로젝트를 찾을 수 없다")
        if not name.strip():
            raise HTTPException(400, "프로젝트 이름이 필요하다")
        db.rename_project(project_id, name.strip())
        if due_date is not None:
            db.update_project_meta(project_id, due_date=due_date.strip())
        return RedirectResponse(f"/project/{project_id}", status_code=303)

    @app.post("/projects/{project_id}/delete")
    def delete_project(project_id: int):
        proj = db.get_project(project_id)
        if proj is None:
            raise HTTPException(404, "프로젝트를 찾을 수 없다")
        db.delete_project(project_id)
        return RedirectResponse(f"/?type={proj['sector']}", status_code=303)

    @app.post("/upload")
    def upload(
        background: BackgroundTasks,
        file: UploadFile = File(...),
        doc_type: str = Form("auto"),
        related_criteria_id: int | None = Form(None),
        project_id: int | None = Form(None),
    ):
        name = file.filename or "이름없음"
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"허용되지 않는 파일 형식: {suffix}")
        if doc_type not in INBOX_TYPES:
            raise HTTPException(400, f"알 수 없는 문서 유형: {doc_type}")
        if project_id and db.get_project(project_id) is None:
            project_id = None  # 삭제됐거나 잘못된 프로젝트 — 연결 없이 접수한다
        stored = inbox_dir / f"{uuid.uuid4().hex}{suffix}"
        with stored.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        doc_id = db.add_document(
            filename=name, stored_path=str(stored), doc_type=doc_type,
            related_criteria_id=related_criteria_id, project_id=project_id,
        )
        background.add_task(processor.process, db, doc_id, stored)
        return RedirectResponse(f"/?type={doc_type}" if related_criteria_id else "/",
                                status_code=303)

    @app.post("/doc/{doc_id}/draft")
    def make_draft(
        background: BackgroundTasks, doc_id: int,
        sections: str = Form(""),
    ):
        doc = db.get_document(doc_id)
        if doc is None:
            raise HTTPException(404)
        if doc["doc_type"] != "grant":
            # 목적별 플로우: 초안 작성은 국고사업 계열, 나머지는 검토·판정
            raise HTTPException(400, "초안 작성은 국고사업 문서에서만 지원한다")
        # 담당자가 요구한 작성 항목(한 줄에 하나) — 공고 목차보다 우선한다
        db.update_document(doc_id, draft_spec=sections.strip() or None)
        # 진행 표시를 먼저 남긴다 — 생성이 끝나면 drafter가 결과로 덮어쓴다
        db.update_document(doc_id, coverage="초안 작성 중입니다 (1~2분 걸립니다)")
        background.add_task(drafter.generate, db, doc_id)
        return RedirectResponse(f"/doc/{doc_id}", status_code=303)

    @app.get("/doc/{doc_id}/draft.{fmt}")
    def draft_export(doc_id: int, fmt: str):
        """초안 내보내기 — docx(한글 호환)·pdf·md."""
        from urllib.parse import quote as _q

        from fastapi.responses import Response

        doc = db.get_document(doc_id)
        if doc is None or not doc.get("draft"):
            raise HTTPException(404, "초안이 없다")
        stem = Path(doc["filename"] or "draft").stem
        title = f"{stem} 계획서 초안"
        if fmt == "md":
            payload: bytes | None = (
                f"# {title}\n\n{doc['draft']}\n"
            ).encode("utf-8")
            media = "text/markdown; charset=utf-8"
        elif fmt == "docx":
            from zzaimy.app.draft_export import build_draft_docx

            payload = build_draft_docx(title, doc["draft"])
            media = ("application/vnd.openxmlformats-officedocument"
                     ".wordprocessingml.document")
        elif fmt == "pdf":
            from zzaimy.app.draft_export import build_draft_pdf

            payload = build_draft_pdf(title, doc["draft"])
            media = "application/pdf"
        else:
            raise HTTPException(404)
        if payload is None:
            raise HTTPException(500, "내보내기 실패")
        return Response(payload, media_type=media, headers={
            "Content-Disposition":
            f"attachment; filename*=UTF-8\'\'{_q(stem)}_초안.{fmt}",
        })

    @app.get("/doc/{doc_id}", response_class=HTMLResponse)
    def detail(request: Request, doc_id: int):
        doc = db.get_document(doc_id)
        if doc is None:
            raise HTTPException(404)
        related = None
        if doc.get("related_criteria_id"):
            related = db.get_document(doc["related_criteria_id"])
        chunks = db.list_doc_chunks(doc_id)
        from zzaimy.app.render import chunk_blocks

        src_path = Path(doc["stored_path"])
        suffix = src_path.suffix.lower()
        original_kind = None
        if src_path.exists():
            original_kind = (
                "pdf" if suffix == ".pdf"
                else "image" if suffix in (".png", ".jpg", ".jpeg") else None
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
            lines_file = Path(db_path).parent / "lines" / f"{doc_id}.json"
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
            lines_file = Path(db_path).parent / "lines" / f"{doc_id}.json"
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
        return templates.TemplateResponse(
            request,
            "doc.html",
            ctx({
                "doc": doc, "reviews": db.get_reviews(doc_id), "related": related,
                "assets": assets,
                "extract_blocks": blocks, "layout_pages": layout,
                "restored_pdf": Path(doc["stored_path"]).suffix.lower()
                in (".pdf", ".png", ".jpg", ".jpeg"),
                "scan_asset": scan_asset,
                "original_kind": original_kind,
                "suggested_criteria": suggested,
                "n_text_chunks": sum(1 for c in chunks if c["kind"] == "text"),
                "n_table_chunks": sum(1 for c in chunks if c["kind"] == "table"),
            }),
        )

    @app.get("/doc/{doc_id}/original")
    def doc_original(doc_id: int):
        from fastapi.responses import FileResponse

        doc = db.get_document(doc_id)
        if doc is None or not Path(doc["stored_path"]).exists():
            raise HTTPException(404)
        return FileResponse(
            doc["stored_path"], filename=doc["filename"],
            content_disposition_type="inline",
        )

    @app.get("/doc/{doc_id}/page/{page_no}.png")
    def doc_page_image(doc_id: int, page_no: int):
        """원본 PDF 페이지 렌더 — 복원 뷰(원본 배치)의 배경. 디스크 캐시."""
        from fastapi.responses import FileResponse

        doc = db.get_document(doc_id)
        if (
            doc is None
            or not Path(doc["stored_path"]).exists()
            or Path(doc["stored_path"]).suffix.lower() != ".pdf"
            or not (1 <= page_no <= 500)
        ):
            raise HTTPException(404)
        cache_dir = Path(db_path).parent / "pagecache"
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

    @app.get("/doc/{doc_id}/restored.pdf")
    def doc_restored_pdf(doc_id: int):
        """복원 문서 — OCR 레이어가 입혀진 PDF를 크롬 내장 뷰어로 바로 연다.

        디지털 PDF는 원본 그대로(이미 완전한 텍스트 레이어), 스캔 PDF는
        보정 페이지 + OCR 줄 레이어로 재조립, 사진은 보정 스캔 + 레이어.
        """
        import json as _rj

        from fastapi.responses import FileResponse

        doc = db.get_document(doc_id)
        if doc is None:
            raise HTTPException(404)
        src = Path(doc["stored_path"])
        if not src.exists():
            raise HTTPException(404)
        suffix = src.suffix.lower()

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

        cache_dir = Path(db_path).parent / "restored"
        cache_dir.mkdir(exist_ok=True)
        cache = cache_dir / f"{doc_id}.pdf"
        if not cache.exists():
            payload: bytes | None = None
            lines_file = Path(db_path).parent / "lines" / f"{doc_id}.json"
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
                raise HTTPException(400, "복원 PDF를 만들 수 없는 형식이다")
            cache.write_bytes(payload)
        # FileResponse — Range 요청 지원으로 뷰어가 필요한 부분만 스트리밍
        return FileResponse(
            cache, media_type="application/pdf",
            content_disposition_type="inline", filename="restored.pdf",
        )

    @app.get("/doc/{doc_id}/images.zip")
    def doc_images_zip(doc_id: int):
        """추출 그림 일괄 내려받기 — 문서 속 사진·도표 이미지를 zip 하나로."""
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
            raise HTTPException(404, "추출된 그림이 없다")
        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, a in enumerate(assets, start=1):
                p = Path(a["path"])
                zf.write(p, f"p{a.get('page_no') or 0:03d}_{i:02d}{p.suffix}")
        stem = Path(doc["filename"] or "document").stem
        return Response(
            buf.getvalue(), media_type="application/zip",
            headers={"Content-Disposition":
                     f"attachment; filename*=UTF-8''{_q(stem)}_images.zip"},
        )

    @app.get("/doc/{doc_id}/asset/{asset_id}")
    def doc_asset(doc_id: int, asset_id: int, dl: int = 0):
        from fastapi.responses import FileResponse

        asset = next(
            (a for a in db.list_doc_assets(doc_id) if a["id"] == asset_id), None
        )
        if asset is None or not Path(asset["path"]).exists():
            raise HTTPException(404)
        if dl:
            doc = db.get_document(doc_id) or {}
            stem = Path(doc.get("filename", "문서")).stem
            name = f"{stem}_그림{asset_id}{Path(asset['path']).suffix}"
            return FileResponse(asset["path"], filename=name)
        return FileResponse(asset["path"])

    @app.get("/doc/{doc_id}/table/{chunk_id}.csv")
    def doc_table_csv(doc_id: int, chunk_id: int):
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
            raise HTTPException(404, "표 구조를 읽을 수 없다") from None
        return Response(
            "\ufeff" + csv_text,  # BOM — 엑셀에서 한글 깨짐 방지
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="table_{doc_id}_{chunk_id}.csv"'},
        )

    @app.get("/doc/{doc_id}/export.pdf")
    def doc_export_pdf(doc_id: int):
        from urllib.parse import quote

        from fastapi.responses import Response

        from zzaimy.app.pdf_layer import build_scan_pdf, build_searchable_pdf

        doc = db.get_document(doc_id)
        if doc is None:
            raise HTTPException(404)
        chunks = db.list_doc_chunks(doc_id)
        if not chunks:
            raise HTTPException(404, "추출 조각이 없다")
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
                    lf = Path(db_path).parent / "lines" / f"{doc_id}.json"
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
            raise HTTPException(400, "이 문서 형식은 PDF 레이어를 만들 수 없다")
        stem = Path(doc["filename"] or src.name).stem
        fname = quote(f"{stem}_OCR.pdf")
        return Response(
            payload, media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
        )

    @app.get("/doc/{doc_id}/export.docx")
    def doc_export_docx(doc_id: int):
        from urllib.parse import quote

        from fastapi.responses import Response

        from zzaimy.app.render import build_docx

        doc = db.get_document(doc_id)
        if doc is None:
            raise HTTPException(404)
        chunks = db.list_doc_chunks(doc_id)
        if not chunks:
            raise HTTPException(404, "추출 조각이 없다")
        asset_paths = {
            Path(a["path"]).name: a["path"]
            for a in db.list_doc_assets(doc_id)
            if Path(a["path"]).exists()
        }
        payload = build_docx(
            doc["filename"], chunks, asset_paths,
            extra_images=list(asset_paths.values()),
        )
        fname = quote(f"{Path(doc['filename']).stem}_복원.docx")
        return Response(
            payload,
            media_type=("application/vnd.openxmlformats-officedocument"
                        ".wordprocessingml.document"),
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
        )

    @app.get("/doc/{doc_id}/export.md")
    def doc_export_md(doc_id: int):
        from urllib.parse import quote

        from fastapi.responses import Response

        from zzaimy.app.render import export_markdown

        doc = db.get_document(doc_id)
        if doc is None:
            raise HTTPException(404)
        chunks = db.list_doc_chunks(doc_id)
        if not chunks:
            raise HTTPException(404, "추출 조각이 없다")
        md = export_markdown(doc["filename"], chunks)
        fname = quote(f"{Path(doc['filename']).stem}_추출결과.md")
        return Response(
            md, media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition":
                     f"attachment; filename*=UTF-8''{fname}"},
        )

    @app.post("/doc/{doc_id}/decision")
    def decide(background: BackgroundTasks, doc_id: int, decision: str = Form(...)):
        if db.get_document(doc_id) is None:
            raise HTTPException(404)
        if decision not in ("approved", "rejected", "rework"):
            raise HTTPException(400, f"알 수 없는 판정: {decision}")
        db.update_document(doc_id, decision=decision)
        if decision == "rework":
            db.update_document(doc_id, status="processing")
            background.add_task(processor.reprocess, db, doc_id)
        return RedirectResponse(f"/doc/{doc_id}", status_code=303)

    @app.post("/doc/{doc_id}/delete")
    def delete_doc(doc_id: int):
        doc = db.get_document(doc_id)
        if doc is None:
            raise HTTPException(404)
        db.delete_document(doc_id)
        stored = Path(doc["stored_path"])
        if stored.exists():
            stored.unlink()
        dest = "/criteria" if doc["doc_type"] == "regulation" else "/"
        return RedirectResponse(dest, status_code=303)

    @app.post("/doc/{doc_id}/review")
    def add_review(doc_id: int, opinion: str = Form(...)):
        if db.get_document(doc_id) is None:
            raise HTTPException(404)
        db.add_review(doc_id, opinion.strip())
        return RedirectResponse(f"/doc/{doc_id}", status_code=303)

    return app


def _default_responder():
    from zzaimy.app.responder import AgentResponder

    return AgentResponder()


def main() -> None:
    import os

    import uvicorn

    from zzaimy.app.drafter import SliceDrafter
    from zzaimy.app.pipeline import DocumentProcessor

    # 외부 접속 모드: ZZAIMY_PASSWORD와 인증서가 있을 때만 0.0.0.0 바인딩 허용.
    # 공인 IP 장비에서 무인증·무암호화 외부 노출 금지 (risks.md §8 — 실사고 이력).
    password = os.environ.get("ZZAIMY_PASSWORD") or None
    host = os.environ.get("ZZAIMY_HOST", "127.0.0.1")
    port = int(os.environ.get("ZZAIMY_PORT", "8800"))
    certfile = os.environ.get("ZZAIMY_TLS_CERT")
    keyfile = os.environ.get("ZZAIMY_TLS_KEY")

    if host != "127.0.0.1" and not (password and certfile and keyfile):
        raise SystemExit(
            "외부 바인딩에는 ZZAIMY_PASSWORD, ZZAIMY_TLS_CERT, ZZAIMY_TLS_KEY가 전부 필요하다"
        )

    app = create_app(
        db_path=Path("data/platform/platform.db"),
        inbox_dir=Path("data/platform/inbox"),
        processor=DocumentProcessor(),
        drafter=SliceDrafter(),
        responder=None,  # 지연 생성 (vLLM 연결은 첫 질문 때)
        password=password,
    )
    uvicorn.run(app, host=host, port=port, ssl_certfile=certfile, ssl_keyfile=keyfile)


if __name__ == "__main__":
    main()
