"""문서 접수·검토 플랫폼 — FastAPI 앱 (대시보드 v0.1).

보안: 127.0.0.1에만 바인딩하고 원격 접속은 SSH 터널로 한다 (docs/risks.md §8).
실행(Spark):
    .venv/bin/python -m zzaimy.app.main
접속(맥):
    ssh -N -L 8800:localhost:8800 jun@<spark> 후 http://localhost:8800
"""

from __future__ import annotations

import os
from urllib.parse import urlencode
import re
import time
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

from zzaimy.app import search_serving, serving_plan
from zzaimy.app import paths as _paths
from zzaimy.app import storage
from zzaimy.app.db import Database

ALLOWED_EXTENSIONS = {
    ".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
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

# 추출 품질 신고 유형 — 신고 화면(doc.html)과 백로그(dev.html)가 같은 이름을 쓴다
QUALITY_KIND_LABELS = {
    "table": "표 구조 깨짐", "typo": "글자 오인식",
    "layout": "배치·순서 어긋남", "other": "기타",
}

DECISION_LABELS = {
    "pending": "판정 대기",
    "approved": "승인",
    "rejected": "반려",
    "rework": "재검토 중",
}

# 외부 AI 참조 관리 — 자동 분류 결과·제출 경로·제거 항목의 화면 표시명
# (dev_egress.html과 데이터 열람 개요가 같은 이름을 쓴다. 저장 값은 영문 키 그대로)
EGRESS_VERDICT_LABELS = {"safe": "안전", "review": "확인 필요", "blocked": "차단"}
EGRESS_SOURCE_LABELS = {"manual": "직접 제출"}
EGRESS_REMOVED_LABELS = {
    "KR_RRN": "주민등록번호", "RRN": "주민등록번호",
    "KR_PHONE": "전화번호", "PHONE": "전화번호",
    "EMAIL": "이메일", "KR_BRN": "사업자등록번호", "KR_BANK_ACCOUNT": "계좌번호",
    "CARD": "카드번호", "KR_NAME": "성명", "PERSON": "성명",
    "PII_MASKER_UNAVAILABLE": "마스킹 도구 미가동",
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


_live_models_cache: dict = {}
_LIVE_TTL = 120.0


def _live_models_cached(cid: str, ttl: float = _LIVE_TTL) -> dict:
    """연결이 지금 내어 주는 모델 목록 — 화면에서 고르라고 보여 준다.

    연결에 저장된 모델 하나만 보여 주면 서버에 새로 올린 모델이 화면에 뜨지 않는다
    (실측 2026-09-20: DGX 에 27B 를 받았는데 목록에 없었다). 페이지마다 물으면 느리므로
    짧게 캐시하고, 용도를 바꾸거나 연결을 고치면 캐시를 비운다.
    """
    import time as _t

    from zzaimy.generate import llm_connections

    hit = _live_models_cache.get(cid)
    if hit and _t.time() - hit[0] < ttl:
        return hit[1]
    conn = llm_connections.get(cid)
    if conn is None:
        return {"ok": False, "models": [], "error": "없는 연결"}
    try:
        got = llm_connections.live_models(conn, timeout=4.0)
    except Exception as e:                      # 서버가 없거나 느려도 화면은 떠야 한다
        got = {"ok": False, "models": [], "error": f"{type(e).__name__}"}
    _live_models_cache[cid] = (_t.time(), got)
    return got


def _tidy_weekly_md(body: str) -> str:
    """모델이 쓴 마크다운을 화면·내보내기가 같은 구조로 읽게 고른다 — 연번 항목 앞에는 빈 줄, 세부 줄은 '   - '.
    빈 줄 없이 '2. …' 가 불릿 뒤에 오면 마크다운이 앞 불릿의 이어지는 글로 붙인다(2026-09-22 실측)."""
    out: list[str] = []
    for ln in body.strip().splitlines():
        st = ln.strip()
        if re.match(r"^\d+[.)]\s", st) or st.startswith("【"):
            st = re.sub(r"^(\d+)\.\s", r"\1) ", st)            # 개조식 번호는 "1)" 로 통일
            if out and out[-1].strip():
                out.append("")
            out.append(st)
        elif re.match(r"^[-•·◦▪*]\s", st):
            out.append("   - " + st[2:].strip())
        else:
            out.append(st)
    return "\n".join(out).strip()


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

    # 계정 저장소 — data/platform/accounts.json (0600).
    # 부트스트랩: zzaimy(담당자)=기존 비밀번호, zzdev(개발자)=초기 devpass.
    # 개발자 전용 화면(/dev)은 dev 역할만 접근한다.
    import json as _aj

    accounts_path = Path(db_path).parent / "accounts.json"
    accounts: dict[str, dict] = {}

    def _save_accounts() -> None:
        accounts_path.write_text(_aj.dumps(accounts, ensure_ascii=False, indent=1))
        accounts_path.chmod(0o600)

    def _now_iso() -> str:
        from datetime import datetime as _d

        return _d.now().isoformat(timespec="seconds")

    # 비밀번호는 해시(pbkdf2-sha256, 개별 salt)로만 저장한다. 예전 파일의 평문 pw 는
    # 로그인에 성공하는 순간 해시로 바꿔 쓴다(승격) — 운영 중 파일을 손으로 고칠 필요 없음.
    def _hash_pw(pw: str, salt: bytes | None = None) -> tuple[str, str]:
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
        return digest.hex(), salt.hex()

    def _set_pw(acct: dict, pw: str) -> None:
        acct["pw_hash"], acct["salt"] = _hash_pw(pw)
        acct.pop("pw", None)
        acct["updated_at"] = _now_iso()

    def _verify_pw(uid: str, pw: str) -> bool:
        """계정이 있고 활성이며 비밀번호가 맞는가. 평문 저장분은 맞으면 해시로 승격."""
        acct = accounts.get(uid)
        if acct is None or not acct.get("active", True):
            return False
        if acct.get("pw_hash"):
            digest, _ = _hash_pw(pw, bytes.fromhex(acct["salt"]))
            return secrets.compare_digest(digest, acct["pw_hash"])
        legacy = str(acct.get("pw", ""))
        # 바이트 비교 — compare_digest는 비ASCII 문자열을 받지 못한다
        if legacy and secrets.compare_digest(pw.encode(), legacy.encode()):
            _set_pw(acct, pw)
            _save_accounts()
            return True
        return False

    def _new_account(uid: str, pw: str, role: str, name: str = "") -> dict:
        acct = {"role": role, "name": name, "active": True, "created_at": _now_iso()}
        _set_pw(acct, pw)
        return acct

    if password is not None:
        try:
            accounts = _aj.loads(accounts_path.read_text())
        except (OSError, ValueError):
            # 부트스트랩: zzaimy(담당자)=기동 비밀번호, zzdev(개발자)=초기 devpass.
            # 첫 로그인 후 변경한다.
            accounts = {
                "zzaimy": _new_account("zzaimy", password, "staff", "담당자"),
                "zzdev": _new_account("zzdev", "devpass", "dev", "개발자"),
            }
            _save_accounts()


    def _make_session(user: str, days: int = 7) -> str:
        exp = str(int(_time.time()) + days * 86400)
        base = f"{exp}.{user}"
        sig = _hmac.new(
            session_secret.encode(), base.encode(), hashlib.sha256
        ).hexdigest()
        return f"{base}.{sig}"

    def _session_user(token: str) -> str | None:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        exp, user, sig = parts
        if not exp.isdigit() or user not in accounts or not accounts[user].get("active", True):
            return None                       # 비활성 계정의 세션은 그 즉시 무효
        good = _hmac.new(
            session_secret.encode(), f"{exp}.{user}".encode(), hashlib.sha256
        ).hexdigest()
        if secrets.compare_digest(sig, good) and int(exp) > _time.time():
            return user
        return None

    _DOC_PATH = re.compile(r"^/doc/(\d+)(?:/|$)")
    from zzaimy.app.access_policy import visible as _visible

    if password is not None:
        basic = HTTPBasic(auto_error=False)

        def check_auth(
            request: Request,
            cred: HTTPBasicCredentials | None = Depends(basic),
        ) -> None:
            # 브라우저는 로그인 페이지의 세션 쿠키로, 스크립트·API는 Basic으로
            request.state.role = ""
            request.state.user = "zzaimy"
            if request.url.path == "/login" or request.url.path.startswith("/static"):
                return
            if request.url.path.startswith("/hwp/agent/"):
                # 한글 에이전트 채널 — 발급 토큰·세션으로 자체 인증 (라우트에서 검증)
                return
            user = _session_user(request.cookies.get("zz_session", ""))
            if user is None and cred is not None:
                if _verify_pw(cred.username, cred.password):
                    user = cred.username
                else:
                    raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
            if user is None:
                # 인증 정보가 아예 없는 브라우저 요청 — 로그인 페이지로
                raise HTTPException(401, detail="login-required")
            request.state.role = accounts.get(user, {}).get("role", "staff")
            request.state.dept = accounts.get(user, {}).get("dept", "")     # 검색·대화 범위(부서). 비면 전체
            request.state.user = user
            if request.url.path.startswith("/dev") and request.state.role != "dev":
                raise HTTPException(403, "개발자 계정 전용입니다")
            # 열람 등급은 검색만이 아니라 문서 경로 전부(화면·원본·쪽 그림·복원·내보내기·삭제)에 강제한다(브리프 절대 규칙 4).
            # 라우트마다 검사를 넣지 않고 여기서 한 번에 — 앞으로 생기는 /doc/{id}/… 경로도 저절로 막힌다.
            m = _DOC_PATH.match(request.url.path)
            if m:
                doc = db.get_document(int(m.group(1)))
                if doc is not None and not _visible(doc, dept=request.state.dept or None, user=user, role=request.state.role):
                    raise HTTPException(404, "문서를 찾을 수 없습니다")

        dependencies = [Depends(check_auth)]
    else:
        # 인증 없는 로컬 개발 모드 — 개발자 뷰까지 전부 연다
        def open_auth(request: Request) -> None:
            request.state.role = "dev"
            request.state.dept = ""
            request.state.user = "zzaimy"

        dependencies = [Depends(open_auth)]

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
    from zzaimy.app.chat_revisions import ChatRevisions, install_routes as install_chat_revisions

    chat_revisions = ChatRevisions(Path(db_path))
    from zzaimy.app.chat_history import ChatHistory, install_routes as install_chat_history
    chat_history = ChatHistory(Path(db_path))
    # 대화의 근거·주제 — 기록 목록이 '일반 대화' 대신 근거 문서의 사업·규정 이름을 보인다
    from zzaimy.app.chat_topics import ChatTopics

    chat_topics = ChatTopics(Path(db_path))
    chat_history.topics = chat_topics
    install_chat_history(app, chat_history)
    _chat_running: set[int] = set()
    # 화면에서 고른 모델 서버 주소·모델을 프로세스 전체에 적용 (설정 > 환경변수)
    from zzaimy.generate import model_config as _mc

    _mc.set_override(db.get_setting("llm_base_url", ""), db.get_setting("llm_model", ""))
    _mc.configure_usage(Path(db_path).parent / "llm_usage.json")
    # LLM 연결 목록(내부 vLLM·외부 API) — data/platform/llm_connections.json (0600)
    from zzaimy.generate import llm_connections as _lc

    _lc.configure(Path(db_path).parent / "llm_connections.json")
    # NAS 수집 — 원천(계정 포함)·반입 상태 파일, 자동 반입은 켜진 원천이 있을 때만 시작
    from zzaimy.ingest import nas_sync as _nas

    _nas.configure(Path(db_path).parent / "nas_sources.json", Path(db_path).parent / "nas_state.json")
    _nas.ensure_scheduler(db, processor, inbox_dir)
    if not _lc.list_public() and db.get_setting("llm_base_url", ""):
        # 예전 '모델 서버 주소' 설정은 내부 vLLM 연결 하나로 옮긴다
        _old = _lc.add("내부 vLLM", "vllm", db.get_setting("llm_base_url", ""),
                       db.get_setting("llm_model", ""), "")
        _lc.activate(_old["id"])
    _paths.ensure_layout(Path(db_path).parent)     # 문서·지식·캐시 폴더 뼈대(ADR-0031)
    app.state.db = db  # 테스트·운영 점검에서 접근할 수 있게 노출
    # 프로젝트 검색(사이드바) — 계정 소유 프로젝트만, 제목·업무 영역으로 (Codex C-71, 독립 라우터)
    from zzaimy.app.chat_documents import router as chat_documents_router
    from zzaimy.app.project_search import router as project_search_router

    app.include_router(project_search_router)
    # 대화에 구글 독스를 연결해 읽고(요청마다), 담당자가 확인한 삽입만 쓴다 (Codex C-73, 독립 라우터)
    app.include_router(chat_documents_router)

    @app.on_event("startup")
    def _warm_models() -> None:
        """임베딩·리랭커를 백그라운드로 예열 — 첫 질문의 수 초 지연 제거."""
        import os as _os
        import threading

        if _os.environ.get("ZZAIMY_NO_WARMUP"):
            return

        def warm() -> None:
            try:
                from zzaimy.app.embed_search import embed_search

                embed_search("예열", top_k=1)
            except Exception:
                pass
            try:
                from zzaimy.app.rerank import rerank_chunks

                rerank_chunks("예열", [
                    {"heading": "a", "content": "x"},
                    {"heading": "b", "content": "y"},
                ])
            except Exception:
                pass

        threading.Thread(target=warm, daemon=True).start()
    inbox_dir.mkdir(parents=True, exist_ok=True)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.globals["status_labels"] = STATUS_LABELS
    templates.env.globals["doc_type_labels"] = DOC_TYPE_LABELS
    templates.env.globals["decision_labels"] = DECISION_LABELS
    templates.env.globals["sector_labels"] = SECTOR_LABELS
    # 문서 이름 — 'law03.pdf' 같은 파일 이름 대신 첫 쪽의 규정 이름·날짜
    from zzaimy.app.doc_title import display_name as _doc_name
    templates.env.globals["doc_name"] = _doc_name
    templates.env.globals["quality_kind_labels"] = QUALITY_KIND_LABELS

    import re as _re

    from markupsafe import Markup, escape

    def md_lite(text: str) -> Markup:
        """이스케이프 후 **볼드**만 살리는 최소 마크다운."""
        escaped = str(escape(text))
        return Markup(_re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped))

    templates.env.filters["md_lite"] = md_lite

    def ctx(request: Request, extra: dict) -> dict:
        # 작업물(문서함·프로젝트·채팅)은 계정별 분리 — 기준·OCR 저장소는 공용
        owner = getattr(request.state, "user", "zzaimy")
        pending = db.pending_documents(limit=50, owner=owner)
        by_type: dict[str, int] = {}
        for d in pending:
            by_type[d["doc_type"]] = by_type.get(d["doc_type"], 0) + 1
        failed = db.failed_documents(owner=owner)
        from zzaimy.generate import model_config as _mc2

        try:
            llm_status = _mc2.status()
        except Exception:
            llm_status = {"ok": False, "configured": False, "model": "", "models": [], "error": "확인 실패"}
        return {
            "llm_status": llm_status,
            "chat_sessions": chat_history.sessions(owner, limit=12),
            "pending_docs": pending[:8],
            "pending_count": len(pending),
            "pending_by_type": by_type,
            "failed_docs": failed,
            "alert_count": len(pending) + len(failed),
            "side_projects": db.list_all_projects(owner=owner),
            "profile_name": db.get_setting("name"),
            "profile_dept": db.get_setting("dept"),
            **extra,
        }

    @app.get("/", response_class=HTMLResponse)
    @app.get("/inbox", response_class=HTMLResponse)
    def index(
        request: Request,
        type: str | None = None,
        q: str | None = None,
        project: int | None = None,
        flt: str | None = None,
        page: int = 0,
    ):
        if request.url.path == "/" and not any((type, q, project, flt, page)):
            return RedirectResponse("/chat", status_code=303)
        doc_type = type if type in INBOX_TYPES else None
        all_docs = [
            d for d in db.list_documents(
                doc_type, q=q, project_id=project,
                owner=getattr(request.state, "user", "zzaimy"),
            )
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
        # 페이징 — 표시 목록만 자른다(위 통계는 전체 기준 유지). "최근 N건"이 아니라 이어보기.
        PER = 30
        page = max(page, 0)
        total_docs = len(docs)
        docs = docs[page * PER:(page + 1) * PER]
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
            ctx(request, {
                "documents": docs, "active_tab": doc_type or "all", "q": q or "",
                "sector_criteria": sector_criteria,
                "projects": projects, "active_project": project,
                "stats": stats, "active_flt": flt, "recent_activity": recent,
                "page": page, "per": PER, "total_docs": total_docs,
                "has_next": (page + 1) * PER < total_docs,
            }),
        )

    def _criteria_docs() -> list[dict]:
        counts = db.regulation_chunk_counts()
        return [
            d | {"n_chunks": counts.get(d["id"], 0)}
            for d in db.list_documents("regulation")
            if d["status"] == "reviewed"
        ]

    @app.get("/connections", response_class=HTMLResponse)
    def connections_page(request: Request):
        return templates.TemplateResponse(request, "connections.html", ctx(request, {}))

    def _owned_chat(request, session_id):
        session = db.get_chat_session(session_id)
        if not session or session.get("owner") != request.state.user:
            raise HTTPException(404, "대화를 찾을 수 없습니다.")
        return session

    def _scope_label(request: Request) -> str:
        """대화 화면 상단의 '내 범위' 한 줄 — 부서와 역할. 부서가 없으면 전체."""
        from zzaimy.app.access_guard import ROLES

        role = ROLES.get(getattr(request.state, "role", ""), "담당자")
        dept = getattr(request.state, "dept", "") or ""
        if getattr(request.state, "role", "") == "student":
            return f"내 범위: 공개 규정·학사 안내 · {role}"
        return f"내 범위: {dept or '전체'} · {role}"

    @app.get("/chat", response_class=HTMLResponse)
    def chat_new(request: Request, project: int | None = None):
        chat_project = db.get_project(project) if project else None
        if project and (not chat_project or chat_project.get("owner") != request.state.user):
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
        return templates.TemplateResponse(
            request,
            "chat_workspace.html",
            ctx(request, {
                "messages": [], "criteria_docs": _criteria_docs(),
                "waiting": False, "session_id": None, "sources": [],
                "recommended_criteria": db.get_project_criteria_ids(project) if project else [],
                "chat_session": None, "chat_project": chat_project, "chat_topic": "",
                "scope_label": _scope_label(request),
            }),
        )

    def _recommended_criteria(session_id: int | None, messages: list) -> list[int]:
        """이 대화에 맞는 기준 — 프로젝트에 연결된 것, 없으면 오간 말과 겹치는 것.

        전부 늘어놓으면 고를 수 없다. 맞는 것이 먼저 와야 한다.
        """
        import re as _re

        if session_id is not None:
            session = db.get_chat_session(session_id)
            if session and session.get("project_id"):
                ids = db.get_project_criteria_ids(int(session["project_id"]))
                if ids:
                    return list(ids)
        said = " ".join(m.get("content") or "" for m in (messages or [])[-6:])
        words = {w for w in _re.findall(r"[0-9A-Za-z가-힣]{2,}", said)}
        if not words:
            return []
        scored: list[tuple[int, int]] = []
        for d in _criteria_docs():
            hay = set(_re.findall(r"[0-9A-Za-z가-힣]{2,}", d.get("filename") or ""))
            n = len(words & hay)
            if n:
                scored.append((n, d["id"]))
        scored.sort(reverse=True)
        return [i for _, i in scored[:6]]

    @app.get("/chat/{session_id}", response_class=HTMLResponse)
    def chat_session(request: Request, session_id: int):
        _owned_chat(request, session_id)
        messages = db.list_chats(session_id)
        waiting = session_id in _chat_running or (bool(messages) and messages[-1]["role"] == "user")
        session = db.get_chat_session(session_id)
        project = (
            db.get_project(session["project_id"])
            if session and session.get("project_id") else None
        )
        latest_question = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        topic = " ".join(_strip_attach_prefix(latest_question).split())
        return templates.TemplateResponse(
            request,
            "chat_workspace.html",
            ctx(request, {
                "messages": messages, "criteria_docs": _criteria_docs(),
                "waiting": waiting, "session_id": session_id,
                "sources": _chat_sources.get(session_id) or chat_topics.latest(session_id),
                "recommended_criteria": _recommended_criteria(session_id, messages),
                "chat_session": session, "chat_project": project, "chat_topic": topic,
                "scope_label": _scope_label(request),
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
            raise HTTPException(400, "맥락 분석은 문서 추출·기준 문서에서만 지원합니다")
        db.update_document(doc_id, coverage="분석 중입니다 (30초~1분)")
        background.add_task(processor.analyze, db, doc_id)
        return RedirectResponse(f"/doc/{doc_id}", status_code=303)

    @app.get("/chat/{session_id}/status")
    def chat_status(request: Request, session_id: int):
        _owned_chat(request, session_id)
        messages = db.list_chats(session_id)
        return {"waiting": session_id in _chat_running or (bool(messages) and messages[-1]["role"] == "user")}

    # 세션별 최근 검색 근거(연관 자료) — 채팅 사이드바에 노출한다.
    _chat_sources: dict[int, list] = {}

    def _answer_task_impl(
        session_id: int, q: str, stored: Path | None, criteria: list[int],
        external: bool = False,
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
        # 외부 참조가 필요한 질문이면, 민감정보를 토큰으로 바꿔 외부에서 먼저 처리하고
        # 되돌린 결과를 교내 모델에 자료로 건넨다. 최종 답은 교내 모델이 만든다.
        if external:
            from zzaimy.app.egress import process_external_tokenized

            out = process_external_tokenized(q)
            if out.get("ok"):
                q = (f"{q}\n\n[외부에서 받은 참고 자료]\n{out['result']}\n"
                     "위 자료는 참고용입니다. 교내 규정·공고에 근거해 답하십시오.")
            else:
                db.add_chat(session_id, "assistant",
                            f"외부 참조를 쓰지 못했습니다 — {out.get('error', '사유 미상')}."
                            " 교내 자료만으로 답합니다.")
        r = responder or _default_responder()
        # 프로젝트에 묶인 세션이면 지침·메모를 맥락으로, 연결 기준을 기본 근거로 쓴다
        session = db.get_chat_session(session_id)
        project = None
        if session and session.get("project_id"):
            project = db.get_project(int(session["project_id"]))
            if project and not criteria:
                criteria = db.get_project_criteria_ids(project["id"])
        # 권한 밖 질문 대처 — 판단은 규칙과 검색이 하고 모델은 결과를 말로 옮긴다
        # (docs/notes/2026-09-22-access-controlled-knowledge-base.md). 범위는 세션 주인의 계정에서 온다.
        from zzaimy.app import access_guard as ag

        owner = (session or {}).get("owner") or ""
        acct = accounts.get(owner, {}) if password is not None else {}
        role = acct.get("role", "dev" if password is None else "staff")
        dept = acct.get("dept") or None
        data_dir = Path(db_path).parent
        # 대화에 연결된 구글 독스가 있으면 본문을 첨부처럼 붙인다. 못 읽으면 읽은 척하지 않고 그렇게 말한다.
        try:
            from zzaimy.app import chat_documents

            doc_material = chat_documents.material(db, session_id, owner)
        except HTTPException:
            doc_material = ""                      # 이 계정의 대화가 아니면 연결을 쓰지 않는다
        except Exception as e:
            _chat_sources[session_id] = []
            db.add_chat(session_id, "assistant",
                        f"연결된 구글 문서를 읽지 못했습니다({type(e).__name__}). 계정 허용과 문서 주소를 확인해 주세요.")
            return
        if doc_material:
            attachment_text = ((attachment_text or "") + "\n\n" + doc_material).strip()
        note = ag.pii_request(q)
        if note:
            ag.audit(data_dir, owner, "pii", q, dept, role)
            _chat_sources[session_id] = []
            db.add_chat(session_id, "assistant", note)
            return
        if ag.injection_like(q):
            ag.audit(data_dir, owner, "injection", q, dept, role)
        depts = [d.get("dept") or "" for d in db.department_counts()]
        scope_msg = ag.scope_note(q, dept, role, depts)
        if scope_msg:
            ag.audit(data_dir, owner, "scope", q, dept, role)
        criteria = ag.allowed_doc_ids(db, criteria, dept, role, owner)
        scope = ag.search_scope(dept, role, owner)
        # 대화에 구글 독스가 연결돼 있으면 답만 하지 않고 문서를 바로 고친다(gdocs_agent) — 명령 → 편집 계획 → 적용
        if not doc_material and _looks_like_working_on(q):
            # "작성서식으로 작업하자" 처럼 프로젝트에 있는 문서(한글 양식 등)를 지목하면 그 문서를 독스로 바꿔 잇는다
            picked = _project_doc_named(project, session_id, q)
            if picked is not None:
                made, why = _link_existing_document(session_id, picked, owner, project)
                if made:
                    doc_material = "existing"
                    if not _looks_like_drafting(q):
                        _chat_sources[session_id] = []
                        return
                elif why:
                    db.add_chat(session_id, "assistant", why)
                    _chat_sources[session_id] = []
                    return
        if not doc_material and _looks_like_drafting(q):
            # 문서가 없는데 초안을 써 달라면 드라이브에 프로젝트 폴더·문서를 만들어 잇는다(주소 붙여넣기 없이)
            made, why = _auto_link_document(session_id, q, owner, project)
            if made:
                doc_material = "new"
            elif why:
                db.add_chat(session_id, "assistant", why)
                _chat_sources[session_id] = []
                return
        if doc_material:
            _edit_linked_doc(session_id, q, owner, data_dir, scope, scope_msg)
            return
        try:
            import inspect as _insp

            kw = dict(attachment_text=attachment_text, criteria_ids=criteria,
                      session_id=session_id, project=project)
            if "scope" in _insp.signature(r.answer).parameters:
                kw["scope"] = scope
            answer = r.answer(db, q, **kw)
        except Exception as e:
            from zzaimy.generate.client import describe_llm_error

            answer = describe_llm_error(e) + ". 검색된 근거 자료는 아래에 표시됩니다."
        answer = ag.scrub(answer)                       # 답변에 남은 식별 정보는 한 번 더 가린다
        if scope_msg:
            answer = scope_msg + "\n\n" + answer
        # 근거(연관 자료)는 LLM 성공·실패와 무관하게 저장 — 검색은 CPU로 동작
        _chat_sources[session_id] = list(getattr(r, "last_sources", []) or [])
        try:
            chat_topics.record(session_id, _chat_sources[session_id])
        except Exception:
            pass                    # 근거 기록이 실패해도 답변은 남긴다
        db.add_chat(session_id, "assistant", answer)

    _DRAFT_EXPLICIT = re.compile(r"초안|보고서로|문서로\s*(?:만들|정리|작성|써)|공문으로")
    _DRAFT_NOUN = re.compile(r"계획서|보고서|공문|제안서|신청서|요청서|의견서|안내문|양식|보도자료|회의록|문서|초안")
    _DRAFT_VERB = re.compile(r"작성|써\s*줘|써줘|써\s*주|만들어|정리해|기안")

    def _looks_like_drafting(q: str) -> bool:
        """문서를 써 달라는 요청인가 — 질문·검토 요청과 구분한다(일반 낱말 단서, 특정 사례 없음).

        '초안'·'문서로 만들어' 같은 명시 단서가 있거나, 서류 갈래 낱말(계획서·공문 …)과 쓰기 동사가 함께 있을 때만.
        '접수된 문서가 무엇인지 정리해 줘' 처럼 있는 문서를 묻는 말은 문서를 만들지 않는다(실측 2026-09-24)."""
        q = q or ""
        if _DRAFT_EXPLICIT.search(q):
            return True
        if not (_DRAFT_NOUN.search(q) and _DRAFT_VERB.search(q)):
            return False
        # 서류 갈래 낱말이 조사 '가·이·는·은' 을 달고 주어로 쓰였으면(문서가 무엇인지, 계획서는 어디에) 질문이다
        return not re.search(r"(?:계획서|보고서|공문|문서|양식)(?:가|이|는|은)\s", q)

    app.state.looks_like_drafting = _looks_like_drafting     # 테스트에서 판정 규칙을 그대로 검사한다

    _TITLE_CUT = re.compile(r"\s*(?:의\s*)?(?:초안|작성|써\s*줘|써줘|만들어|정리해|보고서로|문서로)")

    def _draft_title(q: str) -> str:
        """지시문에서 문서 제목을 추린다 — '2027년 사업계획서 초안을 써 줘. 절은…' → '2027년 사업계획서'."""
        head = (q or "").strip().splitlines()[0] if (q or "").strip() else ""
        m = _TITLE_CUT.search(head)
        cand = head[:m.start()] if m else head
        cand = re.sub(r"[\s,.:;·]+$", "", cand)
        cand = re.sub(r"(을|를|은|는|의|로|으로)$", "", cand).strip()
        return (cand or head or "새 문서")[:60]

    _WORK_WORDS = re.compile(r"작업|작성|수정|편집|채워|채우|고쳐|고치|써\s*(?:보|줘|주)|열어|열고|독스로|구글\s*독스")

    def _looks_like_working_on(q: str) -> bool:
        """있는 문서를 가지고 일하자는 말인가 — 서류 이름과 함께 작업·작성·수정·편집·열기 같은 낱말이 있을 때."""
        return bool(_WORK_WORDS.search(q or ""))

    def _project_doc_named(project: dict | None, session_id: int, q: str) -> dict | None:
        """질문이 지목한 프로젝트 문서 — 제목 낱말(2자 이상)의 6할 이상이 질문에 있으면 그 문서. 가장 많이 겹치는 것."""
        if not q:
            return None
        cands: list[dict] = []
        if project:
            for did in db.get_project_criteria_ids(int(project["id"])):
                d = db.get_document(did)
                if d:
                    cands.append(d)
            cands += db.list_documents(project["sector"], project_id=int(project["id"]))
        for f in db.list_files(kind="attachment", session_id=session_id):
            d = db.get_document(int(f["doc_id"])) if f.get("doc_id") else None
            if d:
                cands.append(d)
        norm_q = re.sub(r"[\s\-_()\[\]{}.,:;·+/]+", "", q).lower()
        best, best_score = None, 0.0
        for d in cands:
            title = storage.title_of(d.get("filename") or "")
            tokens = [t.lower() for t in re.split(r"[\s\-_()\[\]{}.,:;·+/]+", title) if len(t) >= 2]
            if not tokens:
                continue
            hit = sum(1 for t in tokens if t in norm_q)
            score = hit / len(tokens)
            if score >= 0.6 and score > best_score:
                best, best_score = d, score
        return best

    app.state.project_doc_named = _project_doc_named

    def _link_existing_document(session_id: int, doc: dict, owner: str, project: dict | None) -> tuple[bool, str]:
        """프로젝트 문서의 구글 독스 변환본(한글이면 지금 변환)을 이 대화의 작업 문서로 잇는다."""
        from zzaimy.ingest import gdrive, gdrive_files

        if not gdrive.list_accounts():
            return False, ""
        acct_ = accounts.get(owner, {}) if password is not None else {}
        email = gdrive_files.account_for(db, owner, acct_.get("dept") or None)
        if not email or not gdrive_files.has_file_scope(email):
            return False, "구글 계정 허용이 없어 문서를 독스로 열지 못합니다 — 원천 관리에서 구글 계정 허용을 해 주세요."
        title = storage.title_of(doc.get("filename") or "")
        try:
            folder = gdrive_files.project_folder_for(db, email, project, acct_.get("dept") or None, sub="첨부")
            made = gdrive_files.google_copy(db, doc, email, folder)
        except Exception as e:
            return False, f"「{title}」 을 독스로 바꾸지 못했습니다({type(e).__name__})."
        if made.get("mime") != "application/vnd.google-apps.document":
            return False, f"「{title}」 은 독스 문서가 아니라(시트·슬라이드·PDF) 에이전트가 편집할 수 없습니다. 열람은 문서함에서 됩니다."
        db.set_setting(f"chat_google_doc:{session_id}", _aj.dumps({"doc": made["id"], "account": email}))
        db.add_chat(session_id, "assistant", f"「{title}」 을 구글 독스로 열어 이 대화에 연결했습니다. 이제 명령하면 이 문서를 바로 고칩니다. {made['url']}")
        return True, ""

    def _auto_link_document(session_id: int, q: str, owner: str, project: dict | None) -> tuple[bool, str]:
        """대화에 문서가 없을 때 드라이브 폴더(ZZAIMY/<연도>/<프로젝트|대화>)와 문서를 만들어 잇는다.

        돌려주는 것은 (만들었는가, 못 만들었을 때 담당자에게 보일 안내). 허용 계정이 없으면 안내 없이 일반 답변으로 간다.
        """
        from zzaimy.ingest import gdrive, gdrive_files

        if not gdrive.list_accounts():
            return False, ""
        title = _draft_title(q)
        try:
            acct_ = accounts.get(owner, {}) if password is not None else {}
            made = gdrive_files.auto_document(db, session_id, owner, title, project_name=(project or {}).get("name"),
                                              dept=acct_.get("dept") or None)
        except PermissionError as e:
            return False, f"{e} 그 뒤 다시 요청하면 문서를 만들어 바로 씁니다."
        except Exception as e:
            return False, f"문서를 만들지 못했습니다({type(e).__name__}). 원천 관리에서 구글 연결 상태를 확인해 주세요."
        db.add_chat(session_id, "assistant", f"드라이브에 문서 「{title}」 을 만들어 이 대화에 연결했습니다. {made['url']}")
        return True, ""

    def _edit_linked_doc(session_id: int, q: str, owner: str, data_dir: Path, scope: dict, scope_msg: str) -> None:
        """연결된 구글 독스에 대한 명령 — 근거 조각을 붙여 편집 계획을 받고 적용한 결과를 채팅에 남긴다."""
        from zzaimy.app import access_guard as ag
        from zzaimy.app import chat_documents, gdocs_agent
        from zzaimy.app.regulations import find_relevant

        link = chat_documents.binding(db, session_id, owner)
        try:
            hits = find_relevant(db, q, top_k=5, **scope)
        except Exception:
            hits = []
        _chat_sources[session_id] = [{
            "title": h.get("reg_title") or "문서", "heading": h.get("heading") or "",
            "snippet": (h.get("content") or "")[:160], "doc_id": h.get("doc_id"), "origin": "교내 규정",
            "weak": bool(h.get("weak_evidence")),
        } for h in hits[:4]]
        confirm = (db.get_setting(f"chat_google_doc_confirm:{session_id}", "") or "") == "1"
        try:
            from zzaimy.generate.client import VllmClient

            client = VllmClient(role="answer")
            text, _ops = gdocs_agent.run(db, session_id, owner, q, link, client=client, data_dir=data_dir,
                                         scrub=ag.scrub, evidence=hits, confirm=confirm)
            new_name = next((o.get("text") for o in _ops if o.get("op") == "rename" and (o.get("text") or "").strip()), "")
            if new_name and not confirm:
                try:
                    db.rename_chat_session(session_id, new_name.strip()[:60])
                except Exception:
                    pass
            move_to = next((o.get("text") for o in _ops if o.get("op") == "move" and (o.get("text") or "").strip()), "")
            if move_to and not confirm:
                proj = _project_by_name(owner, move_to)
                if proj is None:
                    text += f"\n- 프로젝트 「{move_to}」 를 찾지 못해 옮기지 않았습니다"
                else:
                    try:
                        from zzaimy.ingest import gdrive_files

                        db.set_chat_project(session_id, int(proj["id"]))
                        acct_ = accounts.get(owner, {}) if password is not None else {}
                        gdrive_files.move_to_project(db, session_id, proj["name"], dept=acct_.get("dept") or None)
                        text += f"\n- 드라이브 문서를 「{proj['name']}」 폴더로 옮기고 대화를 그 프로젝트에 넣었습니다"
                    except Exception as e:
                        text += f"\n- 문서를 옮기지 못했습니다({type(e).__name__})"
        except Exception as e:
            from zzaimy.generate.client import describe_llm_error

            text = describe_llm_error(e) + ". 문서는 바꾸지 않았습니다."
        text = ag.scrub(text)
        if scope_msg:
            text = scope_msg + "\n\n" + text
        db.add_chat(session_id, "assistant", text)

    def _answer_task(session_id, q, stored, criteria, external=False):
        try:
            _answer_task_impl(session_id, q, stored, criteria, external)
        finally:
            _chat_running.discard(session_id)

    def _schedule_answer(background, session_id, q, stored, criteria, external=False):
        _chat_running.add(session_id)
        background.add_task(_answer_task, session_id, q, stored, criteria, external)

    install_chat_revisions(app, db, chat_revisions, _schedule_answer,
                           lambda sid: sid in _chat_running, inbox_dir,
                           ALLOWED_EXTENSIONS, _chat_sources)

    @app.post("/chat/send")
    def chat_send(
        request: Request,
        background: BackgroundTasks,
        question: str = Form(...),
        session_id: int | None = Form(None),
        criteria: list[int] = Form([]),
        attachment: UploadFile | None = File(None),
        project_id: int | None = Form(None),
        external: str = Form(""),
    ):
        q = question.strip()
        if not q:
            return RedirectResponse("/chat", status_code=303)
        if session_id is None:
            title = q
            if project_id and (proj := db.get_project(project_id)):
                if proj.get("owner") != request.state.user:
                    raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
                title = f"[{proj['name'][:14]}] {q}"
            else:
                project_id = None
            session_id = db.create_chat_session(
                title=title, project_id=project_id,
                owner=getattr(request.state, "user", "zzaimy"),
            )
        else:
            _owned_chat(request, session_id)
        # 응답 대기 중 중복 전송 방지 — 마지막 메시지가 아직 답변 전이면 무시
        last = db.list_chats(session_id, limit=1)
        if session_id in _chat_running or (last and last[-1]["role"] == "user"):
            return RedirectResponse(f"/chat/{session_id}", status_code=303)

        stored: Path | None = None
        shown = q
        if attachment is not None and attachment.filename:
            suffix = Path(attachment.filename).suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                raise HTTPException(400, f"허용되지 않는 파일 형식입니다: {suffix}")
            stored = storage.attachment_path(Path(db_path).parent, session_id, attachment.filename)
            with stored.open("wb") as out:
                shutil.copyfileobj(attachment.file, out)
            # 첨부는 문서로도 등록한다 — 대화에서 바로 열어 보고(/doc/{id}), 프로젝트 문서 목록에도 선다.
            # 파일은 첨부 폴더에 그대로 두고(ADR-0030 2층 '첨부'), 부서·등급은 올린 사람 기준이다.
            from zzaimy.app.access_policy import classify

            session_ = db.get_chat_session(session_id) or {}
            proj_ = db.get_project(int(session_["project_id"])) if session_.get("project_id") else None
            owner_ = getattr(request.state, "user", "zzaimy")
            a_dept, a_level = classify("auto", owner=owner_, uploader_dept=getattr(request.state, "dept", ""),
                                       project_dept=(proj_ or {}).get("dept"))
            att_type = (proj_ or {}).get("sector") if (proj_ or {}).get("sector") in INBOX_TYPES else "auto"
            att_doc = db.add_document(filename=attachment.filename, stored_path=str(stored), doc_type=att_type,
                                      project_id=(proj_ or {}).get("id"), owner=owner_, dept=a_dept, access_level=a_level)
            db.add_file("attachment", str(stored), name=attachment.filename, session_id=session_id, doc_id=att_doc,
                        size=stored.stat().st_size)
            background.add_task(processor.process, db, att_doc, stored)
            shown = f"[첨부#{att_doc}] {attachment.filename}\n{q}"

        db.add_chat(session_id, "user", shown)
        chat_revisions.remember(db.list_chats(session_id, limit=1)[0]["id"], stored, criteria)
        _schedule_answer(background, session_id, q, stored, criteria, bool(external))
        return RedirectResponse(f"/chat/{session_id}", status_code=303)

    def _strip_attach_prefix(text: str) -> str:
        """저장된 사용자 메시지에서 첨부 표시줄을 떼고 질문만 남긴다."""
        if text.startswith("[첨부") and "\n" in text:
            return text.split("\n", 1)[1]
        return text

    @app.get("/chat/{session_id}/messages")
    def chat_messages(request: Request, session_id: int):
        """대화 내용을 JSON 으로 돌려준다 — 페이지를 떠나지 않는 위젯과 갱신에 쓴다."""
        session = _owned_chat(request, session_id)
        rows = db.list_chats(session_id)
        return {
            "title": session["title"], "project_id": session.get("project_id"),
            "waiting": session_id in _chat_running or (bool(rows) and rows[-1]["role"] == "user"),
            "messages": [
                {"id": m["id"], "role": m["role"], "content": m["content"]} for m in rows
            ],
        }

    @app.post("/chat/{session_id}/retry")
    def chat_retry(request: Request, background: BackgroundTasks, session_id: int):
        """마지막 답변을 지우고 같은 질문으로 다시 생성한다."""
        _owned_chat(request, session_id)
        rows = db.list_chats(session_id)
        if not rows:
            raise HTTPException(404)
        if session_id in _chat_running or rows[-1]["role"] == "user":
            return {"ok": False, "error": "답변을 기다리는 중입니다"}
        question = ""
        for m in reversed(rows):
            if m["role"] == "user":
                question = _strip_attach_prefix(m["content"])
                break
        if not question:
            return {"ok": False, "error": "다시 보낼 질문이 없습니다"}
        db.delete_chat_message(int(rows[-1]["id"]))
        criteria: list[int] = []
        session = db.get_chat_session(session_id)
        if session and session.get("project_id"):
            criteria = db.get_project_criteria_ids(int(session["project_id"]))
        _schedule_answer(background, session_id, question, None, criteria)
        return {"ok": True}

    from zzaimy.generate import llm_connections

    def _find_document(question: str) -> int | None:
        """말 속에서 어느 문서를 가리키는지 찾는다.

        파일 이름과 문서 정체(사업 이름)의 낱말이 얼마나 겹치는지로 고른다.
        으뜸이 버금보다 뚜렷하게 앞설 때만 고른다 — 애매하면 고르지 않는다.
        """
        import re as _re

        words = {w for w in _re.findall(r"[0-9A-Za-z가-힣]{2,}", question or "")}
        if not words:
            return None
        best, second, best_id = 0, 0, None
        for d in db.list_documents():
            name = d.get("filename") or ""
            hay = set(_re.findall(r"[0-9A-Za-z가-힣]{2,}", name))
            ident = db.get_doc_identity(d["id"])
            if ident.get("program"):
                hay |= set(_re.findall(r"[0-9A-Za-z가-힣]{2,}", ident["program"]))
            score = len(words & hay)
            if score > best:
                best, second, best_id = score, best, d["id"]
            elif score > second:
                second = score
        return best_id if best >= 2 and best > second else None

    def _run_action(key: str, ctx: dict) -> str:
        """교내에서 끝나고 되돌릴 수 있는 일은 에이전트가 직접 한다.

        한 줄 결과를 돌려준다. 실패해도 대화는 이어져야 하므로 사유만 남긴다.
        """
        try:
            if key == "doc.identity":
                r = _identify_document(db, int(ctx["doc_id"]))
                if not r["ok"]:
                    return f"사업 정보를 읽지 못했습니다 — {r['error']}"
                return "사업 정보를 정리했습니다 — " + " · ".join(r["identity"].values())
            if key == "doc.analyze":
                doc_id = int(ctx["doc_id"])
                if db.get_document(doc_id) is None:
                    return "없는 문서입니다"
                db.update_document(doc_id, coverage="분석 중입니다 (30초~1분)")
                processor.analyze(db, doc_id)
                return "맥락을 다시 분석했습니다"
            if key == "doc.revise":
                doc_id = int(ctx["doc_id"])
                doc = db.get_document(doc_id)
                if doc is None:
                    return "없는 문서입니다"
                want = (ctx.get("question") or "").strip()
                if len(want) < 4:
                    return "어떻게 고칠지 한 문장으로 알려 주십시오"
                if not (doc.get("draft") or "").strip():
                    return "아직 초안이 없습니다. 먼저 초안을 만들어야 합니다"
                db.add_review(doc_id, want)
                db.update_document(doc_id, coverage="초안을 고치는 중입니다")
                drafter.generate(db, doc_id)
                return f"「{doc['filename']}」 초안을 요청대로 고쳤습니다"
            if key == "doc.route":
                doc_id = int(ctx["doc_id"])
                if db.get_document(doc_id) is None:
                    return "없는 문서입니다"
                line = _auto_route(db, doc_id, user_chose_type=False)
                return line or "지금 값이 알맞아 그대로 두었습니다"
            if key == "plan.start":
                doc_id = int(ctx["doc_id"])
                doc = db.get_document(doc_id)
                if doc is None:
                    return "없는 문서입니다"
                ident = db.get_doc_identity(doc_id)
                if not ident:
                    r = _identify_document(db, doc_id)
                    ident = r.get("identity") or {}
                name = (ident.get("program") or doc["filename"]).strip()[:60]
                sector = doc.get("sector") if doc.get("sector") in INBOX_TYPES else "grant"
                pid = db.create_project(sector, name, owner="zzaimy")
                db.set_document_project(doc_id, pid)
                steps = [f"프로젝트 「{name}」를 만들고 공고를 붙였습니다"]
                if ident.get("organizer") or ident.get("period"):
                    bits = [v for v in (ident.get("organizer"), ident.get("period"),
                                        ident.get("scale")) if v]
                    steps.append("공고에서 확인한 것 — " + " · ".join(bits))
                return " / ".join(steps) + f" (프로젝트 {pid})"
            if key == "doc.draft":
                doc_id = int(ctx["doc_id"])
                doc = db.get_document(doc_id)
                if doc is None:
                    return "없는 문서입니다"
                db.update_document(doc_id, coverage="초안 작성 중입니다")
                drafter.generate(db, doc_id)
                fresh = db.get_document(doc_id) or {}
                if not (fresh.get("draft") or "").strip():
                    return f"「{doc['filename']}」 초안을 만들지 못했습니다"
                return (f"「{doc['filename']}」 초안을 만들었습니다."
                        f" 문서 화면에서 확인하십시오.")
            if key == "llm.test":
                from zzaimy.generate import llm_connections as _lcm

                cid = str(ctx.get("cid", ""))
                conn = _lcm.get(cid) if cid else None
                if conn is None:
                    return "확인할 연결을 찾지 못했습니다"
                pr = _lcm.probe(conn)
                _lcm.record_check(cid, pr["ok"],
                                  f"모델 {len(pr['models'])}개" if pr["ok"] else pr["error"])
                return (f"「{conn['name']}」 연결됨 · 모델 {len(pr['models'])}개"
                        if pr["ok"] else f"「{conn['name']}」 연결 실패 — {pr['error']}")
        except Exception as e:                      # 어떤 실패도 대화를 끊지 않는다
            return f"처리하지 못했습니다 ({type(e).__name__})"
        return ""

    def _page_actions(page: str, question: str = "") -> list[dict]:
        """말과 화면을 보고 지금 할 수 있는 일 — 되돌리기 어려운 일만 단추로 남긴다."""
        from zzaimy.app import actions as _acts

        extra: dict = {}
        active = [c for c in llm_connections.list_public() if c.get("active")]
        if active:
            extra["cid"] = active[0]["id"]
        if "doc_id" not in _acts.context_from_page(page):
            found = _find_document(question)
            if found is not None:
                extra["doc_id"] = found
        extra["question"] = question          # 고치기 요청은 말 자체가 내용이다
        return _acts.suggest(question, page, extra)

    @app.post("/chat/ask")
    def chat_ask(
        request: Request,
        background: BackgroundTasks,
        question: str = Form(...),
        context: str = Form(""),
        session_id: int | None = Form(None),
        page: str = Form(""),
    ):
        """화면을 떠나지 않고 묻는다 — 떠 있는 에이전트 창과 글 선택 질문이 쓴다."""
        q = question.strip()
        if not q:
            return {"ok": False, "error": "질문을 입력하세요"}
        quoted = context.strip()
        if quoted:
            if len(quoted) > 1200:
                quoted = quoted[:1200]
            asked = f"「{quoted}」\n\n{q}"
        else:
            asked = q
        project_match = re.fullmatch(r"/project/(\d+)", page)
        doc_match = re.fullmatch(r"/doc/(\d+)", page)
        page_project = int(project_match[1]) if project_match else None
        if doc_match:
            page_doc = db.get_document(int(doc_match[1]))
            if page_doc and page_doc.get("owner") == request.state.user:
                page_project = page_doc.get("project_id")
        if page_project:
            project = db.get_project(page_project)
            if not project or project.get("owner") != request.state.user:
                raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
        if session_id is not None:
            session = _owned_chat(request, session_id)
            if page_project and session.get("project_id") != page_project:
                raise HTTPException(409, "다른 프로젝트의 대화입니다. 이 프로젝트에서 새 대화를 시작해 주세요.")
            last = db.list_chats(session_id, limit=1)
            if session_id in _chat_running or (last and last[-1]["role"] == "user"):
                return {"ok": False, "error": "앞선 답변을 기다리는 중입니다", "session_id": session_id}
        else:
            session_id = db.create_chat_session(
                title=q[:60], project_id=page_project, owner=getattr(request.state, "user", "zzaimy")
            )
        db.add_chat(session_id, "user", asked)
        found = _page_actions(page, q)
        done: list[str] = []
        for a in [x for x in found if x.get("auto")][:1]:
            line = _run_action(a["key"], a.get("ctx") or {})
            if line:
                done.append(line)
                db.add_chat(session_id, "assistant", line)
        _schedule_answer(background, session_id, asked, None, [])
        return {"ok": True, "session_id": session_id, "done": done,
                "actions": [x for x in found if not x.get("auto")]}

    def _auto_route(db_, doc_id: int, user_chose_type: bool) -> str:
        """반입된 문서의 갈래와 영역을 스스로 정한다.

        담당자가 직접 고른 값은 건드리지 않는다. 확신이 없으면 그대로 둔다 —
        억지로 붙인 갈래는 없느니만 못하다.
        """
        from zzaimy.app import doc_routing

        doc = db_.get_document(doc_id)
        if doc is None:
            return ""
        parts = [c.get("content") or "" for c in db_.list_doc_chunks(doc_id)
                 if not (c.get("content") or "").startswith("{")]
        body = "\n".join(parts)[:4000] or (doc.get("masked_text") or "")[:4000]
        scanned = (doc.get("parse_note") or "").find("OCR") >= 0
        r = doc_routing.route(db_, doc.get("filename") or "", body,
                              db_.get_doc_identity(doc_id), scanned)
        said: list[str] = []
        if not user_chose_type and r["doc_type"] and r["doc_type"] != doc.get("doc_type"):
            db_.set_document_type(doc_id, r["doc_type"])
            said.append(f"갈래를 「{INBOX_TYPES.get(r['doc_type'], r['doc_type'])}」로 정했습니다"
                        f" ({r['why_type']})")
        if r["sector"] and r["sector"] != doc.get("sector"):
            db_.set_document_sector(doc_id, r["sector"])
            said.append(f"영역을 「{SECTOR_LABELS.get(r['sector'], r['sector'])}」로 정했습니다"
                        f" ({r['why_sector']})")
        if r.get("kind") and r["kind"] != doc.get("kind"):
            db_.set_document_kind(doc_id, r["kind"])
            said.append(f"서류를 「{doc_routing.KINDS.get(r['kind'], r['kind'])}」로 보았습니다 ({r['why_kind']})")
        return " / ".join(said)

    def _process_then_identify(db_, doc_id: int, stored, user_chose_type: bool = True) -> None:
        """접수 처리에 이어 문서의 정체까지 한 번에 읽는다.

        반입 시점에 끝나야 담당자가 문서마다 버튼을 누르지 않는다. 모델이 없거나
        실패하면 조용히 넘어간다 — 접수 자체가 막히면 안 되기 때문이다.
        """
        processor.process(db_, doc_id, stored)
        try:
            _identify_document(db_, doc_id)
        except Exception:
            pass
        try:
            _auto_route(db_, doc_id, user_chose_type)
        except Exception:
            pass

    def _identify_document(db_, doc_id: int) -> dict:
        """문서 본문에서 정체를 인출해 저장한다. 확인된 값이 없으면 저장하지 않는다."""
        from zzaimy.app import doc_identity as di

        doc = db_.get_document(doc_id)
        if doc is None:
            return {"ok": False, "identity": {}, "dropped": [], "error": "없는 문서입니다"}
        parts = [c.get("content") or "" for c in db_.list_doc_chunks(doc_id)]
        if not any(p.strip() for p in parts):
            parts = [r.get("content") or "" for r in db_.list_regulation_chunks()
                     if r["doc_id"] == doc_id]
        body = "\n".join(p for p in parts if p.strip()) or (doc.get("masked_text") or "")
        r = di.extract(body, _identity_call)
        if r["ok"]:
            db_.set_doc_identity(doc_id, r["identity"])
        return r

    def _identity_call(prompt: str) -> str:
        """정체 파악에 쓰는 모델 호출 — 기본 연결을 그대로 쓴다."""
        from zzaimy.generate.client import VllmClient

        c = VllmClient(role="answer")
        r = c.client.chat.completions.create(
            model=c.model, temperature=0.0, max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        return r.choices[0].message.content or ""

    @app.post("/doc/{doc_id}/revise")
    def doc_revise(background: BackgroundTasks, doc_id: int, want: str = Form("")):
        """말한 대로 초안을 고친다 — 요청을 남기고 다시 만든다."""
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

    @app.post("/doc/{doc_id}/scope")
    def doc_set_scope(request: Request, doc_id: int, dept: str = Form(""), access_level: str = Form(""),
                      back: str = Form("")):
        """문서의 부서·열람 등급 바꾸기 — 조각에도 즉시 옮겨져 검색 범위가 따라간다(C-60 화면이 부른다).
        관리자, 또는 그 문서를 올린 사람만."""
        from zzaimy.app.access_policy import LEVELS

        doc = db.get_document(doc_id)
        if doc is None:
            raise HTTPException(404)
        user = getattr(request.state, "user", "")
        if getattr(request.state, "role", "") != "dev" and doc.get("owner") != user:
            raise HTTPException(403, "문서를 올린 담당자나 관리자만 바꿀 수 있습니다")
        db.set_document_scope(doc_id, dept=dept.strip() or None,
                              access_level=access_level if access_level in LEVELS else None)
        dest = back if back.startswith("/") and not back.startswith("//") else f"/doc/{doc_id}"
        return RedirectResponse(dest, status_code=303)

    @app.post("/doc/{doc_id}/route")
    def doc_route(doc_id: int):
        """이 문서의 갈래와 영역을 다시 정한다."""
        from urllib.parse import quote as _q

        if db.get_document(doc_id) is None:
            raise HTTPException(404)
        line = _auto_route(db, doc_id, user_chose_type=False)
        return RedirectResponse(
            f"/doc/{doc_id}?ok=" + _q(line or "지금 값이 알맞아 그대로 두었습니다"),
            status_code=303,
        )

    @app.post("/doc/{doc_id}/identity")
    def doc_identity(doc_id: int):
        """이 문서가 어떤 사업에 관한 것인지 본문에서 인출한다.

        값은 모두 본문과 대조해 확인된 것만 남긴다. 확인되지 않은 값은 버리고
        버린 이유를 함께 돌려준다 — 근거 없는 값이 화면에 남으면 안 되기 때문이다.
        """
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

    @app.post("/dev/route-all")
    def dev_route_all():
        """분류가 비어 있거나 뭉뚱그려진 문서를 한꺼번에 다시 정한다.

        확신이 없는 문서는 건드리지 않는다. 결과는 건수로만 알린다.
        """
        from urllib.parse import quote as _q

        changed = 0
        for d in db.list_documents():
            try:
                if _auto_route(db, d["id"], user_chose_type=False):
                    changed += 1
            except Exception:
                continue
        return RedirectResponse(
            "/?ok=" + _q(f"문서 {changed}건의 갈래·영역을 다시 정했습니다"),
            status_code=303,
        )

    @app.get("/graph/evidence")
    def graph_evidence(kind: str = "", s: str = "", t: str = "", term: str = ""):
        """두 문서를 이은 근거 문장 — 미리보기 앞부분이 아니라 본문 전체에서 찾는다.

        s 는 출처 문서 노드(dN), t 는 상대 노드, term 은 화면이 이미 아는 표현이다.
        term 이 없고 인용 관계이면 상대 문서의 규정 제목을 근거 표현으로 삼는다.
        """
        if not s.startswith("d") or not s[1:].isdigit():
            return {"ok": False, "term": "", "quotes": [], "n": 0, "error": "출처 문서가 아닙니다"}
        src_id = int(s[1:])
        reg_rows = db.list_regulation_chunks()

        needle = (term or "").strip()
        if not needle and kind == "cites" and t.startswith("d") and t[1:].isdigit():
            dst_id = int(t[1:])
            for c in reg_rows:
                if c["doc_id"] == dst_id and (c.get("reg_title") or "").strip():
                    needle = c["reg_title"].strip()
                    break
        if not needle:
            return {"ok": False, "term": "", "quotes": [], "n": 0,
                    "error": "근거로 삼을 표현을 찾지 못했습니다"}

        blocks: list[tuple[str, str]] = [
            ((c.get("heading") or "").strip(), c.get("content") or "")
            for c in reg_rows if c["doc_id"] == src_id
        ]
        if not blocks:
            blocks = [((c.get("kind") or "").strip(), c.get("content") or "")
                      for c in db.list_doc_chunks(src_id)]

        quotes: list[dict] = []
        hits = 0
        for heading, body in blocks:
            start = body.find(needle)
            while start >= 0:
                hits += 1
                if len(quotes) < 3:
                    left = max(0, start - 90)
                    right = min(len(body), start + len(needle) + 150)
                    quotes.append({
                        "heading": heading,
                        "text": ("…" if left else "") + body[left:right].strip()
                                + ("…" if right < len(body) else ""),
                    })
                start = body.find(needle, start + len(needle))
        return {"ok": bool(hits), "term": needle, "quotes": quotes, "n": hits,
                "error": "" if hits else "본문에서 이 표현을 찾지 못했습니다"}

    @app.get("/chat/peek/{doc_id}")
    def chat_doc_peek(doc_id: int):
        """문서를 화면 안에서 바로 훑어본다 — 다른 페이지로 옮겨가지 않게 한다."""
        doc = db.get_document(doc_id)
        if doc is None:
            raise HTTPException(404)
        chunks = db.list_doc_chunks(doc_id)
        parts: list[str] = []
        total = 0
        for c in chunks:
            text = (c.get("content") or "").strip()
            if not text:
                continue
            parts.append(text)
            total += len(text)
            if total >= 2400:
                break
        return {
            "id": doc_id,
            "title": doc.get("filename", ""),
            "sector": SECTOR_LABELS.get(doc.get("sector", ""), doc.get("sector", "")),
            "doc_type": doc.get("doc_type", ""),
            "status": doc.get("status", ""),
            "n_chunks": len(chunks),
            "text": "\n\n".join(parts),
        }

    @app.get("/search", response_class=HTMLResponse)
    def search_all(request: Request, q: str = ""):
        """한 곳에서 찾기 — 문서·대화·근거 조각. 상단 검색창이 여기로 온다.

        대화는 제목·프로젝트·사업(주제)뿐 아니라 주고받은 글에서도 찾는다. 근거 조각은
        어휘 검색(Kiwi 명사 겹침)만 쓴다 — 검색창 한 번에 모델을 부르지 않기 위해서다.
        """
        import sqlite3 as _sq

        term = (q or "").strip()[:200]
        docs, chats, chunks, docs_more = [], [], [], False
        if term:
            owner = getattr(request.state, "user", "zzaimy")
            found = db.list_documents(q=term)
            seen = {d["id"] for d in found}
            with _sq.connect(db_path) as conn:      # 이름뿐 아니라 본문에서도 찾는다
                conn.row_factory = _sq.Row
                for r in conn.execute(
                    "SELECT * FROM documents WHERE instr(lower(COALESCE(masked_text,'')), lower(?)) > 0"
                    " ORDER BY id DESC LIMIT 20", (term,)):
                    if r["id"] not in seen:
                        found.append(dict(r))
                        seen.add(r["id"])
            docs_more = len(found) > 10
            for d in found[:10]:
                body = (d.get("masked_text") or "")
                i = body.lower().find(term.lower())
                docs.append(dict(d, snippet=(" ".join(body[max(0, i - 40):i + 80].split())
                                             if i >= 0 else "")))
            rows = chat_history.sessions(owner, term, False, 0, 10,
                                         chat_topics.match_clause(term), 'all')
            names = chat_topics.topics([r["id"] for r in rows])
            with _sq.connect(db_path) as conn:
                conn.row_factory = _sq.Row
                for r in rows:
                    hit = conn.execute(
                        "SELECT content FROM chat_messages WHERE session_id = ?"
                        " AND instr(lower(content), lower(?)) > 0 ORDER BY id LIMIT 1",
                        (r["id"], term)).fetchone()
                    snip = ""
                    if hit:
                        body = hit["content"]
                        i = body.lower().find(term.lower())
                        snip = " ".join(body[max(0, i - 40):i + 80].split())
                    chats.append(dict(r, topic=names.get(r["id"]), snippet=snip))
            try:
                from zzaimy.app.regulations import sparse_search

                for h in sparse_search(db, term, top_k=8):
                    body = h.get("content") or ""
                    i = body.lower().find(term.lower())
                    chunks.append({
                        "doc_id": h.get("doc_id"), "reg_title": h.get("reg_title") or "문서",
                        "heading": h.get("heading") or "",
                        "snippet": " ".join((body[max(0, i - 40):i + 120] if i >= 0 else body[:120]).split()),
                    })
            except Exception:
                chunks = []
        return templates.TemplateResponse(request, "search.html", ctx(request, {
            "q": term, "docs": docs, "docs_more": docs_more, "chats": chats, "chunks": chunks,
        }))

    @app.get("/criteria", response_class=HTMLResponse)
    def criteria(request: Request):
        from zzaimy.app.doc_routing import KINDS

        docs = db.list_documents("regulation")
        counts = db.regulation_chunk_counts()
        families = db.family_counts("regulation")
        names = {d["id"]: d["filename"] for d in docs}
        for d in docs:
            d["n_chunks"] = counts.get(d["id"], 0)
            d["kind_label"] = KINDS.get(d.get("kind") or "", "")
            # 같은 제목의 판본이 여럿이면 몇 판째인지·어느 공고에 딸렸는지 보여 준다
            d["family_count"] = families.get(d.get("family") or "", 1)
            d["head_title"] = names.get(d.get("related_criteria_id") or -1, "")
        return templates.TemplateResponse(
            request, "criteria.html", ctx(request, {"documents": docs, "kind_labels": KINDS})
        )

    @app.post("/criteria/upload")
    def criteria_upload(
        background: BackgroundTasks,
        file: list[UploadFile] = File(...),
        sector: str = Form("common"),
        link_project_id: int | None = Form(None),
    ):
        if sector not in SECTOR_LABELS:
            raise HTTPException(400, f"알 수 없는 업무 영역입니다: {sector}")
        # 일괄 등록 — 형식 검사를 전부 통과해야 하나라도 저장한다
        for f in file:
            suffix = Path(f.filename or "이름없음").suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                raise HTTPException(400, f"허용되지 않는 파일 형식입니다: {suffix}")
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
            stored = storage.adopt_original(db, doc_id, stored)
            new_ids.append(doc_id)
            # 기준 문서로 올린 것이므로 갈래는 담당자가 정한 것으로 본다
            background.add_task(_process_then_identify, db, doc_id, stored, True)
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
        request: Request,
        sector: str = Form(...), name: str = Form(...), due_date: str = Form(""),
    ):
        if sector not in INBOX_TYPES:
            raise HTTPException(400, f"알 수 없는 업무 영역입니다: {sector}")
        if not name.strip():
            raise HTTPException(400, "프로젝트 이름을 입력하세요")
        pid = db.create_project(
            sector, name.strip(), due_date=due_date.strip(),
            owner=getattr(request.state, "user", "zzaimy"),
        )
        return RedirectResponse(f"/project/{pid}", status_code=303)

    _CRITERIA_KINDS = {"announcement", "guideline", "criteria", "regulation"}
    _BASE_PLAN = re.compile(r"기본\s*계획|추진\s*계획|시행\s*계획|운영\s*계획")

    def _bundle_role(filename: str) -> str:
        """묶음 속 파일이 기준(공고·기본계획·지침)인지 접수(계획서·양식)인지 — 이름의 서류 갈래로 정한다."""
        from zzaimy.app.doc_routing import guess_kind

        kind, _why = guess_kind(filename, "")
        if kind in _CRITERIA_KINDS or (kind == "plan" and _BASE_PLAN.search(filename or "")):
            return "criteria"
        return "intake"

    def _intake_bundle(request: Request, background: BackgroundTasks, project: dict, files) -> dict:
        """여러 파일을 한 프로젝트에 들인다 — 공고·기본계획·지침은 기준 문서로 등록해 프로젝트에 잇고, 계획서·양식은 접수 문서로."""
        from zzaimy.app.access_policy import classify

        owner = getattr(request.state, "user", "zzaimy")
        made = {"criteria": [], "intake": [], "skipped": []}
        for f in files:
            name = f.filename or "이름없음"
            suffix = Path(name).suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                made["skipped"].append(name)
                continue
            stored = inbox_dir / f"{uuid.uuid4().hex}{suffix}"
            with stored.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            role_ = _bundle_role(name)
            if role_ == "criteria":
                doc_id = db.add_document(filename=name, stored_path=str(stored), doc_type="regulation",
                                         sector=project["sector"], project_id=int(project["id"]), owner=owner)
                stored = storage.adopt_original(db, doc_id, stored)
                db.add_project_criteria(int(project["id"]), [doc_id])
                background.add_task(_process_then_identify, db, doc_id, stored, True)
                made["criteria"].append(doc_id)
            else:
                d_dept, d_level = classify(project["sector"], owner=owner, uploader_dept=getattr(request.state, "dept", ""),
                                           project_dept=project.get("dept"))
                doc_id = db.add_document(filename=name, stored_path=str(stored), doc_type=project["sector"],
                                         project_id=int(project["id"]), owner=owner, dept=d_dept, access_level=d_level)
                stored = storage.adopt_original(db, doc_id, stored)
                background.add_task(_process_then_identify, db, doc_id, stored, True)
                made["intake"].append(doc_id)
        return made

    def _title_from_bundle(files) -> str:
        """묶음에서 프로젝트 이름 — 공고·기본계획 파일 이름에서 붙임 번호·서류 낱말을 뗀 것, 없으면 첫 파일 제목."""
        from zzaimy.app.doc_family import _ATTACH, _EXT, _KIND_WORDS

        names = [f.filename or "" for f in files if f.filename]
        cand = [n for n in names if _bundle_role(n) == "criteria"] or names
        if not cand:
            return "새 프로젝트"
        t = _EXT.sub("", _ATTACH.sub("", storage.title_of(cand[0])))
        t = re.sub(r"\s*[\[(（].*?[\])）]\s*$", "", t)
        t = _KIND_WORDS.sub("", t).strip(" ·-_,.")
        return re.sub(r"\s+", " ", t)[:60] or cand[0][:60]

    @app.post("/projects/bundle")
    def create_project_bundle(request: Request, background: BackgroundTasks, sector: str = Form("grant"),
                              name: str = Form(""), due_date: str = Form(""), file: list[UploadFile] = File([])):
        """문서 묶음(공고·기본계획·양식·계획서 …)으로 프로젝트를 만든다 — 이름은 비우면 묶음에서 뽑는다."""
        if sector not in INBOX_TYPES:
            raise HTTPException(400, f"알 수 없는 업무 영역입니다: {sector}")
        files = [f for f in file if f and f.filename]
        if not files:
            raise HTTPException(400, "파일을 하나 이상 골라 주세요")
        title = name.strip() or _title_from_bundle(files)
        pid = db.create_project(sector, title, due_date=due_date.strip(), owner=getattr(request.state, "user", "zzaimy"))
        project = db.get_project(pid) or {"id": pid, "sector": sector, "name": title}
        made = _intake_bundle(request, background, project, files)
        return RedirectResponse(f"/project/{pid}?bundle={len(made['criteria'])}+{len(made['intake'])}", status_code=303)

    @app.post("/project/{project_id}/bundle")
    def project_bundle(request: Request, background: BackgroundTasks, project_id: int, file: list[UploadFile] = File([])):
        project = db.get_project(project_id)
        if project is None or project.get("owner") != getattr(request.state, "user", "zzaimy"):
            raise HTTPException(404)
        files = [f for f in file if f and f.filename]
        if not files:
            raise HTTPException(400, "파일을 하나 이상 골라 주세요")
        made = _intake_bundle(request, background, project, files)
        return RedirectResponse(f"/project/{project_id}?bundle={len(made['criteria'])}+{len(made['intake'])}", status_code=303)

    @app.get("/api/chat/{session_id}/documents")
    def chat_project_documents(request: Request, session_id: int):
        """대화 문서함의 플랫폼 문서 목록 — 이 대화의 첨부, 프로젝트의 접수·기준 문서. 각각 /doc/{id}/view 로 연다."""
        from zzaimy.app.access_policy import visible
        from zzaimy.app.doc_routing import KINDS

        session_ = db.get_chat_session(session_id)
        owner = getattr(request.state, "user", "zzaimy")
        if session_ is None or session_.get("owner") not in (None, owner):
            raise HTTPException(404)
        dept, role_ = getattr(request.state, "dept", None), getattr(request.state, "role", "")
        attached = {f["doc_id"] for f in db.list_files(kind="attachment", session_id=session_id) if f.get("doc_id")}
        pid = int(session_["project_id"]) if session_.get("project_id") else None
        project = db.get_project(pid) if pid else None
        rows: list[dict] = []
        seen: set[int] = set()

        def _add(d: dict | None, group: str) -> None:
            if not d or d["id"] in seen or not visible(d, dept=dept, user=owner, role=role_):
                return
            seen.add(d["id"])
            raw = db.get_setting(f"doc_google:{d['id']}", "") or ""
            rows.append({"id": d["id"], "name": storage.title_of(d.get("filename") or ""), "group": group,
                         "kind": KINDS.get(d.get("kind") or "", ""), "status": STATUS_LABELS.get(d.get("status"), d.get("status")),
                         "url": f"/doc/{d['id']}/view", "page": f"/doc/{d['id']}",
                         "google": (_aj.loads(raw).get("url") if raw else None)})

        for did in sorted(attached):
            _add(db.get_document(did), "첨부")
        if project:
            for did in db.get_project_criteria_ids(pid):
                _add(db.get_document(did), "기준")
            for d in db.list_documents(project["sector"], project_id=pid):
                _add(d, "접수")
        return {"project": (project or {}).get("name"), "project_id": pid, "documents": rows}

    @app.get("/api/doc/{doc_id}/google")
    def doc_google(request: Request, doc_id: int):
        """문서의 구글 열람본 — 없으면 지금 만든다. 문서함 패널이 iframe 으로 열 주소(embed_url)까지 준다."""
        from zzaimy.ingest import gdrive_files

        doc = db.get_document(doc_id)
        if doc is None:
            raise HTTPException(404)
        owner = getattr(request.state, "user", "zzaimy")
        acct_ = accounts.get(owner, {}) if password is not None else {}
        email = gdrive_files.account_for(db, owner, acct_.get("dept") or None)
        if not email or not gdrive_files.has_file_scope(email):
            raise HTTPException(400, "구글 열람본을 만들 허용 계정이 없습니다 — 개발자 도구 → 구글 드라이브에서 연결해 주세요")
        project = db.get_project(int(doc["project_id"])) if doc.get("project_id") else None
        try:
            folder = gdrive_files.project_folder_for(db, email, project, acct_.get("dept") or None, sub="첨부")
            made = gdrive_files.google_copy(db, doc, email, folder)
        except Exception as e:
            raise HTTPException(400, f"구글 열람본을 만들지 못했습니다: {e}")
        return {"id": made["id"], "name": made.get("name") or doc["filename"], "mime": made["mime"], "url": made["url"],
                "embed_url": gdrive_files.embed_url(made["id"], made["mime"]), "account": email}

    @app.get("/doc/{doc_id}/view")
    def doc_view(request: Request, doc_id: int):
        """문서를 구글에서 연다 — 엑셀·PPT·워드는 시트·슬라이드·독스, 한글은 복원 docx→독스, PDF·그림은 드라이브 미리보기.
        열람본이 없으면 지금 만든다. 허용 계정이 없으면 플랫폼 문서 화면으로."""
        from zzaimy.ingest import gdrive_files

        doc = db.get_document(doc_id)
        if doc is None:
            raise HTTPException(404)
        owner = getattr(request.state, "user", "zzaimy")
        acct_ = accounts.get(owner, {}) if password is not None else {}
        email = gdrive_files.account_for(db, owner, acct_.get("dept") or None)
        if not email or not gdrive_files.has_file_scope(email):
            return RedirectResponse(f"/doc/{doc_id}?view=local", status_code=303)
        project = db.get_project(int(doc["project_id"])) if doc.get("project_id") else None
        try:
            folder = gdrive_files.project_folder_for(db, email, project, acct_.get("dept") or None, sub="첨부")
            made = gdrive_files.google_copy(db, doc, email, folder)
        except Exception as e:
            return RedirectResponse(f"/doc/{doc_id}?view=local&err={type(e).__name__}", status_code=303)
        return RedirectResponse(made["url"], status_code=303)

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
        uname = username.strip()
        if not _verify_pw(uname, pw):
            return RedirectResponse("/login?err=1", status_code=303)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            "zz_session", _make_session(uname), httponly=True, samesite="lax",
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
            ctx(request, {"documents": docs, "active_tab": "all", "err_ext": err}),
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
            stored = storage.adopt_original(db, doc_id, stored)
            background.add_task(processor.process, db, doc_id, stored)
        return RedirectResponse("/ocr", status_code=303)

    # --- 개발 현황 (개발자 뷰 — 플랫폼 기능이 아니라 캡스톤 개발 과정용) ---

    _DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"

    def _md_view(text: str) -> "Markup":
        """문서 뷰어용 마크다운 렌더 — 제목·표·목록·굵게만 지원."""
        from markupsafe import Markup as _M
        from markupsafe import escape as _esc

        import re as _mre

        def rich(t: str) -> str:
            # 이스케이프한 뒤 굵게(**)와 줄바꿈(<br>)만 되살린다 — 생성 문서의 다른 태그는 글자 그대로
            s2 = _mre.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", str(_esc(t)))
            return s2.replace("&lt;br&gt;", "<br>").replace("&lt;br/&gt;", "<br>").replace("&lt;br /&gt;", "<br>")

        out: list[str] = []
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            ln = lines[i]
            if ln.lstrip().startswith("```"):
                # 코드·도표 블록 — 펜스는 감추고 안쪽은 원문 그대로 보존
                i += 1
                body = []
                while i < len(lines) and not lines[i].lstrip().startswith("```"):
                    body.append(lines[i])
                    i += 1
                i += 1  # 닫는 펜스 소비
                out.append(
                    '<pre class="doc-text" style="white-space:pre; overflow-x:auto;'
                    ' font-size:12px; line-height:1.5;">'
                    + str(_esc("\n".join(body))) + "</pre>"
                )
                continue
            if ln.startswith("|") and ln.rstrip().endswith("|"):
                rows = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    r = lines[i].strip()
                    i += 1
                    if set(r) <= set("|-: "):
                        continue
                    rows.append([c.strip() for c in r.strip("|").split("|")])
                if rows:
                    t = ['<div class="table-scroll"><table class="extract"><tr>']
                    t += [f"<th>{rich(c)}</th>" for c in rows[0]]
                    t.append("</tr>")
                    for row_cells in rows[1:]:
                        t.append("<tr>" + "".join(
                            f"<td>{rich(c)}</td>" for c in row_cells) + "</tr>")
                    t.append("</table></div>")
                    out.append("".join(t))
                continue
            if ln.startswith("# "):
                out.append(f'<h3 style="margin:18px 0 8px;">{rich(ln[2:])}</h3>')
            elif ln.startswith("## "):
                out.append(f'<h4 class="extract-h">{rich(ln[3:])}</h4>')
            elif ln.startswith("### "):
                out.append(
                    f'<h5 style="margin:12px 0 4px; font-size:13.5px;">'
                    f"{rich(ln[4:])}</h5>")
            elif ln.lstrip().startswith("- "):
                items = []
                while i < len(lines) and lines[i].lstrip().startswith("- "):
                    cur_ln = lines[i]
                    depth = (len(cur_ln) - len(cur_ln.lstrip())) // 2
                    body = [cur_ln.lstrip()[2:]]
                    i += 1
                    # 들여쓴 이어짐 줄은 같은 항목에 붙인다
                    while (
                        i < len(lines) and lines[i].strip()
                        and not lines[i].lstrip().startswith("- ")
                        and not lines[i].startswith(("#", "|"))
                    ):
                        body.append(lines[i].strip())
                        i += 1
                    pad = 20 + depth * 16
                    items.append(
                        f'<li style="margin-left:{pad - 20}px;">'
                        f'{rich(" ".join(body))}</li>')
                out.append(
                    '<ul style="margin:4px 0 10px; padding-left:20px;'
                    ' font-size:13.5px; line-height:1.7;">'
                    + "".join(items) + "</ul>")
                continue
            elif ln.strip():
                out.append(
                    f'<p style="margin:4px 0; font-size:13.5px;'
                    f' line-height:1.75;">{rich(ln)}</p>')
            i += 1
        return _M("\n".join(out))

    templates.env.filters["md_view"] = _md_view

    def _cite_links(html, sources) -> "Markup":
        """답변에 나온 문서 이름을 근거 문서 링크로 — 근거 목록에 있는 이름만."""
        from markupsafe import Markup as _M

        from zzaimy.app.citations import linkify

        return _M(linkify(str(html), sources or []))

    templates.env.filters["cite_links"] = _cite_links

    _ATTACH_LINE = re.compile(r"^\[첨부(?:#(\d+))?\] ([^\n]+)\n?")

    def _attach_view(text: str):
        """사용자 말풍선 — 첫 줄이 첨부면 문서 보기 링크로 바꾸고 나머지는 그대로(이스케이프)."""
        from markupsafe import Markup, escape

        m = _ATTACH_LINE.match(text or "")
        if not m:
            return escape(text or "")
        did, name = m.group(1), m.group(2)
        rest = (text or "")[m.end():]
        head = (Markup('<a class="chat-attach" href="/doc/%s">첨부 · %s</a>') % (did, name) if did
                else Markup('<span class="chat-attach">첨부 · %s</span>') % name)
        return head + Markup("<br>") + escape(rest) if rest else head

    templates.env.filters["attach_view"] = _attach_view

    def _eval_md(name: str) -> str:
        """기계가 쓴 사본(data/platform/eval/) 먼저, 없으면 저장소 사본(docs/)."""
        from zzaimy.eval.retrieval_eval import MARKDOWN_PATH

        try:
            path = MARKDOWN_PATH.parent / name
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            pass
        return _dev_read(name)

    def _dev_read(name: str, tail_lines: int | None = None) -> str:
        try:
            text = (_DOCS_DIR / name).read_text(encoding="utf-8")
            if tail_lines:
                text = "\n".join(text.splitlines()[-tail_lines:])
            return text
        except OSError:
            return ""

    def _data_overview() -> dict:
        """데이터 열람용 통계 개요 — 핵심 지표 타일 + 주요 현황 분해."""
        import sqlite3

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        q = conn.execute

        def one(sql: str):
            try:
                return q(sql).fetchone()[0] or 0
            except Exception:
                return 0

        def group(sql: str, labels: dict | None = None) -> list[dict]:
            try:
                rows = q(sql).fetchall()
            except Exception:
                return []
            out = []
            for val, n in rows:
                key = "" if val is None else str(val)
                lbl = (labels or {}).get(key) or key or "(미지정)"
                out.append({"label": lbl, "n": n})
            return out

        n_docs = one("SELECT COUNT(*) FROM documents")
        n_reviewed = one("SELECT COUNT(*) FROM documents WHERE status='reviewed'")
        n_recent = one(
            "SELECT COUNT(*) FROM documents WHERE created_at >= datetime('now','-7 days')")
        n_proj = one("SELECT COUNT(*) FROM projects")
        n_sess = one("SELECT COUNT(*) FROM chat_sessions")
        n_msg = one("SELECT COUNT(*) FROM chat_messages")
        n_reg = one("SELECT COUNT(*) FROM regulation_chunks")
        n_reg_docs = one("SELECT COUNT(DISTINCT doc_id) FROM regulation_chunks")
        n_ent = one("SELECT COUNT(*) FROM entities")
        n_ds = one("SELECT COUNT(*) FROM datasets")
        n_pairs = one("SELECT COALESCE(SUM(n_pairs),0) FROM datasets")
        n_qr_open = one("SELECT COUNT(*) FROM quality_reports WHERE status='open'")
        n_egr = one("SELECT COUNT(*) FROM egress_requests")

        tiles = [
            {"icon": "solar:documents-linear", "label": "접수 문서",
             "value": n_docs, "sub": f"검토 완료 {n_reviewed} · 최근 7일 {n_recent}"},
            {"icon": "solar:folder-2-linear", "label": "프로젝트",
             "value": n_proj, "sub": "사업·공고 단위"},
            {"icon": "solar:chat-round-line-linear", "label": "대화",
             "value": n_sess, "sub": f"메시지 {n_msg}건"},
            {"icon": "solar:book-2-linear", "label": "규정 문단",
             "value": n_reg, "sub": f"검색 근거 · 문서 {n_reg_docs}종"},
            {"icon": "solar:structure-linear", "label": "개체(그래프)",
             "value": n_ent, "sub": "사업·부서·연도 등"},
            {"icon": "solar:database-linear", "label": "학습 데이터쌍",
             "value": n_pairs, "sub": f"데이터셋 {n_ds}개"},
        ]
        groups = []
        for title, sql, labels in [
            ("문서 상태",
             "SELECT status, COUNT(*) FROM documents GROUP BY status ORDER BY 2 DESC",
             STATUS_LABELS),
            ("문서 계열",
             "SELECT sector, COUNT(*) FROM documents GROUP BY sector ORDER BY 2 DESC",
             SECTOR_LABELS),
            ("담당자 판정",
             "SELECT decision, COUNT(*) FROM documents GROUP BY decision ORDER BY 2 DESC",
             DECISION_LABELS),
            ("문서 유형",
             "SELECT doc_type, COUNT(*) FROM documents GROUP BY doc_type ORDER BY 2 DESC",
             DOC_TYPE_LABELS),
        ]:
            rows = group(sql, labels)
            if rows:
                groups.append({"title": title, "rows": rows})
        if n_qr_open:
            groups.append({"title": "품질 리포트",
                           "rows": [{"label": "미해결", "n": n_qr_open}]})
        if n_egr:
            groups.append({"title": "외부 AI 참조 (자동 분류)",
                           "rows": group(
                               "SELECT verdict, COUNT(*) FROM egress_requests"
                               " GROUP BY verdict ORDER BY 2 DESC",
                               EGRESS_VERDICT_LABELS)})
        conn.close()
        return {"tiles": tiles, "groups": groups}

    @app.get("/dev/db", response_class=HTMLResponse)
    def dev_db(
        request: Request, tab: str = "", q: str = "", type: str = "",
        doc: int | None = None, table: str = "",
    ):
        """데이터 열람 — 문서 중심 탐색기. 탭: 문서·규정·국고 코퍼스·채팅 기록.

        표·행 덤프 대신 문서 하나의 개요(마스킹 기록)·RAG 조각(임베딩 유무)·그래프
        연관을 본다. 조립은 zzaimy.app.data_explorer(순수 함수)가 하고, 모델이
        필요한 검색·그래프 생성은 여기서 함수로 넘긴다. 예전 ?table= 주소는
        가장 가까운 탭으로 보낸다.
        """
        from zzaimy.app import data_explorer as dx

        tab = dx.normalize_tab(tab, table)
        index = dx.embedding_index()
        if tab == "docs":
            def _graph() -> dict | None:
                from zzaimy.graph.build import build_graph

                try:
                    return build_graph(db)
                except Exception:   # 그래프는 부가 정보 — 실패해도 열람은 된다
                    return None

            view = dx.docs_tab(db, q=q, doc_type=type, doc_id=doc,
                               graph_fn=_graph, index=index)
        elif tab == "regulation":
            def _search(text: str) -> list[dict]:
                from zzaimy.app.regulations import find_relevant

                return find_relevant(db, text, top_k=10)   # 채팅과 같은 운영 검색 경로

            view = dx.regulation_tab(db, q=q, doc_id=doc, search_fn=_search, index=index)
        elif tab == "corpus":
            from zzaimy.app.corpus_search import _CORPUS_NPZ, corpus_hybrid_search

            cdb = Database(_CORPUS_DB) if _CORPUS_DB.exists() else None
            view = dx.corpus_tab(
                cdb, q=q, dense_ready=_CORPUS_NPZ.exists(),
                search_fn=lambda text: corpus_hybrid_search(cdb, text, top_k=15),
            )
        else:
            view = dx.chat_tab(db, sources_by_session=_chat_sources)
        return templates.TemplateResponse(request, "dev_db.html", ctx(request, {
            "tab": tab, "tabs": dx.TABS, "q": q, "type": type, "doc_id": doc,
            "view": view, "index": index,
            "overview": _data_overview(),
        }))

    def _run_dev_query(sql: str) -> dict:
        """읽기 전용 SELECT 1문만, 상한 50행 — 개발자 DB 점검용."""
        import sqlite3

        q = sql.strip().rstrip(";")
        if not q.lower().startswith("select") or ";" in q:
            return {"error": "SELECT 한 문장만 실행할 수 있습니다."}
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.execute(q)
            cols = [d[0] for d in cur.description or []]
            rows = [
                [str(v)[:200] if v is not None else "" for v in r]
                for r in cur.fetchmany(50)
            ]
            conn.close()
            return {"cols": cols, "rows": rows, "sql": sql}
        except sqlite3.Error as e:
            return {"error": str(e), "sql": sql}

    def _dev_progress() -> dict:
        """구축 현황 — docs/progress.json 의 기능 상태 목록. 영역별 막대는 기능 상태에서
        계산한 완료 비율(완료/전체)이다. 수기로 적은 퍼센트는 쓰지 않는다(근거 없는 수치 금지)."""
        import json as _pj

        try:
            data = _pj.loads((_DOCS_DIR / "progress.json").read_text())
        except (OSError, ValueError):
            return {"updated": "", "features": [], "areas": []}
        feats = data.get("features", [])
        order: list[str] = []
        agg: dict[str, dict] = {}
        for f in feats:
            area = f.get("area") or "기타"
            if area not in agg:
                agg[area] = {"name": area, "done": 0, "total": 0, "features": []}
                order.append(area)
            agg[area]["total"] += 1
            agg[area]["features"].append(f)          # 기능별 상태는 영역별로 묶어 보인다(2026-09-22)
            if f.get("status") == "done":
                agg[area]["done"] += 1
        for a in agg.values():
            a["pct"] = round(100 * a["done"] / a["total"]) if a["total"] else 0
        data["areas"] = [agg[k] for k in order]
        return data

    def _dev_doc_list(sub: str) -> list[dict]:
        """decisions/notes 목록 — 파일명 대신 문서 첫 제목을 보여준다."""
        d = _DOCS_DIR / sub
        out = []
        skip = {"README.md", "0000-template.md"}
        for f in sorted(d.glob("*.md")) if d.exists() else []:
            if f.name in skip:
                continue
            title = f.stem
            try:
                for ln in f.read_text(encoding="utf-8").splitlines()[:5]:
                    if ln.startswith("# "):
                        title = ln[2:].strip()
                        break
            except OSError:
                pass
            out.append({"file": f.name, "title": title, "sub": sub})
        return out

    def _dev_now() -> str:
        return _dev_read("dev-now.md")

    def _dev_now_parts() -> dict:
        """dev-now.md 를 허브용과 이력용으로 나눈다.

        허브(/dev)에는 '최근 작업' 중 최신 날짜 한 묶음만 보이고,
        변경 이력(/dev/history)에는 전체를 보인다.
        """
        text = _dev_now()
        head, sep, recent = text.partition("\n## 최근 작업")
        if not sep:
            return {"hub": text, "recent_all": "", "more": False}
        blocks = [b for b in re.split(r"\n(?=### \S)", recent) if b.strip()]
        if not blocks:
            return {"hub": head, "recent_all": "", "more": False}
        return {
            "hub": head.rstrip() + "\n\n## 최근 작업\n\n" + blocks[0].strip() + "\n",
            # 카드 제목이 '작업 기록'이므로 '## 최근 작업' 제목은 되풀이하지 않는다
            "recent_all": "\n\n".join(b.strip() for b in blocks) + "\n",
            "more": len(blocks) > 1,
        }

    # 커밋이 건드린 파일의 영역 — 변경 이력 줄마다 붙는 표식(어디를 고쳤는지 한눈에)
    _AREA_RULES = (
        ("src/zzaimy/app/templates/", "화면"), ("src/", "코드"), ("scripts/", "스크립트"),
        ("tests/", "테스트"), ("docs/decisions/", "결정"), ("docs/", "문서"), ("configs/", "설정"),
    )
    _git_cache: dict = {}

    def _commit_areas(paths: list[str]) -> list[str]:
        seen: list[str] = []
        for path in paths:
            area = next((a for pre, a in _AREA_RULES if path.startswith(pre)), "기타")
            if area not in seen:
                seen.append(area)
        return seen

    def _git_history() -> list[dict]:
        """커밋 이력을 깃에서 직접 읽는다 — 한 줄 = {day, time, subject, hash, areas, files}.
        저장소 사본(dev-changelog.md)은 손으로 다시 만들어야 낡았으므로(9/22) 깃이 정본이다.
        HEAD 가 같으면 캐시를 쓴다."""
        import subprocess

        root = _DOCS_DIR.parent
        try:
            head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                  timeout=5, cwd=str(root)).stdout.strip()
            if not head:
                return []
            if _git_cache.get("head") == head:
                return _git_cache["items"]
            r = subprocess.run(
                ["git", "log", "--no-merges", "--name-only", "--date=format:%Y-%m-%d %H:%M",
                 "--format=%x00%h\t%ad\t%s"],
                capture_output=True, text=True, timeout=15, cwd=str(root))
        except Exception:
            return []
        items: list[dict] = []
        for blk in (r.stdout or "").split("\x00")[1:]:
            head_ln, _, rest = blk.partition("\n")
            parts = head_ln.split("\t", 2)
            if len(parts) < 3:
                continue
            h, when, subject = parts
            files = [ln.strip() for ln in rest.splitlines() if ln.strip()]
            items.append({"hash": h, "day": when[:10], "time": when[11:16], "subject": subject,
                          "files": len(files), "areas": _commit_areas(files)})
        _git_cache.update(head=head, items=items)
        return items

    def _dev_history() -> list[dict]:
        """변경 이력 — 커밋을 날짜별로 묶는다. 깃이 없으면 저장소 사본(dev-changelog.md)으로 물러난다."""
        from collections import OrderedDict

        days: OrderedDict[str, list[dict]] = OrderedDict()
        items = _git_history()
        if items:
            for it in items:
                days.setdefault(it["day"], []).append(it)
        else:
            for ln in _dev_read("dev-changelog.md").splitlines():
                ln = ln.strip()
                if not ln.startswith("- "):
                    continue
                parts = ln[2:].split(" ", 2)
                if len(parts) < 3:
                    continue
                day, time, subject = parts
                days.setdefault(day, []).append({"day": day, "time": time, "subject": subject,
                                                 "hash": "", "files": 0, "areas": []})
        return [{"day": d, "items": items} for d, items in days.items()]

    # 문서 한 줄 설명 — 목록에서 제목 아래에 보인다. 없는 파일은 설명 없이 제목만.
    _DOC_DESC = {
        "paper/프로젝트-기획서.md": "제출용 기획서 — 목표·개발 내용·장비·일정·기대 효과",
        "paper/논문-원재료.md": "논문 본문의 재료 — 시스템 구성·방법·기여·한계·수치 총람",
        "paper/실험-로그.md": "실험 시계열 — 무엇을 어떻게 재서 어떤 판정을 했는지",
        "paper/논문-양식-가이드.md": "작년 논문집 형식과 우리 목차 대응",
        "paper/중간-발표자료.md": "중간 발표 슬라이드 구성안",
        "paper/제안발표-내용.md": "제안 발표(9/8) 슬라이드 원고",
        "HANDOFF.md": "지금 어떤 구성으로 도는가 · 접속 · 남은 일 (이어받는 사람이 먼저 읽는 문서)",
        "dev-now.md": "지금 하는 일 · 다음 일 · 최근 작업 (진행 현황 탭의 원본)",
        "architecture.md": "아키텍처 설계 — 데이터 흐름·검색·검증기·현황 주석",
        "model-plan.md": "모델 4종 학습 계획 — 데이터·순서·게이트",
        "eval-plan.md": "평가 계획 — 평가셋·지표·베이스라인 절차",
        "capstone-plan.md": "13주 실행 계획 — 주차별 목표와 현황",
        "pilot-plan.md": "실물 문서 파일럿 계획",
        "risks.md": "위험 관리 — 개인정보·환각·파싱·운영",
        "quality-system.md": "품질 5계층 체계 — 문제를 어느 계층에서 막는가",
        "workflow.md": "저장소·결정·보고 방식",
        "retrieval-baseline-mini.md": "검색 기준선(9/4) — 어휘·임베딩·하이브리드",
        "retrieval-weight-sweep.md": "하이브리드 가중 스윕(9/4)",
        "rerank-baseline.md": "리랭커 학습 전 기준선(9/4)",
        "llm-rerank-eval.md": "리랭커 학습본·LLM 리랭킹 비교(9/20)",
        "embed-v0-report.md": "임베딩 v0 리허설(9/3, 미배포)",
        "ocr-cer-bench.md": "OCR 어절 정확도 벤치(9/7)",
        "ocr-duel.md": "OCR 엔진 대결(9/8)",
        "model-cards/zzaimy-embed-v2.md": "운영 임베딩 학습본 카드",
        "model-cards/zzaimy-rerank-v1.md": "운영 리랭커 학습본 카드",
        "notes/2026-09-22-external-split-and-blockchain.md": "외부 모델 반분 위탁·블록체인 — 채택 안 함(이유·대안)",
        "notes/2026-09-22-vllm-vs-ollama.md": "서빙 엔진 비교 — 실측과 vLLM 통일 이유",
        "notes/2026-09-22-access-controlled-knowledge-base.md": "권한별 지식 베이스 — 권한 밖 질문(학생·타 부서 개인정보)에 어떻게 응답하나, 열람 등급·부서 범위",
        "notes/meeting-w2-20260908.md": "2주차 미팅 메모(교수님)와 정합 분석",
        "notes/2026-09-02-ocr-engine-refs.md": "OCR 엔진 후보 조사",
        "notes/2026-09-02-ocr-preprocessing-refs.md": "스캔 전처리·복원 후보 조사",
        "notes/2026-09-02-ocr-upgrade-refs.md": "OCR·문서이해 고도화 레퍼런스",
        "notes/2026-09-02-external-doc-sources.md": "공개 문서 소스 조사",
        "notes/table-layout.md": "표·레이아웃 원본 불일치 원인 진단",
        "notes/automation-pipeline.md": "자동화 파이프라인 목표와 현재 위치",
        "notes/backlog.md": "개선 백로그(9/8 실사용 리뷰)",
        "notes/uiux-audit.md": "UI/UX 전수 조사",
        "notes/ui-glossary.md": "화면 문구 용어집",
        "notes/hwpx-agent-skills.md": "한글 에이전트 스킬 수집 노트",
        "notes/collab-now.md": "협업 분담(현재)",
    }
    # 기술 검토(조사·판단 기록)와 작업 메모(백로그·용어집 같은 내부 메모)를 나눈다
    _NOTE_MEMO = {"backlog.md", "collab-now.md", "ui-glossary.md", "uiux-audit.md", "hwpx-agent-skills.md",
                  "automation-pipeline.md"}

    _PAPER_LABELS = {
        "프로젝트-기획서.md": "프로젝트 기획서",
        "중간-발표자료.md": "중간 발표자료 구성안",
        "논문-원재료.md": "논문 정리 노트",
        "실험-로그.md": "실험 기록",
        "논문-양식-가이드.md": "논문 양식 분석",
        "제안발표-내용.md": "제안 발표 내용",
    }

    _PAPER_ORDER = ["프로젝트-기획서.md", "제안발표-내용.md", "중간-발표자료.md", "논문-원재료.md",
                    "실험-로그.md", "논문-양식-가이드.md"]

    def _dev_papers() -> list[dict]:
        d = _DOCS_DIR / "paper"
        names = sorted((p.name for p in d.glob("*.md")) if d.exists() else [],
                       key=lambda n: (_PAPER_ORDER.index(n) if n in _PAPER_ORDER else 99, n))
        return [
            {"file": n, "label": _PAPER_LABELS.get(n, n.replace(".md", ""))}
            for n in names
        ]

    def _dev_doc_view(text: str, fname: str) -> dict:
        """열람 화면(dev_paper.html)용 — _md_view 는 허브 작업 기록과 공용이라 손대지 않고,
        문서 열람에만 필요한 손질을 앞뒤로 붙인다.

        앞: 줄바꿈으로 감싼(hard-wrap) 문단을 한 줄로 합쳐 문단 하나가 <p> 하나가 되게 한다
            (펜스 안·표·목록·제목·인용·구분선은 그대로).
        뒤: 첫 제목(# )은 머리에 보이므로 본문에서 빼고, `인라인 코드`·> 인용을 살리며,
            ##·### 제목에 id 를 달아 목차를 만든다.
        """
        import html as _html

        from markupsafe import Markup as _M

        block = re.compile(r"^(\s*[-*+]\s|#{1,6}\s|\s*\||>|\s*[-*_]{3,}\s*$)")
        para_start = re.compile(r"^\s*\d+[.)]\s")
        joined: list[str] = []
        buf: list[str] = []
        fence = False
        for ln in text.splitlines():
            if ln.lstrip().startswith("```"):
                fence = not fence
            if fence or ln.lstrip().startswith("```") or not ln.strip() or block.match(ln):
                if buf:
                    joined.append(" ".join(buf))
                    buf = []
                joined.append(ln)
            elif para_start.match(ln):
                if buf:
                    joined.append(" ".join(buf))
                buf = [ln.strip()]
            else:
                buf.append(ln.strip())
        if buf:
            joined.append(" ".join(buf))

        code_re = re.compile(r"`([^`<>\n]+)`")
        tag_re = re.compile(r"<[^>]+>")
        out: list[str] = []
        toc: list[dict] = []
        quote: list[str] = []
        title = ""
        in_pre = False
        for raw in str(_md_view("\n".join(joined))).split("\n"):
            if in_pre or raw.startswith("<pre"):
                out.append(raw)
                in_pre = "</pre>" not in raw
                continue
            m = re.match(r'<p style="[^"]*">&gt;\s?(.*)</p>$', raw)
            if m:
                quote.append("<p>" + code_re.sub(r"<code>\1</code>", m.group(1)) + "</p>")
                continue
            if quote:
                out.append("<blockquote>" + "".join(quote) + "</blockquote>")
                quote = []
            raw = code_re.sub(r"<code>\1</code>", raw)
            if re.match(r'<p style="[^"]*">[-*_]{3,}</p>$', raw):
                out.append("<hr>")
                continue
            if raw.startswith('<div class="table-scroll">'):
                # 표 칸은 _md_view 가 굵게를 살리지 않는다 — 여기서만 살린다
                raw = re.sub(r"\*\*([^<>*]+?)\*\*", r"<b>\1</b>", raw)
            m = re.match(r"<h3 [^>]*>(.*)</h3>$", raw)
            if m and not title and not out:
                title = _html.unescape(tag_re.sub("", m.group(1)))
                continue
            m = re.match(r"<(h4|h5)\b([^>]*)>(.*)</\1>$", raw)
            if m:
                sid = f"s{len(toc) + 1}"
                toc.append({
                    "id": sid, "level": 2 if m.group(1) == "h4" else 3,
                    "text": _html.unescape(tag_re.sub("", m.group(3))),
                })
                raw = f'<{m.group(1)} id="{sid}"{m.group(2)}>{m.group(3)}</{m.group(1)}>'
            out.append(raw)
        if quote:
            out.append("<blockquote>" + "".join(quote) + "</blockquote>")
        return {"title": title or fname, "html": _M("\n".join(out)), "toc": toc}

    @app.get("/dev/doc/{name:path}", response_class=HTMLResponse)
    def dev_doc(request: Request, name: str):
        allowed = {
            "embed-v0-report.md", "retrieval-baseline-mini.md", "llm-rerank-eval.md", "ocr-duel.md",
            "rerank-baseline.md", "retrieval-weight-sweep.md", "ocr-cer-bench.md",
            "quality-system.md", "HANDOFF.md", "dev-now.md", "model-plan.md", "architecture.md",
            "eval-plan.md", "capstone-plan.md", "pilot-plan.md", "risks.md", "workflow.md",
        } | {
            f"{d['sub']}/{d['file']}"
            for sub in ("decisions", "notes") for d in _dev_doc_list(sub)
        } | {f"model-cards/{p.name}" for p in (_DOCS_DIR / "model-cards").glob("*.md")}
        if name not in allowed:
            raise HTTPException(404)
        fname = name.rsplit("/", 1)[-1]
        return templates.TemplateResponse(request, "dev_paper.html", ctx(request, {
            "fname": fname,
            "doc": _dev_doc_view(_dev_read(name), fname),
            "papers": _dev_papers(),
            # 설계·기술 문서는 논문 자료가 아니라 hwpx·docx 내보내기가 없다(항상 404였음)
            "show_export": False, "page_title": "설계·기술 문서",
        }))

    @app.get("/dev/paper/{name}/export.{fmt}")
    def dev_paper_export(name: str, fmt: str):
        """논문 자료 문서를 hwpx·docx 파일로 내려받기."""
        from urllib.parse import quote as _q

        from fastapi.responses import Response

        if name not in {p["file"] for p in _dev_papers()}:
            raise HTTPException(404)
        text = _dev_read(f"paper/{name}")
        title = _PAPER_LABELS.get(name, name.replace(".md", ""))
        body = "\n".join(
            ln for ln in text.splitlines() if not ln.startswith("# ")
        )
        payload: bytes | None
        if fmt == "docx":
            from zzaimy.app.draft_export import build_draft_docx

            payload = build_draft_docx(title, body)
            media = ("application/vnd.openxmlformats-officedocument"
                     ".wordprocessingml.document")
        elif fmt == "hwpx":
            from zzaimy.app.draft_export import build_draft_hwpx

            payload = build_draft_hwpx(title, body)
            media = "application/hwp+zip"
        else:
            raise HTTPException(404)
        if payload is None:
            raise HTTPException(500, "내보내기에 실패했습니다")
        return Response(payload, media_type=media, headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + _q(f"{title}.{fmt}"),
        })

    @app.get("/dev/paper/{name}", response_class=HTMLResponse)
    def dev_paper(request: Request, name: str):
        """논문 원재료 문서 열람 — 개발자 전용(경로 가드)."""
        if "/" in name or ".." in name or not name.endswith(".md"):
            raise HTTPException(404)
        f = _DOCS_DIR / "paper" / name
        if not f.exists():
            raise HTTPException(404)
        return templates.TemplateResponse(request, "dev_paper.html", ctx(request, {
            "fname": name, "doc": _dev_doc_view(f.read_text(encoding="utf-8"), name),
            "papers": _dev_papers(),
            "show_export": True,
        }))

    @app.get("/dev", response_class=HTMLResponse)
    def dev_dashboard(request: Request):
        # 허브 — 요약과 바로가기만. 상세는 전용 페이지(/dev/quality·/dev/docs·/dev/history)
        return templates.TemplateResponse(request, "dev.html", ctx(request, {
            "overview": _data_overview(),   # 규모 타일 — /dev/db 와 같은 원천
            "dev_now": _dev_now_parts()["hub"],
            "dev_now_more": _dev_now_parts()["more"],
            "reindex_needed": (
                Path(db_path).parent / ".reindex-needed"
            ).exists(),
            "retrieval_eval": _retrieval_eval_state(),   # 카드의 '최근 측정' 한 줄에만 쓴다
            "progress": _dev_progress(),
            "reindex_running": _reindex_running(),
            "egress": db.egress_stats(),
            "quality": db.quality_report_stats(),
            "n_papers": len(_dev_papers()),
            "n_adr": len(_dev_doc_list("decisions")),
            "n_notes": len(_dev_doc_list("notes")),
        }))

    @app.get("/dev/quality", response_class=HTMLResponse)
    def dev_quality(request: Request):
        """품질·성능 — 규정 검색 정확도(기계 산출물)와 추출 품질 백로그."""
        return templates.TemplateResponse(request, "dev_quality.html", ctx(request, {
            "retrieval_eval": _retrieval_eval_state(),
            "reindex_running": _reindex_running(),
            "quality": db.quality_report_stats(),
            "quality_open": db.list_quality_reports(status="open", limit=10),
        }))

    _doc_dates: dict[str, str] = {}

    def _doc_last_changed(rel: str) -> str:
        """docs/ 파일의 마지막 변경일(YYYY-MM-DD) — 깃 기록이 있으면 그것, 없으면 파일 수정 시각."""
        if rel in _doc_dates:
            return _doc_dates[rel]
        import subprocess
        from datetime import datetime as _dt

        out = ""
        try:
            r = subprocess.run(["git", "log", "-1", "--format=%ad", "--date=short", "--", str(_DOCS_DIR / rel)],
                               capture_output=True, text=True, timeout=5, cwd=str(_DOCS_DIR.parent))
            out = (r.stdout or "").strip()
        except Exception:
            out = ""
        if not out:
            try:
                out = _dt.fromtimestamp((_DOCS_DIR / rel).stat().st_mtime).strftime("%Y-%m-%d")
            except OSError:
                out = ""
        _doc_dates[rel] = out
        return out

    @app.get("/dev/docs", response_class=HTMLResponse)
    def dev_docs(request: Request):
        """논문 자료·설계 결정·기술 검토·측정 기록 목록."""
        date_re = re.compile(r"\d{4}-\d{2}-\d{2}")
        head_re = re.compile(r"^-\s*\**(상태|날짜)\**\s*:\s*(.+)$")

        def entry(rel: str, href: str, title: str | None = None) -> dict:
            """문서 머리(첫 8줄)에서 뽑은 한 줄 정보 — ADR 은 '- 상태:'·'- 날짜:' 줄,
            그 밖에는 머리에 적힌 첫 날짜. 없으면 비워 둔다."""
            meta = {"status": "", "date": ""}
            first_date = ""
            try:
                head = (_DOCS_DIR / rel).read_text(encoding="utf-8").splitlines()[:8]
            except OSError:
                head = []
            for ln in head:
                if title is None and ln.startswith("# "):
                    title = ln[2:].strip()
                m = head_re.match(ln.strip())
                if m:
                    key = "status" if m.group(1) == "상태" else "date"
                    meta[key] = m.group(2).strip()
                elif not first_date and (d := date_re.search(ln)):
                    first_date = d.group(0)
            if meta["date"] and (d := date_re.search(meta["date"])):
                meta["date"] = d.group(0)
            if not meta["date"] and not first_date:
                # 머리에 날짜가 없는 문서는 깃의 마지막 수정일 — 목록의 날짜 칸이 들쭉날쭉하지 않게(2026-09-22 사용자 지적)
                first_date = _doc_last_changed(rel)
            status = re.split(r"\s*[(（]", meta["status"], maxsplit=1)[0].strip()
            num = ""
            title = title or rel.rsplit("/", 1)[-1]
            # ADR 제목은 '# NNNN. 제목' 이 규칙이지만 'ADR-NNNN' · 'NNNN ·' 꼴도 번호로 읽는다(2026-09-22)
            if m := re.match(r"^(?:ADR-)?(\d{4})\s*[.·:]?\s+(.+)$", title):
                num, title = m.group(1), m.group(2)
            return {"href": href, "title": title, "num": num, "status": status,
                    "date": meta["date"] or first_date, "desc": _DOC_DESC.get(rel, "")}

        from datetime import date as _date, timedelta as _td

        _today = _date.today()
        return templates.TemplateResponse(request, "dev_docs.html", ctx(request, {
            "weekly_docs": _weekly_list(),
            "weekly_template": _weekly_sections()[1],
            "weekly_monday": (_today - _td(days=_today.weekday())).isoformat(),
            "weekly_feedback": _weekly_feedback((_today - _td(days=_today.weekday())).isoformat()),
            "weekly": _weekly_card((_today - _td(days=_today.weekday())).isoformat(), _today.isoformat()),
            "papers": [entry(f"paper/{p['file']}", f"/dev/paper/{p['file']}", p["label"])
                       for p in _dev_papers()],
            "adr_docs": [entry(f"decisions/{d['file']}", f"/dev/doc/decisions/{d['file']}",
                               d["title"]) for d in _dev_doc_list("decisions")],
            # 기술 검토(조사·판단) 와 작업 메모(내부 메모)를 나눈다 — 한 목록에 섞여 무엇이 무엇인지 안 보였다(9/22)
            "note_docs": sorted([entry(f"notes/{d['file']}", f"/dev/doc/notes/{d['file']}", d["title"])
                                 for d in _dev_doc_list("notes") if d["file"] not in _NOTE_MEMO],
                                key=lambda e: e["date"], reverse=True),
            "memo_docs": sorted([entry(f"notes/{d['file']}", f"/dev/doc/notes/{d['file']}", d["title"])
                                 for d in _dev_doc_list("notes") if d["file"] in _NOTE_MEMO],
                                key=lambda e: e["date"], reverse=True),
            # 현황(먼저 볼 것) · 설계·계획 · 측정 기록 · 모델 카드 — docs/ 바로 아래 파일들
            "status_docs": [entry(f, f"/dev/doc/{f}") for f in ("HANDOFF.md", "dev-now.md")],
            "plan_docs": [entry(f, f"/dev/doc/{f}") for f in (
                "architecture.md", "model-plan.md", "eval-plan.md", "capstone-plan.md",
                "pilot-plan.md", "risks.md", "quality-system.md", "workflow.md")],
            "bench_docs": [entry(f, f"/dev/doc/{f}") for f in (
                "llm-rerank-eval.md", "retrieval-baseline-mini.md", "retrieval-weight-sweep.md",
                "rerank-baseline.md", "embed-v0-report.md", "ocr-cer-bench.md", "ocr-duel.md")],
            "card_docs": [entry(f"model-cards/{f}", f"/dev/doc/model-cards/{f}") for f in
                          sorted(p.name for p in (_DOCS_DIR / "model-cards").glob("*.md"))],
        }))

    @app.get("/dev/history", response_class=HTMLResponse)
    def dev_history_page(request: Request):
        """변경 이력 — 작업 기록(날짜별) + 커밋 이력 전체(깃에서 직접, 영역 표식).
        주간 보고서 요약은 논문 자료 화면의 주간 보고서 칸으로 갔다(2026-09-22)."""
        days = []
        for blk in re.split(r"\n(?=### )", _dev_now_parts()["recent_all"]):
            if blk.strip().startswith("### "):
                head, _, body = blk.strip().partition("\n")
                days.append({"day": head[4:].strip(), "body": body.strip()})
        history = [d for d in _dev_history() if re.fullmatch(r"(\d{4}-)?\d{2}-\d{2}", d["day"])]
        area_count: dict[str, int] = {}
        for d in history:
            for it in d["items"]:
                for a in it["areas"]:
                    area_count[a] = area_count.get(a, 0) + 1
        return templates.TemplateResponse(request, "dev_history.html", ctx(request, {
            "history": history,
            "history_count": sum(len(d["items"]) for d in history),
            "history_areas": sorted(area_count.items(), key=lambda kv: -kv[1]),
            "history_source": "깃 커밋" if _git_history() else "저장소 사본(dev-changelog.md)",
            "dev_now_days": days,
        }))

    _CORPUS_DB = _paths.corpus_db_existing(Path(db_path).parent)

    @app.get("/dev/corpus", response_class=HTMLResponse)
    def dev_corpus(request: Request, q: str = ""):
        """국고 코퍼스는 데이터 열람(/dev/db)의 탭으로 합쳤다 — 예전 주소는 그리로 보낸다."""
        from urllib.parse import urlencode

        params = {"tab": "corpus", **({"q": q.strip()} if q.strip() else {})}
        return RedirectResponse("/dev/db?" + urlencode(params), status_code=301)

    @app.get("/dev/pii", response_class=HTMLResponse)
    def dev_pii(request: Request):
        """개인정보 마스킹 감사 — 기록·자가 점검·잔여 검사 (절대 규칙 3).

        플랫폼 DB와, 옆에 있으면 코퍼스 파일럿 DB(스크립트 74 기본 대상)까지
        본다. 자가 점검 결과는 DB와 무관하므로 플랫폼 DB settings에만 둔다.
        """
        from zzaimy.app import access_guard, pii_audit

        sources = [pii_audit.source_view(db, name="플랫폼 DB", linkable=True)]
        corpus_path = pii_audit.corpus_db_path(db)
        if corpus_path is not None:
            sources.append(pii_audit.source_view(
                Database(corpus_path), name="국고 코퍼스 (별도 DB)", linkable=False,
            ))
        return templates.TemplateResponse(request, "dev_pii.html", ctx(request, {
            "sources": sources,
            "selftest": pii_audit.load_json(db, pii_audit.SELFTEST_KEY),
            "policy": pii_audit.MASK_POLICY,
            "type_names": {t: name for t, name, *_ in pii_audit.MASK_POLICY},
            "entity_labels": pii_audit.ENTITY_LABELS,
            # 권한 밖 시도(개인정보 요청·범위 밖 자료·유도 질문) — 최근 24시간, 화면은 C-59
            "access_audit": [
                {**r, "kind_label": access_guard.KIND_LABELS.get(r.get("kind"), r.get("kind"))}
                for r in access_guard.recent(Path(db_path).parent, hours=24)
            ],
            "accounts_scope": [
                {"user": u, "name": a.get("name", ""), "role": a.get("role", "staff"),
                 "role_label": access_guard.ROLES.get(a.get("role", "staff"), a.get("role", "staff")),
                 "dept": a.get("dept", "")}
                for u, a in sorted(accounts.items())
            ],
            "role_choices": access_guard.ROLES,
            "dept_choices": [d.get("dept") for d in db.department_counts() if d.get("dept")],
        }))

    @app.post("/dev/pii/selftest")
    def dev_pii_selftest():
        """합성 표본으로 실제 마스커를 검증 — 결과는 settings에 남는다."""
        from zzaimy.app import pii_audit

        pii_audit.run_selftest(db)
        return RedirectResponse("/dev/pii", status_code=303)

    @app.post("/dev/pii/scan")
    def dev_pii_scan():
        """저장·색인된 본문에 탐지 정규식을 독립 실행 — 잔여 개인정보 검사."""
        from zzaimy.app import pii_audit

        pii_audit.run_scan(db)
        corpus_path = pii_audit.corpus_db_path(db)
        if corpus_path is not None:
            pii_audit.run_scan(Database(corpus_path))
        return RedirectResponse("/dev/pii", status_code=303)

    _REINDEX_LOG = Path("/tmp/reindex.log")
    _reindex_proc: dict = {"p": None}

    def _reindex_running() -> bool:
        """재색인 체인이 아직 도는가 — 이 프로세스가 띄운 것은 직접, 그 외(앱 재시작 뒤)는
        로그의 종료 표식과 최근성으로 판단한다."""
        p = _reindex_proc["p"]
        if p is not None and p.poll() is None:
            return True
        try:
            if _REINDEX_LOG.exists():
                tail = _REINDEX_LOG.read_text(encoding="utf-8", errors="ignore")[-400:]
                fresh = (time.time() - _REINDEX_LOG.stat().st_mtime) < 1800
                return fresh and "REINDEX_DONE" not in tail and "중단" not in tail
        except OSError:
            pass
        return False

    @app.post("/dev/reindex")
    def dev_reindex():
        """재색인 체인 실행 — 규정 조각 변경 후 질의·임베딩·앱 순차 갱신.

        두 번 누르면 두 개가 돌던 문제: 실행 중이면 새로 띄우지 않는다(66 자체의
        flock과 이중 방어). 로그 핸들은 자식이 물려받은 뒤 닫는다."""
        import subprocess

        if _reindex_running():
            return RedirectResponse("/dev?err=재색인이 이미 실행 중입니다", status_code=303)
        script = Path(__file__).resolve().parents[3] / "scripts" / "66_reindex.sh"
        with _REINDEX_LOG.open("w") as log_f:
            _reindex_proc["p"] = subprocess.Popen(
                ["nohup", "bash", str(script)],
                stdout=log_f, stderr=subprocess.STDOUT, start_new_session=True,
            )
        return RedirectResponse("/dev", status_code=303)

    def _retrieval_eval_state() -> dict:
        from zzaimy.eval.retrieval_eval import dashboard_state

        return dashboard_state(Path(db_path).parent / "eval")

    @app.post("/dev/eval/run")
    def dev_eval_run():
        """규정 검색 품질 재측정 — 재색인과 같은 방식(배경 실행·같은 잠금)으로 53을 돌린다.

        합성 질의 세트가 없으면 실행하지 않고 그 사실을 돌려준다 — 가짜 수치는 없다.
        """
        import subprocess
        import sys

        from zzaimy.eval import retrieval_eval as rev

        if not rev.QUERIES_PATH.exists():
            return HTMLResponse(f"아직 측정 없음 ({rev.NO_QUERY_SET_MSG})", status_code=409)
        if rev.running_state(Path(db_path).parent / "eval"):
            return HTMLResponse("이미 측정 중입니다", status_code=409)
        root = Path(__file__).resolve().parents[3]
        subprocess.Popen(
            ["nohup", sys.executable, str(root / "scripts" / "53_eval_retrieval.py"),
             "--db", str(Path(db_path).resolve())],
            stdout=open(rev.LOG_PATH, "w"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=root,
            env={**os.environ, "PYTHONPATH": str(root / "src")},
        )
        return RedirectResponse("/dev", status_code=303)

    @app.post("/account/password")
    def account_password(
        request: Request, current_pw: str = Form(""), new_pw: str = Form(...),
        confirm: str = Form(""), back: str = Form("/"),
    ):
        """내 비밀번호 변경 — 로그인한 본인 계정만. 현재 비밀번호를 맞혀야 바꿀 수 있다."""
        if password is None:
            raise HTTPException(400, "인증 없는 로컬 모드에서는 계정이 없습니다")
        uid = getattr(request.state, "user", "")
        dest = back if back.startswith("/") and not back.startswith("//") else "/"
        sep = "&" if "?" in dest else "?"
        if not _verify_pw(uid, current_pw):
            return RedirectResponse(
                f"{dest}{sep}err=현재 비밀번호가 맞지 않습니다", status_code=303)
        if len(new_pw) < 8:
            return RedirectResponse(
                f"{dest}{sep}err=새 비밀번호는 8자 이상이어야 합니다", status_code=303)
        if new_pw != confirm:
            return RedirectResponse(
                f"{dest}{sep}err=새 비밀번호 확인이 일치하지 않습니다", status_code=303)
        _set_pw(accounts[uid], new_pw)
        _save_accounts()
        return RedirectResponse(f"{dest}{sep}ok=비밀번호를 변경했습니다", status_code=303)

    # ---- 학습 도구 계정·연결 — 모델 학습 화면의 도구 카드에서 다룬다 ----

    def _train_redirect(msg: str, ok: bool = True) -> RedirectResponse:
        return RedirectResponse(f"/dev/train?{'ok' if ok else 'err'}={msg}", status_code=303)

    @app.post("/dev/account/scope")
    def dev_account_scope(request: Request, uid: str = Form(...), dept: str = Form(""), role: str = Form("staff")):
        """계정의 부서·역할 — 관리자만. 검색·대화 범위가 여기서 정해진다(C-59 화면이 부른다)."""
        from zzaimy.app.access_guard import ROLES

        if password is None:
            raise HTTPException(400, "인증 없는 로컬 모드에서는 계정이 없습니다")
        if uid not in accounts:
            raise HTTPException(404, "계정이 없습니다")
        if role not in ROLES:
            raise HTTPException(400, "역할 값이 올바르지 않습니다")
        accounts[uid]["dept"] = dept.strip()
        accounts[uid]["role"] = role
        accounts[uid]["updated_at"] = _now_iso()
        _save_accounts()
        return RedirectResponse("/dev/pii?ok=계정 범위를 저장했습니다", status_code=303)

    @app.get("/dev/accounts")
    def dev_accounts_moved():
        # 예전 주소 — 도구 계정·연결은 모델 학습 화면으로 합쳤다
        return RedirectResponse("/dev/train", status_code=301)


    # ---- 한글 실시간 편집 에이전트 — 서버 채널 (tools/hwp-agent/protocol.md) ----
    #
    # Windows 에이전트가 아웃바운드 롱폴로 붙는다. 인증은 발급 토큰(설정
    # hwp_agent_token) → 등록 시 세션 발급. 상태는 메모리(재시작 시 에이전트가
    # 재등록). 명령에 실리는 값은 서버에서 만든 것만 — 유출 경계는 서버 책임.

    hwp_sessions: dict[str, dict] = {}

    def _hwp_session(session: str) -> dict:
        sess = hwp_sessions.get(session)
        if sess is None:
            raise HTTPException(403, "등록되지 않은 에이전트 세션입니다")
        return sess

    @app.post("/hwp/agent/register")
    async def hwp_register(request: Request):
        import time as _t

        payload = await request.json()
        good = db.get_setting("hwp_agent_token")
        token = str(payload.get("token") or "")
        if not good or not secrets.compare_digest(token.encode(), good.encode()):
            raise HTTPException(403, "접속 키 불일치 — /dev/hwp에서 발급한 접속 키가 필요합니다")
        session = secrets.token_hex(8)
        hwp_sessions[session] = {
            "commands": [], "results": [], "registered": _t.time(),
            "last_poll": _t.time(), "version": str(payload.get("version") or ""),
        }
        return {"session": session, "poll_after": 0}

    @app.get("/hwp/agent/latest.py")
    def hwp_agent_latest():
        """최신 에이전트 코드 — 에이전트가 시작 시 받아 자기 자신을 갱신한다.
        코드가 바뀌어도 재다운로드 없이 시작.bat 재실행만으로 최신이 된다."""
        agent_py = Path(__file__).resolve().parents[3] / "tools" / "hwp-agent" / "hwp_agent.py"
        if not agent_py.exists():
            raise HTTPException(404, "에이전트 코드가 없습니다")
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(agent_py.read_text(encoding="utf-8"))

    @app.get("/hwp/agent/commands")
    async def hwp_commands(session: str, after: int = 0):
        import asyncio
        import time as _t

        sess = _hwp_session(session)
        after = max(0, after)
        for _ in range(40):  # 최대 ~20초 롱폴
            sess["last_poll"] = _t.time()
            pending = sess["commands"][after:]
            if pending:
                return {"commands": pending, "cursor": after + len(pending)}
            await asyncio.sleep(0.5)
        return {"commands": [], "cursor": after}

    @app.post("/hwp/agent/result")
    async def hwp_result(request: Request):
        payload = await request.json()
        sess = _hwp_session(str(payload.get("session") or ""))
        result = {k: payload.get(k) for k in ("id", "ok", "result", "error")}
        sess["results"].append(result)
        del sess["results"][:-50]
        # 문서 목록 결과면 세션에 저장 — /dev/hwp에서 대상 선택에 쓴다
        res = result.get("result")
        if isinstance(res, dict) and "docs" in res:
            sess["docs"] = res["docs"]
        # 산출물 URL이 붙어 왔으면 세션 최신 산출물로 기억(미리보기용)
        if isinstance(res, dict) and res.get("artifact_url"):
            sess.setdefault("artifacts", []).append({
                "url": res["artifact_url"], "format": res.get("format"),
                "bytes": res.get("artifact_bytes"), "cmd": result.get("id"),
            })
            del sess["artifacts"][:-20]
        return {"ok": True}

    _HWP_ARTIFACTS = Path("data/hwp-artifacts")

    @app.post("/hwp/agent/artifact")
    async def hwp_artifact(request: Request):
        """에이전트가 한글에서 뽑은 산출물(PDF·hwpx)을 회수한다. 미리보기·
        다운로드에 쓴다. 세션 검증 후 저장하고 회수 URL을 돌려준다."""
        import base64 as _b64

        payload = await request.json()
        _hwp_session(str(payload.get("session") or ""))
        fmt = str(payload.get("format") or "pdf")
        ext = {"pdf": "pdf", "hwpx": "hwpx", "hwp": "hwp"}.get(fmt, "bin")
        try:
            data = _b64.b64decode(payload.get("b64") or "")
        except Exception:
            raise HTTPException(400, "산출물 데이터가 잘못되었습니다")
        if not data:
            raise HTTPException(400, "산출물이 비어 있습니다")
        _HWP_ARTIFACTS.mkdir(parents=True, exist_ok=True)
        aid = secrets.token_hex(8)
        (_HWP_ARTIFACTS / f"{aid}.{ext}").write_bytes(data)
        return {"url": f"/dev/hwp/artifact/{aid}.{ext}", "id": aid,
                "bytes": len(data)}

    @app.get("/dev/hwp/artifact/{fname}")
    def hwp_artifact_get(fname: str):
        """회수된 산출물 서빙 — PDF는 브라우저 미리보기(inline), 나머지는 다운로드."""
        from fastapi.responses import FileResponse

        if "/" in fname or "\\" in fname or ".." in fname:
            raise HTTPException(400, "파일명이 잘못되었습니다")
        p = _HWP_ARTIFACTS / fname
        if not p.exists():
            raise HTTPException(404, "산출물이 없습니다")
        media = {"pdf": "application/pdf",
                 "hwpx": "application/haansofthwpx",
                 "hwp": "application/x-hwp"}.get(
                     p.suffix.lstrip("."), "application/octet-stream")
        disp = "inline" if p.suffix == ".pdf" else "attachment"
        return FileResponse(
            str(p), media_type=media,
            headers={"Content-Disposition": f'{disp}; filename="{fname}"'})

    @app.get("/hwp/artifact/{fname}")
    def hwp_artifact_public(fname: str):
        """회수된 한글 산출물 서빙(로그인 담당자용) — /dev 밖이라 staff도 본다."""
        return hwp_artifact_get(fname)

    @app.get("/hwp/latest-artifact")
    def hwp_latest_artifact_public():
        """가장 최근 PDF 산출물(담당자용). url은 비-dev 경로로 돌려준다."""
        if not hwp_sessions:
            return {"url": None}
        _sid, s = max(hwp_sessions.items(), key=lambda kv: kv[1]["registered"])
        pdfs = [a for a in (s.get("artifacts") or [])
                if a.get("format") == "pdf" and a.get("url")]
        if not pdfs:
            return {"url": None}
        fname = str(pdfs[-1]["url"]).rsplit("/", 1)[-1]
        return {"url": f"/hwp/artifact/{fname}"}

    # 개발자 화면 — 토큰 발급·상태·명령 콘솔 (LLM 없이도 실시간 데모 가능)

    _HWP_OPS = {
        "ping", "new_doc", "open", "list_docs", "select_doc", "goto",
        "set_title", "find", "insert_text", "replace", "insert_table",
        "fill_table", "set_format", "set_page", "delete_text", "delete_table",
        "get_text", "save", "save_as", "export_artifact", "open_bytes",
    }

    def _hwp_send(op: str, args: dict) -> bool:
        """가장 최근 연결 에이전트로 명령 전송. 연결 없으면 False."""
        return _hwp_send_many([{"op": op, "args": args}])

    # 미리보기 갱신이 필요 없는(문서를 바꾸지 않는) op
    _HWP_NO_EXPORT = {"ping", "list_docs", "get_text", "find", "goto",
                      "export_artifact"}

    def _hwp_send_many(ops: list[dict]) -> bool:
        """op 시퀀스를 한 번에 큐잉한다(문서 저작처럼 순서가 중요한 흐름).
        연결된 에이전트가 없으면 False. 문서를 바꾸는 배치 끝에는
        export_artifact(PDF)를 자동으로 붙여, 작업 완료 시 미리보기가
        최신본으로 갱신되게 한다."""
        if not hwp_sessions:
            return False
        _sid, sess = max(hwp_sessions.items(), key=lambda kv: kv[1]["registered"])
        queue = list(ops)
        names = {o["op"] for o in queue}
        if (names - _HWP_NO_EXPORT) and "export_artifact" not in names:
            queue.append({"op": "export_artifact", "args": {"format": "pdf"}})
        for o in queue:
            sess["commands"].append({
                "id": f"cmd-{secrets.token_hex(3)}",
                "op": o["op"], "args": o.get("args", {}),
            })
        return True

    @app.post("/doc/{doc_id}/to-hwp")
    def doc_to_hwp(doc_id: int, template: str = Form("")):
        """초안(Markdown)을 한글 문서로 저작한다 (ADR-0014).

        양식(template)이 지정되면 그 .hwp/.hwpx를 열어 채우고, 없으면 새 문서를
        만든다 — 어느 쪽이든 사용자가 열어둔 다른 문서는 건드리지 않는다.
        끝에 PDF·hwpx를 뽑아 서버로 회수(미리보기·다운로드).
        """
        from zzaimy.hwp.md_ops import author_sequence

        doc = db.get_document(doc_id)
        if doc is None or not (doc.get("draft") or "").strip():
            raise HTTPException(404, "초안이 없습니다")
        if not hwp_sessions:
            return RedirectResponse(f"/doc/{doc_id}?hwp=none", status_code=303)
        seq = author_sequence(doc["draft"],
                              template_path=(template.strip() or None))
        _hwp_send_many(seq)
        return RedirectResponse(f"/doc/{doc_id}?hwp=sent", status_code=303)

    # ---- NAS 수집 — 읽기 전용 공유 폴더에서 기준 문서·문서 추출로 자동 반입 (개발자 전용) ----

    def _nas_redirect(msg: str, ok: bool = True) -> RedirectResponse:
        return RedirectResponse(f"/dev/nas?{'ok' if ok else 'err'}={msg}", status_code=303)

    @app.get("/dev/nas", response_class=HTMLResponse)
    def dev_nas(request: Request, ok: str = "", err: str = "", probe: str = "", plan: str = ""):
        from zzaimy.ingest import nas_sync

        probe_result = plan_result = None
        if probe:
            src = nas_sync.get(probe)
            probe_result = {"name": src["name"], **nas_sync.probe(src)} if src else None
        if plan:
            src = nas_sync.get(plan)
            plan_result = {"name": src["name"], **nas_sync.plan(src)} if src else None
        smb_ok = True
        try:
            import smbclient  # noqa: F401
        except ImportError:
            smb_ok = False
        from zzaimy.app import storage_status
        from zzaimy.app.access_policy import LEVELS
        from zzaimy.ingest import gdrive

        return templates.TemplateResponse(request, "dev_nas.html", ctx(request, {
            "ok": ok, "err": err, "sources": nas_sync.list_public(),
            "backends": nas_sync.BACKENDS, "targets": nas_sync.TARGETS, "sectors": nas_sync.SECTORS,
            "default_exts": " ".join(nas_sync.DEFAULT_EXTENSIONS), "probe_result": probe_result,
            "plan_result": plan_result, "type_groups": nas_sync.TYPE_GROUPS, "smb_ok": smb_ok,
            "gdrive": {**gdrive.public_status(), "redirect_uri": _gdrive_redirect_uri(request)},
            "levels": LEVELS, "dept_choices": [d.get("dept") for d in db.department_counts() if d.get("dept")],
            "storage": storage_status.snapshot(inbox_dir),
        }))

    # ---- 구글 드라이브 원천 — 관리자가 클라이언트 ID·비밀을 넣고, 담당자가 자기 계정으로 한 번 허용한다(읽기 전용, ADR-0028) ----

    def _gdrive_redirect_uri(request: Request) -> str:
        """구글에 등록해야 하는 되돌아올 주소 — 플랫폼의 공개 주소 + /dev/gdrive/callback."""
        base = os.environ.get("ZZAIMY_PUBLIC_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
        return f"{base}/dev/gdrive/callback"

    @app.post("/dev/gdrive/client")
    def dev_gdrive_client(client_id: str = Form(""), client_secret: str = Form("")):
        from zzaimy.ingest import gdrive

        try:
            gdrive.set_client(client_id, client_secret)
        except ValueError as e:
            return _nas_redirect(str(e), ok=False)
        return _nas_redirect("구글 클라이언트를 저장했습니다 — 이제 계정 허용을 누르세요")

    @app.get("/dev/gdrive/auth")
    def dev_gdrive_auth(request: Request):
        from zzaimy.ingest import gdrive

        try:
            return RedirectResponse(gdrive.auth_url(_gdrive_redirect_uri(request)), status_code=303)
        except ValueError as e:
            return _nas_redirect(str(e), ok=False)

    @app.get("/dev/gdrive/callback")
    def dev_gdrive_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        from zzaimy.ingest import gdrive

        if error or not code:
            return _nas_redirect(f"구글 허용이 취소됐습니다({error or '코드 없음'})", ok=False)
        try:
            email = gdrive.exchange_code(code, state, _gdrive_redirect_uri(request))
        except ValueError as e:
            return _nas_redirect(str(e), ok=False)
        from zzaimy.ingest import gdrive_files

        gdrive_files.bind_account(db, getattr(request.state, "user", ""), email)   # 허용을 누른 계정에 묶인다
        return _nas_redirect(f"구글 계정 {email} 을 허용했습니다 — 이 플랫폼 계정의 문서는 그 드라이브에 만들어집니다")

    # ---- 문서 작업 — 구글 독스를 화면 안에 두고 에이전트가 같은 문서를 읽고, 담당자가 누른 자리에만 쓴다(ADR-0029) ----

    def _gdocs_accounts() -> list[dict]:
        from zzaimy.ingest import gdocs, gdrive

        return [{**a, "docs_ok": gdocs.has_docs_scope(a["email"])} for a in gdrive.list_accounts()]

    def _gdocs_page(request: Request, doc: str, account: str, **extra):
        from zzaimy.ingest import gdocs

        accounts_ = _gdocs_accounts()
        account = account or (accounts_[0]["email"] if accounts_ else "")
        ctx_ = {"doc_id": "", "account": account, "accounts": accounts_, "info": None, "embed_url": "",
                "question": "", "answer": "", "draft_text": "", "writes": [], "ok": "", "err": ""}
        ctx_.update(extra)
        if doc.strip():
            try:
                did = gdocs.doc_id(doc)
                ctx_["doc_id"] = did
                ctx_["embed_url"] = gdocs.embed_url(did)
                if not account:
                    ctx_["err"] = "허용된 구글 계정이 없습니다"
                else:
                    ctx_["info"] = gdocs.get(account, did)
                    ctx_["writes"] = [w for w in gdocs.recent_writes(Path(db_path).parent) if w.get("doc") == did][:10]
            except (ValueError, PermissionError, FileNotFoundError, RuntimeError) as e:
                ctx_["err"] = str(e)
        return templates.TemplateResponse(request, "gdocs_work.html", ctx(request, ctx_))

    @app.get("/gdocs/work", response_class=HTMLResponse)
    def gdocs_work(request: Request, doc: str = "", account: str = "", ok: str = "", err: str = ""):
        return _gdocs_page(request, doc, account, ok=ok, err=err)

    @app.post("/gdocs/ask", response_class=HTMLResponse)
    def gdocs_ask(request: Request, doc: str = Form(""), account: str = Form(""), question: str = Form("")):
        """문서 본문을 첨부처럼 붙여 에이전트에게 묻는다 — 문서는 저장하지 않는다."""
        from zzaimy.app import access_guard as ag
        from zzaimy.ingest import gdocs

        q = question.strip()
        if not q:
            return _gdocs_page(request, doc, account, err="질문을 적어 주세요")
        owner = getattr(request.state, "user", "zzaimy")
        acct = accounts.get(owner, {}) if password is not None else {}
        role = acct.get("role", "dev" if password is None else "staff")
        dept = acct.get("dept") or None
        note = ag.pii_request(q)
        if note:
            ag.audit(Path(db_path).parent, owner, "pii", q, dept, role)
            return _gdocs_page(request, doc, account, question=q, answer=note)
        try:
            info = gdocs.get(account, doc)
        except (ValueError, PermissionError, FileNotFoundError, RuntimeError) as e:
            return _gdocs_page(request, doc, account, question=q, err=str(e))
        material = f"[문서: {info['title']}]\n{info['text']}"[:12000]
        try:
            import inspect as _insp

            r_ = responder or _default_responder()
            kw = dict(attachment_text=material, criteria_ids=ag.allowed_doc_ids(db, [], dept, role, owner))
            if "scope" in _insp.signature(r_.answer).parameters:
                kw["scope"] = ag.search_scope(dept, role, owner)
            answer = r_.answer(db, q, **kw)
        except Exception as e:
            from zzaimy.generate.client import describe_llm_error

            answer = describe_llm_error(e)
        answer = ag.scrub(answer)
        return _gdocs_page(request, doc, account, question=q, answer=answer, draft_text=answer)

    @app.post("/gdocs/insert")
    def gdocs_insert(request: Request, doc: str = Form(""), account: str = Form(""), section: int = Form(0),
                     text: str = Form("")):
        from zzaimy.app import access_guard as ag
        from zzaimy.ingest import gdocs

        owner = getattr(request.state, "user", "zzaimy")
        try:
            r = gdocs.insert_into_section(account, doc, section, text, user=owner, data_dir=Path(db_path).parent,
                                          scrub=ag.scrub)
        except (ValueError, PermissionError, FileNotFoundError, RuntimeError) as e:
            return RedirectResponse(f"/gdocs/work?{urlencode({'doc': doc, 'account': account, 'err': str(e)})}", status_code=303)
        msg = f"「{r['section']}」 아래에 {r['chars']}자를 넣었습니다"
        return RedirectResponse(f"/gdocs/work?{urlencode({'doc': doc, 'account': account, 'ok': msg})}", status_code=303)

    @app.post("/gdocs/replace")
    def gdocs_replace(request: Request, doc: str = Form(""), account: str = Form(""), old: str = Form(""),
                      new: str = Form("")):
        from zzaimy.app import access_guard as ag
        from zzaimy.ingest import gdocs

        owner = getattr(request.state, "user", "zzaimy")
        try:
            r = gdocs.replace_text(account, doc, old, new, user=owner, data_dir=Path(db_path).parent, scrub=ag.scrub)
        except (ValueError, PermissionError, FileNotFoundError, RuntimeError) as e:
            return RedirectResponse(f"/gdocs/work?{urlencode({'doc': doc, 'account': account, 'err': str(e)})}", status_code=303)
        msg = f"{r['count']}곳을 바꿨습니다"
        return RedirectResponse(f"/gdocs/work?{urlencode({'doc': doc, 'account': account, 'ok': msg})}", status_code=303)

    @app.post("/chat/{session_id}/project")
    def chat_set_project(request: Request, session_id: int, project_id: int = Form(0)):
        """대화를 프로젝트에 넣는다(0 이면 뺀다). 이어진 드라이브 문서는 프로젝트 폴더로(없으면 기타로) 따라 옮긴다."""
        from zzaimy.ingest import gdrive_files

        _owned_chat(request, session_id)
        owner = getattr(request.state, "user", "zzaimy")
        proj = db.get_project(project_id) if project_id else None
        if project_id and (not proj or proj.get("owner") != owner):
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다")
        db.set_chat_project(session_id, int(project_id) if proj else None)
        moved = None
        try:
            acct_ = accounts.get(owner, {}) if password is not None else {}
            moved = gdrive_files.move_to_project(db, session_id, (proj or {}).get("name"), dept=acct_.get("dept") or None)
        except Exception as e:
            db.add_chat(session_id, "assistant", f"대화는 프로젝트에 넣었지만 드라이브 문서는 옮기지 못했습니다({type(e).__name__}).")
        if moved:
            db.add_chat(session_id, "assistant", f"드라이브 문서를 「{moved['project']}」 폴더로 옮겼습니다.")
        return {"ok": True, "project_id": int(project_id) if proj else None, "moved": bool(moved)}

    def _project_by_name(owner: str, name: str) -> dict | None:
        from zzaimy.app.project_search import search as _ps

        want = (name or "").strip()
        if not want:
            return None
        hits = _ps(db, owner, want).get("projects", [])
        exact = [p for p in hits if p["name"].strip() == want]
        return (exact or hits or [None])[0]

    @app.post("/api/chat-documents/create")
    def chat_doc_create(request: Request, session_id: int | None = Form(None), title: str = Form(""),
                        project_id: int | None = Form(None), account: str = Form("")):
        """주소 없이 새 구글 독스를 만들어 대화에 잇는다 — 드라이브 ZZAIMY/<연도>/<프로젝트|대화> 폴더 안에."""
        from zzaimy.app import chat_documents
        from zzaimy.ingest import gdrive_files

        owner = getattr(request.state, "user", "zzaimy")
        proj = db.get_project(project_id) if project_id else None
        if proj and proj.get("owner") != owner:
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다")
        if session_id:
            chat_documents.owned(db, session_id, owner)
        name = (title or (proj or {}).get("name") or "새 문서").strip()[:60]
        if not session_id:
            session_id = db.create_chat_session(name, project_id=project_id, owner=owner)
        try:
            made = gdrive_files.auto_document(db, session_id, owner, name, project_name=(proj or {}).get("name"),
                                              email=account or None)
        except (PermissionError, ValueError) as e:
            raise HTTPException(400, str(e))
        return {"session_id": session_id, **made}

    @app.post("/api/chat-documents/{sid}/confirm-mode")
    def chat_doc_confirm_mode(request: Request, sid: int, on: str = Form("")):
        """확인 후 적용 모드 — 켜면 에이전트가 계획만 보여 주고 담당자가 적용을 누른다(기본 꺼짐)."""
        from zzaimy.app import chat_documents

        chat_documents.owned(db, sid, getattr(request.state, "user", None))
        db.set_setting(f"chat_google_doc_confirm:{sid}", "1" if on == "1" else "")
        return {"ok": True, "confirm": on == "1"}

    @app.post("/api/chat-documents/{sid}/apply-pending")
    def chat_doc_apply_pending(request: Request, sid: int):
        from zzaimy.app import access_guard as ag
        from zzaimy.app import chat_documents, gdocs_agent

        owner = getattr(request.state, "user", "zzaimy")
        link = chat_documents.binding(db, sid, owner)
        if not link:
            raise HTTPException(400, "연결된 문서가 없습니다")
        lines = gdocs_agent.apply_pending(db, sid, owner, link, data_dir=Path(db_path).parent, scrub=ag.scrub)
        if lines:
            db.add_chat(sid, "assistant", "적용됨:\n" + "\n".join(f"- {ln}" for ln in lines))
        return {"ok": True, "applied": lines}

    @app.post("/dev/gdrive/revoke")
    def dev_gdrive_revoke(email: str = Form("")):
        from zzaimy.ingest import gdrive

        gdrive.revoke(email.strip())
        return _nas_redirect(f"구글 계정 {email} 의 허용을 지웠습니다(구글 쪽 접근 권한은 계정 설정에서 따로 지웁니다)")

    def _nas_form_bits(ext_group: list[str], interval_min: int) -> tuple[str, bool, int]:
        # 확장자 묶음 칩 → 확장자 목록, 자동 반입 선택(0=끔) → (auto, interval)
        exts = " ".join(ext_group)
        return exts, interval_min > 0, (interval_min if interval_min > 0 else 60)

    @app.post("/dev/nas/probe-draft")
    def dev_nas_probe_draft(backend: str = Form("local"), root: str = Form(""), username: str = Form(""),
                            password: str = Form(""), domain: str = Form(""), ext_group: list[str] = Form([]),
                            recursive: str = Form("1")):
        """저장 전에 입력한 값으로 연결을 확인한다 — 목록만 읽고 아무것도 저장하지 않는다."""
        from zzaimy.ingest import nas_sync

        exts, _auto, _iv = _nas_form_bits(ext_group, 0)
        draft = {"id": "draft", "name": "확인", "backend": backend, "root": root.strip(), "username": username.strip(),
                 "password": password, "domain": domain.strip(), "extensions": nas_sync._norm_exts(exts),
                 "recursive": recursive == "1"}
        if backend not in nas_sync.BACKENDS or not draft["root"]:
            return {"ok": False, "n_all": 0, "n_match": 0, "sample": [], "error": "방식과 경로를 먼저 적어 주세요"}
        return nas_sync.probe(draft, limit=5)

    def _nas_draft(backend, root, username, password, domain, ext_group, recursive, since="", exclude="", max_mb=0,
                   sid: str = "") -> dict:
        from zzaimy.ingest import nas_sync

        exts, _a, _i = _nas_form_bits(ext_group, 0)
        return {"id": sid or "draft", "name": "확인", "backend": backend, "root": root.strip(),
                "username": username.strip(), "password": password, "domain": domain.strip(),
                "extensions": nas_sync._norm_exts(exts), "recursive": recursive == "1",
                "since": since.strip(), "exclude": nas_sync._norm_excludes(exclude), "max_mb": int(max_mb or 0)}

    @app.post("/dev/nas/browse-draft")
    def dev_nas_browse_draft(backend: str = Form("local"), root: str = Form(""), username: str = Form(""),
                             password: str = Form(""), domain: str = Form(""), recursive: str = Form("1"),
                             rel: str = Form("")):
        """입력한 경로의 하위 폴더와 파일 종류별 개수 — 목록만 읽는다."""
        from zzaimy.ingest import nas_sync

        if backend not in nas_sync.BACKENDS or not root.strip():
            return {"ok": False, "dirs": [], "counts": {}, "n_files": 0, "other": 0, "error": "방식과 경로를 먼저 적어 주세요"}
        draft = _nas_draft(backend, root, username, password, domain, [], recursive)
        rel = rel.strip().strip("/\\")
        if ".." in rel.split("/") or ".." in rel.split("\\"):
            return {"ok": False, "dirs": [], "counts": {}, "n_files": 0, "other": 0, "error": "잘못된 경로"}
        return nas_sync.browse(draft, rel)

    @app.post("/dev/nas/plan-draft")
    def dev_nas_plan_draft(backend: str = Form("local"), root: str = Form(""), username: str = Form(""),
                           password: str = Form(""), domain: str = Form(""), ext_group: list[str] = Form([]),
                           recursive: str = Form("1"), since: str = Form(""), exclude: str = Form(""),
                           max_mb: int = Form(0), sid: str = Form("")):
        """저장 전 반입 미리보기 — 새로 들어올 파일 수와 예시(읽지 않음)."""
        from zzaimy.ingest import nas_sync

        if backend not in nas_sync.BACKENDS or not root.strip():
            return {"ok": False, "new": 0, "same": 0, "skipped": 0, "sample": [], "error": "방식과 경로를 먼저 적어 주세요"}
        return nas_sync.plan(_nas_draft(backend, root, username, password, domain, ext_group, recursive,
                                        since, exclude, max_mb, sid=sid))

    @app.post("/dev/nas/{sid}/plan")
    def dev_nas_plan(sid: str):
        return RedirectResponse(f"/dev/nas?plan={sid}", status_code=303)

    @app.post("/dev/nas/add")
    def dev_nas_add(name: str = Form(""), backend: str = Form("local"), root: str = Form(""),
                    target: str = Form("regulation"), sector: str = Form("common"),
                    username: str = Form(""), password: str = Form(""), domain: str = Form(""),
                    ext_group: list[str] = Form([]), extensions: str = Form(""), recursive: str = Form("1"),
                    auto: str = Form(""), interval_min: int = Form(0), since: str = Form(""),
                    exclude: str = Form(""), max_mb: int = Form(0), dept: str = Form(""), access_level: str = Form("")):
        from zzaimy.ingest import nas_sync

        exts, is_auto, iv = _nas_form_bits(ext_group, interval_min)
        if not exts:
            exts = extensions
        if auto == "1":
            is_auto, iv = True, (interval_min or 60)
        try:
            src = nas_sync.add(name, backend, root, target, sector=sector, username=username,
                               password=password, domain=domain, extensions=exts,
                               recursive=(recursive == "1"), auto=is_auto, interval_min=iv,
                               since=since, exclude=exclude, max_mb=max_mb, dept=dept, access_level=access_level)
        except ValueError as e:
            return _nas_redirect(str(e), ok=False)
        nas_sync.ensure_scheduler(db, processor, inbox_dir)
        return _nas_redirect(f"폴더 「{src['name']}」을 연결했습니다")

    @app.post("/dev/nas/{sid}/update")
    def dev_nas_update(sid: str, name: str = Form(""), root: str = Form(""), target: str = Form(""),
                       sector: str = Form(""), username: str = Form(""), password: str = Form(""),
                       domain: str = Form(""), ext_group: list[str] = Form([]), extensions: str = Form(""),
                       recursive: str = Form("1"), auto: str = Form(""), interval_min: int = Form(0),
                       since: str = Form(""), exclude: str = Form(""), max_mb: int = Form(0),
                       dept: str = Form(""), access_level: str = Form("")):
        from zzaimy.ingest import nas_sync

        exts, is_auto, iv = _nas_form_bits(ext_group, interval_min)
        if not exts:
            exts = extensions
        if auto == "1":
            is_auto, iv = True, (interval_min or 60)
        try:
            src = nas_sync.update(sid, name=name, root=root, target=target or None, sector=sector or None,
                                  username=username, password=password, domain=domain,
                                  extensions=exts or None, recursive=(recursive == "1"),
                                  auto=is_auto, interval_min=iv, since=since, exclude=exclude, max_mb=max_mb,
                                  dept=dept, access_level=access_level)
        except ValueError as e:
            return _nas_redirect(str(e), ok=False)
        nas_sync.ensure_scheduler(db, processor, inbox_dir)
        return _nas_redirect(f"폴더 「{src['name']}」 설정을 저장했습니다")

    @app.post("/dev/nas/{sid}/delete")
    def dev_nas_delete(sid: str):
        from zzaimy.ingest import nas_sync

        nas_sync.delete(sid)
        return _nas_redirect("폴더 연결을 지웠습니다 (이미 가져온 문서는 그대로)")

    @app.post("/dev/nas/{sid}/probe")
    def dev_nas_probe(sid: str):
        return RedirectResponse(f"/dev/nas?probe={sid}", status_code=303)

    @app.post("/dev/nas/{sid}/sync")
    def dev_nas_sync(sid: str, wait: str = Form("")):
        """반입 시작 — 보통은 백그라운드, wait=1 이면 끝날 때까지 기다린다(점검·테스트용)."""
        from zzaimy.ingest import nas_sync

        src = nas_sync.get(sid)
        if src is None:
            return _nas_redirect("없는 폴더입니다", ok=False)
        if wait == "1":
            r = nas_sync.sync(src, db, processor, inbox_dir)
            nas_sync._runs[sid] = {"running": False, "started": "", "progress": "", "last": r}
            return _nas_redirect(f"「{src['name']}」 가져오기 완료 — {r.get('summary') or r.get('error')}")
        if not nas_sync.start_sync(src, db, processor, inbox_dir):
            return _nas_redirect(f"「{src['name']}」은 이미 가져오는 중입니다", ok=False)
        return _nas_redirect(f"「{src['name']}」 가져오기를 시작했습니다 — 끝나면 이 화면에 결과가 남습니다")

    # ---- 설치파일(setup.exe) — Windows 에서 빌드 키트로 만든 뒤 여기 올려 배포한다 ----

    def _hwp_dist_dir() -> Path:
        return Path(os.environ.get("ZZAIMY_DIST_DIR") or _HWP_BUNDLE.parent)

    def _hwp_installer_paths() -> tuple[Path, Path]:
        d = _hwp_dist_dir()
        return d / "zzaimy-agent-setup.exe", d / "zzaimy-agent-setup.json"

    def _hwp_installer_info() -> dict | None:
        """올라와 있는 설치파일의 메타(버전·크기·sha256·올린 시각). 없으면 None."""
        exe, meta = _hwp_installer_paths()
        if not exe.is_file():
            return None
        info = {}
        try:
            info = _aj.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        size = exe.stat().st_size
        if info.get("size") != size or not info.get("sha256"):
            import hashlib as _hl

            info = {**info, "size": size, "sha256": _hl.sha256(exe.read_bytes()).hexdigest()}
        info.setdefault("version", "")
        info.setdefault("uploaded_at", "")
        info["mb"] = round(size / 1048576, 1)
        return info

    @app.post("/dev/hwp/installer")
    async def dev_hwp_installer_upload(request: Request, file: UploadFile = File(...),
                                       version: str = Form("")):
        """setup.exe 업로드 — 확장자·PE 서명(MZ)·크기만 검사하고 원자적으로 교체한다."""
        import hashlib as _hl
        from datetime import datetime as _dt

        name = (file.filename or "").strip()
        if not name.lower().endswith(".exe"):
            return RedirectResponse(
                "/dev/hwp?err=setup.exe 파일만 올릴 수 있습니다", status_code=303)
        data = await file.read()
        if len(data) < 1024 or data[:2] != b"MZ":
            return RedirectResponse("/dev/hwp?err=Windows 실행 파일이 아닙니다", status_code=303)
        if len(data) > 300 * 1024 * 1024:
            return RedirectResponse(
                "/dev/hwp?err=파일이 너무 큽니다(300MB 초과)", status_code=303)
        exe, meta = _hwp_installer_paths()
        exe.parent.mkdir(parents=True, exist_ok=True)
        tmp = exe.with_suffix(".exe.part")
        tmp.write_bytes(data)
        tmp.replace(exe)
        info = {"name": name, "version": version.strip()[:40], "size": len(data),
                "sha256": _hl.sha256(data).hexdigest(),
                "uploaded_at": _dt.now().strftime("%Y-%m-%d %H:%M"),
                "by": getattr(request.state, "user", "") or ""}
        meta.write_text(_aj.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        return RedirectResponse(
            f"/dev/hwp?ok=설치파일을 올렸습니다 ({round(len(data) / 1048576, 1)}MB)",
            status_code=303)

    @app.post("/dev/hwp/installer/delete")
    def dev_hwp_installer_delete():
        for p in _hwp_installer_paths():
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        return RedirectResponse("/dev/hwp?ok=설치파일을 내렸습니다", status_code=303)

    @app.get("/hwp/setup.exe")
    def hwp_installer_download():
        """담당자 PC 배포용 — 로그인한 누구나 내려받는다(에이전트 zip 과 같은 접속 정보가 들어 있다)."""
        from fastapi.responses import FileResponse

        exe, _meta = _hwp_installer_paths()
        if not exe.is_file():
            raise HTTPException(404, "올라온 설치파일이 없습니다")
        return FileResponse(exe, media_type="application/octet-stream",
                            filename="zzaimy-agent-setup.exe")

    @app.get("/dev/hwp", response_class=HTMLResponse)
    def dev_hwp(request: Request, err: str = "", ok: str = ""):
        import time as _t

        now = _t.time()
        sessions = [
            {"id": sid, "age": int(now - s["last_poll"]),
             "n_cmd": len(s["commands"]), "results": s["results"][-12:][::-1],
             "docs": s.get("docs") or [], "version": s.get("version") or "",
             "artifacts": (s.get("artifacts") or [])[::-1]}
            for sid, s in sorted(
                hwp_sessions.items(), key=lambda kv: -kv[1]["registered"]
            )
        ]
        return templates.TemplateResponse(request, "dev_hwp.html", ctx(request, {
            "token": db.get_setting("hwp_agent_token"),
            "sessions": sessions,
            "err": err, "ok": ok,
            "bundle_ready": _HWP_BUNDLE.exists(),
            "installer": _hwp_installer_info(),
            "ops": sorted(_HWP_OPS - {"open_bytes"}),   # open_bytes는 서버가 조립하는 내부용
        }))

    @app.get("/hwp/docs")
    def hwp_docs(refresh: int = 0):
        """열린 한글 문서 목록(대상 선택 모달용). refresh=1이면 에이전트에
        list_docs를 요청하고, 현재 캐시된 목록을 함께 돌려준다."""
        connected = bool(hwp_sessions)
        docs, bound = [], None
        if connected:
            _sid, s = max(hwp_sessions.items(), key=lambda kv: kv[1]["registered"])
            if refresh:
                _hwp_send_many([{"op": "list_docs", "args": {}}])
            docs = s.get("docs") or []
            bound = next((d for d in docs if d.get("bound")), None)
        return {"connected": connected, "docs": docs, "bound": bound}

    @app.post("/hwp/target")
    def hwp_target(mode: str = Form("select"), id: int = Form(-1)):
        """작업 대상 지정 — mode=new면 새 문서, 아니면 해당 문서를 선택한다.
        이어서 list_docs로 목록을 갱신한다(모달이 최신 상태를 다시 읽음)."""
        if not hwp_sessions:
            return JSONResponse({"ok": False, "error": "연결된 에이전트가 없습니다."},
                                status_code=409)
        ops = ([{"op": "new_doc", "args": {}}] if mode == "new"
               else [{"op": "select_doc", "args": {"id": id}}])
        ops.append({"op": "list_docs", "args": {}})
        _hwp_send_many(ops)
        return {"ok": True, "mode": mode}

    @app.post("/hwp/open-upload")
    async def hwp_open_upload(file: UploadFile = File(...)):
        """업로드한 .hwp/.hwpx 를 에이전트로 보내 PC 한글에서 열고 대상 지정."""
        import base64 as _b64
        if not hwp_sessions:
            return JSONResponse({"ok": False, "error": "연결된 에이전트가 없습니다."},
                                status_code=409)
        data = await file.read()
        if not data:
            return JSONResponse({"ok": False, "error": "빈 파일입니다."}, status_code=400)
        if len(data) > 20 * 1024 * 1024:
            return JSONResponse({"ok": False, "error": "파일이 너무 큽니다(20MB 초과)."},
                                status_code=413)
        name = file.filename or "upload.hwpx"
        fmt = "hwp" if name.lower().endswith(".hwp") else "hwpx"
        _hwp_send_many([
            {"op": "open_bytes", "args": {"name": name,
             "b64": _b64.b64encode(data).decode(), "format": fmt}},
            {"op": "list_docs", "args": {}},
        ])
        return {"ok": True, "name": name}

    @app.get("/dev/hwp/latest-artifact")
    def hwp_latest_artifact():
        """가장 최근 회수된 PDF 산출물 URL(미리보기 폴링용). 없으면 url=None."""
        if not hwp_sessions:
            return {"url": None, "cmd": None}
        _sid, s = max(hwp_sessions.items(), key=lambda kv: kv[1]["registered"])
        pdfs = [a for a in (s.get("artifacts") or [])
                if a.get("format") == "pdf" and a.get("url")]
        last = pdfs[-1] if pdfs else None
        return {"url": last["url"] if last else None,
                "cmd": last.get("cmd") if last else None}

    @app.post("/dev/hwp/token")
    def dev_hwp_token():
        db.set_setting("hwp_agent_token", secrets.token_hex(16))
        return RedirectResponse("/dev/hwp", status_code=303)

    _HWP_BUNDLE = Path("data/dist/hwp-agent-base.zip")

    @app.get("/dev/hwp/agent.zip")
    def dev_hwp_bundle(request: Request):
        """개인화 에이전트 앱 — 베이스 번들에 접속 정보·인증서를 심어 내려준다."""
        import io as _io
        import zipfile as _zf

        if not _HWP_BUNDLE.exists():
            raise HTTPException(
                404, "기본 패키지가 없습니다 — scripts/71_build_hwp_agent_bundle.py 로 조립하세요"
            )
        token = db.get_setting("hwp_agent_token")
        if not token:
            return RedirectResponse("/dev/hwp?err=접속 키를 먼저 발급하세요", status_code=303)

        host = request.url.hostname or "127.0.0.1"
        if request.url.port and request.url.port not in (80, 443):
            server = f"{request.url.scheme}://{host}:{request.url.port}"
        else:
            server = f"{request.url.scheme}://{host}"

        cert_path = os.environ.get("ZZAIMY_TLS_CERT", "")
        cert_bytes = b""
        if cert_path and Path(cert_path).exists():
            cert_bytes = Path(cert_path).read_bytes()

        buf = _io.BytesIO()
        with _zf.ZipFile(_HWP_BUNDLE) as src, \
                _zf.ZipFile(buf, "w", _zf.ZIP_DEFLATED) as out:
            for item in src.infolist():
                out.writestr(item, src.read(item))
            config = {"server": server, "token": token}
            if cert_bytes:
                out.writestr("server.crt", cert_bytes)
                config["ca_cert"] = "server.crt"
            out.writestr("config.json", _aj.dumps(config, ensure_ascii=False))
        buf.seek(0)

        from fastapi.responses import Response as _Resp

        return _Resp(
            buf.read(), media_type="application/zip",
            headers={"Content-Disposition":
                     'attachment; filename="zzaimy-hwp-agent.zip"'},
        )

    @app.get("/dev/hwp/installer-kit.zip")
    def dev_hwp_installer_kit(request: Request):
        """Inno Setup 빌드 키트 — zzaimy-agent.iss + BUILD.md + payload(개인화
        번들)를 한 폴더 구조로 담아 내려준다. Windows에서 압축을 풀고 .iss 를
        컴파일하면 setup.exe 가 나온다(installer/BUILD.md 참조)."""
        import io as _io
        import zipfile as _zf

        if not _HWP_BUNDLE.exists():
            raise HTTPException(
                404, "기본 패키지가 없습니다 — scripts/71_build_hwp_agent_bundle.py 로 조립하세요"
            )
        iss = Path("installer") / "zzaimy-agent.iss"
        if not iss.exists():
            raise HTTPException(404, "installer/zzaimy-agent.iss 파일이 없습니다")
        token = db.get_setting("hwp_agent_token")
        if not token:
            return RedirectResponse("/dev/hwp?err=접속 키를 먼저 발급하세요", status_code=303)

        host = request.url.hostname or "127.0.0.1"
        if request.url.port and request.url.port not in (80, 443):
            server = f"{request.url.scheme}://{host}:{request.url.port}"
        else:
            server = f"{request.url.scheme}://{host}"
        cert_path = os.environ.get("ZZAIMY_TLS_CERT", "")
        cert_bytes = (Path(cert_path).read_bytes()
                      if cert_path and Path(cert_path).exists() else b"")

        root = "zzaimy-agent-installer/"
        buf = _io.BytesIO()
        with _zf.ZipFile(_HWP_BUNDLE) as src, \
                _zf.ZipFile(buf, "w", _zf.ZIP_DEFLATED) as out:
            out.writestr(root + "zzaimy-agent.iss", iss.read_bytes())
            bd = Path("installer") / "BUILD.md"
            if bd.exists():
                out.writestr(root + "BUILD.md", bd.read_bytes())
            for item in src.infolist():
                out.writestr(root + "payload/" + item.filename, src.read(item))
            config = {"server": server, "token": token}
            if cert_bytes:
                out.writestr(root + "payload/server.crt", cert_bytes)
                config["ca_cert"] = "server.crt"
            out.writestr(root + "payload/config.json",
                         _aj.dumps(config, ensure_ascii=False))
        buf.seek(0)
        from fastapi.responses import Response as _Resp
        return _Resp(
            buf.read(), media_type="application/zip",
            headers={"Content-Disposition":
                     'attachment; filename="zzaimy-agent-installer-kit.zip"'},
        )

    @app.post("/dev/hwp/send")
    def dev_hwp_send(op: str = Form(...), args_json: str = Form("")):
        """개발자 명령 콘솔 — op + 인자(JSON)를 자유롭게 보낸다."""
        if op not in _HWP_OPS:
            raise HTTPException(400, f"허용되지 않은 명령입니다: {op}")
        args: dict = {}
        if args_json.strip():
            try:
                args = _aj.loads(args_json)
                if not isinstance(args, dict):
                    raise ValueError
            except ValueError:
                return RedirectResponse(
                    "/dev/hwp?err=인자는 JSON 객체여야 합니다", status_code=303
                )
        if not _hwp_send(op, args):
            return RedirectResponse(
                "/dev/hwp?err=연결된 에이전트가 없습니다", status_code=303
            )
        return RedirectResponse("/dev/hwp", status_code=303)

    # ---- 모델 학습 — 기성 오픈소스 도구 연결 (ADR-0011) ----
    # 도구는 각자 프로세스로 돌고 플랫폼은 연결·임베드만 한다. GPU(DGX) 확보
    # 후 실제 가동. 주소는 설정에 저장(없으면 미연결로 표시).

    _TRAIN_TOOLS = [
        {"key": "labelstudio", "name": "Label Studio", "step": "검수",
         "role": "학습 예시 검수",
         "gpu": False, "setting": "labelstudio_url"},
        {"key": "llamaboard", "name": "LLaMA Board", "step": "학습 실행",
         "role": "파인튜닝 실행 · 데이터셋과 학습 설정값",
         "gpu": True, "setting": "llamaboard_url"},
        {"key": "tensorboard", "name": "TensorBoard", "step": "진행 확인",
         "role": "손실·학습률·평가지표 그래프",
         "gpu": True, "setting": "tensorboard_url"},
    ]

    @app.get("/dev/train", response_class=HTMLResponse)
    def dev_train(request: Request, err: str = "", ok: str = ""):
        # 연결마다 서버가 지금 내어 주는 모델 목록 — 저장된 값이 아니라 실시간(짧게 캐시)
        # Label Studio 는 실제 연결 여부(connected)로 표시 — 주소만 있다고 연결됨이 아니다.
        # 도구 계정·주소(LS 아이디·비밀번호 재설정, GPU 도구 주소)도 이 화면의 도구 카드에서 다룬다.
        from zzaimy.dataset import ls_admin
        from zzaimy.generate import llm_connections, model_config

        ls = _ls_status()
        tools = [
            {**t, "url": db.get_setting(t["setting"], ""),
             "connected": ls["ok"] if t["key"] == "labelstudio" else None}
            for t in _TRAIN_TOOLS
        ]
        from zzaimy.export.bundle import preview_bundle
        try:
            export_preview = preview_bundle(
                db, model_dir=os.environ.get("ZZAIMY_MODEL_DIR"))
        except Exception:
            export_preview = []
        # 학습 순서 — 단계별 상태는 DB(학습 데이터 묶음), 측정 산출물(검색 정확도), 설정(도구 주소)에서만
        datasets = db.list_datasets(limit=10)
        llamaboard_url = db.get_setting("llamaboard_url", "")
        llm = model_config.current()
        llm_probe = model_config.probe() if llm["configured"] else {"ok": False, "models": [], "error": "주소 없음"}
        llm_url = llm["base_url"] if llm["configured"] else ""
        try:
            ev = _retrieval_eval_state().get("result") or {}
        except Exception:
            ev = {}
        measured = (ev.get("measured_at") or "")[:10]
        steps = [
            {"title": "학습 데이터 준비", "desc": "데이터 공방에서 예시 생성, Label Studio 검수",
             "status": f"묶음 {len(datasets)}개" if datasets else "데이터 없음",
             "state": "done" if datasets else "todo"},
            {"title": "베이스라인 측정", "desc": "학습 전 성능 기록 (개선폭의 기준)",
             "status": (f"검색 정확도 측정 {measured}" if measured else "검색 정확도 측정 전")
             + " · 작성 모델 측정 전",
             "state": "doing" if measured else "todo"},
            {"title": "SFT 실행", "desc": "LLaMA Board에서 능력 학습, TensorBoard로 진행 확인",
             "status": "LLaMA Board 연결됨" if llamaboard_url else "GPU 대기",
             "state": "todo"},
            {"title": "DPO", "desc": "담당자 판정·재작성 이력으로 선호 학습",
             "status": "SFT 후", "state": "todo"},
            {"title": "모델 서버 연결", "desc": "문서 작업의 기본 연결",
             "status": (f"연결됨 · {llm['model'] or (llm_probe['models'][0] if llm_probe['models'] else '모델 미선택')}"
                        if llm_probe["ok"] else ("주소 설정됨 · " + llm_probe["error"] if llm_url else "주소 미설정")),
             "state": "done" if llm_probe["ok"] else "todo"},
        ]
        return templates.TemplateResponse(request, "dev_train.html", ctx(request, {
            "err": err, "ok": ok,
            "tools": tools,
            "steps": steps,
            "ls": ls,
            "ls_username": db.get_setting("labelstudio_username", ""),
            "ls_token_set": bool(db.get_setting("labelstudio_token")),
            "ls_admin_available": ls_admin.available(),
            "llm_url": llm_url,
            "llm": llm, "llm_probe": llm_probe,
            "connections": [dict(c, usage=model_config.usage_today(c["id"]),
                                 live=_live_models_cached(c["id"]))
                            for c in llm_connections.list_public()],
            "llm_kinds": llm_connections.KINDS,
            "usage_all": model_config.usage_today(),
            "datasets": datasets,
            "export_preview": export_preview,
            "llm_roles": llm_connections.roles_public(),
            "role_labels": llm_connections.ROLES,
            "search_serving": search_serving.status(),
            "serving_plan": serving_plan.status(_live_models_cached),
            "train_host": serving_plan.training_box(),
        }))

    @app.get("/dev/train/export.zip")
    def dev_train_export(
        rag: int | None = None, datasets: int | None = None, model: int | None = None,
    ):
        """학습 산출물 반출 번들 — 선택 항목만 개방 표준으로 (ADR-0012).

        폼은 항목마다 hidden 0 + checkbox 1 을 보내므로 해제한 항목은 0으로 온다
        (기본값 1이던 때는 무엇을 꺼도 전부 반출됐다). 인자가 하나도 없는 맨 URL은
        예전처럼 전체 반출."""
        from datetime import datetime as _dt

        from zzaimy.export.bundle import build_bundle

        flags = (("rag", rag), ("datasets", datasets), ("model", model))
        if all(v is None for _, v in flags):
            include = {k for k, _ in flags}
        else:
            include = {k for k, on in flags if on}
        if not include:
            return RedirectResponse(
                "/dev/train?err=반출할 항목을 하나 이상 선택하세요", status_code=303
            )
        model_dir = os.environ.get("ZZAIMY_MODEL_DIR")
        data, _manifest = build_bundle(db, model_dir=model_dir, include=include)
        from fastapi.responses import Response as _Resp

        stamp = _dt.now().strftime("%Y%m%d")
        return _Resp(
            data, media_type="application/zip",
            headers={"Content-Disposition":
                     f'attachment; filename="zzaimy-artifacts-{stamp}.zip"'},
        )

    # ---- LLM 연결 관리 — 내부 vLLM·외부 API 등록, 확인, 기본 지정 (키는 서버 파일에만) ----

    def _llm_redirect(msg: str, ok: bool = True) -> RedirectResponse:
        return RedirectResponse(f"/dev/train?{'ok' if ok else 'err'}={msg}", status_code=303)

    @app.post("/dev/llm/add")
    def dev_llm_add(name: str = Form(""), kind: str = Form("vllm"), base_url: str = Form(""),
                    model: str = Form(""), api_key: str = Form(""), vision_model: str = Form("")):
        from zzaimy.generate import llm_connections, model_config

        try:
            conn = llm_connections.add(name, kind, base_url, model, api_key, vision_model=vision_model)
        except ValueError as e:
            return _llm_redirect(str(e), ok=False)
        _live_models_cache.clear()
        model_config.reset_status_cache()
        return _llm_redirect(f"연결 「{conn['name']}」을 추가했습니다")

    @app.post("/dev/llm/{cid}/update")
    def dev_llm_update(cid: str, name: str = Form(""), base_url: str = Form(""),
                       model: str = Form(""), api_key: str = Form(""), vision_model: str = Form("")):
        from zzaimy.generate import llm_connections, model_config

        try:
            conn = llm_connections.update(cid, name=name, base_url=base_url, model=model, api_key=api_key,
                                          vision_model=vision_model)
        except ValueError as e:
            return _llm_redirect(str(e), ok=False)
        _live_models_cache.clear()
        model_config.reset_status_cache()
        return _llm_redirect(f"연결 「{conn['name']}」을 저장했습니다")

    @app.post("/dev/llm/{cid}/delete")
    def dev_llm_delete(cid: str):
        from zzaimy.generate import llm_connections, model_config

        llm_connections.delete(cid)
        _live_models_cache.clear()
        model_config.reset_status_cache()
        return _llm_redirect("연결을 삭제했습니다")

    @app.post("/dev/llm/{cid}/activate")
    def dev_llm_activate(cid: str, ack: str = Form("")):
        from zzaimy.generate import llm_connections, model_config

        try:
            conn = llm_connections.activate(cid, ack_external=(ack == "1"))
        except ValueError as e:
            return _llm_redirect(str(e), ok=False)
        model_config.reset_status_cache()
        return _llm_redirect(f"「{conn['name']}」을 문서 작업 기본 연결로 지정했습니다")

    @app.post("/dev/llm/{cid}/external")
    def dev_llm_external(cid: str):
        from zzaimy.generate import llm_connections

        try:
            conn = llm_connections.set_external(cid)
        except ValueError as e:
            return _llm_redirect(str(e), ok=False)
        return _llm_redirect(f"「{conn['name']}」을 외부 AI 참조용 연결로 지정했습니다")

    @app.post("/dev/llm/external/clear")
    def dev_llm_external_clear():
        from zzaimy.generate import llm_connections

        llm_connections.clear_external()
        return _llm_redirect("외부 AI 참조용 연결을 해제했습니다")

    @app.post("/dev/llm/deactivate")
    def dev_llm_deactivate():
        from zzaimy.generate import llm_connections, model_config

        llm_connections.deactivate()
        model_config.reset_status_cache()
        return _llm_redirect("기본 연결을 해제했습니다 — 환경변수 설정을 씁니다")

    @app.post("/dev/llm/role")
    def dev_llm_role(role: str = Form(...), cid: str = Form(""), model: str = Form("")):
        """용도마다 쓸 서버와 모델을 정한다 — 문서 작업·이미지 판독·임베딩·학습."""
        from zzaimy.generate import llm_connections, model_config

        try:
            llm_connections.set_role(role, cid.strip(), model.strip())
        except ValueError as e:
            return _llm_redirect(str(e), ok=False)
        model_config.reset_status_cache()
        _live_models_cache.clear()
        label = llm_connections.ROLES.get(role, role)
        if not cid.strip():
            return _llm_redirect(f"「{label}」은(는) 문서 작업 기본 서버를 씁니다")
        conn = llm_connections.get(cid.strip()) or {}
        picked = f" · 모델 {model.strip()}" if model.strip() else ""
        tail = (" 임베딩을 바꾸면 벡터 공간이 달라져 전체 재색인이 필요합니다."
                if role == "embed" else "")
        return _llm_redirect(f"「{label}」은(는) 「{conn.get('name', '')}」{picked} 로 정했습니다.{tail}")

    @app.post("/dev/llm/roles")
    async def dev_llm_roles(request: Request):
        """단계별 모델을 한 번에 저장한다 — 화면의 '변경사항 저장' 하나에 대응한다.

        단건 저장(/dev/llm/role)을 단계마다 부르면 중간 실패 때 절반만 바뀐 상태가 남고,
        화면에도 줄마다 저장 버튼이 붙어 손이 많이 간다. 여기서는 전부 검증한 뒤 한 번에 쓴다.
        """
        from zzaimy.generate import llm_connections, model_config

        form = await request.form()
        roles = form.getlist("role")
        # 화면에서 고르는 것은 '어느 서버의 어느 모델' 하나다 — "연결id|모델" 로 온다.
        # 빈 값이면 그 단계는 기본 서버를 따른다.
        raw = form.getlist("pick")
        if not roles or len(roles) != len(raw):
            return _llm_redirect("보낸 값이 맞지 않습니다", ok=False)
        picks = {}
        for role, value in zip(roles, raw):
            cid, _, model = str(value).partition("|")
            picks[role] = (cid.strip(), model.strip())
        before = {r["role"]: (r["id"], r["model"]) for r in llm_connections.roles_public()}
        try:
            llm_connections.set_roles(picks)
        except ValueError as e:
            return _llm_redirect(str(e), ok=False)
        model_config.reset_status_cache()
        _live_models_cache.clear()
        changed = [llm_connections.ROLES.get(r, r) for r, (c, m) in picks.items()
                   if before.get(r, ("", "")) != (c, m)]
        if not changed:
            return _llm_redirect("바뀐 것이 없습니다")
        tail = (" 임베딩을 바꾸면 벡터 공간이 달라져 전체 재색인이 필요합니다."
                if "embed" in picks else "")
        return _llm_redirect(f"{', '.join(changed)} 을(를) 저장했습니다.{tail}")


    @app.post("/dev/llm/models-live")
    def dev_llm_models_live(kind: str = Form("vllm"), base_url: str = Form(""),
                            api_key: str = Form(""), cid: str = Form("")):
        """저장 전에 서버가 지금 내어 주는 모델 목록만 받아 본다 — 연결 추가·수정 창이 쓴다."""
        from zzaimy.generate import llm_connections

        conn = {"kind": kind, "base_url": base_url.strip(), "api_key": api_key}
        if cid:                                   # 수정 창에서 키를 다시 넣지 않았을 때
            saved = llm_connections.get(cid)
            if saved is None:
                return {"ok": False, "models": [], "error": "없는 연결입니다"}
            conn = {"kind": saved.get("kind", kind),
                    "base_url": (base_url.strip() or saved.get("base_url", "")),
                    "api_key": api_key or saved.get("api_key", "")}
        if not conn["base_url"]:
            return {"ok": False, "models": [], "error": "주소를 먼저 넣으세요"}
        return llm_connections.live_models(conn)


    @app.post("/dev/llm/{cid}/test")
    def dev_llm_test(cid: str):
        from zzaimy.generate import llm_connections

        conn = llm_connections.get(cid)
        if conn is None:
            return _llm_redirect("없는 연결입니다", ok=False)
        r = llm_connections.probe(conn)
        if r["ok"]:
            shown = ", ".join(r["models"][:6]) + (" …" if len(r["models"]) > 6 else "")
            llm_connections.record_check(cid, True, f"모델 {len(r['models'])}개")
            return _llm_redirect(f"「{conn['name']}」 연결 확인 — 모델 {len(r['models'])}개 ({shown})")
        llm_connections.record_check(cid, False, r["error"])
        tail = f" {r['hint']}" if r.get("hint") else ""
        return _llm_redirect(f"「{conn['name']}」 연결 실패 — {r['error']}.{tail}", ok=False)

    @app.get("/dev/train/export/file")
    def dev_train_export_file(path: str):
        """반출 목록의 파일 하나를 그대로 내려받는다 — 묶음이 아니면 zip 을 거칠 이유가 없다."""
        from zzaimy.export.bundle import single_file

        data = single_file(db, path, model_dir=os.environ.get("ZZAIMY_MODEL_DIR"))
        if data is None:
            raise HTTPException(404, "반출 목록에 없는 파일입니다")
        from fastapi.responses import Response as _Resp

        name = path.rsplit("/", 1)[-1]
        return _Resp(data, media_type="application/octet-stream",
                     headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/dev/train/ls-password")
    def dev_train_ls_password(new_pw: str = Form(...), confirm: str = Form("")):
        """Label Studio 계정 비밀번호 재설정 — 값은 환경변수로 헬퍼에만 전달한다."""
        from zzaimy.dataset import ls_admin

        if len(new_pw) < 8:
            return _train_redirect("비밀번호는 8자 이상이어야 합니다", ok=False)
        if new_pw != confirm:
            return _train_redirect("비밀번호 확인이 일치하지 않습니다", ok=False)
        ok, msg = ls_admin.set_password(new_pw)
        if not ok:
            return _train_redirect(f"Label Studio 비밀번호 재설정 실패 — {msg}", ok=False)
        return _train_redirect(
            "Label Studio 비밀번호를 재설정했습니다 — 새 비밀번호로 로그인하세요")

    @app.post("/dev/train/url")
    def dev_tool_url(setting: str = Form(...), url: str = Form(""), back: str = Form("/dev/train")):
        # Label Studio 주소는 구축 스크립트(scripts/68_labelstudio_token.sh)가 넣는다 —
        # 화면에서는 GPU 도구(LLaMA Board·TensorBoard) 주소만 바꾼다
        valid = {t["setting"] for t in _TRAIN_TOOLS if t["key"] != "labelstudio"}
        if setting not in valid:
            raise HTTPException(400, "알 수 없는 도구입니다")
        url = url.strip()
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(400, "http(s):// 주소여야 합니다")
        db.set_setting(setting, url)
        dest = back if back.startswith("/dev/") else "/dev/train"
        return RedirectResponse(dest, status_code=303)

    # ---- 데이터 공방 — 기록 → 학습 데이터(JSONL) 변환 (개발자 전용) ----

    @app.get("/dev/data", response_class=HTMLResponse)
    def dev_data(request: Request, err: str = "", ok: str = "", ds_page: int = 0):
        from zzaimy.dataset.build import preview_sources, rag_status

        ds_page = max(ds_page, 0)
        DS_PER = 20
        ds_total = db.count_datasets()
        return templates.TemplateResponse(request, "dev_data.html", ctx(request, {
            "rag_rows": rag_status(db),
            "source_preview": preview_sources(db),
            "datasets": db.list_datasets(limit=DS_PER, offset=ds_page * DS_PER),
            "ds_page": ds_page, "ds_total": ds_total, "ds_per": DS_PER,
            "ds_has_next": (ds_page + 1) * DS_PER < ds_total,
            "err": err, "ok": ok,
            "ls": _ls_status(),
            "labelstudio_url": db.get_setting("labelstudio_url"),
            "llamaboard_url": db.get_setting("llamaboard_url"),
        }))

    @app.post("/dev/data/build-and-push")
    def dev_data_build_and_push(sources: list[str] = Form([])):
        """예시 만들기 + Label Studio 자동 주입을 한 번에 — 연결돼 있으면 바로 푸시,
        아니면 데이터셋만 만들고 연결 방법(구축 스크립트)을 안내한다."""
        from datetime import datetime as _dt

        from zzaimy.dataset.build import _BUILDERS, export_dataset
        from zzaimy.dataset.ls_client import LabelStudioError

        srcs = [s for s in sources if s in _BUILDERS]
        if not srcs:
            return RedirectResponse("/dev/data?err=기록 종류를 하나 이상 고르세요", status_code=303)
        # 1) 예시 묶음 생성(수치검증 통과분만) — 대장에 기록
        try:
            export_dataset(db, srcs, _dt.now().strftime("예시-%Y%m%d-%H%M"))
        except ValueError as exc:
            return RedirectResponse(f"/dev/data?err={exc}", status_code=303)
        # 2) Label Studio 연결이 있으면 자동 주입 (연결은 scripts/68_labelstudio_token.sh 가 맺는다)
        if not (db.get_setting("labelstudio_url") and db.get_setting("labelstudio_token")):
            return RedirectResponse(
                "/dev/data?ok=예시 묶음을 만들었습니다. Label Studio가 연결되지 않아 보내지는"
                " 않았습니다 — 서버에서 scripts/68_labelstudio_token.sh 를 실행하면"
                " 다음부터 자동으로 보냅니다.",
                status_code=303)
        pairs = []
        for s in srcs:
            pairs.extend(_BUILDERS[s](db).pairs)
        if not pairs:
            return RedirectResponse("/dev/data?err=보낼 예시가 없습니다", status_code=303)
        try:
            cli = _ls_client()
            pid = cli.ensure_project(_LS_PROJECT)
            n = cli.push_tasks(pid, pairs)
        except LabelStudioError as e:
            return RedirectResponse(f"/dev/data?err=Label Studio: {e}", status_code=303)
        _ls_status_reset()   # 진행 수치가 바뀌었다 — 캐시를 비워 바로 반영
        return RedirectResponse(f"/dev/data?ls_pushed={n}", status_code=303)

    @app.post("/dev/data/build")
    def dev_data_build(sources: list[str] = Form([])):
        from datetime import datetime as _dt

        from zzaimy.dataset.build import export_dataset

        # 이름은 자동 부여(사용자가 'sft' 같은 걸 타이핑하지 않게) — 필요 시 대장에서 구분
        nm = _dt.now().strftime("예시-%Y%m%d-%H%M")
        try:
            export_dataset(db, sources, nm)
        except ValueError as exc:
            return RedirectResponse(f"/dev/data?err={exc}", status_code=303)
        return RedirectResponse(f"/dev/data?ok=예시 묶음 「{nm}」을 만들었습니다", status_code=303)

    # ---- Label Studio 자동 연동 (API) — 페이지 버튼 한 번으로 왕복 ----
    # 주소·토큰은 서버 구축 때 scripts/68_labelstudio_token.sh 가 설정에 넣는다.
    # 사람이 토큰을 붙여넣거나 파일을 주고받는 경로는 두지 않는다.
    _LS_PROJECT = "ZZAIMY 검수"
    _LS_STATUS_TTL = 30.0   # 연결 상태 캐시(초) — 화면마다 Label Studio 를 두드리지 않게
    _ls_status_cache: dict = {"at": 0.0, "value": None}

    def _ls_client():
        from zzaimy.dataset.ls_client import LabelStudioClient

        url = db.get_setting("labelstudio_url")
        token = db.get_setting("labelstudio_token")
        return LabelStudioClient(url, token)

    def _ls_status() -> dict:
        """연결 상태 — 2초 제한으로 한 번 묻고 30초 캐시. 설정이 없으면 묻지 않는다."""
        from zzaimy.dataset.ls_client import LabelStudioError

        now = _time.monotonic()
        cached = _ls_status_cache["value"]
        if cached is not None and now - _ls_status_cache["at"] < _LS_STATUS_TTL:
            return cached
        try:
            value = _ls_client().status(_LS_PROJECT)
        except LabelStudioError:   # kind=config — 주소·토큰이 아직 없다 (캐시하지 않음)
            return {"ok": False, "error": "설정 없음", "project": _LS_PROJECT,
                    "project_id": None, "total": 0, "done": 0, "pending": 0}
        _ls_status_cache.update(at=now, value=value)
        return value

    def _ls_status_reset() -> None:
        _ls_status_cache["value"] = None

    @app.post("/dev/data/ls-pull")
    def dev_data_ls_pull(name: str = Form("검수완료")):
        """Label Studio 검수 결과를 API로 가져와 학습 데이터로 확정."""
        from pathlib import Path as _P

        from zzaimy.dataset.build import SFT_DIR
        from zzaimy.dataset.ls_client import LabelStudioError

        try:
            cli = _ls_client()
            pid = cli.ensure_project(_LS_PROJECT)
            pairs = cli.pull_reviewed(pid)
        except LabelStudioError as e:
            return RedirectResponse(f"/dev/data?err=Label Studio: {e}", status_code=303)
        if not pairs:
            return RedirectResponse("/dev/data?err=검수 통과분이 없습니다", status_code=303)
        from datetime import datetime, timezone

        SFT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
        safe = "".join(c for c in name if c.isalnum() or c in "-_") or "reviewed"
        path = _P(SFT_DIR) / f"{safe}-{stamp}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for p in pairs:
                f.write(_aj.dumps(p, ensure_ascii=False) + "\n")
        db.add_dataset(name=name, sources="labelstudio", path=str(path),
                       n_pairs=len(pairs))
        return RedirectResponse(f"/dev/data?ls_pulled={len(pairs)}", status_code=303)

    @app.get("/dev/data/{dataset_id}.jsonl")
    def dev_data_download(dataset_id: int):
        from fastapi.responses import FileResponse

        ds = db.get_dataset(dataset_id)
        if ds is None or not Path(ds["path"]).exists():
            raise HTTPException(404, "데이터셋 파일이 없습니다")
        return FileResponse(
            ds["path"], media_type="application/jsonl",
            filename=Path(ds["path"]).name,
        )

    # ---- 추출 품질 신고 루프 (품질 체계 5계층, docs/quality-system.md) ----

    _QUALITY_KINDS = set(QUALITY_KIND_LABELS)

    @app.post("/doc/{doc_id}/quality-report")
    def doc_quality_report(
        request: Request, doc_id: int,
        kind: str = Form(...), note: str = Form(""),
    ):
        if db.get_document(doc_id) is None:
            raise HTTPException(404, "없는 문서입니다")
        if kind not in _QUALITY_KINDS:
            raise HTTPException(400, "알 수 없는 신고 유형입니다")
        db.add_quality_report(
            doc_id, kind, note.strip(), reporter=request.state.user
        )
        return RedirectResponse(f"/doc/{doc_id}?reported=1", status_code=303)

    @app.post("/dev/quality/{report_id}/done")
    def dev_quality_done(
        request: Request, report_id: int, fix_note: str = Form("")
    ):
        if not db.resolve_quality_report(
            report_id, resolved_by=request.state.user, fix_note=fix_note.strip()
        ):
            raise HTTPException(404, "열려 있는 신고가 아닙니다")
        return RedirectResponse("/dev", status_code=303)

    # ---- 지식 그래프 1단계 — 구조 그래프 (ADR-0009) ----

    @app.get("/graph", response_class=HTMLResponse)
    def graph_page(request: Request, focus: str = ""):
        return templates.TemplateResponse(
            request, "graph.html", ctx(request, {"focus": focus})
        )

    @app.get("/graph.json")
    def graph_json(dept: str = ""):
        from zzaimy.graph.build import build_graph

        return JSONResponse(build_graph(db, dept=dept or None))

    # ---- 외부 참조 이그레스 게이트웨이 (ADR-0008) — 감사·승인·모니터링 ----

    def _egress_ctx(request: Request, extra: dict | None = None):
        from zzaimy.app import egress as _egress

        enabled, reason = _egress.external_status()
        rows = db.list_egress_requests(limit=50)
        queued = db.list_egress_requests(status="queued", limit=50)
        for r in rows + queued:
            try:
                r["removed_list"] = _aj.loads(r.get("removed") or "[]")
            except ValueError:
                r["removed_list"] = []
        base = {
            "egress_stats": db.egress_stats(),
            "rows": rows,
            "queued": queued,
            "external_enabled": enabled,
            "external_reason": reason,
            "egress_verdict_labels": EGRESS_VERDICT_LABELS,
            "egress_source_labels": EGRESS_SOURCE_LABELS,
            "egress_removed_labels": EGRESS_REMOVED_LABELS,
            "tokenized": None,
        }
        base.update(extra or {})
        return templates.TemplateResponse(request, "dev_egress.html", ctx(request, base))

    @app.get("/dev/egress", response_class=HTMLResponse)
    def dev_egress(request: Request):
        return _egress_ctx(request)

    @app.post("/dev/egress/tokenized", response_class=HTMLResponse)
    def dev_egress_tokenized(request: Request, text: str = Form(...)):
        from zzaimy.app import egress as _egress

        t = text.strip()
        result = (_egress.process_external_tokenized(t) if t
                  else {"ok": False, "error": "내용이 비어 있습니다", "tokens": 0})
        result["input"] = t
        return _egress_ctx(request, {"tokenized": result})

    @app.post("/dev/egress/submit")
    def dev_egress_submit(request: Request, query: str = Form(...)):
        from zzaimy.app import egress as _egress

        q = query.strip()
        if q:
            _egress.submit(db, q, requester=request.state.user, source="manual")
        return RedirectResponse("/dev/egress", status_code=303)

    @app.post("/dev/egress/{req_id}/decide")
    def dev_egress_decide(request: Request, req_id: int, action: str = Form(...)):
        from zzaimy.app import egress as _egress

        try:
            _egress.decide(
                db, req_id,
                approve=(action == "approve"),
                decided_by=request.state.user,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return RedirectResponse("/dev/egress", status_code=303)

    @app.post("/dev/egress/{req_id}/retry")
    def dev_egress_retry(req_id: int):
        from zzaimy.app import egress as _egress

        try:
            _egress.retry_send(db, req_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return RedirectResponse("/dev/egress", status_code=303)

    _WEEKLY_PROMPT = """너는 대학 캡스톤 프로젝트(행정문서 AI 플랫폼)의 주간 업무 보고를 쓴다. 독자는 지도교수이고
개발자가 아니다. 아래 원자료를 바탕으로 쓰되 원자료를 옮겨 적지 말고, 무엇을 했고 무엇이 되었는지만 짧게 쓴다.
피드백(미팅 결정·지도교수 의견)이 있으면 그 내용을 먼저 반영하고 그에 대해 한 일을 잇는다.

{sections}

쓰는 법:
- 【이번 주 한 일】은 아래 [기준 틀]의 큰 축(플랫폼·모델(LLM)·데이터 같은 것) 단위로 3~5개. 큰 항목은 "무엇 — 결과" 한 줄
  (예: "서빙 장비 구성 완료 — 젯슨 토르 2대에 27B 모델, DGX는 학습 전용"). 항목은 굵직한 구축 단위로만 잡는다 —
  장비 구성, 플랫폼 구축, 문서 반입 체계, 검색 구축, 모델 준비처럼 "무엇을 구축·준비·구성했다"로 끝나는 제목 한 줄.
  항목은 5~6개, 세부 줄은 항목당 2~3줄. 개조식으로 쓴다 — 번호는 "1)" 꼴, 세부는 "- " 로 시작, 문장은 "~했습니다" 대신
  명사형이나 "~함/~완료/~확인" 으로 끝맺고 한 줄 40자 안팎으로 짧게 끊는다(좁은 화면에서도 한눈에 들어오게).
  예)
  1) 서빙 장비 구성 완료 — 젯슨 토르 2대 + DGX 학습 전용
  - 27B 양자화 판 적용, 원본 대비 3~4배 속도 확인
  - 검토·판독은 토르 03, 대화·초안은 토르 02로 분담
  짧게 줄이려고 사실을 빼지 말고, 늘리려고 없는 말을 넣지도 않는다.
  항목 순서는 장비 구성(토르 2대·DGX) → 표·이미지 OCR 고도화 작업(판독 테스트와 측정 결과 — 무엇으로 시험했고
  속도·정확도가 어땠는지) → 플랫폼 구축(화면·자동화·보고 기능) → 문서 반입·검색 구축(반입 체계, 검색 모델 학습본,
  정확도) → 모델 준비(모델 통일·베이스라인·학습 경로). 그 주에 해당 없는 항목은 뺀다.
  항목 제목은 짧은 명사구에 결과를 붙인다 — "토르·DGX 장비 구성 완료", "표·이미지 OCR 판독 테스트 및 측정"처럼.
  과정 서술(원인을 파악해 무효 처리했다, 보고 방식을 개선했다, 규칙을 고쳤다 같은 말)은
  쓰지 않는다 — 읽는 사람은 결과만 안다. 수치는 항목당 하나까지, 그것도 뜻이 바로 보이는 것만
  (예: "검색 정확도 75%" 는 되고 "R@1 0.750"·"MRR" 은 안 된다). 원자료에 없는 수치·기간·효과("업무 시간 50% 단축" 따위)는
  절대 만들지 않는다 — 수치가 없으면 수치 없이 쓴다.
- 【다음 주 계획】은 3~4개, "1)" 번호로 각 한 줄, 명사구 목표로("실물 문서 반입 및 파일럿", "파인튜닝 착수", "규정 RAG 구축"처럼).
  [다음에 할 일] 원자료에서만 고른다.
- 【이슈/건의사항】은 실제 막힌 것·요청할 것만. 없으면 "없음".
- 피드백에 질문이 있으면(예: "왜 이 모델인가", "표·이미지는 어떻게 처리하나") 【이번 주 한 일】 맨 앞에
  "피드백 답변" 항목을 두고 질문마다 "- " 한두 줄로 원자료에 근거해 답한다 — 모델 선정 이유는 [기준 틀]의 근거(멀티모달·학습 경로·
  벤치마크 수치와 그 한계)를 그대로 쓴다. 라이선스·명명 같은 말은 쓰지 않는다.
  표·이미지 같은 처리 방식 질문에는 "표는 구조를 유지한 채 읽고, 스캔·이미지는 27B 모델이 판독하며, 품질은 원본 대조와
  자가 점검으로 확인한다" 수준으로 답한다 — 내부 결함이나 고친 과정(글자층·잡음·제한 시간·비율 같은 말)은 쓰지 않는다. 질문을 이슈나 건의사항으로 되돌리지 않는다.
- 쓰지 않는 것: 파일·스크립트·함수 이름, 결정 번호(ADR 등), 영문 약어·개발 용어(폴백·타임아웃·리랭커·임베딩 같은 말).
  꼭 필요하면 우리말로 풀어 쓴다(예: "검색 결과를 다시 줄 세우는 모델").
- 숫자는 원자료에 있는 것만. 과장·수식어·볼드·이모지 금지. 【】 제목은 양식 그대로.

[기준 틀 — 기획서 개발 내용]
{frame}

[이번 주 피드백·미팅 결정]
{feedback}

[지금 하는 일·이번 주 한 일 — 현재 상태 문서]
{now}

[다음에 할 일 — 현재 상태 문서]
{plans}

[참고: 이번 주 작업 기록 제목만]
{changelog}"""

    _WEEKLY_SECTIONS_DEFAULT = """마크다운으로, 아래 섹션 구성 그대로:
## 요약
이번 주 작업을 3~4문장으로. 무엇이 가장 큰 진전인지부터.
## 주요 성과
4~7개 항목. 각 항목은 "무엇을 어떻게 해서 무엇이 개선되었다" 형태의 1~2문장.
전문용어는 괄호로 짧게 풀어 쓴다. 예: "스캔 문서에 투명 글자층(이미지 위에 보이지 않는
글자를 입혀 복사·검색이 되게 하는 방식)을 입혀, 스캔본에서도 본문을 긁어 쓸 수 있게 했다."
## 문제와 대응
이번 주 발생한 문제 1~3건과 어떻게 해결했는지.
## 다음 주 계획
3~5개 항목."""

    _WEEKLY_TEMPLATE = _DOCS_DIR / "weekly" / "양식.md"


    def _weekly_sections(template: Path | None = None) -> tuple[str, bool]:
        """보고서 항목 구성 — 사용자가 준 양식(docs/weekly/양식.md)이 있으면 그 구성을 그대로, 없으면 기본."""
        f = template or _WEEKLY_TEMPLATE
        try:
            text = f.read_text(encoding="utf-8").strip() if f.exists() else ""
        except OSError:
            text = ""
        if text:
            return ("마크다운으로, 아래 양식의 항목 구성·순서·제목을 그대로 따른다. 양식의 안내문은 지침이지 본문이 아니다:\n"
                    + text), True
        return _WEEKLY_SECTIONS_DEFAULT, False

    def _weekly_feedback_path(monday: str) -> Path:
        return _weekly_dir() / f"{monday}.feedback.md"

    def _weekly_feedback(monday: str) -> str:
        f = _weekly_feedback_path(monday)
        try:
            return f.read_text(encoding="utf-8").strip() if f.exists() else ""
        except OSError:
            return ""

    def _weekly_dir() -> Path:
        return storage.report_dir(Path(db_path).parent, "주간")

    def _weekly_list() -> list[dict]:
        """지금까지의 주간 보고 — 생성본(data/platform/weekly)과 수기본(docs/weekly)을 한 목록으로, 최근 것부터."""
        out: list[dict] = []
        seen: set[str] = set()
        for src, folder in (("생성", _weekly_dir()), ("수기", _DOCS_DIR / "weekly")):
            if not folder.exists():
                continue
            for f in folder.glob("*.md"):
                if f.name == "양식.md" or f.stem in seen:
                    continue
                m = re.search(r"\d{4}-\d{2}-\d{2}", f.stem)
                title = f.stem
                try:
                    first = next((ln[2:].strip() for ln in f.read_text(encoding="utf-8").splitlines()
                                  if ln.startswith("# ")), "")
                except OSError:
                    first = ""
                seen.add(f.stem)
                out.append({"href": f"/dev/weekly/{f.stem}", "title": first or f"주간 보고 {title}",
                            "num": "", "status": src, "date": m.group(0) if m else "",
                            "stem": f.stem, "kind": src})
        out.sort(key=lambda d: d["date"], reverse=True)
        return out

    def _compose_weekly(monday: str, today: str, fresh: bool = False) -> str:
        """주간 보고 본문 — LLM이 원자료를 읽고 문장으로 쓴다. 결과는 캐시."""
        cache_dir = Path(db_path).parent / "weekly"
        cache_dir.mkdir(exist_ok=True)
        cache = cache_dir / f"{monday}.md"
        if cache.exists() and not fresh:
            return cache.read_text(encoding="utf-8")

        # 원자료는 사람이 읽는 현재 상태 문서가 먼저다 — 커밋 기록은 제목만, 이번 주 것만 참고로 준다.
        dn = _dev_read("dev-now.md")

        def _section(text: str, head: str) -> str:
            if head not in text:
                return ""
            return text.split(head, 1)[1].split("\n## ", 1)[0].strip()

        now_text = _section(dn, "## 지금 하는 일")
        recent = _section(dn, "## 최근 작업")
        # 이번 주 날짜(월요일 이후)의 '### YYYY-MM-DD' 묶음만
        week_parts = []
        for chunk in recent.split("\n### ")[1:] if recent else []:
            day = chunk[:10]
            if day >= monday:
                week_parts.append("### " + chunk.strip())
        if week_parts:
            now_text = (now_text + "\n\n이번 주 한 일:\n" + "\n".join(week_parts)).strip()
        plans = _section(dn, "## 다음에 할 일")
        # 기준 틀 — 기획서의 개발 내용(플랫폼·모델·데이터 축). 보고는 이 축의 높이에서 쓴다.
        plan_doc = _dev_read("paper/프로젝트-기획서.md")
        frame = (_section(plan_doc, "## 개발 내용")[:2500]
                 + "\n\n[추진 일정]\n" + _section(plan_doc, "## 추진 일정")[:1200])   # 일정 질문에도 답할 수 있게
        week_lines = [f"- {it['day'][5:]} {it['subject']}" for it in _git_history() if it["day"] >= monday]
        if not week_lines:                   # 깃이 없으면 저장소 사본
            week_lines = [ln for ln in _dev_read("dev-changelog.md").splitlines()
                          if ln.startswith("- ") and ln[2:7] >= f"{monday[5:7]}-{monday[8:10]}"]
        changelog = "\n".join(week_lines[:60])[:3000]
        body: str | None = None
        try:
            from zzaimy.generate.client import VllmClient

            client = VllmClient(role="answer")
            resp = client.client.chat.completions.create(
                model=client.model,
                messages=[{"role": "user", "content": _WEEKLY_PROMPT.format(
                    sections=_weekly_sections()[0],
                    frame=frame or "(없음)",
                    feedback=_weekly_feedback(monday) or "(없음)",
                    now=now_text[:5000] or "(없음)",
                    plans=plans[:2000] or "(없음)",
                    changelog=changelog or "(없음)",
                )}],
                temperature=0.3, max_tokens=1600,
                extra_body=getattr(client, "_extra", {}),
                timeout=float(os.environ.get("ZZAIMY_REVIEW_TIMEOUT", "600")),   # 1,600 토큰은 3분을 넘긴다
            )
            body = (resp.choices[0].message.content or "").strip()
        except Exception:
            body = None
        if not body:
            # 모델이 없으면 원자료(현재 상태 문서)를 그대로 보인다 — 커밋 목록을 쏟지 않는다(2026-09-22)
            body = ("(모델이 응답하지 않아 자동 작성을 못 했습니다. 아래는 현재 상태 문서의 내용입니다 — "
                    "모델 연결 뒤 '다시 만들기'를 누르십시오.)\n\n" + (now_text or "(기록 없음)"))
            return body                      # 실패본은 캐시하지 않는다

        # 정량 지표는 결정론으로 그대로 붙인다.
        # 검색 품질 표는 기계가 마지막으로 쓴 사본을 먼저 본다 — 평가는 운영에서 돌고
        # 저장소 사본은 --report 로만 갱신되므로, 산출물 폴더가 최신이다.
        base_tbl = "\n".join(
            ln for ln in _eval_md("retrieval-baseline-mini.md").splitlines()
            if ln.startswith("|")
        )
        embed_tbl = "\n".join(
            ln for ln in _dev_read("embed-v0-report.md").splitlines()
            if ln.startswith("|")
        )
        scale = " · ".join(f"{t['label']} {t['value']}" for t in _data_overview()["tiles"])
        # 양식 밖의 것은 붙이지 않는다(2026-09-22 사용자: "너무 어렵게 적혀 있다"). 기간은 제목에 있다.
        full = _tidy_weekly_md(body) + "\n"
        _ = (base_tbl, embed_tbl, scale)      # 지표는 화면(측정 기록)에서 본다
        cache.write_text(full, encoding="utf-8")
        return full

    # 자동 작성 — 논문 자료 화면을 열면 이번 주 보고서가 없을 때 뒤에서 만든다(2026-09-22 사용자: "생성은 자동인가?").
    # 피드백 저장·다시 만들기도 뒤에서 돌고, 화면은 끝나면 스스로 새로 고친다.
    _weekly_state: dict = {"monday": "", "running": False, "started": "", "error": ""}

    def _weekly_can_auto() -> bool:
        """답변 용도로 부를 모델 연결이 있는가 — 없으면 자동 작성을 시도하지 않고 화면에 그 사실을 적는다."""
        from zzaimy.generate import llm_connections as lc

        try:
            return bool(lc.role_conn("answer") or lc.active())
        except Exception:
            return False

    def _weekly_kick(monday: str, today: str, fresh: bool = False) -> bool:
        """이번 주 보고서를 뒤에서 만든다. 이미 만드는 중이면 False."""
        import threading
        from datetime import datetime as _dt

        if _weekly_state["running"]:
            return False
        _weekly_state.update(monday=monday, running=True, started=_dt.now().strftime("%H:%M"), error="")

        def run():
            try:
                body = _compose_weekly(monday, today, fresh=fresh)
                if body.startswith("(모델이 응답하지 않아"):
                    _weekly_state["error"] = "모델이 응답하지 않아 작성하지 못했습니다"
            except Exception:
                _weekly_state["error"] = "작성 중 오류가 났습니다"
            finally:
                _weekly_state["running"] = False

        threading.Thread(target=run, daemon=True, name="weekly-report").start()
        return True

    def _weekly_card(monday: str, today: str) -> dict:
        """주간 보고서 칸의 상태 — 있으면 미리보기, 없으면 자동 작성을 시작한다."""
        from datetime import datetime as _dt

        cache = _weekly_dir() / f"{monday}.md"
        auto = _weekly_can_auto()
        if (not cache.exists() and auto and not _weekly_state["running"]
                and os.environ.get("ZZAIMY_WEEKLY_AUTO", "1") != "0"):
            _weekly_kick(monday, today)
        info = {"exists": cache.exists(), "running": _weekly_state["running"],
                "started": _weekly_state["started"], "error": _weekly_state["error"],
                "auto": auto, "preview": "", "made_at": "", "stem": monday}
        if cache.exists():
            text = cache.read_text(encoding="utf-8")
            info["preview"] = text[:1800] + ("\n\n…" if len(text) > 1800 else "")
            info["made_at"] = _dt.fromtimestamp(cache.stat().st_mtime).strftime("%m-%d %H:%M")
        return info

    @app.get("/dev/weekly/status")
    def dev_weekly_status():
        from datetime import date as _date, timedelta as _td

        today = _date.today()
        monday = (today - _td(days=today.weekday())).isoformat()
        return {"running": _weekly_state["running"], "error": _weekly_state["error"],
                "exists": (_weekly_dir() / f"{monday}.md").exists()}

    @app.get("/dev/weekly/rebuild")
    def dev_weekly_rebuild():
        """다시 만들기 — 뒤에서 새로 쓰고 화면으로 돌아간다."""
        from datetime import date as _date, timedelta as _td
        from fastapi.responses import RedirectResponse

        today = _date.today()
        monday = (today - _td(days=today.weekday())).isoformat()
        _weekly_kick(monday, today.isoformat(), fresh=True)
        return RedirectResponse("/dev/docs#weekly", status_code=303)

    @app.get("/dev/weekly.{fmt}")
    def dev_weekly_report(fmt: str, fresh: int = 0):
        """캡스톤 주간 보고서 — LLM 작성 성과 서술 + 결정론 지표 표."""
        from datetime import date, timedelta
        from urllib.parse import quote as _q

        from fastapi.responses import Response

        today = date.today()
        monday = today - timedelta(days=today.weekday())
        body = _compose_weekly(
            monday.isoformat(), today.isoformat(), fresh=bool(fresh)
        )
        title = f"주간업무보고 ({monday.isoformat()} ~ {today.isoformat()})"
        if fmt == "md":
            payload: bytes | None = f"# {title}\n\n{body}\n".encode()
            media = "text/markdown; charset=utf-8"
        elif fmt == "docx":
            from zzaimy.app.draft_export import build_draft_docx

            payload = build_draft_docx(title, body)
            media = ("application/vnd.openxmlformats-officedocument"
                     ".wordprocessingml.document")
        elif fmt == "hwpx":
            from zzaimy.app.draft_export import build_draft_hwpx

            payload = build_draft_hwpx(title, body)
            media = "application/hwp+zip"
        else:
            raise HTTPException(404)
        if payload is None:
            raise HTTPException(500, "보고서 생성에 실패했습니다")
        return Response(payload, media_type=media, headers={
            "Content-Disposition": "attachment; filename*=UTF-8\'\'"
            + _q(f"주간보고_{monday.isoformat()}.{fmt}"),
        })

    def _weekly_payload(title: str, body: str, fmt: str):
        from fastapi.responses import Response
        from urllib.parse import quote as _q

        if fmt == "md":
            payload: bytes | None = f"# {title}\n\n{body}\n".encode()
            media = "text/markdown; charset=utf-8"
        elif fmt == "docx":
            from zzaimy.app.draft_export import build_draft_docx

            payload = build_draft_docx(title, body)
            media = ("application/vnd.openxmlformats-officedocument"
                     ".wordprocessingml.document")
        elif fmt == "hwpx":
            from zzaimy.app.draft_export import build_draft_hwpx

            payload = build_draft_hwpx(title, body)
            media = "application/hwp+zip"
        else:
            raise HTTPException(404)
        if payload is None:
            raise HTTPException(500, "보고서 생성에 실패했습니다")
        return Response(payload, media_type=media, headers={
            "Content-Disposition": "attachment; filename*=UTF-8\'\'" + _q(f"{title}.{fmt}"),
        })

    def _weekly_file(stem: str) -> Path:
        if "/" in stem or ".." in stem or not stem:
            raise HTTPException(404)
        for folder in (_weekly_dir(), _DOCS_DIR / "weekly"):
            f = folder / f"{stem}.md"
            if f.exists() and f.name != "양식.md":
                return f
        raise HTTPException(404)

    @app.post("/dev/weekly/feedback")
    async def dev_weekly_feedback(request: Request):
        """이번 주 피드백(미팅 결정·지도교수 의견)을 저장한다 — 보고서는 이 내용을 먼저 반영한다."""
        from datetime import date as _date, timedelta as _td
        from fastapi.responses import RedirectResponse

        form = await request.form()
        today = _date.today()
        monday = (today - _td(days=today.weekday())).isoformat()
        text = str(form.get("feedback") or "").strip()
        f = _weekly_feedback_path(monday)
        f.write_text(text, encoding="utf-8")
        cache = _weekly_dir() / f"{monday}.md"
        if cache.exists():
            cache.unlink()                 # 피드백이 바뀌면 보고서를 다시 쓴다
        if _weekly_can_auto() and os.environ.get("ZZAIMY_WEEKLY_AUTO", "1") != "0":
            _weekly_kick(monday, today.isoformat(), fresh=True)
        return RedirectResponse("/dev/docs#weekly", status_code=303)

    @app.get("/dev/weekly/{stem}.{fmt}")
    def dev_weekly_past(stem: str, fmt: str):
        """지난 주간 보고를 파일로 — 생성본이든 수기본이든 같은 형식으로."""
        f = _weekly_file(stem)
        text = f.read_text(encoding="utf-8")
        lines = text.splitlines()
        title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), f"주간 보고 {stem}")
        body = "\n".join(ln for ln in lines if not ln.startswith("# ")).strip()
        return _weekly_payload(title, body, fmt)

    @app.get("/dev/weekly/{stem}", response_class=HTMLResponse)
    def dev_weekly_view(request: Request, stem: str):
        """지난 주간 보고 열람 — 논문 자료 열람 화면과 같은 틀."""
        f = _weekly_file(stem)
        return templates.TemplateResponse(request, "dev_paper.html", ctx(request, {
            "fname": f.name, "doc": _dev_doc_view(f.read_text(encoding="utf-8"), f.name),
            "papers": [], "show_export": False, "page_title": "주간 보고서",
            "weekly_stem": stem,
        }))

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return templates.TemplateResponse(
            request, "settings.html",
            ctx(request, {"s": db.all_settings(), "active_tab": "all",
                          "departments": db.department_counts()}),
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
            d for d in db.list_documents(
                proj_sector, project_id=project_id,
                owner=getattr(request.state, "user", "zzaimy"),
            )
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
            ctx(request, {
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
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다")
        if not name.strip():
            raise HTTPException(400, "프로젝트 이름을 입력하세요")
        db.rename_project(project_id, name.strip())
        if due_date is not None:
            db.update_project_meta(project_id, due_date=due_date.strip())
        return RedirectResponse(f"/project/{project_id}", status_code=303)

    @app.post("/projects/{project_id}/delete")
    def delete_project(project_id: int):
        proj = db.get_project(project_id)
        if proj is None:
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다")
        db.delete_project(project_id)
        return RedirectResponse(f"/?type={proj['sector']}", status_code=303)

    @app.post("/upload")
    def upload(
        request: Request,
        background: BackgroundTasks,
        file: UploadFile = File(...),
        doc_type: str = Form("auto"),
        related_criteria_id: int | None = Form(None),
        project_id: int | None = Form(None),
        dept: str = Form(""),
        access_level: str = Form(""),
    ):
        name = file.filename or "이름없음"
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"허용되지 않는 파일 형식입니다: {suffix}")
        if doc_type not in INBOX_TYPES:
            raise HTTPException(400, f"알 수 없는 문서 유형입니다: {doc_type}")
        if project_id and db.get_project(project_id) is None:
            project_id = None  # 삭제됐거나 잘못된 프로젝트 — 연결 없이 접수한다
        stored = inbox_dir / f"{uuid.uuid4().hex}{suffix}"
        with stored.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        # 부서·열람 등급은 반입 때 정한다(access_policy) — 명시값 → 올린 사람의 부서 → 프로젝트 부서 → 공통
        from zzaimy.app.access_policy import LEVELS, classify

        proj = db.get_project(project_id) if project_id else None
        doc_dept, doc_level = classify(
            doc_type, owner=getattr(request.state, "user", "zzaimy"), dept=dept,
            access_level=access_level if access_level in LEVELS else None,
            uploader_dept=getattr(request.state, "dept", ""), project_dept=(proj or {}).get("dept"),
        )
        doc_id = db.add_document(
            filename=name, stored_path=str(stored), doc_type=doc_type,
            related_criteria_id=related_criteria_id, project_id=project_id,
            owner=getattr(request.state, "user", "zzaimy"), dept=doc_dept, access_level=doc_level,
        )
        stored = storage.adopt_original(db, doc_id, stored)
        background.add_task(processor.process, db, doc_id, stored)
        # 접수한 자리로 돌아간다 — 프로젝트에서 올렸으면 그 프로젝트로
        if project_id:
            dest = f"/project/{project_id}"
        elif doc_type in INBOX_TYPES and doc_type != "auto":
            dest = f"/?type={doc_type}"
        else:
            dest = "/"
        return RedirectResponse(dest, status_code=303)

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
            raise HTTPException(400, "초안 작성은 국고사업 문서에서만 지원합니다")
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
            raise HTTPException(404, "초안이 없습니다")
        stem = Path(doc["filename"] or "draft").stem
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

    def _keep_generated(doc: dict, payload, kind: str, ext: str) -> None:
        """내보낸 파일의 사본을 생성 폴더에 두고 장부에 적는다(ADR-0030). 실패해도 내려받기는 그대로."""
        try:
            data = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
            storage.save_generated(db, data, ref=doc.get("receipt_no") or f"문서-{doc['id']}", kind=kind, ext=ext,
                                   doc_id=int(doc["id"]))
        except Exception:
            import logging

            logging.getLogger(__name__).warning("생성 파일 보관 실패 doc=%s kind=%s", doc.get("id"), kind)

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
        referencing = (
            db.referencing_documents(doc_id)
            if doc["doc_type"] == "regulation" else []
        )
        return templates.TemplateResponse(
            request,
            "doc.html",
            ctx(request, {
                "doc": doc, "reviews": db.get_reviews(doc_id), "related": related,
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
                "suggested_criteria": suggested,
                "referencing": referencing,
                "identity": db.get_doc_identity(doc_id),
                "same_program": db.docs_sharing_program(doc_id),
                "n_text_chunks": sum(1 for c in chunks if c["kind"] == "text"),
                "n_table_chunks": sum(1 for c in chunks if c["kind"] == "table"),
                "n_figtext_chunks": sum(1 for c in chunks if c["kind"] == "image_text"),
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
            raise HTTPException(404, "추출된 그림이 없습니다")
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

    @app.get("/doc/{doc_id}/boxes", response_class=HTMLResponse)
    def doc_boxes_view(doc_id: int):
        """인식 영역 뷰어 — 전 페이지를 신뢰도 색 박스와 함께 세로로.

        각 페이지 이미지는 /doc/{id}/boxes/{n}.png 라우트를 재사용한다.
        """
        import json as _bj

        doc = db.get_document(doc_id)
        lines_file = _paths.lines_dir(Path(db_path).parent) / f"{doc_id}.json"
        if doc is None or not lines_file.exists():
            raise HTTPException(404)
        payload = _bj.loads(lines_file.read_text())
        pages = sorted(
            (int(p) for p in (payload.get("page_sizes") or {})), key=int
        )
        from markupsafe import escape as html_escape

        title = html_escape(doc["filename"])
        imgs = "".join(
            f'<figure><figcaption>{p}쪽</figcaption>'
            f'<img src="/doc/{doc_id}/boxes/{p}.png" loading="lazy" alt="{p}쪽"></figure>'
            for p in pages
        )
        return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>인식 영역 · {title}</title><style>
body{{margin:0;background:#f4f5f7;font-family:system-ui,'Apple SD Gothic Neo',sans-serif;color:#1a1a1a}}
header{{position:sticky;top:0;background:#fff;border-bottom:1px solid #e3e5e8;padding:12px 18px;
 display:flex;align-items:center;gap:16px;flex-wrap:wrap}}
header h1{{font-size:15px;margin:0;font-weight:600}}
.legend{{display:flex;gap:14px;font-size:12.5px;color:#555}}
.legend span{{display:inline-flex;align-items:center;gap:5px}}
.dot{{width:11px;height:11px;border-radius:2px;border:2px solid}}
.g{{border-color:#22a04c}}.o{{border-color:#e68c1e}}
main{{max-width:1000px;margin:0 auto;padding:18px}}
figure{{margin:0 0 22px;background:#fff;border:1px solid #e3e5e8;border-radius:10px;overflow:hidden}}
figcaption{{padding:7px 12px;font-size:12px;color:#666;border-bottom:1px solid #eee}}
figure img{{width:100%;display:block}}
</style></head><body>
<header><h1>인식 영역 — {title}</h1>
<div class="legend"><span><i class="dot g"></i>확신(확정)</span>
<span><i class="dot o"></i>낮은 확신(재판독 대상)</span></div></header>
<main>{imgs or '<p>표시할 페이지가 없습니다.</p>'}</main></body></html>"""

    @app.get("/doc/{doc_id}/boxes/{page_no}.png")
    def doc_boxes_image(doc_id: int, page_no: int):
        """인식 박스 시각화 — 페이지 위에 OCR이 읽은 영역을 신뢰도 색으로.

        초록 = 확신, 주황 = 낮은 확신(재판독 대상). 검수용.
        """
        import io as _io
        import json as _bj

        from fastapi.responses import Response
        from PIL import ImageDraw

        doc = db.get_document(doc_id)
        lines_file = _paths.lines_dir(Path(db_path).parent) / f"{doc_id}.json"
        if doc is None or not lines_file.exists():
            raise HTTPException(404)
        payload = _bj.loads(lines_file.read_text())
        sizes = payload.get("page_sizes") or {}
        if str(page_no) not in sizes:
            raise HTTPException(404)
        src = Path(doc["stored_path"])
        if src.suffix.lower() == ".pdf":
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(src))
            try:
                page = pdf[page_no - 1]
                mw = float(sizes[str(page_no)][0])
                scale = 1.6 * mw / max(page.get_width(), 1.0)
                img = page.render(scale=scale).to_pil()
                f = scale * page.get_width() / mw
            finally:
                pdf.close()
        else:
            from PIL import Image as _Img

            scan = next(
                (a for a in db.list_doc_assets(doc_id)
                 if a["kind"] == "scan" and Path(a["path"]).exists()), None,
            )
            img = _Img.open(scan["path"] if scan else src).convert("RGB")
            f = img.width / float(sizes[str(page_no)][0])
        draw = ImageDraw.Draw(img)
        for ln in payload.get("lines") or []:
            if int(ln.get("page_no") or 0) != page_no:
                continue
            try:
                x0, y0, x1, y1 = (float(v) * f for v in ln["bbox"].split(","))
            except (KeyError, ValueError):
                continue
            score = float(ln.get("score") or 1.0)
            color = (34, 160, 76) if score >= 0.85 else (230, 140, 30)
            draw.rectangle([x0, y0, x1, y1], outline=color, width=2)
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png")

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
            raise HTTPException(404, "표 구조를 읽을 수 없습니다") from None
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
        stem = Path(doc["filename"] or src.name).stem
        fname = quote(f"{stem}_OCR.pdf")
        _keep_generated(doc, payload, "OCR", "pdf")
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
        fname = quote(f"{Path(doc['filename']).stem}_복원.docx")
        _keep_generated(doc, payload, "복원", "docx")
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
            raise HTTPException(404, "추출 조각이 없습니다")
        md = export_markdown(doc["filename"], chunks)
        fname = quote(f"{Path(doc['filename']).stem}_추출결과.md")
        _keep_generated(doc, md, "추출결과", "md")
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
            raise HTTPException(400, f"알 수 없는 판정입니다: {decision}")
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
        storage.remove_intake_dir(db, doc)          # 원본·추출 그림이 든 문서 폴더째 지운다
        dest = "/criteria" if doc["doc_type"] == "regulation" else ("/ocr" if doc["doc_type"] == "ocr" else "/inbox")
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
