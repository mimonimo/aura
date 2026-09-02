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
    # 세션 토큰 — 앱이 뜰 때마다 새로 만든다 (재시작하면 재로그인)
    session_token = secrets.token_hex(32)
    if password is not None:
        basic = HTTPBasic(auto_error=False)

        def check_auth(
            request: Request,
            cred: HTTPBasicCredentials | None = Depends(basic),
        ) -> None:
            # 브라우저는 로그인 페이지의 세션 쿠키로, 스크립트·API는 Basic으로
            if request.url.path == "/login" or request.url.path.startswith("/static"):
                return
            if secrets.compare_digest(
                request.cookies.get("zz_session", ""), session_token
            ):
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
                "stats": stats, "active_flt": flt,
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
            "zz_session", session_token, httponly=True, samesite="lax",
            max_age=60 * 60 * 12,
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
        # 구버전 단일 메모는 노트로 한 번만 이관한다
        legacy_memo = (proj.get("memo") or "").strip()
        if legacy_memo:
            db.add_project_note(project_id, legacy_memo)
            db.update_project_meta(project_id, memo="")
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
    def make_draft(background: BackgroundTasks, doc_id: int):
        doc = db.get_document(doc_id)
        if doc is None:
            raise HTTPException(404)
        if doc["doc_type"] != "grant":
            # 목적별 플로우: 초안 작성은 국고사업 계열, 나머지는 검토·판정
            raise HTTPException(400, "초안 작성은 국고사업 문서에서만 지원한다")
        # 진행 표시를 먼저 남긴다 — 생성이 끝나면 drafter가 결과로 덮어쓴다
        db.update_document(doc_id, coverage="초안 작성 중입니다 (1~2분 걸립니다)")
        background.add_task(drafter.generate, db, doc_id)
        return RedirectResponse(f"/doc/{doc_id}", status_code=303)

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

        suffix = Path(doc["stored_path"]).suffix.lower()
        original_kind = (
            "pdf" if suffix == ".pdf"
            else "image" if suffix in (".png", ".jpg", ".jpeg") else None
        )

        assets = db.list_doc_assets(doc_id)
        asset_by_name = {Path(a["path"]).name: a["id"] for a in assets}
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
                "extract_blocks": (
                    chunk_blocks(chunks, doc_id, asset_by_name) if chunks else None
                ),
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
