"""문서 접수·검토 플랫폼 FastAPI 앱.

보안: 기본은 127.0.0.1 에만 바인딩하고 원격 접속은 SSH 터널로 한다(docs/risks.md §8).
실행:
    .venv/bin/python -m zzaimy.app.main
"""

from __future__ import annotations

import os
from urllib.parse import quote as _q, urlencode
import logging
import re
import json
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

from zzaimy.app import paths as _paths
from zzaimy.app import grades as _gr
from zzaimy.app import storage
from zzaimy.app import korean_text as _ko
from zzaimy.app.chat_history import display_title as _chat_title
from zzaimy.app.db import Database
from zzaimy.generate import model_info as _model_info

ALLOWED_EXTENSIONS = {
    ".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".txt", ".md",
}

_TEMPLATES_DIR = Path(__file__).parent / "templates"

from zzaimy.app.vocab import STATUS_LABELS  # noqa: E402,F401  화면 용어는 한 곳(app/vocab)

# 문서함(검토 대상) 유형 — 기준 문서(regulation)는 별도 공간에서 다룬다
INBOX_TYPES = {
    "auto": "일반 행정",
    "grant": "국고사업",
    "recruit": "채용",
    "admission": "입학",
}
# 모르는 유형 코드도 영어로 보이지 않게 — 라이브러리의 문서 종류 이름으로 잇는다(vocab.TypeLabels·doc_kind_label)
from zzaimy.app.vocab import TypeLabels as _TypeLabels  # noqa: E402
DOC_TYPE_LABELS = _TypeLabels({**INBOX_TYPES, "regulation": "기준 문서", "ocr": "문서 추출"})

from zzaimy.app.work_areas import SECTOR_LABELS  # noqa: E402  업무 목록 — 폴더 연결 설정(nas_sync)과 같은 표

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

    연결에 저장된 모델 하나만 보여 주면 서버에 새로 올린 모델이 화면에 뜨지 않는다.
    페이지마다 물으면 느리므로
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
    빈 줄 없이 '2. …' 가 불릿 뒤에 오면 마크다운이 앞 불릿의 이어지는 글로 붙인다."""
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


def _asset_version() -> str:
    """정적 파일 판 — 정적 폴더에서 가장 최근에 바뀐 파일 시각(배포마다 달라짐)."""
    try:
        d = Path(__file__).parent / "static"
        return str(int(max(p.stat().st_mtime for p in d.rglob("*") if p.is_file())))
    except Exception:
        return "0"


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
    # 첫 기동이면 zzaimy(담당자)=기동 비밀번호, zzdev(개발자)=ZZAIMY_DEV_PASSWORD 또는 무작위 비밀번호로 만든다.
    # 개발자 전용 화면(/dev)은 dev 역할만 접근한다.
    import json as _aj

    accounts_path = Path(db_path).parent / "accounts.json"
    accounts: dict[str, dict] = {}

    from zzaimy.app import org_lock as _org_lock

    def _save_accounts() -> None:
        """계정 표 쓰기 — 조직도·사용자 화면과 같은 잠금(org_lock) 안에서, 임시 파일에 쓴 뒤 바꿔 끼운다(쓰다 끊겨도 반쪽 파일이 남지 않게)."""
        with _org_lock.LOCK:
            tmp = accounts_path.with_suffix(".tmp")
            tmp.write_text(_aj.dumps(accounts, ensure_ascii=False, indent=1))
            tmp.chmod(0o600)
            tmp.replace(accounts_path)
            _org_lock.note(accounts_path)

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
            with _org_lock.LOCK:                          # 승격도 계정 쓰기 — 다시 읽어 아직 평문일 때만 바꾼다
                acct = accounts.get(uid)
                if acct is not None and not acct.get("pw_hash"):
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
            # 첫 기동 — 개발자 비밀번호는 운영자가 정한 값(ZZAIMY_DEV_PASSWORD)이거나, 없으면 무작위로 만들어 0600 파일에만 둔다.
            # 무작위 비밀번호로 만든 계정은 로그인 뒤 비밀번호를 바꿀 때까지 설정 화면으로만 간다. 있는 계정 파일은 건드리지 않는다
            dev_pw = os.environ.get("ZZAIMY_DEV_PASSWORD", "")
            generated = not dev_pw
            if generated:
                dev_pw = secrets.token_urlsafe(12)
            dev_acct = _new_account("zzdev", dev_pw, "dev", "개발자")
            if generated:
                dev_acct["must_change"] = True
            accounts = {"zzaimy": _new_account("zzaimy", password, "staff", "담당자"), "zzdev": dev_acct}
            _save_accounts()
            if generated:
                pw_file = accounts_path.parent / "initial_dev_password.txt"
                pw_file.write_text(dev_pw + "\n")
                pw_file.chmod(0o600)
                logging.getLogger("zzaimy.app.auth").warning("개발자 계정(zzdev)의 첫 비밀번호를 %s 에 두었습니다 — 로그인 뒤 바꿔 주세요", pw_file)


    def _make_session(user: str, days: int = 7) -> str:
        exp = str(int(_time.time()) + days * 86400)
        base = f"{exp}.{user}"
        sig = _hmac.new(
            session_secret.encode(), base.encode(), hashlib.sha256
        ).hexdigest()
        return f"{base}.{sig}"

    _LOCAL_PATH = re.compile(r"/(?![/\\])[^\s\\]*")

    def _local_back(back: str, default: str = "/") -> str:
        """되돌아갈 주소 — 이 서비스 안의 경로만(「//」「/\\」 처럼 브라우저가 다른 사이트로 읽는 꼴·공백·역슬래시 금지)."""
        back = (back or "").strip()
        return back if _LOCAL_PATH.fullmatch(back) else default

    VIEW_AS_ROLES = ("student", "staff")      # 개발자 「테스트 보기」로 고를 수 있는 역할 — 권한을 좁히기만 한다

    def _view_as(user: str) -> dict | None:
        """개발자 계정의 「테스트 보기」 — 고른 역할·부서로 화면·검색·대화 범위를 본다(db 설정 view_as:<계정>). 개발자만, 좁히기만."""
        if not user or (accounts.get(user, {}) or {}).get("role", "staff") != "dev":
            return None
        try:
            v = _aj.loads(db.get_setting(f"view_as:{user}", "") or "null")
        except ValueError:
            return None
        if not isinstance(v, dict) or v.get("role") not in VIEW_AS_ROLES:
            return None
        return {"role": v["role"], "dept": str(v.get("dept") or ""),
                "grade": str(v.get("grade") or "") if v["role"] == "staff" and v.get("grade") in _gr.GRADES else ""}

    def _acct_role_dept(user: str) -> tuple[str, str | None]:
        """계정의 지금 역할·부서 — 계정 설정에 「테스트 보기」를 얹은 값. 화면 요청·뒤에서 도는 답변·도우미가 같은 값을 쓴다."""
        if password is None:
            return "dev", None
        acct = accounts.get(user, {}) or {}
        va = _view_as(user)
        if va:
            return va["role"], va["dept"] or None
        return acct.get("role", "staff"), acct.get("dept") or None

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

    _DOC_PATH = re.compile(r"^/(?:doc|chat/peek|api/doc)/(\d+)(?:/|$)")   # api/doc: 구글 열람본 만들기(드라이브로 나가는 길)
    # 학생 허용 경로 — 대화(보내기·읽기·다시·지우기)와 문서 읽기(학생 공개 규정만, 열람 판정은 _DOC_PATH 가 한다)
    _STUDENT_GET = re.compile(r"^/(?:$|chat(?:/\d+(?:/(?:messages|status))?)?$|chat/peek/\d+$|doc/\d+(?:/(?:view(?:/status)?|original(?:/status|/download)?|page/\d+\.png))?$|settings$|logout$)")
    # 학생도 내 설정(비밀번호 변경만)은 쓴다 — 바꾸는 길은 본인 계정·현재 비밀번호 확인(/account/password)뿐
    _STUDENT_POST = re.compile(r"^/(?:chat/(?:send|ask)|chat/\d+/(?:retry|delete)|account/password|logout)$")

    _PROJECT_PATH = re.compile(r"^/projects?/(\d+)(?:/|$)")
    _DOC_ANYONE_POST = re.compile(r"^/doc/\d+/(?:quality-report|webhwp)$")   # 문서를 볼 수 있는 누구나 하는 POST(품질 신고·기안기 작업본 만들기)

    def _student_allowed(method: str, path: str) -> bool:
        return bool((_STUDENT_POST if method == "POST" else _STUDENT_GET).match(path))
    from zzaimy.app import access_policy as _ap
    from zzaimy.app.access_policy import visible as _visible

    def _extra_depts(user: str | None) -> list[str]:
        """계정별 추가 권한으로 받은 부서 RAG 이름들(RAG 공간 설정 grants) — 열람 검사와 목록 SQL 이 같은 값을 쓴다."""
        return _ap.extra_depts(user, Path(db_path).parent)

    def _req_viewer(request: Request) -> dict:
        """요청의 보는 사람 — 열람 규칙은 access_policy 한 곳(visible·doc_sql)."""
        # 「테스트 보기」 중에는 개발자가 올린 문서를 「자기 문서」로 보지 않는다 — 고른 계정이 실제로 보는 범위와 같게
        user = "" if getattr(request.state, "view_as", None) else getattr(request.state, "user", "")
        return _ap.viewer(getattr(request.state, "role", ""), getattr(request.state, "dept", "") or None,
                          user, Path(db_path).parent, grade=_gr.request_grade(request))

    def _dept_peers(request: Request) -> tuple[str, ...]:
        """같은 부서 동료 계정 — 진행 중인 프로젝트를 읽기 전용으로 서로 본다(대화와 고치기는 담당자만). 교직원 계열·부서가 있을 때만."""
        dept = (getattr(request.state, "dept", "") or "").strip()
        if getattr(request.state, "role", "") not in _ap.STAFF_ROLES or not dept:
            return ()
        me = "" if getattr(request.state, "view_as", None) else getattr(request.state, "user", "")
        # 동료는 교직원 계열이고 보는 범위가 나보다 넓지 않은 계정만 — 관리자나 다른 부서 RAG 추가 권한을 받은 계정의 대화·답에는
        # 내가 볼 수 없는 자료가 섞일 수 있다
        mine = {dept, *_extra_depts(me)} if me else {dept}
        return tuple(u for u, a in accounts.items()
                     if u != me and ((a or {}).get("dept") or "").strip() == dept and not (a or {}).get("must_change")
                     and (a or {}).get("role", "staff") in _ap.STAFF_ROLES and set(_extra_depts(u)) <= mine)

    def _project_access(request: Request, proj: dict | None) -> str:
        """이 요청의 사람이 프로젝트에 갖는 권한 — "edit"(담당자·함께 작업)·"view"·""(못 봄). 관리자는 "edit".
        공유 범위는 프로젝트마다 담당자가 정한다(app/project_access) — 정하지 않은 옛 프로젝트는 담당자 부서에 보기."""
        from zzaimy.app import project_access as _pa
        if not proj:
            return ""
        if getattr(request.state, "role", "") == "dev" and not getattr(request.state, "view_as", None):
            return "edit"
        me = "" if getattr(request.state, "view_as", None) else getattr(request.state, "user", "")
        owner_dept = ((accounts.get(proj.get("owner") or "", {}) or {}).get("dept") or "").strip()
        share = _pa.shares(db, proj, owner_dept)
        if share.get("implicit") and me and proj.get("owner") != me:
            # 담당자가 공유 범위를 정하지 않은 프로젝트 — 예전 동료 규칙 그대로(관리자·나보다 넓은 범위의 담당자 것은 열지 않는다, 10-16 보안 검토)
            return "view" if proj.get("owner") in _dept_peers(request) else ""
        return _pa.access(proj, user=me, role=getattr(request.state, "role", ""), dept=getattr(request.state, "dept", "") or "",
                          also=tuple(_extra_depts(me)) if me else (), share=share)

    def _shared_project_ids(request: Request) -> list[int]:
        """나에게 공유된 진행 중 프로젝트(내 것 제외) — 사이드바 「공유받은 프로젝트」·프로젝트 찾기."""
        me = "" if getattr(request.state, "view_as", None) else getattr(request.state, "user", "")
        if getattr(request.state, "role", "") not in _ap.STAFF_ROLES:
            return []
        with db._conn() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM projects WHERE archived = 0 AND owner <> ? ORDER BY id DESC",
                                                  (me,)).fetchall()]
        return [int(p["id"]) for p in rows if _project_access(request, p)]

    def _covers(viewer: str, other: str) -> bool:
        """viewer 의 검색 범위가 other 의 범위를 다 덮는가 — 구글 드라이브 작업 문서 권한(_share_plan)에만 쓴다: 그 문서에는 other 가 볼 수
        있는 자료가 근거 기록 없이 섞일 수 있어(10-16·10-21 보안 검토). 대화 읽기는 10-27 부터 이 관문 대신 근거를 읽는 사람 범위로 거른다
        (_can_read_chat·chat_routes.chat_read). 관리자(전부)의 것은 관리자만."""
        a, b = accounts.get(viewer) or {}, accounts.get(other) or {}
        if not viewer or not other:
            return False
        if b.get("role", "staff") == "dev":
            return a.get("role", "staff") == "dev"
        if a.get("role", "staff") == "dev":
            return True
        mine = {(a.get("dept") or "").strip(), *_extra_depts(viewer)} - {""}
        theirs = {(b.get("dept") or "").strip(), *_extra_depts(other)} - {""}
        return theirs <= mine

    def _peer_of(viewer: str, other: str) -> bool:
        """other 가 viewer 의 같은 부서 동료인가(대화 읽기·작업 문서 공유 같은 기준) — 둘 다 교직원 계열, 같은 부서, 첫 비밀번호를
        바꾼(쓰기 시작한) 계정, other 가 받은 추가 권한 부서를 viewer 도 볼 수 있을 때."""
        a, b = accounts.get(viewer) or {}, accounts.get(other) or {}
        dept = (a.get("dept") or "").strip()
        if not viewer or not other or viewer == other or not dept or (b.get("dept") or "").strip() != dept:
            return False
        if a.get("role", "staff") not in _ap.STAFF_ROLES or b.get("role", "staff") not in _ap.STAFF_ROLES:
            return False
        if a.get("must_change") or b.get("must_change"):
            return False
        return set(_extra_depts(other)) <= {dept, *_extra_depts(viewer)}

    def _chat_used_docs(session_id: int) -> set[int]:
        """대화가 근거로 쓴 문서와 첨부 — 남이 그 대화(와 작업 문서)를 봐도 되는지 가릴 때."""
        used = set(chat_topics.doc_ids(int(session_id)))
        # 작업본·열람본(구글로 옮긴 라이브러리 문서의 복제본)의 원본도 — 그 내용이 대화의 작업 문서에 그대로 있다
        used |= {int(f["doc_id"]) for f in db.list_files(kind="google", session_id=int(session_id), limit=200) if f.get("doc_id")}
        for m in db.list_chats(int(session_id)):
            used |= {int(x) for x in re.findall(r"\[첨부#(\d+)\]", m.get("content") or "")}
        return used

    def _chat_open_to(session: dict, viewer_: dict) -> bool:
        """비공개가 아닌 프로젝트 대화이고, 근거·첨부 문서를 모두 이 사람(viewer_)이 볼 수 있는가."""
        if not session.get("project_id") or (session.get("channel") or "chat") != "chat" or session.get("is_private"):
            return False
        for did in _chat_used_docs(int(session["id"])):
            d = db.get_document(int(did))
            if d is not None and not _visible(d, **viewer_):
                return False
        return True

    def _share_plan(project: dict) -> tuple[dict[str, set[str]], int]:
        """작업 문서별로 보기 권한을 줄 동료 메일 — 대화 읽기와 같은 규칙(비공개·업무 상담·못 보는 근거를 쓴 대화의 문서는 주지 않는다).
        돌려주는 것 ({구글 파일 id: {메일}}, 구글을 잇지 않아 못 받는 동료 수)."""
        from zzaimy.app import project_access as _pa
        owner = project.get("owner") or ""
        owner_dept = ((accounts.get(owner, {}) or {}).get("dept") or "").strip()
        # 받는 사람 = 프로젝트 공유 범위(나만·부서·지정 사용자) — 보기는 보기 권한, 함께 작업은 편집 권한
        share = _pa.shares(db, project, owner_dept)
        if share.get("implicit"):                       # 정하지 않은 기본값 — 예전 동료 규칙(보는 범위가 담당자보다 좁지 않은 같은 부서 교직원)
            levels = {u: "view" for u in accounts if _peer_of(u, owner)}
        else:                                           # 구글 작업 문서는 담당자 범위를 다 덮는 사람만 받는다(근거 기록 없이 범위 밖 내용이 섞일 수 있어)
            levels = {u: lv for u, lv in _pa.recipients(share, accounts, owner).items() if _covers(u, owner)}
        peers = [u for u in levels if u in accounts]
        emails = {u: (db.get_setting(f"google_account:{u}", "") or "").strip() for u in peers}
        missing = sum(1 for e in emails.values() if not e)
        viewers = {u: _ap.viewer((accounts.get(u) or {}).get("role", "staff"), (accounts.get(u) or {}).get("dept") or None, u,
                                 Path(db_path).parent) for u, e in emails.items() if e}
        want: dict[str, dict[str, str]] = {}             # {파일: {메일: reader|writer}}
        if not viewers:
            return want, missing
        with db._conn() as conn:
            sessions = [dict(r) for r in conn.execute("SELECT * FROM chat_sessions WHERE project_id = ? AND owner = ?",
                                                      (int(project["id"]), owner)).fetchall()]
        for cs in sessions:
            files = set()
            try:
                link = _aj.loads(db.get_setting(f"chat_google_doc:{cs['id']}", "") or "{}")
            except ValueError:
                link = {}
            if link.get("doc"):
                files.add(str(link["doc"]))
            for f in db.list_files(kind="google", session_id=int(cs["id"]), limit=50):
                m = re.search(r"/d/([A-Za-z0-9_-]{10,})", f.get("url") or "")
                if m:
                    files.add(m.group(1))
            if not files:
                continue
            for u, v in viewers.items():
                if _chat_open_to(cs, v):
                    for fid in files:
                        want.setdefault(fid, {})[emails[u]] = "writer" if levels.get(u) == "edit" else "reader"
        return want, missing

    def _share_sync(project: dict | None, wait: bool = False) -> None:
        """프로젝트 작업 문서의 부서 보기 공유를 맞춘다. 거두는 일(끄기·비공개)은 기다리고(wait), 주는 일은 뒤에서."""
        if not project:
            return
        from zzaimy.ingest import drive_share, gdrive_files as _gf
        owner = project.get("owner") or ""
        dept = ((accounts.get(owner, {}) or {}).get("dept") or "").strip() or None

        def plan():                                     # 잠금 안에서 그때의 상태로 — 계획과 맞추기 사이에 비공개로 바뀐 것이 끼지 않게
            fresh = db.get_project(int(project["id"])) or project
            return ({}, 0) if fresh.get("archived") else _share_plan(fresh)

        def work():
            drive_share.sync(db, int(project["id"]), _gf.doc_account_for(db, owner, dept), plan)
        if wait:
            work()
        else:
            import threading as _th
            _th.Thread(target=work, name="drive-share", daemon=True).start()

    def _may_edit_doc(request: Request, doc: dict | None) -> bool:
        """문서를 바꿀 수 있는가 — 관리자, 올린 사람, 또는 그 문서 부서에 속한(추가로 받은 부서 포함) 교직원(access_policy.can_edit).
        「테스트 보기」 중에는 고른 계정처럼(개발자가 올린 문서도 남의 문서)."""
        return _ap.can_edit(doc, **_req_viewer(request)) if doc else False

    if password is not None:
        from zzaimy.app import webhwp_demo as _webhwp_demo
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
            user = _session_user(request.cookies.get("zz_session", ""))
            if user is None and cred is not None:
                if _verify_pw(cred.username, cred.password):
                    user = cred.username
                else:
                    raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
            if user is None:
                # 인증 정보가 아예 없는 브라우저 요청 — 로그인 페이지로
                raise HTTPException(401, detail="login-required")
            request.state.real_role = _ap.role_class(accounts.get(user, {}).get("role", "staff"))   # 예전 교수·부서장 → 교직원
            request.state.role = request.state.real_role
            request.state.dept = accounts.get(user, {}).get("dept", "")     # 검색·대화 범위(부서). 비면 전체
            request.state.user = user
            # 개발자 「테스트 보기」 — 고른 역할·부서로 이 요청을 판정한다. 끄고 켜는 경로(/view-as)만은 원래 권한으로
            va = _view_as(user)
            request.state.view_as = va
            if va and not request.url.path.startswith("/view-as"):
                request.state.role, request.state.dept = va["role"], va["dept"]
            # 공동 편집 시연(ADR-0071) — 관리자 세션에서 칸 표시를 단 기안기 편집 요청만 시연 계정으로(권한은 좁히기만)
            _webhwp_demo.apply(request, accounts)
            # 무작위 첫 비밀번호 계정은 바꾸기 전까지 설정 화면(비밀번호 바꾸기)과 로그아웃만
            if accounts.get(user, {}).get("must_change") and request.url.path not in ("/settings", "/account/password", "/logout"):
                raise HTTPException(401, detail="password-change-required")
            # 구글 허용 콜백은 로그인한 누구나 — 사용자마다 자기 학교 계정을 잇는다(user_admin, 콘솔에 등록된 주소 그대로)
            if request.url.path.startswith("/dev") and request.state.role != "dev" and request.url.path != "/dev/gdrive/callback":
                raise HTTPException(403, "관리자 계정만 여는 화면입니다")
            # 학생은 허용한 경로만 — 대화와 학생 공개 규정 읽기(ADR-0052). 허용 목록이라 앞으로 생기는 화면도 저절로 막힌다
            if request.state.role == "student" and not _student_allowed(request.method, request.url.path):
                raise HTTPException(403, "학생 계정은 학사 질문 대화만 씁니다")
            # 열람 등급은 검색만이 아니라 문서 경로 전부(화면·원본·쪽 그림·복원·내보내기·삭제)에 강제한다(브리프 절대 규칙 4).
            # 라우트마다 검사를 넣지 않고 여기서 한 번에 — 앞으로 생기는 /doc/{id}/… 경로도 저절로 막힌다.
            m = _DOC_PATH.match(request.url.path)
            if m:
                doc = db.get_document(int(m.group(1)))
                if doc is not None and not _visible(doc, **_req_viewer(request)):
                    raise HTTPException(404, "문서를 찾을 수 없습니다")
                # 문서를 바꾸는 일(지우기·판정·초안·다시 읽기·갈래·접수번호·검토 의견…)은 올린 사람과 관리자만 — 볼 수 있다고
                # 바꿀 수 있는 것은 아니다. 볼 수 있는 사람 누구나 하는 일은 품질 신고뿐. 여기서 한 번에 막아 앞으로 생기는 경로도 따른다
                if (doc is not None and request.method == "POST" and request.url.path.startswith("/doc/")
                        and not _DOC_ANYONE_POST.match(request.url.path)
                        and not _may_edit_doc(request, doc)):
                    raise HTTPException(403, "문서를 올린 담당자, 그 부서 교직원, 관리자만 바꿀 수 있습니다")
            # 프로젝트를 바꾸는 일(보관·지침·메모·기준 문서·참고 연결·이름·지우기 …)도 그 프로젝트 담당자와 관리자만.
            # 「불러오기」 는 보관된 과거 사업 묶음(관리자 계정 소유)을 가져오는 길이라 그것만 볼 수 있는 사람 누구나
            # 보기도 같은 규칙(프로젝트 찾기 project_refs.browse 와 같게) — 다른 부서의 진행 중 프로젝트는 보이지 않고, 같은 부서
            # 동료의 것은 읽기 전용, 보관된 사업 묶음은 볼 수 있는 문서가 하나라도 있을 때만 읽기로 연다
            pm = _PROJECT_PATH.match(request.url.path)
            if pm and request.state.role != "dev":
                proj = db.get_project(int(pm.group(1)))
                me = "" if getattr(request.state, "view_as", None) else request.state.user
                if proj is not None and (not me or proj.get("owner") != me):
                    from zzaimy.app import project_refs as _pr
                    shared = bool(proj.get("archived")) and bool(
                        _pr._visible_counts(db, [int(proj["id"])], _req_viewer(request)).get(int(proj["id"])))
                    if request.method == "POST" and re.fullmatch(r"/project/\d+/(bundle|documents/import|notes(/[^/]+)*|criteria/(link|unlink))",
                                                                 request.url.path):
                        shared = _project_access(request, proj) == "edit"   # 「함께 작업」 은 문서 추가·원칙·기준 연결까지(「읽기만」 은 막힘)
                    elif request.method == "POST":
                        shared = shared and proj.get("owner") == "zzdev" and request.url.path.endswith("/unarchive")
                    elif _project_access(request, proj):
                        shared = True                     # 공유받은 프로젝트(project_access) — 화면은 읽기, 프로젝트를 바꾸는 POST 는 위에서 막힌다
                    elif re.fullmatch(r"/project/\d+/activity", request.url.path):
                        shared = True                     # 진행 현황은 등급으로 따로 판단(project_activity.may_view — 총괄은 공유 대상이 아니어도 부서 프로젝트)
                    if not shared:
                        raise HTTPException(404, "프로젝트를 찾을 수 없습니다")
            # 기관 정보(대학명·총장 등)는 모든 사용자 문서에 함께 쓰인다 — 관리자만 바꾼다
            if request.url.path == "/institution" and request.method == "POST" and request.state.role != "dev":
                raise HTTPException(403, "기관 정보는 관리자만 바꿀 수 있습니다")

        dependencies = [Depends(check_auth)]
    else:
        # 인증 없는 로컬 개발 모드 — 개발자 뷰까지 전부 연다
        def open_auth(request: Request) -> None:
            request.state.role = "dev"
            request.state.real_role = "dev"
            request.state.view_as = None
            request.state.dept = ""
            request.state.user = "zzaimy"

        dependencies = [Depends(open_auth)]

    app = FastAPI(title="AURA", dependencies=dependencies)
    app.state.project_access = _project_access     # 프로젝트 권한(보기·함께 작업) — 대화 라우터가 프로젝트 안 새 대화를 열 때
    app.state.shared_project_ids = _shared_project_ids
    app.state.dept_peers = _dept_peers           # 같은 부서 동료 — 프로젝트 찾기(project_search)가 읽기 전용으로 함께 보인다

    from fastapi import status as _status
    from fastapi.responses import JSONResponse

    from starlette.exceptions import HTTPException as _StarletteHTTPException

    # 오류 화면 — 권한으로 막힌 화면(403)과 남의 비공개 문서·프로젝트·대화(404, 있는지조차 숨긴다)는 같은 꼴:
    # 머리 「볼 수 없는 화면입니다」 + 까닭 한 줄, 탭 제목은 짧게. 화면·역할 단위로 막는 것은 403, 개별 문서·프로젝트·대화처럼
    # 남의 것이 있는지 드러나면 안 되는 것은 404 로 둔다(없는 것과 볼 수 없는 것을 같은 말로).
    _DENIED = "볼 수 없는 화면입니다"
    _ERROR_PAGES = {404: (_DENIED, "없는 주소이거나 볼 수 있는 범위 밖입니다. 처음 화면이나 사이드바에서 다시 찾아 주세요."),
                    403: (_DENIED, "계정의 열람 범위 밖입니다. 필요하면 관리자에게 권한을 요청해 주세요."),
                    405: ("이 방법으로는 열 수 없는 주소입니다", "버튼이나 메뉴로 다시 들어와 주세요.")}
    _NOT_FOUND_OF = re.compile(r"^(.{1,20}?)[을를] 찾을 수 없습니다\.?$")
    # 개별 항목 주소 — 없는 것과 남의 비공개 것이 같은 글로 보이게 주소 갈래로 이름을 정한다(상세 글이 없을 때도)
    _ITEM_OF_PATH = ((re.compile(r"^/(?:doc|api/doc)/\d+"), "문서"), (re.compile(r"^/projects?/\d+"), "프로젝트"),
                     (re.compile(r"^/chat/\d+"), "대화"))

    def _error_page_text(code: int, detail: str, path: str = "") -> tuple[str, str]:
        """오류 화면의 (머리, 까닭 한 줄). 화면에 「디렉터리」 대신 「폴더」."""
        detail = (detail or "").replace("디렉터리", "폴더").strip()
        heading, message = _ERROR_PAGES[code]
        if code == 404:
            item = next((name for rx, name in _ITEM_OF_PATH if rx.match(path)), "")
            m = _NOT_FOUND_OF.match(detail)
            noun = item or (m.group(1) if m else "")
            if noun:
                return _DENIED, f"없거나 볼 수 있는 범위 밖의 {noun}입니다."
            return heading, (detail.rstrip(".") + "." if detail else message)
        if code == 403 and detail:
            return _DENIED, detail.rstrip(".") + "."
        return heading, message

    def _wants_page(request: Request) -> bool:
        """사람이 브라우저로 연 화면인가 — API·스크립트 요청(JSON)은 그대로 JSON 으로 둔다."""
        path = request.url.path
        return (request.method == "GET" and not path.startswith(("/api/", "/static/"))
                and "text/html" in request.headers.get("accept", ""))

    @app.exception_handler(_StarletteHTTPException)
    async def _auth_redirect(request: Request, exc: _StarletteHTTPException):
        # 세션 없는 브라우저 접근은 로그인 페이지로 보낸다
        if exc.status_code == 401 and exc.detail == "login-required":
            return RedirectResponse("/login", status_code=_status.HTTP_303_SEE_OTHER)
        if exc.status_code == 401 and exc.detail == "password-change-required":
            return RedirectResponse("/settings?err=첫 비밀번호를 바꿔 주세요", status_code=_status.HTTP_303_SEE_OTHER)
        if exc.status_code in _ERROR_PAGES and _wants_page(request):
            detail = exc.detail if isinstance(exc.detail, str) and exc.detail not in ("Not Found", "Forbidden", "Method Not Allowed") else ""
            heading, message = _error_page_text(exc.status_code, detail, request.url.path)
            short = heading.removesuffix("입니다").rstrip()      # 탭 제목은 짧게 — 「볼 수 없는 화면 · AURA」
            return templates.TemplateResponse(request, "_error.html", {"code": exc.status_code, "heading": heading, "title": short,
                                                                       "message": message}, status_code=exc.status_code)
        return JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )
    class _FreshStatic(StaticFiles):
        """정적 파일은 매번 바뀌었는지 묻게 한다(no-cache, 안 바뀌었으면 304 로 가볍다).
        그러지 않으면 배포 뒤 브라우저가 옛 CSS 를 그대로 써 새 화면이 깨져 보인다."""

        def file_response(self, *args, **kwargs):
            resp = super().file_response(*args, **kwargs)
            resp.headers["Cache-Control"] = "no-cache"
            return resp

    app.mount(
        "/static",
        _FreshStatic(directory=str(Path(__file__).parent / "static")),
        name="static",
    )
    db = Database(db_path)
    from zzaimy.app import privacy_policy
    from functools import partial
    _scrub_internal = partial(privacy_policy.scrub_internal, db)
    if hasattr(processor, "configure_privacy"):
        processor.configure_privacy(db)
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
    _chat_progress: dict[int, dict] = {}

    def _chat_partial(session_id: int, text: str) -> None:
        """쓰는 중인 답 — 진행 상태에 실어 화면이 보인다. 식별 정보 가림은 최종 답과 같게 먼저 한다.
        가림은 완성된 글의 꼴(번호·이름)을 보고 찾으므로, 아직 쓰는 중인 마지막 낱말(「010-12」처럼 덜 쓰인 번호)은
        가림을 비켜 갈 수 있다 — 마지막 빈칸·줄바꿈까지만 보이고, 그 뒤 낱말은 다음 낱말이 시작돼 완성된 뒤에 보인다."""
        progress = _chat_progress.get(session_id)
        if progress is not None:
            try:
                masked = _scrub_internal(text)
                cut = max(masked.rfind(" "), masked.rfind("\n"), masked.rfind("\t"))
                progress["partial"] = masked[:cut][-12000:] if cut > 0 else ""
            except Exception:                                   # 가림이 실패하면 쓰는 중인 답을 보이지 않는다(최종 답만)
                progress.pop("partial", None)

    def _chat_step(session_id: int, label: str):
        progress = _chat_progress.get(session_id)
        if progress is not None:
            steps = progress["steps"]
            if not steps or steps[-1] != label:
                # 단계마다 시작 시각을 함께 — 화면이 단계별 걸린 초를 보인다(어디서 오래 머무는지)
                at = progress.get("at") or [progress.get("started_at", time.time())] * len(steps)
                progress["steps"] = (steps + [label])[-12:]
                progress["at"] = (at + [time.time()])[-12:]
    # 문서에 쓰는 도구 — 진행 상태에 「작성 카드」(지금 쓰는 절·이번 요청에서 쓴 절)를 싣는다
    _WRITE_ACTS = {"write_section", "rewrite_section", "edit_document", "create_document"}

    def _chat_act(session_id: int, name: str, args: dict, phase: str) -> None:
        progress = _chat_progress.get(session_id)
        if progress is None or name not in _WRITE_ACTS:
            return
        w = progress.setdefault("writing", {"done": []})
        section = _scrub_internal(str(args.get("section") or args.get("title") or ""))[:60]
        if phase == "start":
            w.update(act=name, section=section, state="writing", since=time.time())
        else:
            w["state"] = "idle"
            if phase == "done" and section and section not in w["done"]:
                w["done"] = (w["done"] + [section])[-12:]

    # 화면에서 고른 모델 서버 주소·모델을 프로세스 전체에 적용 (설정 > 환경변수)
    from zzaimy.generate import model_config as _mc

    _mc.set_override(db.get_setting("llm_base_url", ""), db.get_setting("llm_model", ""))
    _mc.configure_usage(Path(db_path).parent / "llm_usage.json")
    # LLM 연결 목록(내부 vLLM·외부 API) — data/platform/llm_connections.json (0600)
    from zzaimy.generate import llm_connections as _lc

    _lc.configure(Path(db_path).parent / "llm_connections.json")
    from zzaimy.app import gdrive_import as _gimport

    if not _lc.list_public() and db.get_setting("llm_base_url", ""):
        # 예전 '모델 서버 주소' 설정은 내부 vLLM 연결 하나로 옮긴다
        _old = _lc.add("내부 vLLM", "vllm", db.get_setting("llm_base_url", ""),
                       db.get_setting("llm_model", ""), "")
        _lc.activate(_old["id"])
    _paths.ensure_layout(Path(db_path).parent)     # 문서·지식·캐시 폴더 뼈대(ADR-0031)
    app.state.db = db  # 테스트·운영 점검에서 접근할 수 있게 노출
    # 프로젝트 검색(사이드바) — 계정 소유 프로젝트만, 제목·업무 영역으로(독립 라우터)
    from zzaimy.app.chat_documents import router as chat_documents_router
    from zzaimy.app.project_search import router as project_search_router

    app.include_router(project_search_router)
    # 대화에 구글 독스를 연결해 읽고(요청마다), 담당자가 확인한 삽입만 쓴다(독립 라우터)
    app.include_router(chat_documents_router)
    from zzaimy.app.notifications import router as notifications_router
    app.include_router(notifications_router)
    if password is None and not db.get_setting("google_fallback_shared", ""):
        # 인증 없는 로컬 모드는 사용자가 한 사람뿐 — 연결된 계정 하나를 그대로 쓴다(운영은 각자 연결, gdrive_files.account_for)
        db.set_setting("google_fallback_shared", "1")
    elif password is not None and db.get_setting("google_fallback_shared", "") == "1":
        # 로그인 있는 운영에서는 끈다 — 로컬 모드로 한 번 띄운 DB 에 켜진 값이 남으면 연결 안 한 사람이 남의 계정(드라이브)으로
        # 문서를 만든다(10-21 사용자: 「공용 계정 사용」 표시로 발견). 부서 드라이브·공유는 각자 연결한 계정 기준이다
        db.set_setting("google_fallback_shared", "0")
        logging.getLogger("zzaimy.app").warning("google_fallback_shared 를 껐습니다 — 로그인 모드에서는 각자 연결한 학교 계정만 씁니다")
    # 사용자 관리·사용자별 구글 계정 연결(/dev/users, /account/google) — 계정 표와 저장 함수를 라우터에 넘긴다
    from zzaimy.app.user_admin import router as user_admin_router
    app.state.accounts = accounts
    app.state.data_dir = Path(db_path).parent      # 부서 목록(user_admin.known_depts)이 RAG 공간 설정을 읽는 자리
    app.state.save_accounts = _save_accounts
    app.state.set_pw = _set_pw
    app.state.new_account = _new_account
    app.include_router(user_admin_router)
    # 웹 한글 기안기 — 부서 한글 문서함·기안기로 열기·기안기 서버 프록시(/webhwpctrl/)
    from zzaimy.app.webhwp_routes import router as webhwp_router
    app.include_router(webhwp_router)
    from zzaimy.app.webhwp_demo import router as webhwp_demo_router         # 공동 편집 시연(/dev/coedit-demo, ADR-0071)
    app.include_router(webhwp_demo_router)
    from zzaimy.app.hwp_drive_status import router as hwp_drive_status_router   # 문서함 구글 드라이브 연결 상태(늘 200)
    app.include_router(hwp_drive_status_router)
    from zzaimy.app.webhwp_export_routes import router as webhwp_export_router   # 기안기 내려받기 형식·다른 이름으로 저장·이름 바꾸기
    app.include_router(webhwp_export_router)
    from zzaimy.app.webhwp_live_routes import router as webhwp_live_router   # 열린 기안기 명령 줄·페이지 설정 값(webhwp_edit_ops)
    app.include_router(webhwp_live_router)
    from zzaimy.app.webhwp_coedit_routes import router as webhwp_coedit_router   # 칸 단위 공동 편집(임대·칸 글·알림 흐름, ADR-0070)
    app.include_router(webhwp_coedit_router)
    from zzaimy.app.finder_routes import router as finder_router            # 공용 디렉터리 창(부서 폴더·드라이브 고르기·전체 찾기)
    app.include_router(finder_router)
    # 사업 중심 그래프(온톨로지 v3, ADR-0048) — kg_nodes·kg_edges 를 사업 하나 기준으로 펼친다
    from zzaimy.app.kg_view import router as kg_view_router
    app.include_router(kg_view_router)
    from zzaimy.app.kg_explore import router as kg_explore_router   # 지식 그래프 탐색(Cytoscape.js) — 노드 하나씩 펼침
    app.include_router(kg_explore_router)
    from zzaimy.app.exports import router as exports_router   # 산출물 내려받기(/dev/export, 관리자만)
    app.include_router(exports_router)
    from zzaimy.app.collect import router as collect_router
    app.include_router(collect_router)
    from zzaimy.app.archive_view import router as archive_view_router   # DGX 원본 보관소(원본 목록 장부)
    app.include_router(archive_view_router)
    from zzaimy.app.program_admin import router as program_admin_router; app.include_router(program_admin_router)   # 사업 관리(/programs/admin, ADR-0066)
    from zzaimy.app.dept_dictionary import router as dept_dict_router; app.include_router(dept_dict_router)   # 부서 사전 자동완성(/api/depts)
    from zzaimy.app.project_activity import router as project_activity_router   # 프로젝트 진행 현황(참여·요청·문맥 사용·작업 요약)
    app.include_router(project_activity_router)

    @app.on_event("startup")
    def _warm_regulation_nouns() -> None:
        """규정 조각 명사를 뒤에서 미리 — 서비스를 다시 띄운 뒤 첫 규정 검색이 형태소 분석(1분 가까이)을 기다리지 않게."""
        import threading

        def _run() -> None:
            try:
                from zzaimy.app import regulations as _regs
                _regs.warm_nouns(db)
            except Exception as e:                             # 실패해도 첫 검색이 그때 센다(지금과 같음)
                logging.getLogger("zzaimy.app").warning("규정 명사 미리 세기 실패: %s", type(e).__name__)
        if os.environ.get("ZZAIMY_WARM_NOUNS", "1") != "0":
            threading.Thread(target=_run, name="warm-nouns", daemon=True).start()

    @app.on_event("startup")
    def _recover_dangling_chats() -> None:
        """재시작으로 끊긴 대화 복구 — 마지막 말이 담당자 질문이면(답변 스레드가 죽었다) 안내를 붙여 대화가 잠기지 않게 한다.

        답 없이 남은 요청이 있으면 그 대화는 새 질문·재생성이 모두 막힌다(409)."""
        try:
            for sess in db.list_chat_sessions(limit=500):
                last = db.list_chats(int(sess["id"]), limit=1)
                if last and last[-1]["role"] == "user":
                    db.add_chat(int(sess["id"]), "assistant",
                                "서비스가 다시 시작되어 이 요청의 처리가 중단됐습니다. 같은 요청을 다시 보내 주세요.")
        except Exception:
            pass

    def _requeue_stalled_docs(min_age_min: int = 10) -> list[int]:
        """등록만 되고 처리가 끊긴 문서(받음·처리 중으로 멈춤)를 다시 처리한다 — 플랫폼에 파일이 있는 것만.

        재시작하면 처리 작업(BackgroundTasks)이 사라져 문서가 「받음」으로 남는다.
        DGX 원본(dgx://)은 결과 들이기(168)가 맡으므로 여기서 다루지 않는다."""
        import threading
        from datetime import datetime as _dt, timedelta as _td
        cut = (_dt.now() - _td(minutes=min_age_min)).isoformat(timespec="seconds")
        try:
            from zzaimy.app.pipeline import REVIEW_PENDING
            with db._conn() as conn:                    # 재시작으로 끊긴 미룬 검토 — 「쓰는 중」으로 남지 않게 비운다(문서 화면에서 다시 만든다)
                conn.execute("UPDATE documents SET ai_review = '' WHERE ai_review = ?", (REVIEW_PENDING,))
        except Exception:
            pass
        with db._conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, stored_path FROM documents WHERE status IN ('received', 'processing') AND created_at < ?"
                " AND stored_path NOT LIKE 'dgx://%' ORDER BY id", (cut,)).fetchall()]
        todo = [(int(r["id"]), Path(r["stored_path"])) for r in rows if r["stored_path"] and Path(r["stored_path"]).is_file()]

        def run():
            for did, path in todo:
                try:
                    processor.process(db, did, path)
                except Exception as e:
                    logging.getLogger(__name__).warning("멈춘 문서 다시 처리 실패 doc=%s: %s", did, e)
        if todo:
            threading.Thread(target=run, name="requeue-stalled", daemon=True).start()
        return [d for d, _p in todo]

    @app.on_event("startup")
    def _recover_stalled_docs() -> None:
        try:
            _requeue_stalled_docs()
        except Exception:
            pass

    app.state.requeue_stalled_docs = _requeue_stalled_docs

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
    app.state.templates = templates
    templates.env.globals["status_labels"] = STATUS_LABELS
    templates.env.globals["cot_of"] = lambda mid: (db.get_setting(f"chat_cot:{int(mid)}", "") or "") if mid else ""   # 답 앞 단계 추론
    templates.env.globals["feedback_reasons"] = ("근거 문서가 틀림", "숫자·사실이 틀림", "질문과 다른 답", "너무 길거나 짧음", "느림", "기타")
    # 정적 파일 판 — 배포(재시작)마다 바뀌어 브라우저가 옛 CSS·JS 를 붙들지 않는다
    templates.env.globals["asset_v"] = _asset_version()
    templates.env.globals["doc_type_labels"] = DOC_TYPE_LABELS
    # 사람 이름 한 곳(app/people) — 장부 이름 → 내 설정 성명 → 아이디. 사이드바·사용자 메뉴·설정·사람 칩·잠금 안내가 같은 값을 쓴다
    from zzaimy.app import people as _people

    def _person_name(uid: str | None, fallback: str | None = None) -> str:
        if password is None:                       # 인증 없는 로컬 모드 — 계정 장부가 없어 내 설정 성명(없으면 예전 공통 값)
            got = _people.display_name(uid, None, db, fallback="") or (db.get_setting("name", "") or "").strip()
            return got or ((uid or "") if fallback is None else fallback)
        return _people.display_name(uid, accounts, db, fallback=fallback)
    app.state.person_name = _person_name
    templates.env.globals["person_name"] = _person_name
    templates.env.globals["decision_labels"] = DECISION_LABELS
    templates.env.globals["sector_labels"] = SECTOR_LABELS
    from zzaimy.app.access_guard import ROLES as _ROLES
    templates.env.globals["role_labels"] = _ROLES
    # 문서 이름 — 'law03.pdf' 같은 파일 이름 대신 첫 쪽의 규정 이름·날짜
    from zzaimy.app.doc_title import display_name as _doc_name
    templates.env.globals["doc_name"] = _doc_name
    from zzaimy.app.doc_title import display_title as _doc_title, receipt_label as _receipt_label
    templates.env.globals["doc_title"] = _doc_title
    # 라이브러리 탭 이름 — 탭(_library_tabs)과 안내문이 같은 이름을 쓴다
    templates.env.globals["library_tabs"] = {"docs": "문서", "archive": "원본 보관소"}
    templates.env.globals["receipt_label"] = _receipt_label
    from zzaimy.app import read_failure as _rf
    templates.env.globals["read_fail"] = lambda d: _rf.info(db, d)   # 읽지 못한 문서의 사유·다시 하는 단추(목록·문서 화면·알림)
    templates.env.globals["quality_kind_labels"] = QUALITY_KIND_LABELS
    # 지표 이름·정의(metric_label·metric_tip)와 개발 화면 이름표(dev_page_name) — 화면마다 같은 이름을 쓰게
    from zzaimy.app import dev_nav as _dev_nav
    from zzaimy.app import metrics as _metrics
    _metrics.install(templates.env)
    _dev_nav.install(templates.env)
    from zzaimy.app import passwords as _passwords
    _passwords.install(templates.env)

    import re as _re

    from markupsafe import Markup, escape

    def md_lite(text: str) -> Markup:
        """이스케이프 후 **볼드**만 살리는 최소 마크다운."""
        escaped = str(escape(text))
        return Markup(_re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped))

    templates.env.filters["md_lite"] = md_lite

    def _drive_new(owner: str) -> list[dict]:
        """드라이브 「ZZAIMY 가져오기」 에서 새로 가져온 내 문서 가운데 아직 알림에서 확인하지 않은 것(새것이 앞)."""
        from zzaimy.app import gdrive_import as _gi
        try:
            seen_imp = set(_aj.loads(db.get_setting(f"gimport_seen:{owner}", "[]") or "[]"))
        except ValueError:
            seen_imp = set()
        out = []
        for r in (_gi.status(db, owner).get("recent") or [])[:_gi.RECENT_KEEP]:
            try:
                d = db.get_document(int(r.get("doc_id") or 0))
            except (TypeError, ValueError):
                d = None
            if d and d.get("owner") == owner and int(d["id"]) not in seen_imp:
                out.append(d)
        return out

    def ctx(request: Request, extra: dict) -> dict:
        from zzaimy.app.model_tracks import TRACKS, UPDATED, learning_now
        # 작업물(문서함·프로젝트·채팅)은 계정별 분리 — 기준·OCR 저장소는 공용
        owner = getattr(request.state, "user", "zzaimy")
        pending = db.pending_documents(limit=50, owner=owner)
        actionable = [d for d in pending if d["doc_type"] in ("recruit", "admission")]
        by_type: dict[str, int] = {}
        for d in pending:
            by_type[d["doc_type"]] = by_type.get(d["doc_type"], 0) + 1
        failed = db.failed_documents(owner=owner)
        from zzaimy.app.notifications import unread
        actionable = unread(db, owner, actionable)
        failed = unread(db, owner, failed)
        recent_reviewed = unread(db, owner, db.reviewed_documents(owner))
        drive_new = _drive_new(owner)
        if getattr(request.state, "view_as", None):
            # 테스트 보기 — 알림도 고른 계정이 볼 수 있는 문서만(개발자가 올린 문서라도 범위 밖이면 빼고)
            _v = _req_viewer(request)
            actionable, failed, recent_reviewed = ([d for d in xs if _visible(d, **_v)]
                                                   for xs in (actionable, failed, recent_reviewed))
        from zzaimy.generate import model_config as _mc2

        try:
            llm_status = _mc2.status()
        except Exception:
            llm_status = {"ok": False, "configured": False, "model": "", "models": [], "error": "확인 실패"}
        return {
            "model_tracks": TRACKS, "model_tracks_updated": UPDATED, "learning_now": learning_now(),
            "llm_status": llm_status,
            "chat_sessions": chat_history.sessions(owner, limit=12),
            "pending_docs": pending[:8],
            "notification_pending": actionable[:8],
            "recent_reviewed": recent_reviewed,
            "pending_count": len(pending),
            "pending_by_type": by_type,
            "failed_docs": failed,
            "drive_new": drive_new,
            "alert_count": len(actionable) + len(failed) + len(drive_new),
            "side_projects": db.list_all_projects(owner=owner),
            # 공유받은 프로젝트 — 내 것 아래 따로(project_access). 학생·관리자 화면에는 없다
            "side_shared": [p for p in (db.get_project(i) for i in _shared_project_ids(request)[:20]) if p]
                           if getattr(request.state, "role", "") in _ap.STAFF_ROLES else [],
            "profile_name": _person_name(getattr(request.state, "user", None), fallback=""),   # 사람 이름 한 곳(app/people)
            "profile_dept": (accounts.get(getattr(request.state, "user", "") or "", {}) or {}).get("dept", ""),
            **extra,
        }

    app.state.ctx = ctx                              # 화면 틀 — 따로 둔 라우터(webhwp_routes 등)도 같은 틀로 그린다

    @app.get("/inbox")
    def inbox(request: Request):
        """옛 문서함 주소 — 라이브러리 등록 문서 목록과 같은 화면이라 그쪽으로 보낸다(걸린 조건은 그대로)."""
        qs = list(request.query_params.multi_items())
        if not any(k == "type" for k, _v in qs):
            qs.insert(0, ("type", "all"))
        return RedirectResponse("/?" + urlencode(qs), status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        type: str | None = None,
        q: str | None = None,
        project: int | None = None,
        flt: str | None = None,
        page: int = 1,
        use: str = "all",
        prog: str = "",
        year: str = "",
        folder: str = "",
        body: str = "",
        fmt: str = "",
        scope: str = "",
        since: str = "",
        sort: str = "new",
        pq: str = "",
    ):
        from zzaimy.app import library
        from zzaimy.app import library_folders as _lf

        if getattr(request.state, "role", "") == "student":
            return RedirectResponse("/chat", status_code=303)        # 학생 첫 화면은 대화(ADR-0052)
        if not any((type, q, project, flt, prog, folder, pq, request.query_params.get("page"), request.query_params.get("use"),
                    request.query_params.get("group"))):
            return RedirectResponse("/chat", status_code=303)
        if "group" in request.query_params:
            # 옛 「문서 출처」 선택(group=…) — 「기준 문서 탭에 등록」 은 기준 칩으로, 나머지 출처 구분은 없어졌다(ADR-0062)
            qs = [(k, v) for k, v in request.query_params.multi_items() if k not in ("group", "use")]
            if request.query_params.get("group") == "criteria":
                qs.append(("use", "criteria"))
            return RedirectResponse("/?" + urlencode(qs or [("type", "all")]), status_code=303)
        business = type if type in INBOX_TYPES else None
        # 없는 업무 값은 조용히 전체로 떨어뜨리지 않고 알린다
        unknown_type = type if type and type != "all" and business is None else ""
        use = use if use in dict(library.USES) else "all"
        # 문서 목록은 SQL 에서 거르고 쪽으로 자른다(열람 권한 포함). 판본 묶음은 id·제목만 읽어 묶는다(library.grouped)
        kind = (request.query_params.get("kind") or "").strip()
        acc, acc_args = _doc_access_sql(request)
        biz_col = library.business_sql(INBOX_TYPES)
        cond, args = ["1 = 1" + acc], list(acc_args)
        if project:
            cond.append("d.project_id = ?")
            args.append(project)
        tab_where, tab_args = " WHERE " + " AND ".join(cond), list(args)
        if business:
            c_, a_ = library.business_filter_sql(business, INBOX_TYPES)
            cond.append(c_)
            args += a_
        if q:
            # 낱말마다 제목에 있어야 한다(띄어쓰기 무시) — 「핵심성과지표 총괄」 이 「핵심 성과지표 총괄표」 도 찾는다
            words = [w for w in q.split() if w][:6]
            title = " AND ".join("REPLACE(d.filename, ' ', '') LIKE ?" for _ in words)
            # 화면의 접수번호(「2026-43149」 — 기본 유형 코드를 뺀 꼴)로도 찾는다
            from zzaimy.app.doc_title import receipt_queries
            rq = receipt_queries(q)
            # 본문까지 — 사업 문서 어휘 색인(grant_lex)에서 낱말이 든 조각의 문서. 권한은 색인 조회에서 거른다(채팅 검색과 같은 범위)
            body_ids: list[int] = []
            if body == "1":
                try:
                    from zzaimy.app import access_guard as _ag, grant_lex as _gl
                    sp = _ag.search_scope(getattr(request.state, "dept", "") or None, request.state.role, "" if getattr(request.state, "view_as", None) else (request.state.user or ""),
                                          Path(db_path).parent, grade=_gr.request_grade(request))
                    if sp.get("grant"):
                        cids = _gl.rank(db, _gl.lex_terms(q), None, None if request.state.role == "dev" else (request.state.user or ""),
                                        limit=3000, depts=sp.get("grant_depts"))
                        if cids:
                            with db._conn() as _c:
                                for i in range(0, len(cids), 900):
                                    part = cids[i:i + 900]
                                    body_ids += [int(r[0]) for r in _c.execute(
                                        f"SELECT DISTINCT doc_id FROM doc_chunks WHERE id IN ({','.join('?' * len(part))})", part)]
                except Exception:
                    body_ids = []
                body_ids = sorted(set(body_ids))[:5000]
            cond.append(f"(({title}) OR COALESCE(d.receipt_no, '') LIKE ?" + "".join(" OR d.receipt_no = ?" for _ in rq)
                        + (f" OR d.id IN ({','.join('?' * len(body_ids))})" if body_ids else "") + ")")
            args += [f"%{w}%" for w in words] + [f"%{q}%"] + rq + body_ids
        # 더 거르기 — 파일 형식·검색 범위·등록 시기. 사업 목록 수도 같은 조건으로 센다
        FMTS = {"pdf": ("pdf",), "hwp": ("hwp", "hwpx"), "word": ("doc", "docx"), "excel": ("xls", "xlsx", "xlsm", "csv"),
                "ppt": ("ppt", "pptx"), "image": ("png", "jpg", "jpeg"), "text": ("txt", "md")}
        if fmt in FMTS:
            cond.append("(" + " OR ".join("LOWER(d.stored_path) LIKE ?" for _ in FMTS[fmt]) + ")")
            args += [f"%.{e}" for e in FMTS[fmt]]
        else:
            fmt = ""
        if scope:
            cond.append("COALESCE(d.dept, '공통') = ?")
            args.append(scope)
        SINCE = {"7d": 7, "30d": 30, "1y": 365}
        if since in SINCE:
            from datetime import datetime as _dt, timedelta as _td
            cond.append("d.created_at >= ?")
            args.append((_dt.now() - _td(days=SINCE[since])).strftime("%Y-%m-%d"))
        else:
            since = ""
        fc, fa = _lf.filter_sql(folder) if folder else ("", [])
        if fc:                                                                 # 고른 폴더 — 사업 목록도 그 폴더 안에서 센다
            cond.append(fc)
            args += fa
        else:
            folder = ""
        rail_cond, rail_args = list(cond), list(args)                         # 사업 목록은 사업 조건만 빼고 같은 조건으로 센다
        rep, members = _program_groups()
        if prog.startswith("prog:"):                                           # 그래프의 대표가 아닌 이름으로 와도 묶음 대표로
            prog = "prog:" + rep.get(prog[5:], prog[5:])
        rc, ra = library.rail_sql(prog, year, members) if prog else ("", [])
        if rc:                                                                 # 고른 사업 — 쓰임새 칩·종류 수도 그 안에서
            cond.append(rc)
            args += ra
        else:
            prog = ""
        base_where, base_args = " WHERE " + " AND ".join(cond), list(args)
        use_c, use_a = library.use_sql(use)
        if use_c:
            cond.append(use_c)
            args += use_a
            rail_cond.append(use_c)
            rail_args += use_a
        if kind == "_none":
            cond.append("COALESCE(d.kind, '') = ''")
            rail_cond.append("COALESCE(d.kind, '') = ''")
        elif kind:
            cond.append("d.kind = ?")
            args.append(kind)
            rail_cond.append("d.kind = ?")
            rail_args.append(kind)
        where = " WHERE " + " AND ".join(cond)
        rail_where = " WHERE " + " AND ".join(rail_cond)
        PER = 50
        with db._conn() as conn:
            r = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN d.status IN ('received', 'processing') THEN 1 ELSE 0 END),"
                " SUM(CASE WHEN d.status = 'reviewed' THEN 1 ELSE 0 END),"
                " SUM(CASE WHEN d.status = 'reviewed' AND d.decision = 'pending' THEN 1 ELSE 0 END)"
                f" FROM documents d{where}", args).fetchone()
            stats = {"total": int(r[0] or 0), "processing": int(r[1] or 0), "reviewed": int(r[2] or 0), "pending": int(r[3] or 0)}
            fcond = {"processing": " AND d.status IN ('received', 'processing')", "reviewed": " AND d.status = 'reviewed'",
                     "pending": " AND d.status = 'reviewed' AND d.decision = 'pending'"}
            flt = flt if flt in fcond else None
            fwhere = where + (fcond[flt] if flt else "")
            total_docs = stats[flt] if flt else stats["total"]
            groups = library.grouped(conn, fwhere, args, sort)
            pages = max(1, -(-len(groups) // PER))
            if page > pages or page < 1:
                # 범위 밖 쪽은 빈 목록 대신 가까운 끝 쪽으로
                qs = [(k, v) for k, v in request.query_params.multi_items() if k != "page"]
                qs.append(("page", str(min(max(page, 1), pages))))
                return RedirectResponse("/?" + urlencode(qs), status_code=303)
            shown = groups[(page - 1) * PER: page * PER]
            ids = [i for g in shown for i in g[:21]]
            rows: dict[int, dict] = {}
            if ids:
                rows = {int(x["id"]): dict(x) for x in conn.execute(
                    "SELECT d.id, d.receipt_no, d.filename, d.kind, d.doc_type, d.sector, d.status, d.decision, d.created_at,"
                    f" d.project_id, p.name AS project_name, {biz_col} AS business, d.stored_path"
                    " FROM documents d LEFT JOIN projects p ON p.id = d.project_id"
                    f" WHERE d.id IN ({','.join('?' * len(ids))}){acc}", [*ids, *acc_args]).fetchall()}   # 열람 조건을 다시 건다
            rail = library.rail(conn, rail_where, rail_args, rep)
        # 사업 목록의 대·중·소 분류(외부 확인 장부 = 학교 재정지원사업 현황 기준) — 그래프 탐색 왼쪽 목록과 같은 나무
        from zzaimy.app import program_tree
        from zzaimy.app.program_names import load_ledger
        ledger = load_ledger()
        rail_tree = program_tree.build([g for g in rail if g["key"].startswith("prog:")], ledger)
        shown_label = {it["key"]: it["label"] for t in rail_tree for gg in t["groups"] for it in gg["progs"]}
        for g in rail:
            g["label"] = shown_label.get(g["key"], g["label"])
        from zzaimy.app.doc_routing import KINDS as _KINDS_LIB
        # 사업 찾기·사업 머리 — 장부 항목의 별칭·분류·기간·부서(장부에 적힌 것만). 항목 찾기는 사업 수만큼(문서 수와 무관)
        lg = program_tree.Ledger(ledger)
        tree_items = []
        for t in rail_tree:
            for gg in t["groups"]:
                for it in gg["progs"]:
                    it["l1"], it["l2"] = t["label"], gg["label"]
                    it["entry"] = lg.find(it.get("raw_label") or it.get("label") or "") if it.get("in_ledger") else None
                    it["aliases"] = library.program_facts(it["entry"], lg.categories).get("aliases", [])
                    it["span"] = library.year_span(it.get("years") or [])
                    tree_items.append(it)
        pq = (pq or "").strip()[:60]
        prog_hits = library.find_programs(tree_items, pq) if pq else []
        if pq and len(prog_hits) == 1 and not prog:                       # 하나로 좁혀지면 바로 그 사업 한눈에 보기로
            return RedirectResponse("/?" + urlencode([("type", business or "all"), ("prog", prog_hits[0]["key"])]), status_code=303)
        overview = None
        pick_item = next((it for it in tree_items if it["key"] == prog), None)
        if prog.startswith("prog:"):
            oc, oa = library.rail_sql(prog, "", members)                   # 표는 연도·거르기와 무관하게 사업 전체(열람 조건만)
            with db._conn() as conn:
                mx = library.matrix(conn, " WHERE 1 = 1" + acc + " AND " + oc, [*acc_args, *oa], _KINDS_LIB)
                key_ids = sorted({i for v in mx["key_docs"].values() for i in v.values()})
                key_rows = {int(x["id"]): dict(x) for x in conn.execute(
                    "SELECT d.id, d.receipt_no, d.filename, d.kind, d.created_at FROM documents d"
                    f" WHERE d.id IN ({','.join('?' * len(key_ids))}){acc}", [*key_ids, *acc_args]).fetchall()} if key_ids else {}
            facts = library.program_facts(pick_item.get("entry") if pick_item else None, lg.categories)
            key_docs = []
            for y in mx["years"]:
                got = [(k, key_rows[i]) for k in library.KEY_KINDS if (i := mx["key_docs"].get(y["tail"], {}).get(k)) in key_rows]
                if got:
                    key_docs.append({"tail": y["tail"], "label": y["label"], "docs": got})
            overview = {"m": mx, "facts": facts, "key_docs": key_docs[:12],
                        "l1": (pick_item or {}).get("l1", ""), "l2": (pick_item or {}).get("l2", ""),
                        "raw_label": (pick_item or {}).get("raw_label", "")}
        # 첫 화면(아무것도 고르지 않음) — 사업 찾기 아래에 문서 많은 사업 몇 개를 바로가기로
        top_progs = sorted(tree_items, key=lambda it: -int(it.get("n") or 0))[:8] if not (prog or q or folder or pq or kind or flt or fmt or since or scope) else []
        with db._conn() as conn:
            kinds = [(x[0] or "", int(x[1])) for x in conn.execute(
                f"SELECT d.kind, COUNT(*) FROM documents d{where} GROUP BY d.kind ORDER BY 2 DESC", args).fetchall()]
            case_sql, case_args = library.use_case_sql()
            ucount = {str(x[0]): int(x[1]) for x in conn.execute(
                f"SELECT {case_sql} AS u, COUNT(*) FROM documents d{base_where} GROUP BY 1", [*case_args, *base_args]).fetchall()}
            bcount = {str(x[0]): int(x[1]) for x in conn.execute(
                f"SELECT {biz_col} AS b, COUNT(*) FROM documents d{tab_where} GROUP BY 1", tab_args).fetchall()}
            # 규정 색인 문서(기준 꼬리표) 가운데 조각이 없는 것 — 처리는 끝났는데 규정 검색에 안 잡힌다
            reg_ids = [i for i, x in rows.items() if x.get("doc_type") == "regulation"]
            reg_chunks = {int(x[0]): int(x[1]) for x in conn.execute(
                f"SELECT doc_id, COUNT(*) FROM regulation_chunks WHERE doc_id IN ({','.join('?' * len(reg_ids))}) GROUP BY doc_id",
                reg_ids).fetchall()} if reg_ids else {}
        from zzaimy.app.doc_routing import KINDS as _KINDS_LIB
        docs = []
        for g in shown:
            head = rows.get(g[0])
            if not head:
                continue
            head["kind_label"] = _KINDS_LIB.get(head.get("kind") or "", "")
            head["in_index"] = head.get("doc_type") == "regulation"
            head["no_chunks"] = head["in_index"] and head.get("status") == "reviewed" and not reg_chunks.get(int(head["id"]))
            head["versions"] = [rows[i] for i in g[1:21] if i in rows]
            head["n_versions"] = len(g)
            docs.append(head)
        # 문서마다 원본 보관소의 그 원본으로 — 학교 원본(dgx://)에서 들어온 문서만. 이 쪽 문서의 원본 줄만 한 번에 읽는다
        dgx = {int(d["id"]): (d.get("stored_path") or "")[6:] for d in docs if (d.get("stored_path") or "").startswith("dgx://")}
        if dgx:
            from zzaimy.app import archive as _archive, doc_origin as _dorig
            _archive.ensure(db)
            rels = sorted(set(dgx.values()))
            with db._conn() as conn:
                arow = {str(r[0]): {"program": r[1], "program_name": r[2], "year": r[3]} for r in conn.execute(
                    f"SELECT rel, program, program_name, year FROM archive_files WHERE rel IN ({','.join('?' * len(rels))})", rels).fetchall()}
            for d in docs:
                if int(d["id"]) in dgx:
                    d["origin_href"] = _dorig.archive_location(dgx[int(d["id"])], arow.get(dgx[int(d["id"])]))["href"]
        ucount["all"] = sum(ucount.values())
        projects = db.list_projects(business) if business else []
        return templates.TemplateResponse(
            request,
            "index.html",
            ctx(request, {
                "documents": docs, "active_tab": business or "all", "q": q or "", "use": use,
                "uses": library.USES, "use_counts": ucount, "use_metrics": library.USE_METRICS,
                "business_counts": bcount, "business_tabs": library.show_business_tabs(bcount, INBOX_TYPES),
                "unknown_type": unknown_type,
                "projects": projects, "active_project": project,
                "stats": stats, "active_flt": flt,
                "page": page, "pages": pages, "page_numbers": library.page_numbers(page, pages),
                "per": PER, "total_docs": total_docs, "n_rows": len(groups),
                "kind": kind, "kind_counts": [(k, _KINDS_LIB.get(k, "분류 전") if k else "분류 전", n) for k, n in kinds],
                "folders": (_folders := _lf.folders(db, Path(db_path).parent, _req_viewer(request))), "folder": folder,
                "body": body == "1", "fmt": fmt, "scope": scope, "since": since, "sort": sort if sort in ("new", "old", "name") else "new",
                "folder_pick": next((f for f in _folders if f["key"] == folder), None),
                "scope_choices": _scope_choices(request), "inbox_types": INBOX_TYPES,
                "ministries": sorted({str(e.get("ministry") or "").split(",")[0].strip() for e in load_ledger().get("programs", [])} - {""}),
                "rail": rail, "rail_tree": rail_tree, "rail_fixed": [g for g in rail if not g["key"].startswith("prog:")],
                "prog": prog, "year": year, "pq": pq, "prog_hits": prog_hits, "overview": overview, "top_progs": top_progs,
                "kind_labels": _KINDS_LIB, "year_label_of": library.year_label,
                "rail_pick": next((g for g in rail if g["key"] == prog), None),
                "common_templates": [t for t in _common_templates() if business and t["sector"] == business],
            }),
        )

    def _common_templates() -> list[dict]:
        """구글 독스 공통 양식(scripts/180 이 만든 것) — 업무 탭의 「사본 만들기」 바로가기.

        공용 원본을 편집 모드로 열지 않는다 — 누르면 사본을 만든다(library.copy_url). 업무는 양식 사양의 sector,
        없으면 국고사업(양식이 국고사업 실문서 절 집계에서 나왔다). 이름에서 「 공통 양식(구글 독스)」는 뗀다."""
        try:
            from zzaimy.app.library import copy_url
            from zzaimy.ingest.gdocs_templates import SPECS

            sectors = {s["title"]: s.get("sector") or "grant" for s in SPECS.values()}
            pub = _aj.loads((Path(__file__).resolve().parents[3] / "data/generated/templates/gdocs/published.json").read_text(encoding="utf-8"))
            return [{"name": re.sub(r"\s*공통 양식\(구글 독스\)$", "", x["title"]), "url": copy_url(x.get("url") or ""),
                     "sector": sectors.get(x["title"], "grant")} for x in pub if copy_url(x.get("url") or "")]
        except Exception:
            return []

    _pg_cache: dict = {"at": 0.0, "v": ({}, {})}

    def _program_groups() -> tuple[dict, dict]:
        """그래프의 사업 이름 묶음 — (사업 id → 대표 id, 대표 id → 묶인 id 들). 10분 재사용, 그래프가 없으면 빈 묶음."""
        import time as _t
        from zzaimy.graph import kg_store as _ks
        _ver = _ks.version(db)
        if _t.time() - _pg_cache["at"] > 600 or _pg_cache.get("ver") != _ver:
            _pg_cache["ver"] = _ver
            try:
                from zzaimy.app.kg_explore import programs as _progs
                rows = [p for p in _progs(db, None) if p["type"] == "program"]
                rep = {i: p["id"] for p in rows for i in p["ids"]}
                members = {p["id"]: list(p["ids"]) for p in rows}
                # 그래프 목록에 서지 않은 사업 노드(문서가 거의 없거나 이름만 다른 것)도 장부 항목이 같으면 그 사업으로 —
                # 보관 묶음(projects.program)은 그런 노드를 가리킬 수 있다
                from zzaimy.app.program_names import load_ledger as _ll
                from zzaimy.app.program_tree import Ledger as _Lg
                lg = _Lg(_ll())
                owner = {}
                for p in sorted(rows, key=lambda p: -p["n_docs"]):
                    e = lg.find(p["label"])
                    if e is not None:
                        owner.setdefault(id(e), p["id"])
                with db._conn() as _c:
                    loose = [(r[0], r[1]) for r in _c.execute("SELECT id, label FROM kg_nodes WHERE type = 'program'").fetchall() if r[0] not in rep]
                    # 그래프 노드가 없는 보관 묶음 열쇠 — 묶음 이름(「2024년 ○○사업 (2차년도)」)에서 사업 이름으로
                    seen = {nid for nid, _ in loose}
                    for prog_key, pname in _c.execute(
                            "SELECT program, MAX(name) FROM projects WHERE program LIKE 'program:%' GROUP BY program").fetchall():
                        base = str(prog_key).split("|", 1)[0]
                        if base not in rep and base not in seen:
                            seen.add(base)
                            loose.append((base, __import__("zzaimy.app.library", fromlist=["_bundle_name"])._bundle_name(pname or "")))
                for nid, label in loose:
                    e = lg.find(label or "")
                    if e is not None and id(e) in owner:
                        rep[nid] = owner[id(e)]
                        members[owner[id(e)]].append(nid)
                _pg_cache.update(at=_t.time(), v=(rep, members))
            except Exception:
                _pg_cache.update(at=_t.time(), v=({}, {}))
        return _pg_cache["v"]

    _popt_cache: dict = {"at": 0.0, "v": []}

    def _program_options() -> list[dict]:
        """사업 고르기 목록 — 사업 목록 나무(program_tree)의 정식 이름, 대분류로 묶음(10분 재사용)."""
        import time as _t
        from zzaimy.graph import kg_store as _ks
        _ver = _ks.version(db)
        if _t.time() - _popt_cache["at"] > 600 or _popt_cache.get("ver") != _ver:
            _popt_cache["ver"] = _ver
            try:
                from zzaimy.app import program_tree as _pt
                from zzaimy.app.kg_explore import programs as _progs
                from zzaimy.app.program_names import load_ledger as _ll
                rows = [{"key": p["id"], "label": p["label"], "n": p["n_docs"]} for p in _progs(db, None) if p["type"] == "program"]
                tree = _pt.build(rows, _ll())
                _popt_cache.update(at=_t.time(), v=[{"label": t["label"], "items": [{"key": it["key"], "label": it["label"]}
                                                                                       for g in t["groups"] for it in g["progs"]]}
                                                     for t in tree if not t["unknown"]])
            except Exception:
                _popt_cache.update(at=_t.time(), v=[])
        return _popt_cache["v"]

    _units_cache: dict = {"at": 0.0, "v": []}

    def _business_units() -> list[str]:
        """프로젝트 만들기의 사업단 선택지 — 원본 보관소 최상위 폴더(10분 재사용)."""
        import time as _t
        if _t.time() - _units_cache["at"] > 600:
            from zzaimy.app import archive as _archive
            _units_cache.update(at=_t.time(), v=_archive.business_units(db))
        return _units_cache["v"]

    templates.env.globals["project_unit_options"] = _business_units   # 사이드바의 프로젝트 추가 창(_project_modal.html)

    def _criteria_docs(request: Request | None = None) -> list[dict]:
        counts = db.regulation_chunk_counts()
        # 기준 문서 목록도 열람 규칙 그대로 — 부서 제한 규정은 그 부서(와 추가 권한) 계정에만. 요청이 없으면 부르는 쪽이 거른다
        v = _req_viewer(request) if request is not None else None
        return [
            d | {"n_chunks": counts.get(d["id"], 0)}
            for d in db.list_documents("regulation")
            if d["status"] == "reviewed" and (v is None or _visible(d, **v))
        ]


    def _owned_chat(request, session_id):
        session = db.get_chat_session(session_id)
        if not session or session.get("owner") != request.state.user:
            raise HTTPException(404, "대화를 찾을 수 없습니다.")
        # 남의 프로젝트 안에서 연 내 대화 — 그 프로젝트를 「함께 작업」으로 받고 있을 때만 이어 쓴다. 공유가 풀리면 읽기만(10-21 보안 검토:
        # 공유를 거둬도 프로젝트 맥락으로 계속 묻지 못하게)
        if request.method == "POST" and session.get("project_id"):
            proj = db.get_project(int(session["project_id"]))
            if proj and proj.get("owner") != request.state.user and _project_access(request, proj) != "edit":
                raise HTTPException(403, "이 프로젝트의 함께 작업 공유가 풀려 이 대화를 이어 쓸 수 없습니다")
        return session

    def _can_read_chat(request, session: dict | None) -> bool:
        """남의 대화를 읽기 전용으로 볼 수 있는가 — 프로젝트 대화(업무 상담 제외)이고 비공개가 아니며, 그 프로젝트를 볼 수 있을 때
        (담당자·「함께 작업」·「읽기만」, 다른 부서 공유 포함). 관리자는 비공개가 아닌 것만. 쓰기(보내기·다시·지우기·문서 연결)는
        여전히 만든 사람만(_owned_chat). 질문과 답 글은 그대로 보이고, 답의 근거(출처 문서 링크·원문 조각·첨부)는 읽는 사람이 볼 수 있는
        것만 연다(_reader_doc_ok, chat_routes.chat_read) — 출처 문서 주소(/doc/{id})는 원래 열람 검사(access_policy)를 그대로 거친다."""
        if not session:
            return False
        me = "" if getattr(request.state, "view_as", None) else getattr(request.state, "user", "")
        if me and session.get("owner") == me:
            return True
        # 비공개는 만든 사람만 — 관리자도 읽지 않는다. 업무 상담(떠 있는 창)·프로젝트 밖 대화도 본인만
        if not session.get("project_id") or (session.get("channel") or "chat") != "chat" or session.get("is_private"):
            return False
        if getattr(request.state, "role", "") == "dev":
            return True
        proj = db.get_project(int(session["project_id"]))
        # 10-27 사용자 결정: 공유받은 사람은 담당자 범위를 다 덮지 않아도(다른 부서) 대화 글을 읽는다. 대신 근거 열람은 계속 막는다 —
        # 읽기 화면이 근거·첨부를 읽는 사람의 열람 범위로 거르고, 범위 밖은 「볼 수 없는 자료」 로 이름을 가린다(예전 _covers 관문을 대신함)
        return bool(proj) and bool(_project_access(request, proj))

    def _drive_doc_open(request, session: dict) -> bool:
        """남의 대화에 이은 구글 작업 문서를 프로젝트 화면 목록에 올려도 되는가 — 드라이브 권한(_share_plan)과 같은 규칙:
        쓴 사람 범위를 내가 다 덮고, 비공개가 아니며, 그 대화의 근거·첨부를 모두 내가 볼 수 있을 때. 구글 문서에는 근거 기록 없이
        범위 밖 내용이 섞일 수 있어 대화 읽기(근거만 거름)보다 좁다."""
        me = "" if getattr(request.state, "view_as", None) else getattr(request.state, "user", "")
        if me and session.get("owner") == me:
            return True
        if getattr(request.state, "role", "") == "dev" and me:
            return True
        return _covers(me, session.get("owner") or "") and _chat_open_to(session, _req_viewer(request))

    def _reader_doc_ok(request, doc_id) -> bool:
        """읽는 사람이 이 문서(근거·첨부)를 열 수 있는가 — 문서 화면(/doc/{id})과 같은 열람 규칙(access_policy.visible)."""
        try:
            d = db.get_document(int(doc_id))
        except (TypeError, ValueError):
            return False
        return d is not None and _visible(d, **_req_viewer(request))

    # 세션별 최근 검색 근거(연관 자료) — 채팅 사이드바에 노출한다.
    _chat_sources: dict[int, list] = {}
    # 떠 있는 창이 보낸 「지금 보는 화면」 경로 — 에이전트 상태에 넣는다(platform_guide.where_line)
    _chat_pages: dict[int, str] = {}

    def _record_run(session_id: int, message_id: int, kind: str, r=None, extra: dict | None = None) -> None:
        """답 실행 기록(answer_runs) — 어느 모델이, 얼마나 걸려, 어떤 근거·검색 단계로, 근거 확인 표시를 몇 개 붙여 썼나.
        베타 수집·모델 비교·느린 답 찾기에 쓴다. 기록이 실패해도 답은 남는다."""
        try:
            sess = db.get_chat_session(session_id) or {}
            timing = dict(getattr(r, "last_timing", None) or {})
            timing.update(extra or {})
            db.add_answer_run(message_id, session_id, sess.get("owner") or "", kind,
                              model=getattr(r, "last_model", None) or {}, timing=timing,
                              sources=[x.get("doc_id") for x in (getattr(r, "last_sources", None) or []) if isinstance(x, dict) and x.get("doc_id")]
                              + [d for d in (getattr(r, "last_grant_docs", None) or []) if d],
                              steps=list(getattr(r, "last_steps", None) or []), flagged=len(getattr(r, "last_flagged", None) or []))
        except Exception:
            pass

    def _answer_task_impl(
        session_id: int, q: str, stored: Path | None, criteria: list[int],
        web: str = "",
    ) -> None:
        # 학생 계정은 학생용 학사 규정 RAG 만 — 외부 검색·모델 지식·프로젝트·연결 문서를 쓰지 않는다(ADR-0052)
        _sess0 = db.get_chat_session(session_id) or {}
        is_student = password is not None and accounts.get(_sess0.get("owner") or "", {}).get("role") == "student"
        if is_student:
            web = ""
        # 모델 지식 모드: 검색·문서 없이 27B 가 학습한 지식으로만 답한다 — 밖으로 나가는 것이 없고, 출처 없음을 답 머리에 붙인다
        if web == "model":
            _chat_step(session_id, "모델 지식으로 답변 작성 중")
            from zzaimy.app import web_search

            try:
                _t_m = time.time()
                _mid = db.add_chat(session_id, "assistant", web_search.render_model(web_search.answer_from_model(q)))
                _record_run(session_id, _mid, "model", extra={"total_sec": round(time.time() - _t_m, 2)})
            except Exception as e:
                logging.getLogger("zzaimy.app.web").warning("모델 지식 답변 실패 (대화 %s): %s", session_id, type(e).__name__)
                db.add_chat(session_id, "assistant", f"모델 지식 답변을 만들지 못했습니다({_ko.reason(e)}). 잠시 뒤 다시 해 주세요.")
            return
        # 외부 검색 모드: 문서 작업이 아닌 일반 질문을 27B 가 웹 검색 결과로 답한다. 밖으로는 질문 글만 나간다
        if web:
            _chat_step(session_id, "웹 검색과 답변 작성 중")
            from zzaimy.app import web_search

            try:
                res = web_search.answer_with_web(q)
                _mid = db.add_chat(session_id, "assistant", web_search.render(res))
                _record_run(session_id, _mid, "web")
            except web_search.Blocked as e:
                db.add_chat(session_id, "assistant", str(e))
            except Exception as e:
                logging.getLogger("zzaimy.app.web").warning("외부 검색 실패 (대화 %s): %s", session_id, type(e).__name__)   # 질문 글은 남기지 않는다
                db.add_chat(session_id, "assistant", f"외부 검색을 쓰지 못했습니다({_ko.reason(e)}) — 검색 결과가 없는 것과 다릅니다. 잠시 뒤 다시 하거나 외부 검색을 끄고 물어 주세요.")
            return
        # 전송 직후 화면을 돌려주기 위해 무거운 단계(첨부 파싱·LLM)는 백그라운드에서
        attachment_text = None
        if stored is not None:
            _chat_step(session_id, "첨부 문서 읽는 중")
            try:
                attachment_text = processor.extract_text(stored)
            except Exception as e:
                log_note = f"({_ko.reason(e)})"
                db.add_chat(session_id, "assistant", f"첨부 문서를 읽지 못했습니다 {log_note}")
                return

        r = responder or _default_responder()
        # 프로젝트에 묶인 세션이면 지침·메모를 맥락으로, 연결 기준을 기본 근거로 쓴다
        session = db.get_chat_session(session_id)
        _chat_step(session_id, "대화·프로젝트 맥락 확인 중")
        project = None
        if session and session.get("project_id") and not is_student:
            project = db.get_project(int(session["project_id"]))
            if project and not criteria:
                criteria = db.get_project_criteria_ids(project["id"])
        # 권한 밖 질문 대처 — 판단은 규칙과 검색이 하고 모델은 결과를 말로 옮긴다
        # (docs/notes/2026-09-22-access-controlled-knowledge-base.md). 범위는 세션 주인의 계정에서 온다.
        from zzaimy.app import access_guard as ag

        owner = (session or {}).get("owner") or ""
        role, dept = _acct_role_dept(owner)
        data_dir = Path(db_path).parent
        # 대화에 연결된 구글 독스가 있으면 본문을 첨부처럼 붙인다. 못 읽으면 읽은 척하지 않고 그렇게 말한다.
        try:
            from zzaimy.app import chat_documents

            _chat_step(session_id, "연결 문서 확인 중")
            doc_material = "" if is_student else chat_documents.material(db, session_id, owner)
        except HTTPException:
            doc_material = ""                      # 이 계정의 대화가 아니면 연결을 쓰지 않는다
        except Exception as e:
            _chat_sources[session_id] = []
            db.add_chat(session_id, "assistant",
                        f"연결된 구글 문서를 읽지 못했습니다({_ko.reason(e)}). 계정 허용과 문서 주소를 확인해 주세요.")
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
        depts = sorted({*(d.get("dept") or "" for d in db.department_counts()), *_all_depts()})
        scope_msg = ag.scope_note(q, dept, role, depts, _extra_depts(owner) if role in _ap.STAFF_ROLES else ())
        if scope_msg:
            ag.audit(data_dir, owner, "scope", q, dept, role)
        criteria = ag.allowed_doc_ids(db, criteria, dept, role, owner, data_dir)
        scope = ag.search_scope(dept, role, "" if _view_as(owner) else owner, Path(db_path).parent,
                                grade=_gr.user_grade(accounts.get(owner), _view_as(owner)))
        # 대화에 구글 독스가 연결돼 있으면 답만 하지 않고 문서를 바로 고친다(gdocs_agent) — 명령 → 편집 계획 → 적용
        # 문서 작업 에이전트 — 안전 관문 뒤부터는 Writer 가 도구를 골라 가며 처리한다. 실패하면 아래 기존 경로로 물러난다
        if os.environ.get("ZZAIMY_DOC_AGENT", "1") != "0" and not is_student:
            if _run_doc_agent(session_id, q, owner, project, scope, scope_msg, data_dir, attachment_text, criteria, r):
                return
        # 아래 낱말 규칙 갈래(판독·문서 지목·초안·연결 문서 명령)는 모델이 돌지 못했을 때(_run_doc_agent 가 False)만 쓰는 대비 경로다.
        # 문서를 만들거나 고치는 갈래는 담당자 글에 그 뜻이 있을 때만 — 질문(「문서는 고치지 말고」)이면 답만 한다
        doc_change = _wants_doc_change(session_id, q)
        if _looks_like_reading(q):
            target = _project_doc_named(project, session_id, q)
            if target is not None:
                db.add_chat(session_id, "assistant", _read_named_document(session_id, target))
                _chat_sources[session_id] = []
                return
        if doc_change and not doc_material and _looks_like_working_on(q):
            # "작성서식으로 작업하자" 처럼 프로젝트에 있는 문서(한글 양식 등)를 지목하면 그 문서를 독스로 바꿔 잇는다
            picked = _project_doc_named(project, session_id, q)
            rivals = getattr(_project_doc_named, "rivals", []) or []
            if picked is not None and rivals:
                names = "\n".join(f"- {storage.title_of(d.get('filename') or '')}" for d in [picked, *rivals][:6])
                db.add_chat(session_id, "assistant", f"어느 문서로 작업할지 분명하지 않습니다. 아래에서 골라 주세요.\n{names}")
                _set_options(session_id, [{"kind": "pick", "text": f"{_ko.quote(_ko.clip(storage.title_of(d.get('filename') or ''), 28), '으로')} 작업",
                                           "question": f"{storage.title_of(d.get('filename') or '')}으로 작업하자"} for d in [picked, *rivals][:4]])
                _chat_sources[session_id] = []
                return
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
        if doc_change and not doc_material and _looks_like_drafting(q) and not _COMMON_ASKED.search(q):
            # 양식은 27B 가 고른다 — 코드는 후보(프로젝트 작성 서식·대화에서 정한 목차·공통 양식)만 모으고, 고른 것으로
            # 바로 작업한다. 다른 후보는 「바꾸기」 선택지로. 고른 기록은 학습 재료(form_choice_episodes.jsonl)
            from zzaimy.app import form_choice

            _chat_step(session_id, "양식 고르는 중")
            cands = _form_candidates(session_id, project, q)
            try:
                from zzaimy.generate.client import VllmClient
                fc_client = VllmClient(role="answer")
            except Exception:
                fc_client = None
            dec = form_choice.decide(fc_client, q, cands)
            chosen = next(c for c in cands if c["id"] == dec["choice"])
            form_choice.record(data_dir, {"session": session_id, "request": q[:1500], "candidates": [
                {"id": c["id"], "label": c["label"], "note": c["note"]} for c in cands], **dec,
                **({"llm": _model_info.stamp(fc_client)} if dec.get("by") == "model" else {})})
            if chosen["kind"] == "form":
                made, why = _link_existing_document(session_id, chosen["doc"], owner, project)
                if made:
                    doc_material = "existing"
                elif why:
                    db.add_chat(session_id, "assistant", why)
                    _chat_sources[session_id] = []
                    return
            else:
                made, why = _auto_link_document(session_id, q, owner, project, spec_override=chosen["spec"], title_override=dec.get("title") or "")
                if made:
                    doc_material = "new"
                elif why:
                    db.add_chat(session_id, "assistant", why)
                    _chat_sources[session_id] = []
                    return
            if made:
                why_txt = f" — {dec['why']}" if dec.get("why") else ""
                others = [c["label"] for c in cands if c["id"] != dec["choice"] and c["kind"] != "common"][:2]
                hint = (" 다른 양식으로 하려면 " + ", ".join(f"「{o}」" for o in others) + " 또는 「공통 양식으로 새 문서」라고 말해 주세요."
                        if others else " 다른 양식으로 하려면 「공통 양식으로 새 문서」 또는 서식 이름을 말해 주세요.")
                db.add_chat(session_id, "assistant", f"양식: 「{chosen['label']}」{why_txt}.{hint}")
                db.set_setting(f"chat_form_choice:{session_id}", _aj.dumps({"choice": dec["choice"], "alts": [
                    c["id"] for c in cands if c["id"] != dec["choice"]][:4]}))
        if doc_change and not doc_material and _looks_like_drafting(q):
            # 문서가 없는데 초안을 써 달라면 드라이브에 프로젝트 폴더·문서를 만들어 잇는다(주소 붙여넣기 없이)
            made, why = _auto_link_document(session_id, q, owner, project)
            if made:
                doc_material = "new"
            elif why:
                db.add_chat(session_id, "assistant", why)
                _chat_sources[session_id] = []
                return
        if doc_material and doc_change:
            _chat_step(session_id, "연결 문서 작업 중")
            try:
                _edit_linked_doc(session_id, q, owner, data_dir, scope, scope_msg)
            finally:
                # 편집·절 작성의 근거(기준 조각·프로젝트 문서·문서함 검색)도 답변처럼 남긴다 — 문서함의 「참고 문서」에 선다
                try:
                    chat_topics.record(session_id, _chat_sources.get(session_id) or [])
                except Exception:
                    pass
            return
        try:
            import inspect as _insp

            kw = dict(attachment_text=attachment_text, criteria_ids=criteria,
                      session_id=session_id, project=project)
            if "scope" in _insp.signature(r.answer).parameters:
                kw["scope"] = scope
            if "on_progress" in _insp.signature(r.answer).parameters:
                kw["on_progress"] = lambda label: _chat_step(session_id, label)
            if "on_partial" in _insp.signature(r.answer).parameters:
                kw["on_partial"] = lambda text: _chat_partial(session_id, text)
            _chat_step(session_id, "답변 준비 중")
            answer = r.answer(db, q, **kw)
        except Exception as e:
            from zzaimy.generate.client import describe_llm_error

            answer = describe_llm_error(e) + ". 검색된 근거 자료는 아래에 표시됩니다."
        answer = _scrub_internal(answer)                       # 답변에 남은 식별 정보는 한 번 더 가린다
        if scope_msg:
            answer = scope_msg + "\n\n" + answer
        # 근거(연관 자료)는 LLM 성공·실패와 무관하게 저장 — 검색은 CPU로 동작
        _chat_sources[session_id] = list(getattr(r, "last_sources", []) or [])
        try:
            chat_topics.record(session_id, _chat_sources[session_id])
        except Exception:
            pass                    # 근거 기록이 실패해도 답변은 남긴다
        mid = db.add_chat(session_id, "assistant", answer)
        if getattr(r, "last_reasoning", ""):                    # 답 앞의 단계 추론 — 화면의 「생각 과정」 에서 펼쳐 본다
            db.set_setting(f"chat_cot:{int(mid)}", r.last_reasoning[:6000])
        _record_run(session_id, mid, "rag", r)

    _DRAFT_EXPLICIT = re.compile(r"초안|보고서로|문서로\s*(?:만들|정리|작성|써)|공문으로")
    _DRAFT_NOUN = re.compile(r"계획서|보고서|공문|제안서|신청서|요청서|의견서|안내문|양식|보도자료|회의록|문서|초안")
    _DRAFT_VERB = re.compile(r"작성|써\s*줘|써줘|써\s*주|만들어|정리해|기안")

    def _looks_like_drafting(q: str) -> bool:
        """문서를 써 달라는 요청인가 — 질문·검토 요청과 구분한다(일반 낱말 단서, 특정 사례 없음).

        '초안'·'문서로 만들어' 같은 명시 단서가 있거나, 서류 갈래 낱말(계획서·공문 …)과 쓰기 동사가 함께 있을 때만.
        '접수된 문서가 무엇인지 정리해 줘' 처럼 있는 문서를 묻는 말은 문서를 만들지 않는다."""
        q = q or ""
        if _DRAFT_EXPLICIT.search(q):
            return True
        if not (_DRAFT_NOUN.search(q) and _DRAFT_VERB.search(q)):
            return False
        # 서류 갈래 낱말이 조사 '가·이·는·은' 을 달고 주어로 쓰였으면(문서가 무엇인지, 계획서는 어디에) 질문이다
        return not re.search(r"(?:계획서|보고서|공문|문서|양식)(?:가|이|는|은)\s", q)

    app.state.looks_like_drafting = _looks_like_drafting     # 테스트에서 판정 규칙을 그대로 검사한다

    _TITLE_CUT = re.compile(r"\s*(?:의\s*)?(?:초안|작성|써\s*줘|써줘|만들어|정리해|보고서로|문서로)")

    # 조각마다 길이 상한·사이 공백 필수 — 공백이 선택이면 긴 입력에서 나누는 경우의 수가 폭주한다(ReDoS)
    _LAST_SECTION_REF = re.compile(r"(?:방금|아까|직전에?|앞서|조금 ?전에?) ?(?:쓴|작성한|채운|고친)(?: \S{1,20}){0,4}? ?\S{0,20}?절|(?:이|그) ?절(?:을|를|도|만)? ")
    _OUTLINE_POINTER = re.compile(r"(?:위|앞|이|그|방금|정한|잡은|만든)\s*(?:의\s*)?(?:목차|구성|개요|틀)|목차\s*(?:대로|에\s*따라|에\s*맞춰|를\s*바탕)")
    _POINTER_WORDS = {"위", "이", "그", "저", "아래", "앞", "앞의", "위의", "방금", "지금", "이번", "해당", "말한", "정리한", "잡은", "만든",
                      "좋아", "좋아요", "좋습니다", "그래", "네", "응", "예", "오케이", "그럼", "자", "이제", "아니", "아니요", "아냐"}     # 맞장구·이음말(「좋아. 사업계획서」)
    _POINTER_TAIL = re.compile(r"(대로|따라|따라서|맞춰|맞춰서|맞게|바탕으로|기준으로)$")

    def _draft_title(q: str) -> str:
        """지시문에서 문서 제목을 추린다 — '2027년 사업계획서 초안을 써 줘. 절은…' → '2027년 사업계획서'."""
        head = (q or "").strip().splitlines()[0] if (q or "").strip() else ""
        m = _TITLE_CUT.search(head)
        cand = head[:m.start()] if m else head
        cand = re.sub(r"[\s,.:;·]+$", "", cand)
        cand = re.sub(r"(을|를|은|는|의|로|으로)$", "", cand).strip()
        # 앞 대화를 가리키는 말(「위 목차대로」「이 내용에 맞춰」「방금」)은 제목이 아니다
        words = [w for w in cand.split() if w.strip(".,!~?") not in _POINTER_WORDS and not _POINTER_TAIL.search(w.strip(".,!~?"))]
        cand = " ".join(words)
        return (cand or head or "새 문서")[:60]

    _WORK_WORDS = re.compile(r"작업|작성|수정|편집|채워|채우|고쳐|고치|써\s*(?:보|줘|주)|열어|열고|독스로|구글\s*독스")

    def _looks_like_working_on(q: str) -> bool:
        """있는 문서를 가지고 일하자는 말인가 — 서류 이름과 함께 작업·작성·수정·편집·열기 같은 낱말이 있을 때."""
        return bool(_WORK_WORDS.search(q or ""))

    _COMMON_ASKED = re.compile(r"공통\s*양식")
    _COMMON_PREFIX = "공통 양식으로 새 문서를 만들어 줘. "
    _FORM_TITLE = re.compile(r"서식|양식")

    def _project_forms(project: dict | None, session_id: int, q: str) -> list[dict]:
        """프로젝트·대화 첨부 가운데 요청한 서류 갈래의 작성 서식 — 제목에 서식·양식이 있고, 요청 갈래(gdocs_templates.PICK)의 낱말도 맞는 것.
        서류 갈래를 모르는 요청이면 제목에 서식·양식만 있으면 된다. 일반 낱말 규칙(사업 이름 규칙 없음)."""
        from zzaimy.ingest import gdocs_templates

        if not project:
            return []
        spec = gdocs_templates.pick(q)
        pats = dict(gdocs_templates.PICK).get(spec["id"], ()) if spec else ()
        cands = [db.get_document(i) for i in db.get_project_criteria_ids(int(project["id"]))]
        cands += db.list_documents(project["sector"], project_id=int(project["id"]))
        out, seen = [], set()
        for d in cands:
            if not d or d["id"] in seen:
                continue
            seen.add(d["id"])
            title = storage.title_of(d.get("filename") or "")
            if _FORM_TITLE.search(title) and all(re.search(p_, title) for p_ in pats[-1:]):
                out.append(d)
        return out

    def _project_doc_candidates(project: dict | None, session_id: int) -> list[dict]:
        """이 대화가 짚을 수 있는 문서 — 프로젝트 기준 문서·프로젝트 접수 문서·대화 첨부(같은 문서는 한 번)."""
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
        seen: set[int] = set()
        # 대화 주인의 열람 범위 밖 문서는 제목조차 모델·담당자에게 보이지 않는다(문서 화면·검색과 같은 규칙)
        return [d for d in cands if not (d["id"] in seen or seen.add(d["id"])) and _viewer_sees(session_id, d)]

    def _form_candidates(session_id: int, project: dict | None, q: str) -> list[dict]:
        """양식 후보(form_choice) 가운데 대화 주인이 볼 수 있는 것만 — 프로젝트 작성 서식도 열람 범위를 따른다."""
        from zzaimy.app import form_choice

        return [c for c in form_choice.candidates(db, project, db.list_chats(session_id), q)
                if c.get("kind") != "form" or _viewer_sees(session_id, c.get("doc") or {})]

    def _scoped_criteria(session_id: int) -> list[int]:
        """대화가 속한 프로젝트의 기준 문서 가운데 대화 주인이 볼 수 있는 것."""
        session_ = db.get_chat_session(session_id) or {}
        if not session_.get("project_id"):
            return []
        return [i for i in db.get_project_criteria_ids(int(session_["project_id"])) if _viewer_sees(session_id, db.get_document(i) or {})]

    app.state.scoped_criteria = _scoped_criteria

    def _session_viewer(session_id: int) -> tuple[str, str, str | None]:
        """대화 주인의 (계정, 역할, 부서) — _answer_task_impl 과 같은 규칙(인증 없이 띄운 앱은 관리자)."""
        owner = (db.get_chat_session(session_id) or {}).get("owner") or ""
        role, dept = _acct_role_dept(owner)
        return owner, role, dept

    def _viewer_sees(session_id: int, doc: dict) -> bool:
        from zzaimy.app.access_policy import visible

        owner, role, dept = _session_viewer(session_id)
        return visible(doc, **_ap.viewer(role, dept, "" if _view_as(owner) else owner, Path(db_path).parent))

    def _mentions_document(request: str, title: str, min_len: int = 4) -> bool:
        """담당자 요청 글이 이 문서를 스스로 짚었는가 — 공백·문장부호를 뺀 두 글이 min_len 자 이상 이어진 공통 부분을 갖고,
        그 부분이 흔한 갈래 낱말·숫자만으로 이뤄지지 않았을 때. 모델이 문서·검색 결과 속 글에 끌려 다른 문서로 옮겨 가지 않게 한다."""
        def norm(t: str) -> str:
            return re.sub(r"[\s\-_()\[\]{}.,:;·+/「」『』\"'<>]+", "", t or "").lower()
        a, b = norm(request), norm(title)
        if len(a) < min_len or len(b) < min_len:
            return False
        generic = sorted(_GENERIC_WORDS, key=len, reverse=True)

        def specific(sub: str) -> bool:
            rest = re.sub(r"\d+", "", sub)
            for w in generic:
                rest = rest.replace(w, "")
            return len(rest) >= 2
        # 가장 긴 공통 부분 문자열(동적 계획법) — 길이 min_len 이상인 공통 부분 가운데 하나라도 구체적이면 짚은 것
        prev = [0] * (len(b) + 1)
        for i in range(1, len(a) + 1):
            cur = [0] * (len(b) + 1)
            for j in range(1, len(b) + 1):
                if a[i - 1] == b[j - 1]:
                    cur[j] = prev[j - 1] + 1
                    if cur[j] >= min_len and specific(a[i - cur[j]:i]):
                        return True
            prev = cur
        return False

    app.state.mentions_document = _mentions_document

    def _project_doc_named(project: dict | None, session_id: int, q: str) -> dict | None:
        """질문이 지목한 프로젝트 문서 — 제목 낱말(2자 이상)의 6할 이상이 질문에 있으면 그 문서. 가장 많이 겹치는 것."""
        if not q:
            return None
        cands = _project_doc_candidates(project, session_id)
        norm_q = re.sub(r"[\s\-_()\[\]{}.,:;·+/]+", "", q).lower()
        best, best_score = None, 0.0
        scored: list[tuple[float, dict]] = []
        for d in cands:
            title = storage.title_of(d.get("filename") or "")
            # 날짜·판번호·붙임 번호(20260219, ver5, 붙임2)는 사람이 부르는 이름이 아니다 — 뜻 있는 낱말만, 글자 수로 잰다
            tokens = [t.lower() for t in re.split(r"[\s\-_()\[\]{}.,:;·+/]+", title)
                      if len(t) >= 2 and not re.fullmatch(r"(?:v|ver|rev)?\.?\d[\d.]*|붙임\d*|별첨\d*", t, re.I)]
            if not tokens:
                continue
            total = sum(len(t) for t in tokens)
            hit = sum(len(t) for t in tokens if t in norm_q)
            score = hit / total
            if score >= 0.5 and any(len(t) >= 3 and t in norm_q for t in tokens):
                scored.append((score, d))
                if score > best_score:
                    best, best_score = d, score
        # 비슷하게 맞는 후보가 둘 이상이면 짐작하지 않는다 — 호출 쪽이 되묻는다(고칠 문서는 정확해야 한다)
        rivals = [d for sc, d in scored if d is not best and sc >= best_score - 0.15 and d["id"] != (best or {}).get("id")]
        if best is None:
            # 사람은 긴 제목을 다 부르지 않는다("합본", "작성서식") — 한 문서에만 있는 뜻 있는 낱말 하나면 그 문서다
            owners: dict[str, list[dict]] = {}
            for d in cands:
                title = storage.title_of(d.get("filename") or "")
                for t in {t.lower() for t in re.split(r"[\s\-_()\[\]{}.,:;·+/]+", title) if len(t) >= 2}:
                    if t in norm_q and t not in _GENERIC_WORDS and not re.fullmatch(r"(?:v|ver|rev)?\.?\d[\d.]*[년월일]?|\d+학년도", t, re.I):
                        owners.setdefault(t, []).append(d)
            uniq = {t: ds[0] for t, ds in owners.items() if len(ds) == 1}
            if uniq:
                counts: dict[int, int] = {}
                picks: dict[int, dict] = {}
                for t, d in uniq.items():
                    counts[d["id"]] = counts.get(d["id"], 0) + 1
                    picks[d["id"]] = d
                top = max(counts.values())
                tops = [picks[i] for i, n in counts.items() if n == top]
                best = tops[0]
                rivals = tops[1:]                       # 서로 다른 문서를 가리키는 낱말이 같은 수면 되묻는다
                if len(tops) == 1:
                    rivals = []
        _project_doc_named.rivals = rivals   # type: ignore[attr-defined]
        return best

    _GENERIC_WORDS = {"사업", "계획", "계획서", "사업계획서", "문서", "자료", "지원", "지원사업", "전환", "중점", "전문대학", "대학", "학년도", "년도",
                      "양식", "서식", "붙임", "별첨", "최종", "수정", "제출", "안", "및", "관련"}

    app.state.project_doc_named = _project_doc_named

    def _link_existing_document(session_id: int, doc: dict, owner: str, project: dict | None) -> tuple[bool, str]:
        """프로젝트 문서의 구글 독스 변환본(한글이면 지금 변환)을 이 대화의 작업 문서로 잇는다."""
        from zzaimy.ingest import gdrive, gdrive_files

        if not gdrive.list_accounts():
            return False, ""
        if not _viewer_sees(session_id, doc):
            # 드라이브로 올리기 전 관문 — 대화 주인이 열람할 수 없는 문서는 어느 길로 골랐든 올리지 않는다
            return False, "열람 범위 밖 문서라 작업본을 만들 수 없습니다."
        acct_ = accounts.get(owner, {}) if password is not None else {}
        email = gdrive_files.doc_account_for(db, owner, acct_.get("dept") or None)
        if not email or not gdrive_files.has_file_scope(email):
            return False, gdrive_files.NEED_ACCOUNT
        title = storage.title_of(doc.get("filename") or "")
        try:
            folder = gdrive_files.project_folder_for(db, email, project, acct_.get("dept") or None, sub="첨부")
            made = gdrive_files.google_copy(db, doc, email, folder)
        except Exception as e:
            return False, f"{_ko.quote(title, '을')} 독스로 바꾸지 못했습니다({_ko.reason(e)})."
        if made.get("mime") != "application/vnd.google-apps.document":
            return False, f"{_ko.quote(title, '은')} 독스 문서가 아니라(시트·슬라이드·PDF) 에이전트가 편집할 수 없습니다. 열람은 문서함에서 됩니다."
        # 원본 서식(변환본)은 그대로 두고 복제본을 만들어 그 복제본에서 작업한다 — 프로젝트 폴더의 '작성' 아래
        from datetime import date as _date

        work_title = f"{title} 작업본 {_date.today().isoformat()}"
        try:
            work_folder = gdrive_files.project_folder_for(db, email, project, acct_.get("dept") or None, sub="작성")
            copy = gdrive_files.copy_document(email, made["id"], work_title, work_folder)
        except Exception as e:
            return False, f"{_ko.quote(title, '의')} 작업본을 만들지 못했습니다({_ko.reason(e)})."
        db.set_setting(f"chat_google_doc:{session_id}", _aj.dumps({"doc": copy["id"], "account": email}))
        db.add_file("google", copy["url"], name=work_title, session_id=session_id, doc_id=int(doc["id"]))
        db.add_chat(session_id, "assistant", f"{_ko.quote(title, '의')} 복제본 {_ko.quote(work_title, '을')} 만들어 이 대화에 연결했습니다. 원본 서식은 그대로 두고 이 복제본을 고칩니다. {copy['url']}")
        return True, ""

    def _auto_link_document(session_id: int, q: str, owner: str, project: dict | None,
                            spec_override: dict | None = None, title_override: str = "") -> tuple[bool, str]:
        """대화에 문서가 없을 때 드라이브 폴더(ZZAIMY/<연도>/<프로젝트|대화>)와 문서를 만들어 잇는다.

        돌려주는 것은 (만들었는가, 못 만들었을 때 담당자에게 보일 안내). 허용 계정이 없으면 안내 없이 일반 답변으로 간다.
        """
        from zzaimy.ingest import gdocs_templates, gdrive, gdrive_files

        if not gdrive.list_accounts():
            return False, ""
        # 제목은 양식을 고른 Writer 가 짓는다(「좋아. 사업계획서」 같은 말버릇을 낱말 목록으로 막는 데는 끝이 없다).
        # 못 지었을 때만 요청 문장에서 추린다
        title = title_override.strip() or _draft_title(q.removeprefix(_COMMON_PREFIX))
        kind_spec = spec_override or gdocs_templates.pick(q)
        if project and all(t in _GENERIC_WORDS or len(t) < 2 for t in re.split(r"\s+", title.strip()) if t):
            pname = project.get("name", "").strip()
            if kind_spec and title.strip() in ("문서", "자료", "새 문서", ""):
                # 「문서」만 남으면 양식 갈래로 — 「아니 문서」·「인공지능 문서」 대신 「인공지능 사업계획서」
                title = re.sub(r"\s*공통\s*양식.*$", "", kind_spec.get("title", "")).strip() or title
            # 「사업계획서」 만으로는 어느 사업 것인지 모른다 — 프로젝트 이름에 이미 그 낱말이 있으면 이름만(「… 사업계획서 사업계획서」 방지)
            title = (pname if title.replace(" ", "") in pname.replace(" ", "") else f"{pname} {title}").strip()[:60] or title
        try:
            acct_ = accounts.get(owner, {}) if password is not None else {}
            made = gdrive_files.auto_document(db, session_id, owner, title, project_name=(project or {}).get("name"),
                                              dept=acct_.get("dept") or None)
            _share_sync(project)                               # 새 작업 문서도 프로젝트 폴더의 부서 공유를 따른다
        except PermissionError as e:
            return False, f"{e} 그 뒤 다시 요청하면 문서를 만들어 바로 씁니다."
        except Exception as e:
            return False, f"문서를 만들지 못했습니다({_ko.reason(e)}). 원천 관리에서 구글 연결 상태를 확인해 주세요."
        note = ""
        spec = spec_override or gdocs_templates.pick(q)
        if spec_override is None and _OUTLINE_POINTER.search(q or ""):
            # 「위 목차대로 써 줘」 — 앞 답에서 정한 목차로 뼈대를 만든다(공통 양식은 같은 이름 절의 지침·표만 빌린다)
            from zzaimy.app.form_choice import latest_outline

            from_outline = latest_outline(db.list_chats(session_id))
            if from_outline:
                spec = from_outline
        if spec:                                              # 서류 갈래에 맞는 공통 양식(절·작성 지침·표)을 깔고 그 위에 쓴다
            _chat_step(session_id, "공통 양식 까는 중")
            try:
                from datetime import datetime as _now_dt

                from zzaimy.app import institution as _inst
                _uni = (_inst.facts(db).get("대학명") or "").strip()
                gdocs_templates.render(made["account"], made["doc"], spec, titled=True,
                                       fill={"○○대학교": _uni, "20○○. ○.": f"{_now_dt.now():%Y}. {_now_dt.now().month}."})
                # 공통 양식으로 만든 문서 — 한글 원본 서식이 없으니 내보내기는 Word 로(남의 서식에 붓지 않게)
                db.set_setting(f"chat_google_doc:{session_id}", _aj.dumps({"doc": made["doc"], "account": made["account"], "template": spec["id"]}))
                if spec.get("intro", "").startswith("대화에서 정한 목차"):
                    n_ch = sum(1 for b_ in spec["blocks"] if b_.get("h") == 1)
                    n_sec = sum(1 for b_ in spec["blocks"] if b_.get("h") == 2)
                    note = f" 대화에서 정한 목차(장 {n_ch}개·절 {n_sec}개)로 뼈대를 깔았습니다 — 회색 작성 지침은 다 쓴 뒤 지웁니다."
                else:
                    note = f" {_ko.quote(spec['title'].replace('(구글 독스)', '').strip(), '을')} 적용했습니다. 회색 작성 지침은 다 쓴 뒤 지웁니다."
            except Exception as e:
                logging.getLogger("zzaimy.app.gdocs").warning("공통 양식 깔기 실패 (대화 %s): %s", session_id, type(e).__name__)
                note = " 공통 양식은 깔지 못해 빈 문서로 시작합니다."
        db.add_chat(session_id, "assistant", f"드라이브에 문서 {_ko.quote(title, '을')} 만들어 이 대화에 연결했습니다.{note} {made['url']}")
        return True, ""

    _sparse_cache: dict[tuple, list[int]] = {}

    def _sparse_pages_of(doc: dict) -> list[int]:
        """글자 없는 쪽 목록 — 파일(경로·크기·수정 시각)로 캐시한다(137쪽 PDF 훑기가 1초 남짓, 대화 화면마다 돌면 느리다)."""
        fn = getattr(processor, "sparse_pages", None)
        if not fn or (doc.get("status") != "reviewed"):
            return []
        try:
            p = Path(doc["stored_path"])
            try:
                st = p.stat()
                key = (str(p), st.st_size, int(st.st_mtime))
            except OSError:
                key = (str(p), 0, 0)
            if key not in _sparse_cache:
                _sparse_cache[key] = list(fn(p))
            return _sparse_cache[key]
        except Exception:
            return []

    def _unread_pages(doc: dict) -> list[int]:
        """글자 없는 쪽 가운데 아직 판독하지 않은 쪽."""
        pages = _sparse_pages_of(doc)
        if not pages:
            return []
        done = set(db.vision_pages(int(doc["id"])))
        return [p for p in pages if p not in done]

    def _set_options(session_id: int, options: list[dict]) -> None:
        """이 답변에 딸린 선택지 — 담당자의 선택이 필요할 때 답 아래에 띄운다. 다음 질문이 오면 지운다."""
        db.set_setting(f"chat_options:{session_id}", _aj.dumps(options[:4], ensure_ascii=False) if options else "")

    _OLD_LABELS = ((re.compile(r"^이어서 다음 절 작성\s*\(남은 절 (\d+)개\)$"), r"다음 절 쓰기 (\1개 남음)"),
                   (re.compile(r"^방금 쓴 절 검토$"), "쓴 절 검토"),
                   (re.compile(r"^(.*?)\s*다시 써 줘$"), r"\1 다시 쓰기"),
                   (re.compile(r"^(.*?)\s*써 줘$"), r"\1 쓰기"))

    def _relabel_option(o):
        """예전에 저장된 선택지 단추 글을 지금 말투로 — 「다시 써 줘」 같은 말 대신 「다시 쓰기」(보낼 질문은 그대로)."""
        if not isinstance(o, dict) or not isinstance(o.get("text"), str):
            return o
        t = o["text"].strip()
        for pat, rep in _OLD_LABELS:
            if pat.search(t):
                t = pat.sub(rep, t).strip()
                break
        return {**o, "text": t or "다시 쓰기"}

    def _chat_suggestions(session_: dict | None, owner: str) -> list[dict]:
        """대화 안에 선택지로 띄우는 다음 작업 — 작업 맥락에서 입력이 필요할 때만.

        답변이 직접 낸 선택지(chat_options)만 보인다. 프로젝트·연결 문서 상태로 만든 고정 제안은 두지 않는다.
        질문 그대로 보낼 수 있게 question 을 함께 준다."""
        if not session_:
            return []
        raw = db.get_setting(f"chat_options:{session_['id']}", "") or ""
        try:
            opts = list(_aj.loads(raw) or []) if raw else []
        except Exception:
            opts = []
        # 아직 받지 못한 값이 있으면 「필요한 값 입력」 칸을 늘 다시 띄운다 — 답이 그 이름을 가리키는데 들어갈 곳이 없지 않게
        from zzaimy.app import asks as _asks_ui
        pend = _asks_ui.pending(db, int(session_["id"]))
        # 칸은 저장된 선택지를 쓰지 않고 지금의 받을 값·자료에서 찾은 확인 전 값으로 같은 함수(form_option)가 다시 만든다
        # — 미리 채운 값·출처 안내·「확인」 단추가 답을 만들 때와 같다
        # (받을 값이 없으면 저장된 옛 칸도 내리지 않는다)
        opts = [o for o in opts if not (isinstance(o, dict) and o.get("kind") == "form" and o.get("text") == "필요한 값 입력")]
        opts = [_relabel_option(o) for o in opts]
        if pend:
            opts = [_asks_ui.form_option(pend, _asks_ui.suggestions(db, session_))] + opts
        return opts[:4]

    def _project_suggestions(project: dict | None) -> list[dict]:
        """다음 작업 제안 — 무조건 돌리지 않고 알림처럼 내민다.

        양식이 있으면 '그 양식으로 작성 시작', 글자 없는 그림 쪽이 있는 접수 문서가 있으면 '그림 쪽 판독'."""
        if not project:
            return []
        out: list[dict] = []
        docs = db.list_documents(project["sector"], project_id=int(project["id"]))
        for d in docs:
            title = storage.title_of(d.get("filename") or "")
            if d.get("kind") == "form" and d.get("status") == "reviewed":
                out.append({"kind": "draft", "doc_id": d["id"], "text": f"{_ko.quote(title, '으로')} 작성을 시작할까요? 원본 서식은 두고 복제본에서 씁니다.",
                            "question": f"{title}으로 작업하자"})
        for d in docs:
            title = storage.title_of(d.get("filename") or "")
            pages = [p for p in _sparse_pages_of(d) if p not in set(db.vision_pages(int(d["id"])))]
            if pages:
                out.append({"kind": "read", "doc_id": d["id"], "text": f"{_ko.quote(title, '의')} {len(pages)}쪽은 그림이라 글이 없습니다. 판독해 둘까요? (쪽 {', '.join(map(str, pages[:8]))}{' …' if len(pages) > 8 else ''})",
                            "question": f"{title} 그림 쪽 판독해 줘"})
        return out[:6]

    _READ_WORDS = re.compile(r"판독|그림\s*쪽|이미지\s*쪽")

    def _looks_like_reading(q: str) -> bool:
        return bool(_READ_WORDS.search(q or ""))

    def _read_named_document(session_id: int, doc: dict) -> str:
        """지목한 문서의 그림 쪽을 지금 판독한다(대화의 배경 작업 안에서 돈다). 결과 문장을 돌려준다."""
        title = storage.title_of(doc.get("filename") or "")
        fn = getattr(processor, "read_image_pages", None)
        if not fn:
            return f"「{title}」 판독 기능이 없습니다."
        try:
            res = fn(db, int(doc["id"]))
        except Exception as e:
            return f"「{title}」 판독에 실패했습니다({_ko.reason(e)})."
        if not res.get("read"):
            return f"「{title}」 에 판독할 그림 쪽이 없거나 판독 모델이 꺼져 있습니다. {res.get('reason') or ''}".strip()
        return f"{_ko.quote(title, '의')} 그림 쪽 {res['read']}쪽(쪽 {', '.join(map(str, res['pages']))})을 판독해 조각 {res.get('chunks', 0)}개를 더했습니다. 이제 그 내용으로 작성을 요청할 수 있습니다."

    def _project_evidence(session_id: int, q: str, limit: int = 6) -> list[dict]:
        """대화가 속한 프로젝트의 접수 문서·첨부에서 명령과 낱말이 겹치는 조각 — 명사 겹침으로 고른다(어휘 검색과 같은 키).

        기준 문서는 find_relevant 가 맡고, 여기서는 합본·계획서·현황표 같은 접수 문서의 doc_chunks 를 본다. 표 조각은 평문(text)으로."""
        from zzaimy.app.regulations import extract_nouns

        session_ = db.get_chat_session(session_id) or {}
        pid = int(session_["project_id"]) if session_.get("project_id") else None
        proj = db.get_project(pid) if pid else None
        doc_ids: list[int] = []
        if proj:
            doc_ids += [d["id"] for d in db.list_documents(proj["sector"], project_id=pid) if d.get("status") == "reviewed"]
        doc_ids += [int(f["doc_id"]) for f in db.list_files(kind="attachment", session_id=session_id) if f.get("doc_id")]
        doc_ids = [d for d in dict.fromkeys(doc_ids) if _viewer_sees(session_id, db.get_document(d) or {})]   # 열람 범위 안만
        # 명령이 문서를 지목하면("합본의 내용으로") 그 문서만. 아니면 지금 고치는 작업본의 원본 서식은 뺀다(작성방법 상자가 근거로 잡힌다)
        named = _project_doc_named(proj, session_id, q)
        if named is not None and named["id"] in doc_ids:
            doc_ids = [named["id"]]
        else:
            sources = {int(f["doc_id"]) for f in db.list_files(kind="google", session_id=session_id) if f.get("doc_id")}
            doc_ids = [d for d in doc_ids if d not in sources]
        if not doc_ids:
            return []
        q_nouns = extract_nouns(q)
        if not q_nouns:
            return []
        titles = {did: storage.title_of((db.get_document(did) or {}).get("filename") or "") for did in dict.fromkeys(doc_ids)}
        scored: list[tuple[float, dict]] = []
        from zzaimy.app.chunk_path import attach_paths

        for did in dict.fromkeys(doc_ids):
            for ch in attach_paths(db.list_doc_chunks(did)):
                text = ch.get("content") or ""
                if ch.get("kind") == "table":
                    try:
                        text = _aj.loads(text).get("text") or text
                    except Exception:
                        pass
                if len(text) < 40:
                    continue
                overlap = len(q_nouns & extract_nouns(text[:1500]))
                if overlap >= 2:
                    where = ch.get("path_text") or f"{ch.get('page_no') or ''}쪽"        # 제목 계층(breadcrumb)이 있으면 그것이 자리다
                    scored.append((overlap / (1 + len(text) / 2000), {"reg_title": titles[did], "heading": where,
                                                                       "content": text[:900], "doc_id": did, "origin": "프로젝트 문서"}))
        scored.sort(key=lambda x: -x[0])
        return [h for _, h in scored[:limit]]

    def _working_copy_source(session_id: int, session_: dict, q: str, purpose: str) -> dict | None:
        """이 대화 작업본의 원본 서식(문서함 문서). 장부(files google → doc_id)에서, 없으면 프로젝트의 양식 문서(하나뿐일 때) 또는
        질문이 지목한 문서. 못 찾으면 안내를 남기고 None — 여럿이면 고르는 선택지를 낸다."""
        src_doc_id = next((int(f["doc_id"]) for f in db.list_files(kind="google", session_id=session_id) if f.get("doc_id")), None)
        src = db.get_document(src_doc_id) if src_doc_id else None
        if src is not None and not _viewer_sees(session_id, src):
            src = None                                      # 열람 범위 밖 원본 서식은 쓰지 않는다
        proj_ = db.get_project(int(session_["project_id"])) if session_.get("project_id") else None
        if not src and proj_:
            forms = [d_ for d_ in db.list_documents(proj_["sector"], project_id=int(proj_["id"]))
                     if d_.get("kind") == "form" and d_.get("status") == "reviewed" and _viewer_sees(session_id, d_)]
            if len(forms) == 1:
                src = forms[0]
            elif len(forms) > 1:
                _set_options(session_id, [{"kind": "pick", "text": f"{_ko.quote(_ko.clip(storage.title_of(d_.get('filename') or ''), 24), '으로')} 작성", "question": f"{_ko.quote(storage.title_of(d_.get('filename') or ''), '으로')} {q}"} for d_ in forms[:4]])
                db.add_chat(session_id, "assistant", f"어느 양식으로 {purpose}지 골라 주세요.")
                return None
            named = _project_doc_named(proj_, session_id, q)
            if named is not None:
                src = named
        if not src:
            db.add_chat(session_id, "assistant", f"이 작업본의 원본 서식(문서함 문서)을 찾지 못해 {purpose} 수 없습니다. 문서함에서 '이 문서로 작업'을 다시 골라 주세요.")
        return src

    app.state.working_copy_source = _working_copy_source

    _EDIT_CONSENT = r"고쳐|수정|바꿔|정리|지워|삭제|맞춰|넣어|빼|추가|편집"   # 문서를 바꾸는 도구 — 담당자 요청 글에 이 뜻이 있을 때만
    _EXPORT_CONSENT = r"내보내|한글\s*파일|hwp|제출본|완성본"
    _COPY_CONSENT = r"작업본"
    _REWRITE_CONSENT = r"다시|새로|고쳐|수정|바꿔|보완|보강"

    def _wants_doc_change(session_id: int, q: str) -> bool:
        """대비 경로가 문서를 만들거나 고치는 갈래로 가도 되는가 — 에이전트 도구·편집 계획(이름 바꾸기·옮기기)의 동의 관문과 같은 패턴.
        담당자가 그 뜻을 부정했으면(「문서는 고치지 말고」) 다른 낱말이 있어도 고치지 않는다. 아니면 질문으로 보고 답만 한다."""
        from zzaimy.app import asks as _asks
        from zzaimy.app import doc_agent, gdocs_agent

        pats = (_EDIT_CONSENT, _REWRITE_CONSENT, _EXPORT_CONSENT, _COPY_CONSENT, _WORK_WORDS.pattern,
                gdocs_agent._RENAME_CUE.pattern, gdocs_agent._MOVE_CUE.pattern)
        if any(re.search(p_, q or "") and not doc_agent.consented(p_, q) for p_ in pats):
            return False                                         # 고치지 말라는 말이 있다
        if any(doc_agent.consented(p_, q) for p_ in pats):
            return True
        pend = _asks.pending(db, session_id)
        return _looks_like_drafting(q) or bool(pend and _asks.parse_pairs(q, [a["name"] for a in pend]))

    _agent_notes: dict[int, str] = {}                      # 에이전트가 찾은 근거 — 절 쓰기 재료에 덧붙인다(대화별, 쓰기 동안만)

    _TOOL_LABELS = {"search": "근거 찾기", "read_found": "문서 읽기", "write_section": "절 쓰기", "hwp_write": "절 쓰기", "hwp_fill": "칸 채우기",
                    "hwp_forms": "양식 찾기", "hwp_start": "작업본 만들기", "hwp_form_create": "양식 만들기", "hwp_fields": "빈 자리 보기", "answer_question": "답하기",
                    "ask_user": "묻기", "finish": "마무리", "create_document": "문서 만들기", "hwp_style": "표 서식", "hwp_tables": "표 보기",
                    "delegate_section": "절 맡기기", "hwp_revise": "고쳐 쓰기"}

    def _tool_label(name: str) -> str:
        return _TOOL_LABELS.get(name, name or "생각")

    def _run_doc_agent(session_id: int, q: str, owner: str, project: dict | None, scope: dict, scope_msg: str, data_dir: Path,
                       attachment_text: str | None, criteria: list[int] | None, r) -> bool:
        """Writer 가 도구를 골라 요청을 처리한다(app/doc_agent). 처리했으면 True — 모델이 못 돌았고 아무것도 남기지 않았으면 False(기존 경로)."""
        import inspect as _insp

        from zzaimy.app import asks as _asks
        from zzaimy.app import chat_documents, doc_agent, drafting, form_choice, section_context
        from zzaimy.app.access_guard import reg_kwargs as _reg_kwargs
        from zzaimy.app.regulations import extract_nouns, find_relevant
        from zzaimy.generate.client import VllmClient
        from zzaimy.ingest import gdocs as _gd

        try:
            client = VllmClient(role="answer")
        except Exception:
            return False
        from zzaimy.app import llm_budget as _lb_agent
        try:
            _ctx_len_agent = _lb_agent.context_len(client)          # 대화 기억에 쓰는 몫은 이 모델의 문맥 한도 기준
        except Exception:
            _ctx_len_agent = _lb_agent.DEFAULT_CONTEXT
        before = len(db.list_chats(session_id))
        _before_id = max([int(m_["id"]) for m_ in db.list_chats(session_id, limit=1)] or [0])
        crit_ids = _scoped_criteria(session_id)
        mats_box: dict = {}

        def link() -> dict:
            return chat_documents.binding(db, session_id, owner) or {}

        # 편집기를 웹 한글 기안기로 고른 사람(또는 이미 한글 작업본이 이어진 대화) — 같은 흐름, 쓰기 도구만 한글(webhwp_agent)
        from zzaimy.app import webhwp_agent as _wa
        hw = (_wa.HwpTools(db, Path(db_path).parent, session_id, owner, (accounts.get(owner, {}) or {}).get("dept", "") if password is not None else "",
                           project, lambda: _project_doc_candidates(project, session_id))
              if (_webhwp.editor_of(db, owner) == "webhwp" or _wa.bound(db, session_id)) and not link().get("doc") else None)

        def mats():
            if "m" not in mats_box:
                sources = {int(f["doc_id"]) for f in db.list_files(kind="google", session_id=session_id) if f.get("doc_id")}
                mats_box["m"] = drafting.Materials(db, project, sources, find_relevant, extract_nouns,
                                                   db.chunks_for_docs(crit_ids) if crit_ids else [], visible=lambda d_: _viewer_sees(session_id, d_), scope=scope, criteria_ids=crit_ids,
                                                   sink=_chat_sources.setdefault(session_id, []))
            return mats_box["m"]

        def sections() -> list[dict]:
            lk = link()
            if not lk.get("doc"):
                return []
            try:
                return _gd.get(lk["account"], lk["doc"])["sections"]
            except Exception:
                return []

        def find_section(name: str) -> dict | None:
            name = (name or "").strip().strip("「」")
            secs = [x for x in sections() if x.get("index", 0) > 0]
            num = section_context.split_number(name)[0]
            for pred in (lambda x: x["heading"].strip() == name,
                         lambda x: bool(num) and section_context.split_number(x["heading"])[0] == num and name in x["heading"],
                         lambda x: bool(num) and section_context.split_number(x["heading"])[0] == num,
                         lambda x: name and name in x["heading"]):
                hit = next((x for x in secs if pred(x)), None)
                if hit:
                    return hit
            return None

        def state() -> str:
            lines = [f"프로젝트: {project['name'] if project else '(없음 — 일반 대화)'}"]
            docs = _project_doc_candidates(project, session_id)
            if project or docs:
                # 열거나 판독할 문서는 이 제목으로 짚는다(open_project_document·read_document). 양식은 작성 서식, 판독 대기는 그림 쪽이 남은 문서
                # 찾을 곳도 함께 — 기준 문서는 search source=criteria, 프로젝트 접수 문서는 source=project 로 찾힌다
                proj_ids = {int(d["id"]) for d in mats().past_docs()}
                rows_ = []
                for d in docs[:16]:
                    where = ("criteria" if int(d["id"]) in crit_ids else "project" if int(d["id"]) in proj_ids
                             else "양식" if d.get("kind") == "form" else "찾기 대상 아님")
                    tags = [where] + (["그림 쪽 판독 전"] if _unread_pages(d) else [])
                    rows_.append(f"「{storage.title_of(d.get('filename') or '')[:50]}」({', '.join(tags)})")
                lines.append("프로젝트 문서(괄호 안은 search 의 source): " + (", ".join(rows_) or "(없음)"))
                if project and not crit_ids:
                    lines.append("이 프로젝트에 이어진 기준 문서가 없다 — 공고·지침도 프로젝트 문서에 있으면 source=project 로 찾는다.")
            if attachment_text:
                lines.append(f"이번 메시지 첨부: 있음({len(attachment_text)}자)")
            lk = link()
            if hw is not None:
                lines += hw.state_lines()
            elif lk.get("doc"):
                secs = [x for x in sections() if x.get("index", 0) > 0]
                rows = [f"  {x['heading'][:50]} — {'쓸 수 있음' if drafting.writable(x) else '장 제목'}"
                        f"{', 비어 있음' if drafting.writable(x) and drafting.is_unfilled(x) else (', 작성됨' if drafting.writable(x) else '')}"
                        for x in secs[:70]]
                lines.append(f"연결된 문서: 있음({len(secs)}절)\n" + "\n".join(rows))
            else:
                cands = _form_candidates(session_id, project, q)
                lines.append("연결된 문서: 없음. 만들 때 고를 양식 후보:\n" + form_choice.render_candidates(cands))
            pend = _asks.pending(db, session_id)
            if pend:
                sug_ = _asks.suggestions(db, db.get_chat_session(session_id))

                def _tag(a_: dict) -> str:
                    k_ = _asks.norm_kind(a_.get("kind")) or "갈래 모름"
                    extra_ = (" · 자료 제안 있음, 담당자 확인 대기" if a_["name"] in sug_
                              else " · 이번 요청에서 자료에서 못 찾음 — 더 찾지 않는다" if a_["name"] in gave_up else "")
                    return f"{a_['name']}({k_}{extra_})"
                lines.append("아직 담당자에게 받지 못한 값: " + ", ".join(_tag(a) for a in pend[:10])
                             + " — 사실 값만 search 로 찾고, 결과에 그대로 있으면 바로 다음 걸음에 resolve_from_evidence 로 출처와 함께 남긴다. "
                               "결정 값(담당자가 정할 목표·계획)은 찾지 않고 묻는다. 방금 결과에 받지 못한 값의 글이 있으면 바로 resolve_from_evidence.")
            # 대화 기억 — 작업 메모(목표·결정·진행·남은 일) + 문맥 한도 몫만큼의 최근 대화(chat_memory). 지금 요청은 따로 들어간다
            from zzaimy.app import chat_memory as _cm
            from zzaimy.app.responder import strip_reasoning as _strip_r
            _last = db.list_chats(session_id, limit=1)
            memo_, hist = _cm.window(db, session_id, int(_ctx_len_agent * 0.5), exclude_ids={int(m_["id"]) for m_ in _last if m_.get("role") == "user"})
            if memo_:
                lines.append(_cm.block(memo_))
            if hist:
                lines.append("최근 대화(맥락일 뿐 근거가 아니다 — 여기의 사실을 쓰려면 search 로 다시 확인):\n" + "\n".join(f"  [{m_['role']}] {_strip_r(m_['content'] or '')[:600]}" for m_ in hist))
            return "\n".join(lines)

        notes: list[str] = []                                   # 에이전트가 찾은 근거 — 절 쓰기 재료로 넘긴다
        search_misses: dict[str, int] = {}                      # 값마다 이번 요청에서 찾았지만 못 찾은 횟수
        gave_up: set[str] = set()                               # 세 번 못 찾아 이번 요청에서는 더 찾지 않는 값
        found: list[dict] = []                                  # 같은 근거를 조각으로 — 질문 답에 첫 근거로 넘긴다
        found_docs: dict[int, dict] = {}                        # 이번 요청의 search 결과에 나온 문서 — read_found 가 읽을 수 있는 것만

        def asks_in(query: str) -> list[dict]:
            """이 검색어가 다루는 받지 못한 값 — 값 이름의 낱말이 검색어에 모두 있을 때(값마다 찾기 횟수를 세는 데 쓴다)."""
            out_ = []
            for a_ in _asks.pending(db, session_id):
                words_ = [w for w in a_["name"].split() if len(w) >= 2] or [a_["name"]]
                if all(w in query for w in words_) or sum(1 for w in _asks.ask_keys(a_["name"]) if len(w) >= 2 and w in query) >= 2:
                    out_.append(a_)
            return out_

        def t_search(a: dict) -> str:
            src, query = str(a.get("source") or "library"), str(a.get("query") or "").strip()
            if not query:
                return "query 가 비었다."
            targets = asks_in(query)
            if targets and all(_asks.norm_kind(x.get("kind")) == _asks.DECISION for x in targets):
                raise doc_agent.Retry("이 검색어는 결정 값(" + ", ".join(f"「{x['name']}」" for x in targets)
                                      + ")을 찾는다 — 결정 값은 담당자가 정하므로 자료에서 찾지 않는다. 쓰기·고쳐 쓰기 요청이면 그 값 자리만 「확인 필요」 로 두고 "
                                      "지금 근거로 절을 쓴다(hwp_write·delegate_section). 아니면 ask_user 로 묻거나 다른 걸음을 고른다.")
            if targets and all(x["name"] in gave_up for x in targets):
                raise doc_agent.Retry("이 값(" + ", ".join(f"「{x['name']}」" for x in targets)
                                      + ")은 이번 요청에서 세 번 찾아도 자료에 없었다 — 더 찾지 말고 다른 값으로 넘어가거나 ask_user·finish.")
            if src == "rules":
                hits = [{"title": h.get("reg_title") or "규정", "text": h.get("content") or ""}
                        for h in find_relevant(db, query, top_k=4, **_reg_kwargs(scope))]
            else:
                fn = {"project": mats().search_project, "criteria": mats().search_criteria, "library": mats().search_library}.get(src)
                if fn is None:
                    return "source 는 project·criteria·library·rules 가운데 하나."
                hits = fn(query)
            def _missed() -> None:
                for x in targets:
                    if _asks.norm_kind(x.get("kind")) != _asks.DECISION:
                        search_misses[x["name"]] = search_misses.get(x["name"], 0) + 1
                        if search_misses[x["name"]] >= 3:
                            gave_up.add(x["name"])
            if not hits:
                _missed()
                if src == "criteria":
                    return (f"「{query}」(criteria) — 찾지 못함. 이 프로젝트의 기준 문서에서 맞는 글이 없다"
                            + ("" if crit_ids else "(이어진 기준 문서가 없다)") + " — 공고·지침이 프로젝트 문서로 올라와 있으면 source=project 로 찾는다.")
                return f"「{query}」({src}) — 찾지 못함"
            body = "\n".join(f"《{h['title']}》{(' · ' + h['note']) if h.get('note') else ''}\n{h['text'][:500]}" for h in hits[:4])
            for h in hits[:4]:                                  # 이어 읽을 수 있는 문서 — 이번 검색(열람 범위 안)이 낸 것만
                if h.get("doc_id") is not None:
                    fd = found_docs.setdefault(int(h["doc_id"]), {"title": str(h.get("title") or "문서"), "chunks": [], "src": src})
                    if h.get("chunk_id") is not None:
                        fd["chunks"].append(int(h["chunk_id"]))
            # 모델에게 보일 결과에는 문서 번호를 붙인다(read_found 로 이어 읽을 때 짚는 데) — 절 쓰기 재료(notes)에는 싣지 않는다
            shown = "\n".join(f"《{h['title']}》{(' · ' + h['note']) if h.get('note') else ''}"
                              + (f" (문서 {h['doc_id']})" if h.get("doc_id") is not None else "")
                              + f"\n{h['text'][:500]}" for h in hits[:4])
            hint = doc_agent.match_hint(query, [h["text"][:500] for h in hits[:4]], extract_nouns)
            joined_ = re.sub(r"\s+", "", "\n".join(h["text"][:500] for h in hits[:4]))
            seen_asks = [x["name"] for x in _asks.pending(db, session_id)
                         if _asks.norm_kind(x.get("kind")) != _asks.DECISION and x["name"] not in gave_up
                         and sum(1 for w in _asks.ask_keys(x["name"]) if len(w) >= 2 and w in joined_) >= 2]
            if seen_asks and src in ("project", "criteria"):
                hint = (hint + "\n" if hint else "") + ("받지 못한 값과 겹치는 글이 있다: " + ", ".join(f"「{n_}」" for n_ in seen_asks)
                                                         + " — 결과에 값이 그대로 있으면 다음 걸음에 resolve_from_evidence.")
            targets[:] = [x for x in targets if x["name"] not in seen_asks]   # 결과에 그 값의 글이 없으면 못 찾은 것으로 센다
            _missed()
            notes.append(f"◇ {a.get('for') or query} — 검색 「{query}」({src})\n{body}")
            found.extend({"title": h.get("title") or "문서", "text": h.get("text") or "", "doc_id": h.get("doc_id"),
                          "note": h.get("note") or ""} for h in hits[:4])
            if src in ("project", "criteria"):                  # 값 채우기 근거는 이 프로젝트 문서의 결과만 — 문서함 전체는 아니다
                seen_hits.extend({"title": str(h.get("title") or "문서"), "text": h["text"][:500], "doc_id": h.get("doc_id"),
                                  "chunk_id": h.get("chunk_id")} for h in hits[:4])   # 모델에게 보인 글 그대로
            if any(len(h["text"]) > 500 for h in hits[:4] if h.get("doc_id") is not None):
                hint = (hint + "\n" if hint else "") + "잘린 결과가 있다 — 필요한 부분이 잘렸으면 read_found 로 그 문서를 이어 읽는다."
            return (hint + "\n" + shown) if hint else shown        # 관찰은 길이 상한에서 잘리므로 일치 단서를 앞에 둔다

        def found_texts(did: int) -> tuple[list[str], list[int]]:
            """찾은 문서의 조각 글(차례대로)과 조각 id — 검색이 조각을 건넬 때와 같은 글(접수 때 가린 조각, 표는 같은 평문 변환)."""
            from zzaimy.app import grant_search

            rows = [c for c in db.list_doc_chunks(did) if c.get("kind") in grant_search.TEXT_KINDS]
            if rows:
                return [grant_search._text(c) for c in rows], [int(c["id"]) for c in rows]
            regs = db.chunks_for_docs([did])                    # 규정 색인에 있는 기준 문서
            return ([(f"{c['heading']}\n" if (c.get("heading") or "").strip() else "") + str(c.get("content") or "") for c in regs],
                    [int(c["id"]) for c in regs])

        def pick_found(want: str) -> tuple[int | None, str]:
            """모델이 짚은 문서 — 이번 search 결과의 문서 번호나 《제목》. 결과에 없던 문서는 고를 수 없다."""
            def norm(t: str) -> str:
                return re.sub(r"[\s\-_()\[\]{}.,:;·+/「」《》]+", "", t or "").lower()
            names = ", ".join(f"《{v['title'][:40]}》(문서 {k})" for k, v in list(found_docs.items())[:8])
            m_ = re.fullmatch(r"\s*(?:문서\s*)?(\d+)\s*", want or "")
            if m_:
                did_ = int(m_.group(1))
                return (did_, "") if did_ in found_docs else (None, f"문서 {did_} 은(는) 이번 search 결과에 없다 — 결과에 나온 문서만 읽는다: {names}")
            w = norm(want)
            if not w:
                return None, f"document 가 비었다 — 이번 search 결과의 《제목》이나 문서 번호: {names}"
            exact = [k for k, v in found_docs.items() if norm(v["title"]) == w]
            hits_ = exact or [k for k, v in found_docs.items() if w in norm(v["title"]) or norm(v["title"]) in w]
            if len(hits_) == 1:
                return hits_[0], ""
            if hits_:
                return None, "맞는 문서가 여럿이다 — 문서 번호로 고른다: " + ", ".join(f"《{found_docs[k]['title'][:40]}》(문서 {k})" for k in hits_[:8])
            return None, f"「{want}」 은(는) 이번 search 결과에 없던 문서다 — 결과에 나온 문서만 읽는다: {names}"

        def t_read_found(a: dict) -> str:
            # 읽지 못한 경우는 Retry — 근거를 찾은 걸음으로 세지 않는다(먼저 찾기 관문을 건너뛰지 못하게)
            if not found_docs:
                raise doc_agent.Retry("이번 요청에서 search 로 찾은 문서가 없다 — 먼저 search 로 찾은 뒤 그 결과의 문서를 읽는다.")
            did, why = pick_found(str(a.get("document") or ""))
            if did is None:                                     # 문서 제목은 문서에서 온 글 — 안내가 아니라 자료로
                raise doc_agent.Retry("이번 search 결과에 나온 문서 하나를 골라야 읽는다 — 위 자료를 보고 《제목》이나 문서 번호로 다시 고른다.",
                                      data=why)
            doc = db.get_document(did)
            if not doc or not _viewer_sees(session_id, doc):    # 검색이 거른 범위를 한 번 더 — 그 사이 권한이 바뀌었을 수도
                raise doc_agent.Retry("열람 범위 밖 문서라 읽지 않는다.")
            fd = found_docs[did]
            # 한 요청에서 한 문서는 READ_PER_DOC 번까지 — 찾는 내용이 그 문서에 없을 때 조각을 하나씩 넘기며 걸음을 다 쓰지 않게
            fd["reads"] = fd.get("reads", 0) + 1
            if fd["reads"] > doc_agent.READ_PER_DOC:
                raise doc_agent.Retry(f"이 문서는 이번 요청에서 이미 {doc_agent.READ_PER_DOC}번 읽었다 — 찾는 내용이 여기 없으면 다른 걸음을 고른다"
                                      + ("(근거는 이만하면 됐다 — 이제 hwp_write 로 절을 쓴다)." if hw is not None else
                                         "(절을 쓰는 일이면 write_section 이 그 절의 작성방법·평가 기준·지난 자료를 모아 쓴다)."))
            texts, ids = found_texts(did)
            if not texts:
                raise doc_agent.Retry("고른 문서에 읽을 글이 없다 — 다른 걸음을 고른다.")
            note_ = ""
            try:
                start, line = int(a.get("from") or 0) - 1, max(0, int(a.get("line") or 1) - 1)
            except (TypeError, ValueError):
                start, line = -1, 0
            if start < 0 or start >= len(texts):
                start, line = -1, 0
                around = str(a.get("around") or "").strip()
                at = doc_agent.locate(texts, around) if around else None
                if at:
                    start, line = at
                    head_ = "\n".join(texts[start].split("\n")[:line])
                    if len(head_) < doc_agent.READ_CHARS // 2:     # 조각 머리(표 머리)부터 보여도 넉넉하면 조각 처음부터
                        line = 0
                else:
                    if around:
                        note_ = f"「{around}」 은(는) 이 문서에 없다 — 검색에 걸린 조각부터 보인다.\n"
                    pos = {cid: k for k, cid in enumerate(ids)}
                    start = min((pos[c] for c in fd["chunks"] if c in pos), default=0)
            text, end, nxt = doc_agent.read_window(texts, start, line)
            head = f"《{fd['title']}》 (문서 {did}) — 조각 {start + 1}~{end + 1} / 전체 {len(texts)}" + (f", {line + 1}째 줄부터" if line else "")
            tail = (f"뒤에 더 있음 · 다음 위치: read_found {{document: {did}, from: {nxt[0] + 1}" + (f", line: {nxt[1] + 1}" if nxt[1] else "") + "}"
                    if nxt else "문서 끝")
            # 찾은 근거로 기록 — 질문 답의 근거(먼저 읽은 것부터)·절 쓰기 재료·값 채우기 근거에 search 결과와 같은 길로
            notes.append(f"◇ {a.get('for') or fd['title']} — 이어 읽기 《{fd['title']}》\n{text}")
            piece, pieces = "", []
            for ln in text.split("\n"):                         # 답변기는 근거 하나를 1200자까지 읽는다 — 줄 경계로 나눠 싣는다
                if piece and len(piece) + len(ln) + 1 > 1100:
                    pieces.append(piece)
                    piece = ""
                piece += ("\n" if piece else "") + ln
            pieces.append(piece)
            found.extend({"title": fd["title"], "text": p_, "doc_id": did, "note": "", "read": True} for p_ in pieces if p_.strip())
            if fd["src"] in ("project", "criteria"):
                seen_hits.append({"title": fd["title"], "text": text, "doc_id": did, "chunk_id": ids[start]})
            return note_ + head + "\n" + text + "\n" + tail

        seen_hits: list[dict] = []                             # 이번 요청의 프로젝트·기준 문서 search 결과 — 자료로 값 채우기의 근거
        resolved_now: list[str] = []                           # 이번 요청에서 자료 제안으로 남긴 값
        run_id = f"{session_id}-{__import__('time').time_ns()}"                # 감사용 실행 id

        def t_resolve(a: dict) -> str:
            name, value, title = (str(a.get(k) or "").strip() for k in ("name", "value", "source_title"))
            sess_ = db.get_chat_session(session_id)
            allowed = {int(d["id"]) for d in mats().past_docs()} | {int(x) for x in crit_ids}
            why = _asks.resolve_from_evidence(db, sess_, name, value, title, seen_hits, allowed_docs=allowed, run=run_id)
            if why:
                cands = _asks.candidate_spans(name, seen_hits, title) if "그대로 없다" in why else []
                if cands:
                    why += " 아래 자료는 그 출처 결과 글에서 그대로 뗀 후보다 — 맞는 것을 글자 그대로 value 로 다시 낸다."
                raise doc_agent.Retry(why, data="\n".join(f"후보: {c}" for c in cands))
            # 확인 전 제안 — 「필요한 값 입력」 칸에 미리 채워 담당자가 「확인」 하거나 고친다. 그 전에는 담당자 값이 아니다
            _set_options(session_id, [_asks.form_option(_asks.pending(db, session_id), _asks.suggestions(db, sess_))])
            resolved_now.append(name)
            db.add_chat(session_id, "assistant", _scrub_internal(
                f"「{name}」 값을 자료에서 찾음: 《{title}》 — {value} (확인 전 — 「필요한 값 입력」에서 확인하거나 고쳐 주세요)"))
            return f"자료 제안으로 남겼다(담당자 확인 전) — {name} (《{title}》)"

        def t_read(a: dict) -> str:
            sec = find_section(str(a.get("section") or ""))
            return (sec.get("text") or "(빈 절)")[:1500] if sec else "그런 절이 없다 — 상태의 절 목록에서 고른다."

        def last_reply() -> str:
            ms = db.list_chats(session_id)
            return (ms[-1]["content"] if ms and ms[-1]["role"] == "assistant" else "")[:1500]

        def with_notes(fn, focus: str = ""):
            """에이전트가 찾은 근거를 절 쓰기 재료(gathered)에 덧붙여 fn 을 부른다 — 쓰는 동안만 둔다.
            focus(절 제목)를 주면 근거를 문장 단위로 그 절에 맞게 압축한다(app/delegate.compress — 리랭커가 없으면 원문 그대로)."""
            if notes:
                body_ = "\n".join(notes[-8:])
                if focus:
                    from zzaimy.app import delegate as _dg_c
                    body_, _ci = _dg_c.compress(focus, notes[-8:])
                    _chat_step(session_id, f"근거 압축 {_ci['before']}→{_ci['after']} 토큰" + (f"(원문 그대로: {_ci['note']})" if _ci["method"] != "rerank" else ""))
                _agent_notes[session_id] = "[에이전트가 찾은 근거 — 출처는 《 》 안의 문서 제목만]\n" + body_
            try:
                fn()
            finally:
                _agent_notes.pop(session_id, None)

        def run_doc(fn, focus: str = "") -> str:
            """연결 문서 작업 하나를 직접 부르고(모델이 고른 인자 그대로) 이번에 대화에 더해진 답을 관찰로 돌려준다.
            실패하면 문서는 그대로라고 대화에 남긴다."""
            if not link().get("doc"):
                return "연결된 문서가 없다 — create_document 나 open_project_document 로 먼저 잇는다."
            n0 = len(db.list_chats(session_id))
            try:
                with_notes(fn, focus)
            except Exception as e:
                from zzaimy.generate.client import describe_llm_error

                logging.getLogger("zzaimy.app.gdocs").exception("에이전트 문서 작업 실패 (대화 %s)", session_id)
                db.add_chat(session_id, "assistant", describe_llm_error(e) + ". 문서는 바꾸지 않았습니다.")
            added = [m_["content"] for m_ in db.list_chats(session_id)[n0:] if m_["role"] == "assistant"]
            return "\n".join(added)[:1500] or "(결과 없음)"

        def t_write(a: dict, redo: bool = False) -> str:
            """모델이 고른 절을 그대로 쓴다 — 쓸 절·다시 쓰기 여부를 명령 글로 만들어 낱말 규칙으로 되읽지 않는다."""
            sec = find_section(str(a.get("section") or "")) if link().get("doc") else None
            if link().get("doc") and sec is None:
                return "그런 절이 없다 — 상태의 절 목록에서 고른다."
            memo = str(a.get("notes") or a.get("reason") or "").strip()
            request = q + (f"\n에이전트 메모: {memo}" if memo else "")
            return run_doc(lambda: _draft_sections(session_id, owner, data_dir, scope, scope_msg, targets=[sec], redo=redo,
                                                   request=request, client=client),
                           focus=f"{sec['heading']} {memo}".strip() if sec else "")

        def t_review(a: dict) -> str:
            sec = find_section(str(a.get("section") or "")) if link().get("doc") else None
            if link().get("doc") and sec is None:
                return "그런 절이 없다 — 상태의 절 목록에서 고른다."
            return run_doc(lambda: _run_doc_command(session_id, owner, data_dir, scope, scope_msg, client=client, review_focus=sec,
                                                    command=f"「{sec['heading']}」 절을 평가지표·공고 기준으로 검토해 줘", request=q,
                                                    read_only=True))

        def t_edit(a: dict) -> str:
            instruction = str(a.get("instruction") or "").strip() or q
            return run_doc(lambda: _run_doc_command(session_id, owner, data_dir, scope, scope_msg, client=client,
                                                    command=instruction, request=q))

        def pick_document(title: str) -> tuple[dict | None, str]:
            """모델이 상태의 「프로젝트 문서」 에서 고른 제목 → 문서. 같은 제목이 먼저, 없으면 한쪽이 다른 쪽을 품는 제목이 하나뿐일 때."""
            def norm(t: str) -> str:
                return re.sub(r"[\s\-_()\[\]{}.,:;·+/「」]+", "", t or "").lower()
            want = norm(title)
            docs = _project_doc_candidates(project, session_id)
            if not want:
                return None, "document 가 비었다 — 상태의 프로젝트 문서 제목 가운데 하나."
            exact = [d for d in docs if norm(storage.title_of(d.get("filename") or "")) == want]
            hits = exact or [d for d in docs if want in norm(storage.title_of(d.get("filename") or ""))
                             or norm(storage.title_of(d.get("filename") or "")) in want]
            if len(hits) == 1:
                return hits[0], ""
            names = ", ".join(f"「{storage.title_of(d.get('filename') or '')[:40]}」" for d in (hits or docs)[:8])
            return None, (f"「{title}」 에 맞는 문서가 여럿이다: {names} — 정확한 제목으로 고르거나 ask_user 로 묻는다." if hits
                          else f"「{title}」 문서가 없다. 있는 문서: {names or '(없음)'}")

        def t_open(a: dict) -> str:
            doc, why = pick_document(str(a.get("document") or ""))
            if doc is None:
                return why
            if not _mentions_document(q, storage.title_of(doc.get("filename") or "")):
                # 담당자가 이 문서를 짚지 않았다 — 문서·검색 결과 속 글이 시킨 것일 수 있어 하지 않고 묻는다
                return ("담당자 요청 글이 이 문서를 짚지 않아 작업본을 열 수 없다 — ask_user 로 어느 문서로 할지 묻거나 다른 걸음을 고른다.")
            made, why = _link_existing_document(session_id, doc, owner, project)
            if made:
                return last_reply() or "작업본을 만들어 연결했다."
            return f"열지 못함: {why or '구글 연결 확인 필요'}"

        def t_read_doc(a: dict) -> str:
            doc, why = pick_document(str(a.get("document") or ""))
            if doc is None:
                return why
            if not _mentions_document(q, storage.title_of(doc.get("filename") or "")):
                # 담당자가 이 문서를 짚지 않았다 — 문서·검색 결과 속 글이 시킨 것일 수 있어 하지 않고 묻는다
                return ("담당자 요청 글이 이 문서를 짚지 않아 판독할 수 없다 — ask_user 로 어느 문서로 할지 묻거나 다른 걸음을 고른다.")
            text = _read_named_document(session_id, doc)
            db.add_chat(session_id, "assistant", text)
            return text

        def t_remember(a: dict) -> str:
            vals = a.get("values") if isinstance(a.get("values"), dict) else {}
            asked = {x["name"] for x in _asks.pending(db, session_id)}
            said = re.sub(r"\s+", "", q)
            # 물었던 값만, 담당자 글에 두 글자 이상 그대로 이어진 값만 받는다 — 모델이 값을 지어 넣거나 다른 이름으로 쓰지 않게
            given = {str(k).strip(): str(v).strip()[:_asks.VALUE_MAX] for k, v in vals.items()
                     if str(k).strip() in asked and len(re.sub(r"\s+", "", str(v))) >= 2 and re.sub(r"\s+", "", str(v)) in said}
            dropped = [str(k) for k in vals if str(k).strip() not in given]
            if not given:
                return ("받은 값이 없다 — 물었던 값(상태의 받지 못한 값)만, 담당자 요청 글에 있는 그대로 기억한다."
                        + (f" (받지 않은 것: {', '.join(dropped[:6])})" if dropped else ""))
            text = _remember_given(session_id, given)
            return text + (f"\n받지 않은 값(묻지 않은 이름이거나 요청 글에 없음): {', '.join(dropped[:6])}" if dropped else "")

        def t_create(a: dict) -> str:
            if link().get("doc"):
                return "이미 연결된 문서가 있다."
            cands = _form_candidates(session_id, project, q)
            chosen = next((c for c in cands if c["id"] == str(a.get("form") or "")), None)
            if chosen is None:
                return "form 은 상태의 양식 후보 id 가운데 하나여야 한다."
            form_choice.record(data_dir, {"session": session_id, "request": q[:1500], "by": "agent", "choice": chosen["id"],
                                          "title": str(a.get("title") or ""), "why": str(a.get("why") or ""),
                                          "candidates": [{"id": c["id"], "label": c["label"]} for c in cands]})
            if chosen["kind"] == "form":
                made, why = _link_existing_document(session_id, chosen["doc"], owner, project)
            else:
                made, why = _auto_link_document(session_id, q, owner, project, spec_override=chosen["spec"], title_override=str(a.get("title") or ""))
            return f"「{chosen['label']}」 양식으로 문서를 만들어 연결했다." if made else f"만들지 못함: {why or '구글 연결 확인 필요'}"

        answer_timing: dict = {}

        def t_answer(a: dict) -> str:
            kw = dict(attachment_text=attachment_text, criteria_ids=criteria, session_id=session_id, project=project)
            params = _insp.signature(r.answer).parameters
            if "scope" in params:
                kw["scope"] = scope
            if "on_partial" in params:
                kw["on_partial"] = lambda text: _chat_partial(session_id, text)
            if "evidence" in params and found:
                # 찾은 근거를 버리지 않고 답의 근거로 — 이어 읽은 글(잘린 결과의 나머지)을 앞에, 검색 조각은 뒤에
                ordered = [e for e in found if e.get("read")] + [e for e in found if not e.get("read")]
                kw["evidence"] = list({(e["title"], e["text"][:200]): e for e in ordered}.values())
            text = r.answer(db, str(a.get("question") or q), **kw)
            answer_timing.update(getattr(r, "last_timing", None) or {})    # 걸음 기록용 — 근거 찾기·생성 시간
            src = getattr(r, "last_sources", None)
            if src is not None:
                _chat_sources.setdefault(session_id, []).extend(src)
            mid_ = db.add_chat(session_id, "assistant", _scrub_internal((scope_msg + "\n\n" if scope_msg else "") + text))
            if getattr(r, "last_reasoning", ""):
                db.set_setting(f"chat_cot:{int(mid_)}", r.last_reasoning[:6000])
            return text[:600]

        def t_ask(a: dict) -> str:
            items = [{"name": str(x.get("name"))[:60], "hint": _ko.clip(str(x.get("why") or x.get("hint") or ""), 120),
                      "kind": _asks.norm_kind(x.get("kind"))}
                     for x in (a.get("items") or []) if isinstance(x, dict) and x.get("name")]
            if not items:
                return "items 가 비었다."
            pend = _asks.pending(db, session_id)
            have = {x["name"] for x in pend}
            _asks.set_pending(db, session_id, pend + [x for x in items if x["name"] not in have])
            _set_options(session_id, [_asks.form_option(_asks.pending(db, session_id), _asks.suggestions(db, db.get_chat_session(session_id)))])
            msg = str(a.get("message") or "자료에서 찾지 못한 값이 있어 여쭙니다 — 아래 「필요한 값 입력」에 적어 주세요.")
            db.add_chat(session_id, "assistant", _scrub_internal(msg + "\n" + ", ".join(x["name"] for x in items)))
            return "물었다."

        def t_finish(a: dict) -> str:
            msg = str(a.get("message") or "").strip()
            if msg:
                db.add_chat(session_id, "assistant", _scrub_internal(msg))
            return msg

        tools = [
            doc_agent.Tool("search", "근거 찾기. args {source: project|library|criteria|rules, query: 검색어(명사 2~6낱말), for: 무엇을 위해}", t_search,
                           gathers=True),
            doc_agent.Tool("read_found", "이번 search 결과에 나온 문서를 이어 읽기(맞는 문서를 찾았는데 결과가 잘려 표·목록의 나머지가 필요할 때). "
                           "args {document: 결과의 《제목》 또는 (문서 번호), around: 이어 볼 낱말이나 표 머리(선택), "
                           "from·line: 앞 결과가 알려 준 다음 위치(선택)}", t_read_found, gathers=True),
            doc_agent.Tool("read_section", "연결 문서의 절 글 읽기. args {section: 절 제목 또는 번호}", t_read),
            doc_agent.Tool("create_document", "연결 문서가 없을 때 양식을 골라 새 문서. args {form: 양식 후보 id, title: 문서 제목(사업 이름+서류 갈래), why}",
                           t_create, writes=True),
            doc_agent.Tool("write_section", "절 하나를 작성. 그 절의 양식 안내·작성방법 상자, 공고·평가 기준, 지난 사업 자료, 기관 정보, 담당자가 알려 준 값은 "
                           "이 도구가 모아 넘기고 절마다 필요한 정보도 따로 찾는다 — 작성 방법·서식을 미리 찾지 않아도 된다. "
                           "args {section: 절 제목, notes: 담당자 요청에서 이 절에 꼭 넣을 것·이번에 찾은 근거 요약}",
                           t_write, writes=True),
            doc_agent.Tool("open_project_document", "프로젝트 문서(작성 서식 등)의 작업본을 만들어 이 대화의 작업 문서로 잇기(담당자가 그 문서로 작업하자고 할 때). "
                           "args {document: 상태의 프로젝트 문서 제목}", t_open, writes=True),
            doc_agent.Tool("read_document", "프로젝트 문서의 글자 없는 그림 쪽을 판독해 검색 재료로 더하기(담당자가 판독을 청할 때). "
                           "args {document: 상태의 프로젝트 문서 제목}", t_read_doc, final=True),
            doc_agent.Tool("remember_values", "담당자가 이번 요청에서 알려 준 값(받지 못한 값 등)을 기억해 이후 절·기입란에 쓰기. "
                           "args {values: {이름: 담당자가 적은 값 그대로}}", t_remember),
            doc_agent.Tool("resolve_from_evidence", "받지 못한 값을 이번 search 결과에서 찾았을 때 출처와 함께 기억하기(값은 결과 글에 적힌 그대로). "
                           "args {name: 받지 못한 값 이름, value: 결과 글에 적힌 그대로 복사(띄어쓰기·기호까지, 바꿔 쓰지 않는다), source_title: 결과의 《제목》}", t_resolve),
            doc_agent.Tool("rewrite_section", "이미 쓴 절을 비우고 다시 작성(담당자가 다시 쓰라고 했을 때만). args {section, reason: 무엇을 고칠지}",
                           lambda a: t_write(a, redo=True), writes=True, consent=_REWRITE_CONSENT),
            doc_agent.Tool("review_section", "쓴 절을 공고·평가지표 기준으로 검토(문서는 고치지 않음). args {section}", t_review),
            doc_agent.Tool("edit_document", "절 단위가 아닌 문서 고치기(표 정리·문구 바꾸기·제목 변경 등, 담당자가 고치라고 했을 때만). args {instruction: 구체적 지시}",
                           t_edit, writes=True, consent=_EDIT_CONSENT),
            doc_agent.Tool("remove_guide_boxes", "양식의 안내 상자(작성방법·증빙자료) 지우기 — 처음엔 개수를 알리고 확인을 받으며, 담당자가 확인한 뒤에 지운다. args {}",
                           lambda a: run_doc(lambda: _remove_guide_boxes(session_id, owner, data_dir, confirmed=True)),
                           final=True, consent=_EDIT_CONSENT),
            doc_agent.Tool("export_hwp", "작업본을 한글(제출) 파일로 내보내기(담당자가 내보내라고 했을 때만). args {remove_boxes: 안내 상자를 뺀 제출본이면 true}",
                           lambda a: run_doc(lambda: _export_hwp(session_id, owner, data_dir, remove_boxes=a.get("remove_boxes") is True, request=q)),
                           final=True, consent=_EXPORT_CONSENT),
            doc_agent.Tool("new_work_copy", "작업본을 지금 변환기로 새로 만들기(담당자가 말했을 때만). args {}",
                           lambda a: run_doc(lambda: _new_work_copy(session_id, owner, data_dir, request=q)),
                           final=True, consent=_COPY_CONSENT),
            doc_agent.Tool("answer_question", "문서를 고치지 않고 질문에 답하기(이번 요청에서 search 로 찾은 근거로). "
                           "args {question}", t_answer,
                           final=True, needs_evidence=True),
            doc_agent.Tool("ask_user", "자료로 찾을 수 없어 담당자만 아는 값 묻기. args {items: [{name, why, kind: 사실(자료에 있을 수 있는 값) 또는 결정(담당자가 정할 목표·계획)}], message}", t_ask, final=True),
            doc_agent.Tool("finish", "이번 요청을 마치며 담당자에게 한 일과 다음 할 일 알리기. args {message}", t_finish, final=True),
        ]
        if hw is not None:
            # 한글 기안기 — 구글 문서 쓰기 도구를 한글 도구로 바꾼다(찾기·읽기·값 기억·묻기·답하기·마치기는 같다)
            from zzaimy.app import webhwp_form_create as _wfc       # 맞는 양식이 없을 때 양식 설계·만들기
            _docs_only = {"read_section", "create_document", "write_section", "open_project_document", "rewrite_section",
                          "review_section", "edit_document", "remove_guide_boxes", "export_hwp", "new_work_copy"}
            tools = [t_ for t_ in tools if t_.name not in _docs_only] + [
                doc_agent.Tool("hwp_forms", "쓸 한글 양식 찾기 — 이 프로젝트의 한글 양식(사업이 준 서식)이 먼저, 그다음 공문·국고 사업·학교 양식. args {query: 문서 갈래 낱말}", hw.forms),
                doc_agent.Tool("hwp_start", "고른 양식으로 한글 작업본을 만들어 이 대화에 잇기(담당자가 문서를 쓰자고 했을 때). args {form: hwp_forms 의 id, title: 문서 제목}",
                               hw.start, writes=True),
                doc_agent.Tool("hwp_form_create", _wfc.TOOL_DESC, hw.form_create, writes=True),
                doc_agent.Tool("hwp_fields", "작업본의 필드(절·칸·표)와 채움 상태 보기. args {}", hw.fields),
                doc_agent.Tool("hwp_fill", "칸·표 필드에 짧은 값 넣기(이름·날짜·금액·한 줄 요약 — 근거로 찾았거나 담당자가 알려 준 값만, 양식 예시 글자 ○○·000 은 넣지 않음). "
                               "여러 줄 글·표가 들어갈 큰 칸은 hwp_write 로. args {values: {필드 이름: 값}}", hw.fill, writes=True),
                doc_agent.Tool("hwp_write", "절 필드에 본문 쓰기 — 개조식 번호 문단으로 쓴다: 1) → 가) → (1) 단계, 한 줄에 한 항목, 문장 끝은 「~함」「~임」 "
                               "(서술형 「~하였다」 금지). 수치·일정·내역은 마크다운 표(| 머리 | … |, 머리글 구분 줄 포함)로, 표 바로 윗줄에 「<표> 표 제목」 을 두면 번호 붙은 표 제목이 된다. "
                               "꼭 알릴 참고·유의 사항은 「> 」 로 시작하는 줄로 쓰면 바탕 있는 상자로 들어간다(절마다 한두 개까지). "
                               "추진체계·절차·흐름은 도식 블록으로: ```도식 줄바꿈 후 줄마다 「상자 → 상자」(줄 사이는 아래 화살표로 이어짐, 상자 하나뿐인 줄은 상위 기관) ```. "
                               "예) 표: 「| 구분 | 내용 | 일정 |\\n|---|---|---|\\n| 1단계 | 교육과정 개발 | 3~6월 |」, "
                               "도식: 「```도식\\n총장\\n사업단장 → 운영위원회\\n교육팀 → 성과관리팀\\n```」. "
                               "상태의 필드 목록에 「작성방법 요구: 표·도식·출처」 가 붙은 절은 그 요소를 넣는다(근거가 있을 때). "
                               "담당자가 이번 요청에서 알려 준 사실과 search 로 찾은 근거만 쓰고 지어내지 않는다. 큰 칸(사업 추진계획 같은 요약 표의 본문 칸)도 field 에 칸 이름을 주면 그 칸 안에 쓴다. args {field: 절 또는 칸 필드 이름, text: 본문}", hw.write, writes=True),
                doc_agent.Tool("hwp_tables", "작업본의 표 목록(번호·크기·첫 행) — 서식을 고칠 표를 짚을 때. args {}", hw.tables),
                doc_agent.Tool("hwp_style", "표 서식 고치기(담당자가 표 모양을 바꿔 달라고 할 때). 주지 않은 값은 학교 표 기본(머리줄·라벨 칸 회색 #E5E5E5·굵게·가운데, "
                               "바깥 0.4mm·안쪽 0.12mm 실선, 10pt). args {tables: [hwp_tables 번호, 비우면 전부], head_fill: 색(#RRGGBB·회색·연파랑·없음), "
                               "label_fill, outer: 바깥 선 mm, inner: 안쪽 선 mm, head_bottom: 실선|이중선, font_pt, head_bold: true|false, "
                               "head_align·body_align: 가운데|왼쪽|오른쪽|양쪽}", hw.style, writes=True),
            ]
            from zzaimy.app import webhwp_edit_ops as _weo           # 기안기 편집 도구(페이지 설정·표 만들기·표 고치기·쪽 번호·…·열린 기안기 명령)
            tools += _weo.doc_tools(hw, doc_agent.Tool)
            _TOOL_LABELS.update(_weo.LABELS)
        if hw is not None:
            # 절 본문 관문(webhwp_agent.check_body)의 근거 — 이번 요청 글, 찾은 근거, 담당자가 이 대화에 쓴 글. 작업 메모는 맥락일 뿐 근거가 아니다
            hw.client, hw.request = client, q
            hw.doc_visible = lambda d_: _viewer_sees(session_id, d_)
            hw.form_search = lambda query_: list(mats().search_project(query_) or [])[:3] + (list(mats().search_criteria(query_) or [])[:3] if crit_ids else [])
            hw.evidence = lambda: [q, *notes, *[str(m_.get("content") or "") for m_ in db.list_chats(session_id, limit=400) if m_.get("role") == "user"]]
            from zzaimy.app import delegate as _dg
            if _dg.enabled():
                # 절 맡기기(ADR-0065 4번) — 맡길지는 에이전트가 상태 글(남은 문맥·빈 절·남은 단계)을 보고 정한다
                hw.delegating = True

                def _dg_search(query_: str) -> list[dict]:
                    hits_ = list(mats().search_project(query_) or [])[:3]
                    if crit_ids:
                        hits_ += list(mats().search_criteria(query_) or [])[:3]
                    return hits_

                # 서브 연결 묶음 — 검토 연결(서빙 장비 둘째) 위주로 동시 상한만큼, 메인 연결은 하나(메인은 맡기는 동안 기다린다).
                # 두 역할이 같은 서버·모델이면 그 연결 하나의 상한만큼. 동시 상한은 연결 설정 parallel(없으면 ZZAIMY_DELEGATE_PARALLEL)
                _pool = [(client, 1)]
                if _dg.parallel_on():
                    try:
                        _rc = VllmClient(role="review")
                        _same = (str(getattr(_rc.client, "base_url", "")) == str(getattr(client.client, "base_url", ""))
                                 and _rc.model == client.model)
                        _pool = [(client, _dg.conn_cap(client))] if _same else [(_rc, _dg.conn_cap(_rc)), (client, 1)]
                    except Exception:
                        _pool = [(client, _dg.conn_cap(client))]
                _dlg = _dg.Delegator(client, db, session_id, hw, q, search=_dg_search, notes=lambda: notes[-8:], pool=_pool)
                hw.delegate_cap = len(_dlg.slots())
                tools.append(doc_agent.Tool("delegate_section", _dg.TOOL_DESC, _dlg.run, writes=True))
                if _dg.revise_on():
                    # 고쳐 쓰기(ADR-0068) — 이미 쓴 절을 담당자 지시대로, 서브가 점검표를 만들고 코드가 셀 수 있는 것만 점검
                    hw.revising = True
                    _rev = _dg.Reviser(client, db, session_id, hw, q, search=_dg_search, notes=lambda: notes[-8:], pool=_pool)
                    tools.append(doc_agent.Tool("hwp_revise", _dg.REVISE_DESC, _rev.run, writes=True))
        def on_json(step_: dict) -> None:                       # 걸음 JSON 에 덧붙인 받지 못한 값의 갈래(한 번)
            if isinstance(step_.get("ask_kinds"), dict):
                _asks.set_kinds(db, session_id, step_["ask_kinds"])

        ep = doc_agent.run(client, q, state, tools, on_step=lambda label: _chat_step(session_id, label), on_json=on_json,
                           on_act=lambda n, a, ph: _chat_act(session_id, n, a, ph),
                           # 한글 기안기는 칸 채우기가 가볍고 여러 번이다 — 한 요청에서 작업본 만들기·칸·절을 끝까지 쓰게 한도를 넉넉히
                           # 서식이 길면(절 수십 개) 근거 읽기·칸·절 쓰기에 걸음이 많다 — 한글 작업본이 있을 때 한도를 절 수에 맞춰 늘린다
                           max_writes=(30 if hw is not None else doc_agent.MAX_WRITES),
                           **({"max_steps": hw.step_budget()} if hw is not None else {}))
        # 에이전트 걸음의 생각(thought)도 답과 함께 「생각 과정」 으로 — 걸음마다 [N단계: 도구] 생각
        try:
            new_msgs = [m_ for m_ in db.list_chats(session_id, limit=20) if m_.get("role") == "assistant" and int(m_["id"]) > _before_id]
            thoughts = [(s_.get("action") or "", str(s_.get("thought") or "").strip()) for s_ in ep.steps if s_.get("thought")]
            if new_msgs and thoughts:
                last_id = int(new_msgs[-1]["id"])
                prev = db.get_setting(f"chat_cot:{last_id}", "") or ""
                steps_txt = "\n".join(f"[{i + 1}단계: {_tool_label(a_)}] {t_}" for i, (a_, t_) in enumerate(thoughts[:20]))
                db.set_setting(f"chat_cot:{last_id}", (steps_txt + ("\n\n" + prev if prev else ""))[:8000])
        except Exception:
            pass
        doc_agent.record(data_dir, {"session": session_id, "request": q[:1500], "finished": ep.finished, "writes": ep.writes, "stop": ep.stop, "run": run_id, "steps": ep.steps,
                                    "llm": _model_info.stamp(client),
                                    "answer_timing": answer_timing, "graph_read": os.environ.get("ZZAIMY_GRAPH_READ", "0") == "1"})
        if not ep.finished and resolved_now:
            left_ = [x["name"] for x in _asks.pending(db, session_id) if x["name"] not in resolved_now]
            msg_ = ("이번 요청에서 자료로 찾은 값: " + ", ".join(f"「{n_}」" for n_ in resolved_now)
                    + " — 「필요한 값 입력」에서 확인해 주세요.")
            if gave_up:
                msg_ += " 자료에서 못 찾은 값: " + ", ".join(f"「{n_}」" for n_ in sorted(gave_up)) + "."
            rest_ = [n_ for n_ in left_ if n_ not in gave_up]
            if rest_:
                msg_ += " 남은 값: " + ", ".join(f"「{n_}」" for n_ in rest_) + " — 직접 적어 주시거나 다시 찾아 달라고 해 주세요."
            if not ep.writes:
                db.add_chat(session_id, "assistant", _scrub_internal(msg_))
        if not ep.finished and ep.writes and hw is not None and hw.fid():
            # 한글 작업본 — 쓴 절·칸, 남은 빈 절, 담당자가 알려 줘야 할 빈 칸을 알린다(긴 서식은 한 번에 다 못 쓴다)
            ok_ = [s_ for s_ in ep.steps if s_.get("action") in ("hwp_write", "hwp_fill", "delegate_section", "hwp_revise")
                   and str(s_.get("observation", "")).startswith(("「", "채웠다"))]
            fl_ = hw._fields()
            left_s = [x["name"][2:] for x in fl_ if x["kind"] == "절" and not x["text"]]
            left_c = [re.sub(r"^(칸|표\d+):", "", x["name"]) for x in fl_ if x["kind"] != "절" and not x["text"]]
            msg = f"이번 요청에서 절·칸 {len(ok_)}곳을 썼습니다."
            if left_s:
                msg += f" 남은 빈 절은 {len(left_s)}개이고, 다음은 「{left_s[0]}」입니다 — 「계속 써 줘」라고 하면 이어 씁니다."
            if left_c:
                msg += (f" 자료에 없어 비워 둔 칸 {len(left_c)}개(" + ", ".join(f"「{c_}」" for c_ in left_c[:6])
                        + (" 등" if len(left_c) > 6 else "") + ")는 값을 알려 주시면 채웁니다.")
            db.add_chat(session_id, "assistant", _scrub_internal(msg))
        elif not ep.finished and ep.writes:
            # 단계 한도에 닿아 마무리 없이 끝났다 — 쓴 절과 다음 할 일을 담당자에게 알린다(대화가 끊긴 것처럼 보이지 않게)
            done = [str(s_.get("args", {}).get("section") or "") for s_ in ep.steps
                    if s_.get("action") in ("write_section", "rewrite_section") and not str(s_.get("observation", "")).startswith(("그런 절", "담당자 요청에", "이번 요청의", "연결된 문서가 없다"))]
            left = [x for x in sections() if drafting.writable(x) and drafting.is_unfilled(x)]
            msg = "이번 요청에서 " + (", ".join(f"「{d}」" for d in done if d) or f"{ep.writes}개 절") + " 을 썼습니다."
            if left:
                msg += f" 남은 빈 절은 {len(left)}개이고, 다음은 「{left[0]['heading']}」입니다 — 「계속 써 줘」라고 하면 이어 씁니다."
            db.add_chat(session_id, "assistant", _scrub_internal(msg))
        if len(db.list_chats(session_id)) > before:
            return True                                          # 무엇이든 남겼다(쓰기 결과·답·물음)
        if ep.may_fall_back:
            return False                                         # 모델에 닿지 못했거나 쓸 수 없는 답 — 대비 경로로
        # 도구 실패·문맥 한도·단계 한도 — 낱말 규칙 경로로 문서를 건드리지 않고, 무엇이 안 됐는지 담당자에게 알린다
        db.add_chat(session_id, "assistant", _scrub_internal(doc_agent.failure_message(ep)))
        return True

    def _remember_given(session_id: int, given: dict) -> str:
        """담당자가 알려 준 값(물었던 값) — 기억해 두고(프로젝트 값·기관 정보) 남은 질문만 다시 띄운다. 지어내지 않고 물어서 받는 길.
        대화에 남긴 글을 돌려준다."""
        from zzaimy.app import asks as _asks

        session_ = db.get_chat_session(session_id) or {}
        pend = _asks.pending(db, session_id)
        # 기관 설정(대학명·총장 …)은 모든 사용자에게 쓰이는 값이라 관리자가 알려 줄 때만 바꾼다
        local_only = _asks.remember(db, session_, given, global_ok=_session_viewer(session_id)[1] == "dev")
        left_asks = [a for a in pend if a["name"] not in given]
        _asks.set_pending(db, session_id, left_asks)
        text = "기억했습니다 — " + ", ".join(f"{k}: {v}" for k, v in given.items()) + ". 이후 작성하는 절과 기입란에 넣습니다."
        if local_only:
            text += (f"\n{', '.join(local_only)} 은(는) 이 프로젝트(대화)의 값으로만 기억했습니다 — 기관 정보 설정은 관리자만 바꿉니다.")
        opts = []
        if left_asks:
            text += "\n아직 못 받은 값: " + ", ".join(a["name"] for a in left_asks)
            opts.append(_asks.form_option(left_asks, _asks.suggestions(db, session_)))
        last = db.get_setting(f"chat_last_section:{session_id}", "") or ""
        if last:
            opts.append({"kind": "redo", "text": f"{_ko.quote(_ko.clip(last, 18))} 다시 쓰기", "question": f"「{last}」 절을 알려 준 값을 반영해 다시 써 줘"})
        opts.append({"kind": "continue", "text": "다음 절 쓰기", "question": "다음 절을 작성 방법에 맞춰 작성해 줘"})
        _set_options(session_id, opts)
        text = _scrub_internal(text)
        db.add_chat(session_id, "assistant", text)
        return text

    def _draft_sections(session_id: int, owner: str, data_dir: Path, scope: dict, scope_msg: str, *, targets: list[dict], redo: bool,
                        request: str, client, info: dict | None = None) -> None:
        """정해진 절(targets)을 하나씩 쓴다 — 쓸 절과 다시 쓰기 여부는 부르는 쪽이 정해 넘긴다(에이전트는 모델이 고른 절, 물러난 경로는 낱말 규칙).

        절마다 Writer 가 찾을 것을 정하고(context_plan) 재료(작성방법·평가지표·지난 자료의 같은 절·기관 정보·서식 표 격자)를 모아
        gdocs_agent 로 쓴다. 근거에 없는 수치는 number_evidence 대조로 가린다. 결과·남은 빈 값·다음 선택지를 대화에 남긴다."""
        from zzaimy.app import asks as _asks
        from zzaimy.app import chat_documents, drafting, gdocs_agent
        from zzaimy.app.regulations import extract_nouns, find_relevant
        from zzaimy.ingest import gdocs as _gd

        link = chat_documents.binding(db, session_id, owner)
        session_ = db.get_chat_session(session_id) or {}
        crit_ids = _scoped_criteria(session_id)
        confirm = (db.get_setting(f"chat_google_doc_confirm:{session_id}", "") or "") == "1"
        if info is None:
            info = _gd.get(link["account"], link["doc"])
        try:
            # 이미 다룬 절은 연결 문서마다 둔다 — 대화 단위로 두면 문서를 새로 만든 뒤에도 옛 문서에서 다룬 절을 건너뛴다
            raw_skip = _aj.loads(db.get_setting(f"chat_skip_sections:{session_id}", "") or "[]")
            skip_ = set(raw_skip.get("heads", [])) if isinstance(raw_skip, dict) and raw_skip.get("doc") == link["doc"] else set()
        except ValueError:
            skip_ = set()
        proj_ = db.get_project(int(session_["project_id"])) if session_.get("project_id") else None
        sources = {int(f["doc_id"]) for f in db.list_files(kind="google", session_id=session_id) if f.get("doc_id")}
        mats = drafting.Materials(db, proj_, sources, find_relevant, extract_nouns, db.chunks_for_docs(crit_ids) if crit_ids else [], visible=lambda d_: _viewer_sees(session_id, d_), scope=scope, criteria_ids=crit_ids,
                                  sink=_chat_sources.setdefault(session_id, []))
        if drafting.is_event_record(info) and not mats.past_docs() and not sources:
            # 회의록·결과보고서는 이번 회의·회차 자료가 있어야 쓴다 — 자료 없이 절마다 27B 를 돌리면 「자료 필요」만 되풀이한다.
            # 필요한 자료를 한 번에 알리고 모델은 부르지 않는다
            need = drafting.needed_sources(info)
            text = ("이 문서는 한 번의 회의·운영을 적는 기록이라 이번 자료가 있어야 씁니다 — 문서함의 다른 회의·회차 값은 옮기지 않습니다.\n"
                    "필요한 자료: " + (", ".join(need[:10]) if need else "회의·운영 기록") +
                    "\n프로젝트에 올리거나 대화에 붙인 뒤 다시 「초안 써 줘」라고 하면 절마다 채웁니다.")
            db.add_chat(session_id, "assistant", _scrub_internal(text))
            return
        inst = _asks.facts(db, session_)
        parts_: list[str] = []
        pend_before = {a["name"] for a in _asks.pending(db, session_id)}   # 이번 차례 전에 이미 모은 빈 값 — 답마다 되풀이하지 않게
        all_ops: list[dict] = []
        for sec in targets:
            before_apply = None
            if redo and not drafting.family_unfilled(info, sec):        # 소제목에 나눠 쓴 절도 다시 쓰기면 비운다
                # 다시 쓰기 — 모델의 계획이 나온 뒤에 기존 초안을 비우고 넣는다(모델이 실패하면 문서는 그대로).
                # 모델에게는 비운 뒤의 모습(제목·작성방법 상자만)을 보인다.
                subs = drafting.subsections(info, sec)
                after = next((x for x in sorted(info["sections"], key=lambda s_: s_.get("start", 0)) if x.get("start", 0) > (subs[-1] if subs else sec).get("start", 0)), None)

                def before_apply(sec_=sec, subs_=subs, after_=after):
                    cleared = _gd.clear_section_body(link["account"], link["doc"], sec_["index"], user=owner, data_dir=data_dir,
                                                     end_index=after_["start"] if after_ else None,
                                                     keep_headings={x["heading"] for x in subs_})
                    return [f"기존 초안 {cleared['chars']}자를 지우고 다시 씀"] if cleared.get("chars") else []
                # 모델에게는 비운 뒤의 모습(제목·작성방법 상자·소제목 뼈대)을 보인다
                box_lines = [ln for ln in (sec.get("text") or "").split("\n") if ln.startswith(sec["heading"]) or "작성방법" in ln]
                sec = dict(sec, text="\n".join(box_lines) + "".join(f"\n[절 {x['index']}] {x['heading']}" for x in subs), chars=0, body_chars=0)
            else:
                sec = dict(sec, text=drafting.family_text(info, sec))     # 소제목 뼈대가 있으면 같이 보인다 — 어디에 넣을지는 모델이 정한다
            m = mats.for_section(info, sec, request, storage.title_of)
            cmd = f"「{sec['heading']}」 절을 작성방법에 맞춰 {'새로 ' if redo else ''}작성해 줘. 담당자 지시: {request}"
            if redo:
                try:
                    rv = _aj.loads(db.get_setting(f"chat_last_review:{session_id}", "") or "{}")
                except ValueError:
                    rv = {}
                if rv.get("heading") == sec["heading"] and rv.get("text"):
                    cmd += "\n반영할 검토 의견(보완할 점을 고쳐 쓴다 — 자료에 없는 값은 여전히 비우고 묻는다):\n" + rv["text"][:2000]
            refs = [{"title": p_["title"], "text": p_["text"][:4000]} for p_ in m["past"] if p_["how"] == "같은 절"]
            # 절에 양식 표가 있으면 격자를 재료에 붙인다 — 모델이 fill 로 칸을 채운다(격자가 없으면 표를 채우지 못한다)
            m["intent"] = drafting.conversation_intent(db.list_chats(session_id))
            # 찾을 것을 Writer 가 정한다(컨텍스트 구성 리즈닝) — 코드는 그 계획대로 검색만 한다
            from zzaimy.app import context_plan

            _chat_step(session_id, f"「{sec['heading'][:24]}」 필요한 자료 찾는 중")
            cp_ = context_plan.plan(client, doc_title=info.get("title", ""), heading=sec["heading"], guide=sec.get("text") or "",
                                    intent=m["intent"], project_docs=context_plan._doc_list(mats.past_docs(), storage.title_of),
                                    criteria_docs=context_plan._doc_list([db.get_document(i) for i in (crit_ids or [])], storage.title_of))
            found_ = context_plan.execute(cp_, search_project=mats.search_project, search_criteria=mats.search_criteria,
                                          search_library=mats.search_library)
            m["gathered"] = context_plan.render(cp_, found_) + ("\n\n" + _agent_notes[session_id] if _agent_notes.get(session_id) else "")
            context_plan.record(data_dir, {"session": session_id, "doc": link["doc"], "section": sec["heading"], "request": request[:800],
                                           "llm": _model_info.stamp(client),
                                           "plan": cp_, "found": [{**{k_: v_ for k_, v_ in n_.items() if k_ != "hits"},
                                                                   "hits": [{"title": h_["title"], "doc_id": h_.get("doc_id"), "chunk_id": h_.get("chunk_id")}
                                                                            for h_ in n_["hits"]]} for n_ in found_]})
            materials_ = drafting.render_materials(m, inst)
            asked_ = [a["name"] for a in _asks.pending(db, session_id)]
            if asked_:
                # 이미 담당자에게 묻고 있는 값 — 같은 뜻의 빈 값을 새 이름으로 또 묻지 않게(같은 이름이면 코드가 하나로 합친다)
                materials_ += "\n\n[이미 담당자에게 묻고 있는 값]\n" + "\n".join(f"- {n}" for n in asked_[-30:])
            try:
                grids_ = _gd.table_grids(link["account"], link["doc"], sec["index"], info=info)
                for x in drafting.subsections(info, sec):
                    grids_ += _gd.table_grids(link["account"], link["doc"], x["index"], info=info)
                if grids_:
                    materials_ += "\n\n" + _gd.render_table_grids(grids_)
            except Exception:
                logging.getLogger("zzaimy.app.gdocs").exception("표 격자 읽기 실패 (대화 %s)", session_id)
            fig_folder = None
            try:
                if proj_:
                    from zzaimy.ingest import gdrive_files as _gf2

                    acct_ = accounts.get(owner, {}) if password is not None else {}
                    fig_folder = _gf2.project_folder_for(db, link["account"], proj_, acct_.get("dept") or None, sub="그림")
            except Exception:
                fig_folder = None
            t_, o_ = gdocs_agent.run(db, session_id, owner, cmd, link, client=client, data_dir=data_dir, scrub=_scrub_internal,
                                     evidence=m["criteria"], number_evidence=mats.number_evidence(), confirm=confirm, materials=materials_,
                                     focus=sec, references=refs, before_apply=before_apply, figure_folder=fig_folder)
            # 담당자에게는 참고한 자료 이름만 보인다 — 「낱말 겹침」「기준 조각 0건」 같은 내부 말은 답에 넣지 않는다
            titles_ = list(dict.fromkeys(p_["title"] for p_ in m["past"] if p_.get("title")))
            used = ", ".join(f"「{t[:30]}」" for t in titles_[:4]) + (f" 외 {len(titles_) - 4}건" if len(titles_) > 4 else "")
            parts_.append(f"[{sec['heading'][:40]}]\n{t_}" + (f"\n참고한 자료: {used}" if used else ""))
            # 정답지(같은 절이 있는 완성본)와 견준 반영률 — 초안이 넣은 글 대 그 절의 핵심 사실(수치·고유명사)
            if refs and o_:
                draft_text = "\n".join(str(o.get("text") or "") for o in o_) + "\n" + "\n".join(
                    str(c.get("text") or "") for o in o_ if o.get("op") == "fill" for c in (o.get("cells") or []) if isinstance(c, dict))
                sc = drafting.score_against_reference(draft_text, "\n".join(r_["text"] for r_ in refs))
                if sc["ratio"] is not None:
                    miss = (" · 빠진 것: " + ", ".join(sc["missing"][:8])) if sc["missing"] else ""
                    parts_.append(f"완성본 대비 핵심 사실 반영 {sc['covered']}/{sc['total']} ({int(sc['ratio'] * 100)}%){miss}")
            all_ops += o_
            if not confirm:
                # 이 대화에서 다룬 절 — 쓴 것이 있든(근거 없는 칸은 비워 둠) 없든 「다음 절」은 그 뒤부터(같은 표를 두 번 채우지 않게).
                # 절 번호로 짚으면 다시 쓴다
                skip_.add(sec["heading"])
                db.set_setting(f"chat_skip_sections:{session_id}", _aj.dumps({"doc": link["doc"], "heads": sorted(skip_)}, ensure_ascii=False))
            info = _gd.get(link["account"], link["doc"])        # 다음 절의 위치는 방금 넣은 글 뒤로 밀렸다
        db.set_setting(f"chat_last_section:{session_id}", targets[-1]["heading"])
        pend = _asks.pending(db, session_id)
        new_ = [a for a in pend if a["name"] not in pend_before]
        if new_:
            older = len(pend) - len(new_)
            parts_.append("이번 절에서 자료에 없어 비워 둔 값 — " + ", ".join(f"{a['name']}({a['hint']})" if a.get("hint") else a["name"] for a in new_[:6])
                          + ". 알려 주시면 기억해 두고 이후 작성에 넣습니다." + (f" (앞 절에서 모은 빈 값 {older}개는 「필요한 값 입력」에서 이어서 받습니다)" if older else ""))
        elif pend:
            parts_.append(f"아직 비어 있는 값 {len(pend)}개는 「필요한 값 입력」에서 채울 수 있습니다.")
        text = "\n\n".join(parts_)
        left = [x for x in info["sections"] if drafting.writable(x) and drafting.is_unfilled(x) and x.get("heading") not in skip_]
        opts = []
        if pend:
            opts.append(_asks.form_option(pend, _asks.suggestions(db, db.get_chat_session(session_id))))
        # 단추만 보고 무엇을 어느 절에 하는지 알 수 있게 절 이름을 단추에 넣는다
        last_h = targets[-1]["heading"]
        short_h = _ko.clip(last_h, 18)
        if left:
            opts.append({"kind": "continue", "text": f"다음 절 쓰기 · {_ko.quote(_ko.clip(left[0]['heading'], 18))} ({len(left)}개 남음)",
                         "question": f"「{left[0]['heading']}」 절을 작성 방법에 맞춰 작성해 줘"})
        opts += [{"kind": "review", "text": f"「{short_h}」 검토", "question": f"「{last_h}」 절을 평가지표·공고 기준으로 검토해 줘"},
                 {"kind": "redo", "text": f"「{short_h}」 다시 쓰기", "question": f"「{last_h}」 절을 더 구체적인 수치와 근거로 다시 써 줘"}]
        _set_options(session_id, opts)
        text = _scrub_internal(text)
        if scope_msg:
            text = scope_msg + "\n\n" + text
        db.add_chat(session_id, "assistant", text)

    def _export_hwp(session_id: int, owner: str, data_dir: Path, *, remove_boxes: bool, request: str) -> None:
        """한글 완성본(ADR-0035) — 작업본의 절 본문을 원본 서식(hwpx)에 서식을 보존한 채 넣어 프로젝트 '작성' 폴더에 올린다.
        원본이 hwpx 가 아니거나 공통 양식 문서면 Word 로 낸다. remove_boxes 면 안내 상자를 뺀 제출본. 결과를 대화에 남긴다."""
        from zzaimy.app import drafting
        from zzaimy.app import chat_documents

        link = chat_documents.binding(db, session_id, owner)
        session_ = db.get_chat_session(session_id) or {}
        # 한글 완성본(ADR-0035): 작업본의 절 본문을 원본 서식(hwpx)에 서식을 보존한 채 넣어 프로젝트 '작성' 폴더에 올린다.
        # 협업은 독스에서, 최종본은 원본 서식으로 — 스크립트가 정답을 채우는 것이 아니라 에이전트·담당자가 쓴 작업본을 옮기는 것
        import tempfile
        from datetime import date as _date

        from zzaimy.ingest import gdocs as _gd, gdrive_files as _gf, hwpx_fill as _hf

        proj_ = db.get_project(int(session_["project_id"])) if session_.get("project_id") else None
        def _word_export(reason: str) -> str:
            """작업본을 Word(docx)로 같은 '작성' 폴더에 — 회색 작성 지침은 빼고(작업본엔 남김), 같은 이름의 옛 판은 휴지통."""
            try:
                info_ = _gd.get(link["account"], link["doc"])
                from zzaimy.ingest import gdrive as _gdr
                http_ = _gdr._http()
                r_ = http_.get(f"{_gdr.API}/files/{_gd.doc_id(link['doc'])}/export", headers=_gf._headers(link["account"], http_),
                               params={"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"})
                if r_.status_code != 200:
                    raise RuntimeError(f"내보내기 {r_.status_code}")
                title_ = info_["title"]
                name_ = f"{title_} 완성본 {_date.today().isoformat()}.docx"
                from zzaimy.ingest import gdocs_templates as _gt
                try:
                    data_, n_guides = _gt.strip_guides_docx(r_.content)       # 제출본엔 회색 작성 지침이 없어야 한다(작업본엔 남김)
                except Exception:
                    data_, n_guides = r_.content, 0
                acct_ = accounts.get(owner, {}) if password is not None else {}
                folder_ = _gf.project_folder_for(db, link["account"], proj_, acct_.get("dept") or None, sub="작성")
                _gf.trash_older(link["account"], folder_, f"{title_} 완성본 ", ".docx")
                up_ = _gf.upload_file(link["account"], data_, name_,
                                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document", folder_, reuse=False)
                db.add_file("google", up_["url"], name=name_, session_id=session_id)
                return (f"{reason} Word 파일 {_ko.quote(name_, '로')} 내보냈습니다"
                        f"{f'(회색 작성 지침 {n_guides}곳은 뺐고, 작업본에는 남아 있습니다)' if n_guides else ''} — 한글에서 열어 hwpx 로 저장할 수 있습니다. "
                        f"{up_['url']}")
            except Exception as e:
                logging.getLogger("zzaimy.app.gdocs").exception("Word 내보내기 실패 (대화 %s)", session_id)
                return f"Word 로 내보내지 못했습니다({_ko.reason(e)}). 작업본은 그대로입니다."

        if link.get("template"):
            # 공통 양식으로 쓴 문서 — 같은 정의로 만든 한글 공통 양식(webhwp_templates, 한글 문서함 「공통/공통 양식」)에 채운다.
            # 그 양식을 만들 수 없을 때(라이브러리·정의 문제)만 Word(docx)로 내보낸다
            from zzaimy.app import webhwp_templates as _wt
            tpl_path = None
            try:
                _wt.ensure_all(db, Path(db.path).parent)
                tpl_path = _wt.path_for(db, link["template"])
            except Exception:
                logging.getLogger("zzaimy.app.gdocs").exception("한글 공통 양식 준비 실패 (대화 %s)", session_id)
            if tpl_path is None:
                text = _word_export("이 문서는 공통 양식으로 만들어 한글 원본 서식이 없습니다.")
                if not text.startswith("Word 로 내보내지 못했"):
                    text += "\n제출 서식이 따로 있으면 그 서식(hwpx)을 프로젝트에 올리고 「서식으로 작성」으로 다시 쓰면 서식 그대로 채웁니다."
                db.add_chat(session_id, "assistant", _scrub_internal(text))
                return
            src = {"id": None, "stored_path": str(tpl_path), "receipt_no": "",
                   "filename": (session_.get("title") or tpl_path.stem.replace(" 공통 양식", "")) + ".hwpx"}
        else:
            src = _working_copy_source(session_id, session_, request, "한글 완성본을 만들")
        if not src:
            return
        src_path = Path(src.get("stored_path") or "")
        if src_path.suffix.lower() != ".hwpx" or not src_path.exists():
            # 옛 한글(hwp) 서식은 서식 보존 채우기를 못 한다 — 그 자리에서 Word 로 내보내고, hwpx 로 다시 올리는 길을 알린다
            text = _word_export(f"원본 서식 {_ko.quote(storage.title_of(src.get('filename') or ''), '이')} hwpx 가 아니라(또는 파일이 없어) "
                                "서식 보존 채우기를 할 수 없어,")
            text += ("\n서식 그대로의 한글 완성본이 필요하면 한글에서 그 서식을 'hwpx 로 저장'해 프로젝트에 올린 뒤 다시 「한글 파일로 내보내 줘」 라고 해 주세요.")
            db.add_chat(session_id, "assistant", _scrub_internal(text))
            return
        email = link["account"]
        remove = remove_boxes
        try:
            bodies = _gd.section_bodies(email, link["doc"])              # 빈 절도 함께 — 소제목의 부모를 알기 위해
            out_dir = Path(tempfile.mkdtemp(prefix="zz-fill-"))
            rep = _hf.fill(src_path, bodies, out_dir / "완성본.hwpx", remove_boxes=remove)
            data = (out_dir / "완성본.hwpx").read_bytes()
            title = storage.title_of(src.get("filename") or "")
            name = f"{title} 완성본 {_date.today().isoformat()}.hwpx"
            storage.save_generated(db, data, ref=src.get("receipt_no") or f"대화-{session_id}", kind="한글 완성본", ext="hwpx",
                                   doc_id=int(src["id"]) if src.get("id") else None, session_id=session_id)
            acct_ = accounts.get(owner, {}) if password is not None else {}
            if _webhwp.editor_of(db, owner) == "webhwp":
                # 편집기를 웹 한글 기안기로 고른 사람 — 부서 한글 문서함에 두고 기안기 링크를 준다(드라이브에는 올리지 않는다)
                fid_ = _webhwp.add(db, Path(db.path).parent, acct_.get("dept") or _webhwp.COMMON, name, data,
                                   owner=owner, source=f"대화-{session_id}")
                where = f"부서 한글 문서함({acct_.get('dept') or _webhwp.COMMON})에 두었습니다 — 기안기로 열기: /hwp/{fid_}/edit"
                if not _webhwp.status()["ok"]:
                    where += " (지금은 기안기 서버에 연결되지 않아 내려받기만 됩니다)"
            else:
                folder = _gf.project_folder_for(db, email, proj_, acct_.get("dept") or None, sub="작성")
                try:                                        # 같은 서식의 옛 완성본은 휴지통으로 — 드라이브에 판이 쌓이지 않게(되돌릴 수 있음)
                    _gf.trash_older(email, folder, f"{title} 완성본 ", ".hwpx")
                except Exception:
                    logging.getLogger("zzaimy.app.gdocs").warning("옛 완성본 정리 실패 (대화 %s)", session_id)
                up = _gf.upload_file(email, data, name, "application/hwp+zip", folder, reuse=False)
                db.add_file("google", up["url"], name=name, session_id=session_id, doc_id=int(src["id"]) if src.get("id") else None)
                where = up["url"]
            text = (f"{_ko.quote(name, '을')} 만들었습니다 — 원본 서식에 작업본의 절 {len(rep['filled'])}개(새 문단 {rep['paragraphs']}·새 표 {rep['tables']}"
                    f"·서식 표 채움 {rep['tables_updated']})를 서식 그대로 넣었습니다. 서식에 이미 있던 글·표 {rep['existing_kept']}건은 그대로 두었습니다. {where}")
            if rep["folded"]:
                text += f"\n서식에 없는 소제목 {len(rep['folded'])}개는 바로 앞 절의 본문으로 이어 넣었습니다(" + ", ".join(h[:16] for h in rep["folded"][:5]) + ("…" if len(rep["folded"]) > 5 else "") + ")."
            real_skipped = [h for h in rep["skipped"] if drafting.has_words(h)]   # '2cm' 같은 변환 찌꺼기 제목은 알릴 것이 아니다(쓸 절 판정과 같은 기준)
            if real_skipped:
                text += "\n서식에서 제목을 찾지 못한 절: " + ", ".join(h[:24] for h in real_skipped)
            if rep["duplicates"]:
                text += f"\n작업본에 같은 문단·표가 두 번 있는 곳 {len(rep['duplicates'])}건은 앞의 것만 넣었습니다 — 작업본에서 정리해 주세요: " + "; ".join(rep["duplicates"][:3])
            if rep.get("checks"):
                text += (f"\n자가 점검에서 문제 {len(rep['checks'])}건이 나왔습니다 — 한글에서 열리지 않거나 모양이 틀릴 수 있어 제출하지 마세요: "
                         + "; ".join(rep["checks"][:3]))
            if remove:
                text += f"\n안내 상자 {rep['boxes_removed']}개를 뺀 제출본입니다. 한글에서 열어 표지·직인란·쪽수를 확인하세요."
            else:
                text += "\n안내 상자(【작성방법】)는 그대로 두었습니다. 제출본이면 '안내 상자 없이 한글로 내보내 줘'."
            _set_options(session_id, [{"kind": "review", "text": "작업본 전체 검토", "question": "문서 전체를 공고·평가지표 기준으로 검토해 줘"},
                                      {"kind": "continue", "text": "다음 절 쓰기", "question": "다음 절을 작성 방법에 맞춰 작성해 줘"}])
        except Exception as e:
            logging.getLogger("zzaimy.app.gdocs").exception("한글 완성본 만들기 실패 (대화 %s)", session_id)
            text = f"한글 완성본을 만들지 못했습니다({_ko.reason(e)}). 작업본은 그대로입니다."
        db.add_chat(session_id, "assistant", _scrub_internal(text))
        return
    def _new_work_copy(session_id: int, owner: str, data_dir: Path, *, request: str) -> None:
        """작업본 새로 만들기 — 원본 서식을 지금 변환기로 다시 변환해 새 복제본을 만들고, 지금까지 쓴 절 본문을 옮긴다."""
        from zzaimy.app import chat_documents

        link = chat_documents.binding(db, session_id, owner)
        session_ = db.get_chat_session(session_id) or {}
        # 작업본 새로 만들기: 변환기가 좋아진 뒤 원본 서식을 다시 변환해 새 복제본을 만들고, 지금까지 쓴 절 본문을 옮긴다
        from zzaimy.ingest import gdocs as _gd, gdrive_files as _gf

        proj_ = db.get_project(int(session_["project_id"])) if session_.get("project_id") else None
        src = _working_copy_source(session_id, session_, request, "새 작업본을 만들")
        if not src:
            return
        acct_ = accounts.get(owner, {}) if password is not None else {}
        email = link["account"]
        try:
            folder = _gf.project_folder_for(db, email, proj_, acct_.get("dept") or None, sub="첨부")
            made = _gf.google_copy(db, src, email, folder, refresh=True)
            from datetime import date as _date

            title = storage.title_of(src.get("filename") or "")
            work_title = f"{title} 작업본 {_date.today().isoformat()}"
            work_folder = _gf.project_folder_for(db, email, proj_, acct_.get("dept") or None, sub="작성")
            copy = _gf.copy_document(email, made["id"], work_title, work_folder)
            moved = _gd.migrate_bodies(email, link["doc"], copy["id"], user=owner, data_dir=data_dir, scrub=_scrub_internal)
            from zzaimy.app.gdocs_agent import quality_pass

            checked = quality_pass(email, copy["id"])            # 옮긴 뒤 표 폭·반복 줄 관문
            try:
                _gf.rename_document(email, link["doc"], f"{_gd.get(email, link['doc'])['title']} (이전)")
            except Exception:
                pass
            db.set_setting(f"chat_google_doc:{session_id}", _aj.dumps({"doc": copy["id"], "account": email}))
            db.add_file("google", copy["url"], name=work_title, session_id=session_id, doc_id=int(src["id"]))
            ok = [r for r in moved if r["done"] == "ok"]
            text = (f"{_ko.quote(work_title, '을')} 새로 만들어 이 대화에 연결했습니다(원본 서식을 지금 변환기로 다시 변환). "
                    f"옮긴 절 {len(ok)}개 · 글 {sum(r['chars'] for r in ok):,}자 · 표 {sum(r['tables'] for r in ok)}개. 이전 작업본은 이름 뒤에 '(이전)'을 붙여 두었습니다. {copy['url']}")
            skipped = [r for r in moved if r["done"] != "ok"]
            if skipped:
                text += "\n옮기지 못한 절: " + ", ".join(r["heading"][:24] for r in skipped)
            if checked:
                text += "\n" + "\n".join(checked)
            _set_options(session_id, [{"kind": "review", "text": "옮긴 내용 검토", "question": "옮긴 절들이 빠짐없이 들어갔는지 검토해 줘"},
                                      {"kind": "continue", "text": "다음 절 쓰기", "question": "다음 절을 작성 방법에 맞춰 작성해 줘"}])
        except Exception as e:
            logging.getLogger("zzaimy.app.gdocs").exception("작업본 새로 만들기 실패 (대화 %s)", session_id)
            text = f"작업본을 새로 만들지 못했습니다({_ko.reason(e)}). 이전 작업본은 그대로 연결돼 있습니다."
        db.add_chat(session_id, "assistant", _scrub_internal(text))
        return
    def _remove_guide_boxes(session_id: int, owner: str, data_dir: Path, *, confirmed: bool) -> None:
        """양식의 안내 상자(【작성방법】·【증빙자료】) 지우기 — 되돌릴 수 없어 두 차례로 한다. 먼저 몇 개인지 세어 묻고(대기 표시),
        대기 표시가 있고 confirmed 일 때만 지운다."""
        from zzaimy.app import chat_documents

        from zzaimy.ingest import gdocs as _gd

        link = chat_documents.binding(db, session_id, owner)

        sure = confirmed and (db.get_setting(f"chat_pending_boxes:{session_id}", "") or "") == "1"
        if not sure:
            cnt = _gd.remove_instruction_boxes(link["account"], link["doc"], user=owner, data_dir=data_dir, dry_run=True)["count"]
            if cnt:
                db.set_setting(f"chat_pending_boxes:{session_id}", "1")
                text = f"작업본에 안내 상자(【작성방법】·【증빙자료】)가 {cnt}개 있습니다. 지우면 되돌릴 수 없고, 아직 안 쓴 절의 작성방법도 사라집니다. 지울까요?"
                _set_options(session_id, [{"kind": "confirm", "text": f"안내 상자 {cnt}개 삭제 확인", "question": "안내 상자 삭제 확인, 지워 줘"},
                                          {"kind": "skip", "text": "아직 두기", "question": "안내 상자는 아직 두고 다음 절을 작성해 줘"}])
            else:
                text = "지울 안내 상자가 없습니다."
            db.add_chat(session_id, "assistant", _scrub_internal(text))
            return
        db.set_setting(f"chat_pending_boxes:{session_id}", "")
        res = _gd.remove_instruction_boxes(link["account"], link["doc"], user=owner, data_dir=data_dir)
        text = f"안내 상자 {res['count']}개를 지웠습니다. 제출 전에 표지의 대학명·직인란과 쪽수를 확인하세요."
        _set_options(session_id, [{"kind": "review", "text": "전체 검토", "question": "문서 전체를 공고·평가지표 기준으로 검토해 줘"}])
        db.add_chat(session_id, "assistant", _scrub_internal(text))
        return
    def _command_evidence(session_id: int, command: str, scope: dict) -> tuple[list[dict], list[dict]]:
        """연결 문서 명령의 근거 — 프로젝트 기준 문서(없으면 문서함) 조각과 프로젝트 접수 문서·첨부 조각. 대화의 근거 목록도 새로 둔다."""
        from zzaimy.app.regulations import find_relevant

        crit_ids = _scoped_criteria(session_id)
        try:
            if crit_ids:
                hits = find_relevant(db, command, top_k=5, chunks=db.chunks_for_docs(crit_ids), **scope)
            else:
                hits = find_relevant(db, command, top_k=5, **scope)
        except Exception:
            hits = []
        # 프로젝트의 접수 문서(완성된 합본·지난 계획서·현황표)와 이 대화의 첨부도 근거다 — 양식을 채울 재료는 거기 있다
        proj_hits: list[dict] = []
        try:
            proj_hits = _project_evidence(session_id, command, limit=6)
            hits = proj_hits + hits
        except Exception:
            pass
        _chat_sources[session_id] = [{
            "title": h.get("reg_title") or "문서", "heading": h.get("heading") or "",
            "snippet": (h.get("content") or "")[:160], "doc_id": h.get("doc_id"), "origin": "교내 규정",
            "weak": bool(h.get("weak_evidence")),
        } for h in hits[:4]]
        return hits, proj_hits

    def _run_doc_command(session_id: int, owner: str, data_dir: Path, scope: dict, scope_msg: str, *, command: str, client,
                         review_focus: dict | None = None, request: str | None = None,
                         evidence: tuple[list[dict], list[dict]] | None = None, read_only: bool = False) -> None:
        """연결 문서에 명령 하나를 편집 계획으로 적용한다(gdocs_agent). review_focus 가 있으면 그 절을 재료와 함께 검토한다.
        결과·다음 선택지를 대화에 남기고, 계획에 이름 바꾸기·프로젝트 옮기기가 있으면 대화에도 반영한다."""
        from zzaimy.app import asks as _asks
        from zzaimy.app import chat_documents, drafting, gdocs_agent
        from zzaimy.app.regulations import extract_nouns, find_relevant
        from zzaimy.ingest import gdocs as _gd

        request = request if request is not None else command
        link = chat_documents.binding(db, session_id, owner)
        session_ = db.get_chat_session(session_id) or {}
        crit_ids = _scoped_criteria(session_id)
        # read_only(에이전트의 검토) — 편집 계획이 나와도 문서에 쓰지 않고 보류만 한다(담당자가 「적용」 해야 쓴다). 검토는 동의 관문이 없는 도구다
        confirm = read_only or (db.get_setting(f"chat_google_doc_confirm:{session_id}", "") or "") == "1"
        hits, proj_hits = evidence if evidence is not None else _command_evidence(session_id, command, scope)
        review_mat = ""
        if review_focus is not None:
            # 절 검토: 그 절을 재료(작성방법·평가지표·지난 자료 같은 절)와 함께 준다(긴 문서의 앞부분만 보지 않게)
            try:
                info = _gd.get(link["account"], link["doc"])
                review_focus = next((x for x in info["sections"] if x["heading"] == review_focus["heading"]), review_focus)
                proj_ = db.get_project(int(session_["project_id"])) if session_.get("project_id") else None
                sources = {int(f["doc_id"]) for f in db.list_files(kind="google", session_id=session_id) if f.get("doc_id")}
                mats = drafting.Materials(db, proj_, sources, find_relevant, extract_nouns, db.chunks_for_docs(crit_ids) if crit_ids else [], visible=lambda d_: _viewer_sees(session_id, d_), scope=scope, criteria_ids=crit_ids)
                mm = mats.for_section(info, review_focus, request, storage.title_of)
                review_mat = drafting.render_materials(mm, _asks.facts(db, session_))
                hits = mm["criteria"] or hits
            except Exception:
                review_focus, review_mat = None, ""
        extra_kw = {"materials": review_mat, "focus": review_focus} if review_focus is not None else {}
        text, _ops = gdocs_agent.run(db, session_id, owner, command, link, client=client, data_dir=data_dir,
                                     scrub=_scrub_internal, evidence=hits, confirm=confirm, **extra_kw)
        if not _ops and not proj_hits:
            session_ = db.get_chat_session(session_id) or {}
            proj_ = db.get_project(int(session_["project_id"])) if session_.get("project_id") else None
            reads = [sg for sg in _project_suggestions(proj_) if sg["kind"] == "read"
                     and _viewer_sees(session_id, db.get_document(int(sg["doc_id"])) or {})]
            if reads:
                text += "\n\n재료가 될 그림 쪽이 아직 판독되지 않았습니다. 아래에서 고르면 바로 진행합니다."
                _set_options(session_id, [{"kind": "read", "text": sg["text"][:40], "question": sg["question"]} for sg in reads[:2]]
                             + [{"kind": "skip", "text": "판독 없이 지금 있는 내용으로", "question": request}])
        if review_focus is not None and (not _ops or read_only):
            # 절 검토 뒤 — 의견을 기억해 두고 「반영해 다시 쓰기」로 잇는다(검토 뒤 선택지가 비면 흐름이 끊긴다)
            db.set_setting(f"chat_last_review:{session_id}", _aj.dumps({"heading": review_focus["heading"], "text": text[:2500]}, ensure_ascii=False))
            _set_options(session_id, [{"kind": "redo", "text": f"{_ko.quote(_ko.clip(review_focus['heading'], 18))} 검토 의견 반영해 다시 쓰기",
                                       "question": f"「{review_focus['heading']}」 절을 방금 검토 의견을 반영해 다시 써 줘"},
                                      {"kind": "continue", "text": "다음 절 쓰기", "question": "다음 절을 작성 방법에 맞춰 작성해 줘"}])
        if _ops and not read_only:
            _set_options(session_id, [{"kind": "continue", "text": "다음 절 쓰기", "question": "작업본에서 아직 비어 있는 다음 절을 근거 문서 내용으로 채워 줘"},
                                      {"kind": "review", "text": "방금 쓴 내용 검토", "question": "방금 쓴 절을 공고·평가지표 기준으로 검토해 줘"},
                                      {"kind": "redo", "text": "방금 쓴 내용 다시 쓰기", "question": "방금 넣은 내용을 더 구체적인 수치와 근거로 다시 써 줘"}])
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
                text += f"\n- 프로젝트 {_ko.quote(move_to, '를')} 찾지 못해 옮기지 않았습니다"
            else:
                try:
                    from zzaimy.ingest import gdrive_files

                    db.set_chat_project(session_id, int(proj["id"]))
                    acct_ = accounts.get(owner, {}) if password is not None else {}
                    gdrive_files.move_to_project(db, session_id, proj["name"], dept=acct_.get("dept") or None)
                    text += f"\n- 드라이브 문서를 「{proj['name']}」 폴더로 옮기고 대화를 그 프로젝트에 넣었습니다"
                except Exception as e:
                    text += f"\n- 문서를 옮기지 못했습니다({_ko.reason(e)})"
        text = _scrub_internal(text)
        if scope_msg:
            text = scope_msg + "\n\n" + text
        db.add_chat(session_id, "assistant", text)


    def _edit_linked_doc(session_id: int, q: str, owner: str, data_dir: Path, scope: dict, scope_msg: str) -> None:
        """연결된 구글 독스에 대한 명령 — 모델 없이 물러난 대비 경로. 요청 글을 낱말 규칙으로 갈래짓고(값 알려 줌·절 쓰기·한글 내보내기·
        작업본 새로·안내 상자·검토·일반 편집) 에이전트 도구와 같은 함수로 보낸다."""
        from zzaimy.app import chat_documents, drafting

        link = chat_documents.binding(db, session_id, owner)
        hits, proj_hits = _command_evidence(session_id, q, scope)
        try:
            from zzaimy.generate.client import VllmClient

            client = VllmClient(role="answer")
            from zzaimy.app import asks as _asks

            pend = _asks.pending(db, session_id)
            given = _asks.parse_pairs(q, [a["name"] for a in pend]) if pend else {}
            if given:
                _remember_given(session_id, given)
                return
            def _has_writable() -> bool:
                from zzaimy.ingest import gdocs as _gd0
                try:
                    return any(drafting.writable(x) for x in _gd0.get(link["account"], link["doc"])["sections"])
                except Exception:
                    return False
            # 「방금 쓴 절 다시 써 줘」「이 절」 — 이 대화에서 마지막으로 쓴 절로 짚는다(안 그러면 일반 편집으로 빠진다)
            last_sec = db.get_setting(f"chat_last_section:{session_id}", "") or ""
            if last_sec and _LAST_SECTION_REF.search(q) and "「" not in q and not drafting._SECTION_NO.search(q):
                q = f"{q} 「{last_sec}」 절"
            # 문서 전체 초안 요청인데 쓸 절 구조가 없는 문서(빈 새 문서)면 일반 편집 — 모델이 절부터 짜서 쓴다
            if drafting.looks_like_section_draft(q) and (not drafting.doc_draft_only(q) or _has_writable()):
                # 모델 없이 물러난 경로 — 쓸 절은 낱말 규칙(target_sections)으로 고르고, 쓰기는 에이전트와 같은 _draft_sections
                from zzaimy.ingest import gdocs as _gd

                info = _gd.get(link["account"], link["doc"])
                try:
                    # 이미 다룬 절은 연결 문서마다 둔다 — 대화 단위로 두면 문서를 새로 만든 뒤에도 옛 문서에서 다룬 절을 건너뛴다
                    raw_skip = _aj.loads(db.get_setting(f"chat_skip_sections:{session_id}", "") or "[]")
                    skip_ = set(raw_skip.get("heads", [])) if isinstance(raw_skip, dict) and raw_skip.get("doc") == link["doc"] else set()
                except ValueError:
                    skip_ = set()
                targets = drafting.target_sections(info, q, skip=skip_)
                if not targets:
                    text = "쓸 절을 찾지 못했습니다. 절 번호(예: 1.1)를 말해 주거나, 빈 절이 없으면 어느 절을 다시 쓸지 골라 주세요."
                    _set_options(session_id, [{"kind": "pick", "text": f"{_ko.quote(_ko.clip(s_['heading'], 24))} 다시 쓰기", "question": f"「{s_['heading']}」 절을 다시 써 줘"}
                                              for s_ in [x for x in info["sections"] if drafting.writable(x)][:3]])
                    db.add_chat(session_id, "assistant", _scrub_internal(text))
                    return
                redo = bool(re.search(r"다시\s*(?:써|쓰|작성)|새로\s*(?:써|쓰|작성)|바꿔\s*(?:써|쓰)", q))
                _draft_sections(session_id, owner, data_dir, scope, scope_msg, targets=targets, redo=redo, request=q, client=client, info=info)
                return
            if re.search(r"(한글|hwpx?)\s*(파일|문서|서식|완성본)?\s*(로|으로)?\s*(내보내|저장|만들|뽑|출력|채워|완성)", q, re.I) and not re.search(r"작업본", q):
                _export_hwp(session_id, owner, data_dir, request=q,
                            remove_boxes=bool(re.search(r"제출본|(안내\s*상자|작성방법)\s*(없이|빼고|지우고|삭제하고)", q)))
                return
            if re.search(r"작업본\s*(?:을|를)?\s*(?:새로|다시)\s*(?:만들|떠|뜨|생성)|새\s*작업본|작업본\s*갱신", q):
                _new_work_copy(session_id, owner, data_dir, request=q)
                return
            if re.search(r"(작성방법|안내|가이드)\s*(상자|박스|표)?[을를은는]?\s*(지워|삭제|없애|정리)|제출본|마무리\s*(해|정리)", q):
                _remove_guide_boxes(session_id, owner, data_dir, confirmed=bool(re.search(r"확인|그래|응|네|진행|지워\s*줘|삭제해\s*줘", q)))
                return
            review_focus = None
            if re.search(r"검토|점검|평가해|맞는지|채점|비교해", q):
                # 절 검토: 지목한 절(번호) 또는 방금 쓴 절
                from zzaimy.ingest import gdocs as _gd

                try:
                    info = _gd.get(link["account"], link["doc"])
                    cands = drafting.target_sections(info, q, filled_ok=True)
                    last = db.get_setting(f"chat_last_section:{session_id}", "") or ""
                    if not cands and last:
                        cands = [x for x in info["sections"] if x["heading"] == last]
                    review_focus = cands[0] if cands else None
                except Exception:
                    review_focus = None
            _run_doc_command(session_id, owner, data_dir, scope, scope_msg, command=q, client=client, review_focus=review_focus,
                             evidence=(hits, proj_hits))
            return
        except Exception as e:
            from zzaimy.generate.client import describe_llm_error

            logging.getLogger("zzaimy.app.gdocs").exception("연결 문서 명령 실패 (대화 %s)", session_id)
            text = describe_llm_error(e) + ". 문서는 바꾸지 않았습니다."
        text = _scrub_internal(text)
        if scope_msg:
            text = scope_msg + "\n\n" + text
        db.add_chat(session_id, "assistant", text)

    _RETRIEVAL_NOTE = {"fulltext": "풀텍스트 RAG — 그래프 단계 끔", "graph": "온톨로지 + RAG(운영 기본)",
                       "graph_read": "온톨로지 + RAG, 27B 질문 읽기"}

    _chat_usage_live: dict[int, dict] = {}                         # 답을 쓰는 중인 대화의 문맥 사용(실시간 표시)

    def _answer_task(session_id, q, stored, criteria, web="", retrieval=""):
        from zzaimy.app import grant_search as _gs_mode
        from zzaimy.app import llm_budget as _lb_usage
        usage_ = _lb_usage.track()                                # 이 답변에서 모델에 보낸 양 — 화면 「문맥 사용」 표시
        _chat_usage_live[session_id] = usage_                     # 답을 쓰는 동안에도 화면이 지금까지의 호출을 본다
        try:
            # 관리자 실험(GraphRAG 비교) — 이 답변 안의 사업 문서 검색만 그 모드로. 기본("")은 운영 그대로
            with _gs_mode.graph_mode(retrieval):
                if retrieval:
                    _chat_step(session_id, f"[실험] 검색 방식: {_RETRIEVAL_NOTE.get(retrieval, retrieval)}")
                _answer_task_impl(session_id, q, stored, criteria, web)
        finally:
            _chat_usage_live.pop(session_id, None)
            if usage_["calls"]:
                db.set_setting(f"chat_ctx:{int(session_id)}", json.dumps({**usage_, "at": time.time()}))
                # 답마다 남긴다(chat_ctx 는 마지막 답만) — 프로젝트 「진행 현황」의 문맥·토큰 집계(app/project_activity)
                _last = next((m for m in reversed(db.list_chats(session_id, limit=4)) if m.get("role") == "assistant"), None)
                if _last:
                    db.set_setting(f"chat_ctx_msg:{int(_last['id'])}", json.dumps({**usage_, "at": time.time()}))
                from zzaimy.app import chat_memory as _cm2
                _cm2.compact_later(db, session_id)                   # 답을 보낸 뒤 작업 메모 고치기(배경)
            _chat_running.discard(session_id)
            _chat_progress.pop(session_id, None)

    @app.get("/api/chat/{session_id}/context")
    def chat_context_usage(request: Request, session_id: int):
        """마지막 답변의 문맥 사용 — 가장 큰 호출의 입력+출력 / 모델 문맥 한도, 모델에 보낸 입력 합, 줄였는지."""
        _chat_owned(request, session_id)
        from zzaimy.app import chat_memory as _cm3
        live = _chat_usage_live.get(session_id)
        if live is not None and live.get("calls"):
            u = {**live, "items": list(live.get("items") or []), "running": True}
        else:
            try:
                u = json.loads(db.get_setting(f"chat_ctx:{int(session_id)}", "") or "{}")
            except ValueError:
                u = {}
        memo = _cm3.load(db, session_id)
        lim = int(u.get("limit") or 0) or 16384
        msgs = [m for m in db.list_chats(session_id, limit=400) if m.get("role") in ("user", "assistant")]
        _, recent = _cm3.split(msgs, int(lim * _cm3.RECENT_SHARE))
        u["memo"] = {"text": memo.get("text", ""), "folded": int(memo.get("n") or 0), "at": memo.get("at")}
        u["window"] = {"messages": len(msgs), "recent": len(recent), "recent_share": _cm3.RECENT_SHARE,
                       "memo_share": _cm3.SUMMARY_SHARE, "limit": lim}
        return u

    def _schedule_answer(background, session_id, q, stored, criteria, web="", retrieval=""):
        import time

        _chat_progress[session_id] = {"started_at": time.time(), "steps": ["요청 확인 중"], "at": [time.time()]}
        _chat_running.add(session_id)
        background.add_task(_answer_task, session_id, q, stored, criteria, web, retrieval)

    app.state.schedule_answer = _schedule_answer      # 채팅과 같은 답 길(문서 작업 에이전트 포함) — 공동 편집 시연의 「AURA로 쓰기」 가 쓴다
    app.state.chat_running = lambda sid: sid in _chat_running

    install_chat_revisions(app, db, chat_revisions, _schedule_answer,
                           lambda sid: sid in _chat_running, inbox_dir,
                           ALLOWED_EXTENSIONS, _chat_sources)

    def _link_criteria_kind(db_, doc_id: int) -> None:
        """공고·지침·기준으로 분류된 문서는 그 프로젝트의 기준 문서로 잇는다(project_documents.link_criteria_kind). 실패해도 접수는 유지."""
        from zzaimy.app import project_documents
        try:
            project_documents.link_criteria_kind(db_, doc_id)
        except Exception:
            logging.getLogger("zzaimy.app.web").exception("기준 문서 자동 연결 실패 doc=%s", doc_id)

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

    def _process_then_identify(db_, doc_id: int, stored, user_chose_type: bool = True, defer_review: bool = False) -> None:
        """접수 처리에 이어 문서의 정체까지 한 번에 읽는다.

        반입 시점에 끝나야 담당자가 문서마다 버튼을 누르지 않는다. 모델이 없거나
        실패하면 조용히 넘어간다 — 접수 자체가 막히면 안 되기 때문이다.
        """
        import inspect as _ins

        if defer_review and "defer_review" in _ins.signature(processor.process).parameters:
            processor.process(db_, doc_id, stored, defer_review=True)
            # 판독·색인까지 끝났으니 문서는 이미 쓸 수 있다 — 뒤 단계(정체 읽기 27B ~100초·분류·열람 PDF)는 묶음 판독이 다 끝난 뒤
            # 한 줄 대기열로(_process_bundle). 판독과 겹치면 PDF 라이브러리(pdfium)를 두 스레드가 함께 써 프로세스가 죽는다
            return
        processor.process(db_, doc_id, stored)
        _after_process(db_, doc_id, user_chose_type)

    def _after_process(db_, doc_id: int, user_chose_type: bool = True) -> None:
        """판독 뒤 단계 — 정체 읽기 → 자동 분류 → 열람 PDF → 맥락 분석. 하나가 실패해도 다음은 한다."""
        try:
            _identify_document(db_, doc_id)
        except Exception:
            pass
        try:
            _auto_route(db_, doc_id, user_chose_type)
        except Exception:
            pass
        _link_criteria_kind(db_, doc_id)
        # 사무 문서(한글·워드·엑셀·PPT)의 열람 PDF 도 반입 때 만든다 — 문서 화면 '원문' 탭이 바로 열린다
        try:
            from zzaimy.app import office_pdf

            doc_ = db_.get_document(doc_id) or {}
            if office_pdf.is_office(doc_.get("stored_path") or ""):
                office_pdf.render(db_, doc_)
        except Exception:
            pass
        # 맥락 분석도 반입 때 해 둔다 — 버튼을 누르게 하지 않는다. 실패해도 접수는 유지
        try:
            doc_ = db_.get_document(doc_id) or {}
            if doc_.get("status") == "reviewed" and not (doc_.get("ai_review") or "").strip():
                db_.update_document(doc_id, coverage="분석 중입니다 (30초~1분)")
                processor.analyze(db_, doc_id)
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
        """정체 파악에 쓰는 모델 호출 — 반입 뒤 단계라 검토 연결(review)을 쓴다. 답변 연결(answer)은 채팅에 비워 둔다."""
        from zzaimy.generate.client import VllmClient

        c = VllmClient(role="review")
        r = c.client.chat.completions.create(
            model=c.model, temperature=0.0, max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        return r.choices[0].message.content or ""

    def _doc_access_sql(request: Request) -> tuple[str, list]:
        """문서 목록 SQL 의 열람 조건 — access_policy.doc_sql(visible 과 같은 뜻). 관리자는 조건 없음."""
        return _ap.doc_sql(_req_viewer(request))

    @app.get("/search", response_class=HTMLResponse)
    def search_all(request: Request, q: str = ""):
        """한 곳에서 찾기 — 문서·근거·프로젝트·대화. 상단 검색창이 여기로 온다.

        문서는 제목(파일 이름·접수번호)과 본문 어휘 색인에서 찾고 같은 문서의 판본은 한 줄로 묶는다(library.version_key).
        근거는 열람 범위 안의 모든 문서 본문 — 사업 문서 조각(grant_lex)과 기준 문서 조각(regulations.sparse_search)을
        함께 보인다. 어휘 검색만 쓴다 — 검색창 한 번에 모델을 부르지 않기 위해서다.
        대화는 제목·프로젝트·사업(주제)뿐 아니라 주고받은 글에서도 찾는다.
        """
        from zzaimy.app import library
        from zzaimy.app import project_search as _ps

        term = (q or "").strip()[:200]
        docs, chats, chunks, projects, docs_more = [], [], [], [], False
        DOCS = 10
        if term:
            owner = getattr(request.state, "user", "zzaimy")
            # 열람 권한은 SQL 에서 거른다(라이브러리와 같은 규칙) — 다른 담당자 한정 문서의 이름·본문이 보이지 않게
            acc, acc_args = _doc_access_sql(request)
            from zzaimy.app import access_guard as _ag

            space = _ag.search_scope(getattr(request.state, "dept", "") or None, request.state.role, "" if getattr(request.state, "view_as", None) else (request.state.user or ""),
                                     Path(db_path).parent, grade=_gr.request_grade(request))
            with db._conn() as conn:
                found = [dict(r) for r in conn.execute(
                    "SELECT d.id, d.filename, d.doc_type, d.status, d.created_at, d.receipt_no, d.project_id, d.kind FROM documents d"
                    f" WHERE d.filename LIKE ?{acc} ORDER BY d.id DESC LIMIT 200", [f"%{term}%", *acc_args]).fetchall()]
            for d in found:
                d["snippet"] = ""
            # 본문은 사업 문서 어휘 색인(grant_lex)으로 찾는다 — 권한은 색인 조회에서 거른다. 찾은 조각은 근거로도 보인다
            body_hits: list[dict] = []
            try:
                from zzaimy.app import grant_lex, grant_search

                # 채팅 검색과 같은 범위(RAG 공간·계정별 추가 권한, ADR-0053) — 검색창이 공간 제한을 우회하지 않게
                ids = []
                if space.get("grant"):
                    user = None if request.state.role == "dev" else (request.state.user or "")
                    ids = grant_lex.rank(db, grant_lex.lex_terms(term), None, user, limit=60, depts=space.get("grant_depts"))
                body_hits = grant_search.chunks_by_ids(db, ids[:60])
                order = {cid: i for i, cid in enumerate(ids)}
                body_hits.sort(key=lambda h: order.get(h["id"], 1 << 30))
            except Exception:
                body_hits = []

            def snippet(body: str, n: int = 120) -> str:
                # 판독 본문의 그림 표시(![image](…))·표 칸 줄(|)·글머리 사용자 영역 글자는 미리 보기 글이 아니다 — 걷어 내고,
                # 검색어 전체가 없으면 첫 낱말이 나온 곳 앞뒤를 보인다
                body = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body or "")
                body = re.sub(r"[\uE000-\uF8FF]|\s*\|\s*", " ", body)
                low = body.lower()
                i = low.find(term.lower())
                if i < 0:
                    i = min((j for j in (low.find(w.lower()) for w in term.split()) if j >= 0), default=-1)
                a, b = (max(0, i - 40), i + n) if i >= 0 else (0, n)
                # 낱말 가운데서 자르지 않는다(「산업의」 가 「업의」 로 시작하던 것) — 앞은 다음 빈칸 뒤부터, 뒤는 앞 빈칸까지, 잘렸으면 「…」
                if a > 0:
                    sp = body.find(" ", a, i if i >= 0 else b)
                    a = sp + 1 if sp >= 0 else a
                if b < len(body):
                    sp = body.rfind(" ", max(a, i if i >= 0 else a), b)
                    b = sp if sp > a else b
                out = " ".join(body[a:b].split())
                return ("…" if a > 0 else "") + out + ("…" if b < len(body.rstrip()) else "")

            seen = {d["id"] for d in found}
            for h in body_hits:
                if h["doc_id"] not in seen:
                    found.append({"id": h["doc_id"], "filename": h["filename"], "doc_type": "grant", "status": "reviewed",
                                  "snippet": snippet(h["content"] or "")})
                    seen.add(h["doc_id"])
            try:
                from zzaimy.app.regulations import sparse_search

                reg_hits = sparse_search(db, term, top_k=8, **_ag.reg_kwargs(space))
            except Exception:
                reg_hits = []
            # 근거: 사업 문서 본문과 기준 문서 조문을 번갈아 — 한쪽 갈래가 다른 쪽을 밀어내지 않게. 한 문서에서 둘까지
            grant_ev = [{"doc_id": h["doc_id"], "title": h["filename"], "heading": "", "snippet": snippet(h["content"] or "")}
                        for h in body_hits]
            reg_ev = [{"doc_id": h.get("doc_id"), "title": h.get("reg_title") or "문서", "heading": h.get("heading") or "",
                       "snippet": snippet(h.get("content") or "")} for h in reg_hits]
            per_doc: dict = {}
            for pair in zip(grant_ev + [None] * len(reg_ev), reg_ev + [None] * len(grant_ev)):
                for ev in pair:
                    if ev is None or len(chunks) >= 10 or per_doc.get(ev["doc_id"], 0) >= 2:
                        continue
                    per_doc[ev["doc_id"]] = per_doc.get(ev["doc_id"], 0) + 1
                    chunks.append(ev)
            # 기준 문서는 본문 대신 규정 조각 검색(권한 범위 안)에서 찾은 문서로도 채운다
            for ev in reg_ev:
                if ev["doc_id"] is not None and ev["doc_id"] not in seen:
                    found.append({"id": ev["doc_id"], "filename": ev["title"], "doc_type": "regulation", "status": "reviewed",
                                  "snippet": ev["snippet"][:120]})
                    seen.add(ev["doc_id"])
            # 같은 문서의 판본은 한 줄 — 최신 판이 대표, 판본 수를 붙인다
            groups: dict[tuple, dict] = {}
            for d in found:
                key = (d.get("doc_type") or "", library.version_key(d.get("filename") or "") or f"#{d['id']}")
                g = groups.get(key)
                if g is None:
                    groups[key] = dict(d, n_versions=1)
                else:
                    g["n_versions"] += 1
                    if not g.get("snippet") and d.get("snippet"):
                        g["snippet"] = d["snippet"]
            docs_more = len(groups) > DOCS or len(found) >= 200
            docs = list(groups.values())[:DOCS]
            # 줄마다 서류 갈래·사업 이름 — 거의 다 국고사업이라 문서 계열 표시는 가려 주는 것이 없다(라이브러리 목록과 같은 표시)
            if docs:
                from zzaimy.app.doc_routing import KINDS as _kinds

                with db._conn() as conn:
                    extra = {r["id"]: dict(r) for r in conn.execute(
                        "SELECT d.id, d.kind, p.name AS project_name FROM documents d LEFT JOIN projects p ON p.id = d.project_id"
                        f" WHERE d.id IN ({','.join('?' * len(docs))})", [d["id"] for d in docs]).fetchall()}
                for d in docs:
                    e = extra.get(d["id"]) or {}
                    d["kind_label"] = _kinds.get(e.get("kind") or d.get("kind") or "", "")
                    d["project_name"] = e.get("project_name") or ""
            projects = _ps.search(db, owner, term)["projects"][:5]
            rows = chat_history.sessions(owner, term, False, 0, 10,
                                         chat_topics.match_clause(term), 'all')
            names = chat_topics.topics([r["id"] for r in rows])
            with db._conn() as conn:
                for r in rows:
                    hit = conn.execute(
                        "SELECT content FROM chat_messages WHERE session_id = ?"
                        " AND instr(lower(content), lower(?)) > 0 ORDER BY id LIMIT 1",
                        (r["id"], term)).fetchone()
                    chats.append(dict(r, topic=names.get(r["id"]), snippet=snippet(hit["content"]) if hit else ""))
        return templates.TemplateResponse(request, "search.html", ctx(request, {
            "q": term, "docs": docs, "docs_more": docs_more, "chats": chats, "chunks": chunks, "projects": projects,
        }))

    @app.get("/api/quick-search")
    def quick_search(request: Request, q: str = ""):
        """빠른 찾기 창(Ctrl+K) — 이름·제목만 바로(본문 색인·모델은 쓰지 않는다). 권한은 /search 와 같은 조건.
        대화·프로젝트·문서(라이브러리)·한글 문서(쓸 수 있는 부서 폴더) 각 몇 개, 본문까지는 「모든 결과 보기」(/search)."""
        from zzaimy.app import project_search as _ps
        term = (q or "").strip()[:100]
        out: dict = {"q": term, "recent": not term, "chats": [], "projects": [], "docs": [], "hwp": []}
        owner = getattr(request.state, "user", "zzaimy")
        role = request.state.role
        # 찾을 말이 없으면 최근 것(최근 대화·내 프로젝트·최근 문서·최근 고친 한글 문서) — 창을 열자마자 바로 고를 수 있게
        rows = (chat_history.sessions(owner, term, False, 0, 6, chat_topics.match_clause(term), 'all') if term
                else chat_history.sessions(owner, '', False, 0, 6))
        out["chats"] = [{"id": r["id"], "title": r.get("title") or "제목 없는 대화", "at": str(r.get("updated_at") or r.get("created_at") or "")[:10],
                         "project": r.get("project_name") or ""} for r in rows]
        if not _ap.can_browse(role):                         # 학생은 대화만
            return out
        out["projects"] = [{"id": p["id"], "name": p["name"], "at": str(p.get("last_activity") or "")[:10]}
                           for p in _ps.search(db, owner, term)["projects"][:5]]
        acc, acc_args = _doc_access_sql(request)
        pattern = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with db._conn() as conn:
            out["docs"] = [{"id": r["id"], "name": _doc_title(dict(r)), "at": str(r["created_at"] or "")[:10]} for r in conn.execute(   # 라이브러리와 같은 제목(파일 이름 아님)
                f"SELECT d.id, d.filename, d.created_at FROM documents d WHERE d.filename LIKE ? ESCAPE '\\'{acc}"
                " ORDER BY d.id DESC LIMIT 6", [pattern, *acc_args]).fetchall()]
        try:
            from zzaimy.app import webhwp
            from zzaimy.app.webhwp_routes import _base, _depts
            depts, base, words = set(_depts(request)), _base(request), term.lower().split()
            files = db.list_files(kind="webhwp", limit=8000)
            if not term:                                   # 최근 고친 것부터
                files = sorted(files, key=lambda r: str(webhwp.meta(db, r["id"]).get("saved") or r.get("created_at") or ""), reverse=True)
            for r in files:
                name = str(r["name"] or "")
                if not all(w in name.lower() for w in words):
                    continue
                d = webhwp.dept_of_path(base, r["path"])
                if d in depts and not any(x in webhwp.HIDDEN for x in Path(r["path"]).parts) and Path(r["path"]).exists():
                    out["hwp"].append({"id": r["id"], "name": name, "where": " › ".join([d, *[x for x in webhwp.folder_of(base, r["path"]).split("/") if x]])})
                if len(out["hwp"]) >= 6:
                    break
        except Exception:
            pass
        return out

    CRITERIA_LIST = "/?type=all&use=criteria"     # 기준 문서 목록 — 라이브러리 한 목록의 「기준」 칩(ADR-0062)

    @app.get("/criteria")
    def criteria(request: Request):
        """옛 기준 문서 탭 — 라이브러리 목록의 「기준」 칩으로 넘긴다(북마크·걸린 링크가 깨지지 않게)."""
        return RedirectResponse(CRITERIA_LIST, status_code=303)

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
        return RedirectResponse(CRITERIA_LIST, status_code=303)

    from zzaimy.app.project_documents import CRITERIA_KINDS as _CRITERIA_KINDS
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
        jobs: list[tuple[int, Path]] = []
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
                jobs.append((doc_id, stored))
                made["criteria"].append(doc_id)
            else:
                d_dept, d_level = classify(project["sector"], owner=owner, uploader_dept=getattr(request.state, "dept", ""),
                                           project_dept=project.get("dept"))
                doc_id = db.add_document(filename=name, stored_path=str(stored), doc_type=project["sector"],
                                         project_id=int(project["id"]), owner=owner, dept=d_dept, access_level=d_level)
                stored = storage.adopt_original(db, doc_id, stored)
                jobs.append((doc_id, stored))
                made["intake"].append(doc_id)
        if jobs:
            # 묶음 문서 처리 — 동시 수는 ZZAIMY_BUNDLE_WORKERS(기본 1). 측정 결과 3건씩 돌리면 한 건씩보다 느렸다 —
            # 시간 대부분이 토르 27B 생성(문서당 ~3분)인데 젯슨은 동시 요청에도 전체 생성 속도가 거의 안 늘고, MinerU(VM CPU)는 서로 다툰다
            background.add_task(_process_bundle, jobs)
        return made

    from concurrent.futures import ThreadPoolExecutor as _TPE
    _TAIL_QUEUE = _TPE(max_workers=1, thread_name_prefix="intake-tail")

    def _process_bundle(jobs: list[tuple[int, Path]]) -> None:
        from concurrent.futures import ThreadPoolExecutor

        workers = max(1, int(os.environ.get("ZZAIMY_BUNDLE_WORKERS", "1") or 1))
        with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as ex:
            for fut in [ex.submit(_process_then_identify, db, did, path, True, True) for did, path in jobs]:
                try:
                    fut.result()
                except Exception:
                    logging.getLogger(__name__).exception("묶음 문서 처리 실패")
        for did, _path in jobs:                          # 판독이 다 끝난 뒤에 뒤 단계 — 판독과 겹치지 않게
            _TAIL_QUEUE.submit(_after_process, db, did, True)

    def _link_past_materials(project: dict) -> list[dict]:
        """같은 사업의 지난 자료(다른 연도의 공고·기본계획·지침) 를 프로젝트 기준으로 잇는다 — 이름 낱말(연도·번호 제외) 7할 이상 겹치는
        문서함의 기준 문서. 새 대화에 문서를 던지면 지난 자료가 자동으로 이어진다."""
        name = project.get("name") or ""
        toks = [t.lower() for t in re.split(r"[\s\-_()\[\]{}.,:;·+/]+", name)
                if len(t) >= 2 and not re.fullmatch(r"\d{2,4}(?:학년도|년도|년)?|(?:v|ver)?\d[\d.]*", t, re.I)]
        if not toks:
            return []
        linked = set(db.get_project_criteria_ids(int(project["id"])))
        picked: list[dict] = []
        for d in db.list_documents("regulation"):
            if d["id"] in linked or d.get("status") != "reviewed" or d.get("project_id") == project["id"]:
                continue
            title = storage.title_of(d.get("filename") or "").lower()
            norm = re.sub(r"[\s\-_()\[\]{}.,:;·+/]+", "", title)
            total = sum(len(t) for t in toks)
            hit = sum(len(t) for t in toks if t in norm)
            if total and hit / total >= 0.7:
                picked.append(d)
        if picked:
            db.add_project_criteria(int(project["id"]), [d["id"] for d in picked])
        return picked

    def _link_past_projects(request: Request, project: dict, limit: int = 3, min_score: float = 0.4) -> list[dict]:
        """새 프로젝트에 이름이 많이 겹치는 과거 사업 보관 묶음을 참조로 붙인다(담당자가 만들면 관련 과거 사업이 붙는다).
        참조는 보관 해제·소유권 변경·문서 이동이 아니다. 지침·기준 탭에서 풀 수 있다. 열람 범위의 문서가 있는 묶음만."""
        from zzaimy.app import project_refs
        try:
            scope = _ref_scope(request)
            got = [c for c in project_refs.candidates(db, project, "", scope, limit=limit * 3) if c["score"] >= min_score][:limit]
            for c in got:
                project_refs.link(db, int(project["id"]), int(c["id"]), f"새 프로젝트 이름과 겹침(자동) · {c['why']}",
                                  getattr(request.state, "user", "") or "")
            return got
        except Exception:
            logging.getLogger("zzaimy.app.web").exception("관련 보관 사업 자동 참조 실패")
            return []

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

    def _viewer(request: Request) -> dict:
        return _req_viewer(request)

    def _doc_groups(request: Request, project: dict | None, session_id: int | None = None) -> dict[str, list[dict]]:
        """프로젝트·대화 문서의 세 갈래(사용자 첨부 / 참고 / 규정·지침) — 대화 문서함·프로젝트 화면·지침·기준 탭이 같이 쓴다."""
        from zzaimy.app import project_documents

        return project_documents.collect(db, project, session_id, **_viewer(request))

    # 대화 문서 창의 「한글 기안기」 — 이 대화에서 만든 한글 완성본(부서 한글 문서함)을 기안기로 연다. 없으면 만들기를 건다
    _hwp_busy: dict[int, float] = {}

    def _chat_owned(request: Request, session_id: int) -> tuple[dict, str]:
        session_ = db.get_chat_session(session_id)
        owner = getattr(request.state, "user", "zzaimy")
        if session_ is None or session_.get("owner") not in (None, owner):
            raise HTTPException(404)
        return session_, owner

    def _chat_hwp_file(session_id: int) -> dict | None:
        from zzaimy.app import webhwp_agent as _wa_
        b_ = _wa_.bound(db, session_id)                    # 에이전트가 이 대화에 이은 한글 작업본이 먼저
        if b_ and _webhwp.get(db, b_) and Path(_webhwp.get(db, b_)["path"]).exists():
            return _webhwp.get(db, b_)
        best = None
        for r in db.list_files(kind="webhwp", limit=5000):
            if _webhwp.meta(db, r["id"]).get("source") == f"대화-{session_id}" and Path(r["path"]).exists():
                if best is None or r["id"] > best["id"]:
                    best = r
        return best

    @app.get("/api/chat/{session_id}/hwp")
    def chat_hwp_state(request: Request, session_id: int):
        _chat_owned(request, session_id)
        from zzaimy.app.webhwp_routes import EDIT_PAGE
        f = _chat_hwp_file(session_id)
        busy = time.time() - _hwp_busy.get(session_id, 0) < 300
        files = []
        ids_ = _wa_files.files_of(db, session_id)
        if f and f["id"] not in ids_:                          # 한글 완성본(작업본을 원본 서식에 채운 것)도 목록에
            ids_.insert(0, f["id"])
        for i in ids_:
            r_ = _webhwp.get(db, i)
            m_ = _webhwp.meta(db, i)
            files.append({"id": i, "name": r_["name"], "url": f"{EDIT_PAGE}?id={i}", "current": bool(f and f["id"] == i),
                          "where": " / ".join(x_ for x_ in (m_.get("dept", ""), _webhwp.folder_of(Path(db_path).parent, r_["path"])) if x_), "saved": m_.get("saved", ""),
                          "saved_by": m_.get("saved_by", "")})
        return {"editor": _webhwp.editor_of(db, getattr(request.state, "user", "")), "ok": _webhwp.status()["ok"], "busy": busy,
                "file": ({"id": f["id"], "name": f["name"], "url": f"{EDIT_PAGE}?id={f['id']}"} if f else None), "files": files}

    @app.post("/api/chat/{session_id}/hwp/bind")
    async def chat_hwp_bind(request: Request, session_id: int):
        """문서 패널에서 고른 한글 파일을 이 대화의 작업 문서로 — 부서 폴더 파일(id) 또는 드라이브 파일(drive_id, 부서 폴더로 받아서)."""
        _, owner = _chat_owned(request, session_id)
        if request.state.role not in ("dev", *_ap.STAFF_ROLES):
            raise HTTPException(403)
        from zzaimy.app import webhwp_routes as _wr
        from zzaimy.app.webhwp_routes import EDIT_PAGE
        body = await request.json()
        fid = None
        if body.get("id"):
            f = _webhwp.get(db, int(body["id"]))
            if not _webhwp.may_open(Path(db_path).parent, f, _wr._depts(request)) or not _wr._access(request, f):
                raise HTTPException(404, "문서를 찾을 수 없습니다")
            fid = int(f["id"])
            if _webhwp.locked(db, fid):                       # 잠긴 양식은 내 부서 폴더에 복사해 잇는다
                p_ = Path(f["path"])
                fid = _webhwp.add(db, Path(db_path).parent, getattr(request.state, "dept", "") or _webhwp.COMMON,
                                  p_.name.replace(" 공통 양식", ""), p_.read_bytes(), owner=owner, source=f"양식복사:{f['id']}", folder="작업")
        elif body.get("drive_id"):
            import re as _re2
            from zzaimy.app import webhwp_drive
            if not _re2.fullmatch(r"[A-Za-z0-9_-]{10,128}", str(body["drive_id"])):
                raise HTTPException(400, "드라이브 파일 id 가 올바르지 않습니다")
            email = _wr._google(request)
            dept_ = getattr(request.state, "dept", "") or _webhwp.COMMON
            try:
                fid = webhwp_drive.import_file(db, Path(db_path).parent, email, str(body["drive_id"]), dept=dept_, folder="작업", owner=owner)
            except (RuntimeError, ValueError) as e:
                raise HTTPException(502, str(e))
        else:
            raise HTTPException(400, "id 또는 drive_id 가 필요합니다")
        _wa_files.attach(db, session_id, fid)
        f = _webhwp.get(db, fid)
        return {"file": {"id": fid, "name": f["name"], "url": f"{EDIT_PAGE}?id={fid}"}}

    @app.post("/api/chat/{session_id}/hwp/current")
    async def chat_hwp_current(request: Request, session_id: int):
        """작업 문서 목록에서 고른 한글 파일을 지금 작업본으로(에이전트도 이 파일에 쓴다)."""
        _chat_owned(request, session_id)
        from zzaimy.app.webhwp_routes import EDIT_PAGE
        body = await request.json()
        fid = int(body.get("id") or 0)
        if fid not in _wa_files.files_of(db, session_id):
            raise HTTPException(404, "이 대화의 작업 문서가 아닙니다")
        _wa_files.attach(db, session_id, fid)
        f = _webhwp.get(db, fid)
        return {"file": {"id": fid, "name": f["name"], "url": f"{EDIT_PAGE}?id={fid}"}}

    @app.delete("/api/chat/{session_id}/hwp/files/{fid}")
    def chat_hwp_detach(request: Request, session_id: int, fid: int):
        """작업 문서 목록에서 빼기 — 파일은 부서 폴더에 그대로 남는다."""
        _chat_owned(request, session_id)
        _wa_files.detach(db, session_id, fid)
        return {"ok": True}

    @app.post("/api/chat/{session_id}/hwp/export")
    def chat_hwp_export(request: Request, session_id: int):
        """작업본을 원본 서식(hwpx)에 채워 부서 한글 문서함에 두기 — 끝나면 대화에 결과가 남고 위 상태에 파일이 선다."""
        _, owner = _chat_owned(request, session_id)
        if request.state.role not in ("dev", *_ap.STAFF_ROLES):
            raise HTTPException(403)
        if time.time() - _hwp_busy.get(session_id, 0) < 300:
            return {"started": False, "busy": True}
        _webhwp.set_editor(db, owner, "webhwp")
        _hwp_busy[session_id] = time.time()

        def _run():
            try:
                _export_hwp(session_id, owner, Path(db_path).parent, remove_boxes=False, request="한글 기안기로 열기")
            except Exception:
                logging.getLogger("zzaimy.app.gdocs").exception("기안기용 한글 완성본 만들기 실패 (대화 %s)", session_id)
            finally:
                _hwp_busy.pop(session_id, None)

        import threading as _th
        _th.Thread(target=_run, daemon=True, name=f"hwp-export-{session_id}").start()
        return {"started": True}

    @app.get("/api/chat/{session_id}/documents")
    def chat_project_documents(request: Request, session_id: int):
        """대화 문서함의 플랫폼 문서 — 세 갈래(project_documents). 프로젝트 대화면 프로젝트 화면과 같은 목록이다.
        답이 끝날 때마다 화면이 다시 부르므로 에이전트가 불러온 문서도 참고 문서에 바로 선다."""
        from zzaimy.app import project_documents
        from zzaimy.app.doc_routing import KINDS

        session_ = db.get_chat_session(session_id)
        owner = getattr(request.state, "user", "zzaimy")
        if session_ is None or session_.get("owner") not in (None, owner):
            raise HTTPException(404)
        pid = int(session_["project_id"]) if session_.get("project_id") else None
        project = db.get_project(pid) if pid else None
        groups = _doc_groups(request, project, session_id)

        def _row(d: dict) -> dict:
            raw = db.get_setting(f"doc_google:{d['id']}", "") or ""
            return {"id": d["id"], "name": storage.title_of(d.get("filename") or ""), "group": d["group_label"],
                    "group_key": d["group"], "via": d["via"],
                    "original_format": storage.original_ext(d).lstrip("."),
                    "kind": KINDS.get(d.get("kind") or "", ""), "status": STATUS_LABELS.get(d.get("status"), d.get("status")), "state": d.get("status"),
                    "url": f"/doc/{d['id']}/view", "page": f"/doc/{d['id']}",
                    "google": (_aj.loads(raw).get("url") if raw else None)}

        rows = {key: [_row(d) for d in groups[key]] for key, _ in project_documents.GROUPS}
        return {"project": (project or {}).get("name"), "project_id": pid,
                "groups": [{"key": key, "label": label, "documents": rows[key]} for key, label in project_documents.GROUPS],
                "documents": [r for key, _ in project_documents.GROUPS for r in rows[key]]}

    @app.get("/api/doc/{doc_id}/google")
    def doc_google(request: Request, doc_id: int):
        """문서의 구글 열람본 — 없으면 지금 만든다. 문서함 패널이 iframe 으로 열 주소(embed_url)까지 준다."""
        from zzaimy.ingest import gdrive_files

        doc = db.get_document(doc_id)
        if doc is None or not _visible(doc, **_doc_scope(request)):
            raise HTTPException(404)                        # 열람 범위 밖 문서는 드라이브로 올리지 않는다
        owner = getattr(request.state, "user", "zzaimy")
        acct_ = accounts.get(owner, {}) if password is not None else {}
        email = gdrive_files.doc_account_for(db, owner, acct_.get("dept") or None)
        if not email or not gdrive_files.has_file_scope(email):
            raise HTTPException(400, gdrive_files.NEED_ACCOUNT)
        project = db.get_project(int(doc["project_id"])) if doc.get("project_id") else None
        try:
            folder = gdrive_files.project_folder_for(db, email, project, acct_.get("dept") or None, sub="첨부")
            made = gdrive_files.google_copy(db, doc, email, folder)
        except Exception as e:
            raise HTTPException(400, f"구글 열람본을 만들지 못했습니다: {e}")
        return {"id": made["id"], "name": made.get("name") or doc["filename"], "mime": made["mime"], "url": made["url"],
                "embed_url": gdrive_files.embed_url(made["id"], made["mime"]), "account": email}

    @app.post("/api/chat/{session_id}/work-on/{doc_id}")
    def chat_work_on(request: Request, session_id: int, doc_id: int):
        """문서함에서 고른 프로젝트 문서로 작업 — 독스 변환본(한글이면 변환)의 복제본을 만들어 이 대화에 잇는다."""
        session_ = db.get_chat_session(session_id)
        owner = getattr(request.state, "user", "zzaimy")
        if session_ is None or session_.get("owner") not in (None, owner):
            raise HTTPException(404)
        doc = db.get_document(doc_id)
        if doc is None or not _viewer_sees(session_id, doc):
            raise HTTPException(404)                        # 열람 범위 밖 문서는 없는 것과 같다(문서 화면과 같은 규칙)
        project = db.get_project(int(session_["project_id"])) if session_.get("project_id") else None
        made, why = _link_existing_document(session_id, doc, owner, project)
        if not made:
            raise HTTPException(400, why or "이 문서로 작업하지 못했습니다")
        raw = db.get_setting(f"chat_google_doc:{session_id}", "") or "{}"
        return {"ok": True, "linked": _aj.loads(raw)}

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
            # 기울임 「*AI물류:*」 — 별표가 그대로 보이지 않게. 곱셈·각주 별표(「* 표시」)는 양끝이 붙은 것만
            s2 = _mre.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"<i>\1</i>", s2)
            return s2.replace("&lt;br&gt;", "<br>").replace("&lt;br/&gt;", "<br>").replace("&lt;br /&gt;", "<br>")

        # 목록 줄 — 「- 」「* 」「• 」(모델이 「* 」로 쓴 목록도 목록으로). 「**굵게**」로 시작하는 줄은 목록이 아니다
        _item = _mre.compile(r"^\s*(?:[-•]|\*(?!\*))\s+")
        _num = _mre.compile(r"^\s*(?:\d{1,2}|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)[.)]\s+")

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
            if _mre.match(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", ln):
                out.append("<hr>")
                i += 1
                continue
            m_h = _mre.match(r"^(#{4,6})\s+(.*)$", ln)
            if m_h:                                         # 「#### Ⅰ.」 — 4단계 이하 제목도 제목으로 그린다
                out.append(f'<h5 style="margin:14px 0 6px; font-size:14px;">{rich(m_h.group(2))}</h5>')
                i += 1
                continue
            if ln.startswith("# "):
                out.append(f'<h3 style="margin:18px 0 8px;">{rich(ln[2:])}</h3>')
            elif ln.startswith("## "):
                out.append(f'<h4 class="extract-h">{rich(ln[3:])}</h4>')
            elif ln.startswith("### "):
                out.append(
                    f'<h5 style="margin:12px 0 4px; font-size:13.5px;">'
                    f"{rich(ln[4:])}</h5>")
            elif _item.match(ln):
                items = []
                while i < len(lines) and _item.match(lines[i]):
                    cur_ln = lines[i]
                    depth = (len(cur_ln) - len(cur_ln.lstrip())) // 2
                    body = [_item.sub("", cur_ln, count=1)]
                    i += 1
                    # 들여쓴 이어짐 줄은 같은 항목에 붙인다
                    while (
                        i < len(lines) and lines[i].strip()
                        and not _item.match(lines[i])
                        and not _num.match(lines[i])          # 「2.  사업 목표」는 새 번호 항목이라 앞 항목에 붙이지 않는다
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

    def _for_people(text: str) -> str:
        """화면에 보이는 개발 메모에서 내부 표기를 걷어 낸다 — 코드 경로·파일 이름·스크립트 번호·환경 변수(괄호 묶음째)와
        `코드` 표기. 문장 뜻은 남는다(메모 원문은 문서 화면에 그대로 있다)."""
        import re as _re
        t = str(text or "")
        internal = r"(?:[\w.-]+/[\w./-]+|\b[\w-]+\.(?:py|json|jsonl|md|html|sh|npz)\b|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|\bscripts?/?\d{2,3}\b|\b\d{3}(?=[ ·,)]|$)|[a-z]+_[a-z_]+)"
        t = _re.sub(r"\s*\((?=[^()]*" + internal + r")[^()]*\)", "", t)       # 내부 표기를 담은 괄호 묶음
        t = _re.sub(r"`[^`]*`", "", t)                                       # 남은 코드 표기
        t = _re.sub(r"[ \t]{2,}", " ", t)
        t = _re.sub(r" ([,.·])", r"\1", t)
        return t

    templates.env.filters["for_people"] = _for_people

    def _cite_links(html, sources) -> "Markup":
        """답변에 나온 문서 이름을 근거 문서 링크로 — 근거 목록에 있는 이름만."""
        from markupsafe import Markup as _M

        from zzaimy.app.citations import linkify

        return _M(linkify(str(html), sources or []))

    templates.env.filters["cite_links"] = _cite_links

    _ATTACH_LINE = re.compile(r"^\[첨부(?:#(\d+))?\] ([^\n]+)\n?")

    def _attach_view(text: str, allowed=None):
        """사용자 말풍선 — 첫 줄이 첨부면 문서 보기 링크로 바꾸고 나머지는 그대로(이스케이프).
        allowed(문서 id 모음)를 주면 그 밖의 첨부는 이름을 가리고 열지 않는다(공유받은 사람의 읽기 화면)."""
        from markupsafe import Markup, escape

        rest = text or ""
        heads: list = []
        while True:
            m = _ATTACH_LINE.match(rest)
            if not m:
                break
            did, name = m.group(1), m.group(2)
            if did and allowed is not None and int(did) not in allowed:
                heads.append(Markup('<span class="chat-attach is-hidden">첨부 · 볼 수 없는 자료</span>'))
            else:
                heads.append(Markup('<a class="chat-attach" href="/doc/%s">첨부 · %s</a>') % (did, name) if did
                             else Markup('<span class="chat-attach">첨부 · %s</span>') % name)
            rest = rest[m.end():]
        if not heads:
            return escape(text or "")
        head = Markup(" ").join(heads)
        return head + Markup("<br>") + escape(rest) if rest else head

    templates.env.filters["attach_view"] = _attach_view
    templates.env.filters["tidy_reply"] = _ko.tidy_reply          # 예외 이름 빼기·되풀이 문장 한 번만(저장 때도 같은 함수, db.add_chat)
    templates.env.filters["particles"] = _ko.attach_particles      # 「제목」 을 → 「제목」을 — 옛 답변도 화면에서 바로잡는다
    templates.env.filters["chat_title"] = _chat_title              # 대화 제목 — 전각→반각, 프로젝트 안에서는 접두어 생략

    @app.post("/account/password")
    def account_password(
        request: Request, current_pw: str = Form(""), new_pw: str = Form(...),
        confirm: str = Form(""), back: str = Form("/"),
    ):
        """내 비밀번호 변경 — 로그인한 본인 계정만. 현재 비밀번호를 맞혀야 바꿀 수 있다.
        화면은 「보기」 토글로 확인 칸 없이 보내고, 확인 칸(confirm)은 예전 폼·스크립트를 위해 왔을 때만 맞춰 본다.
        규칙(길이·현재와 다름)은 passwords.problem 한 곳."""
        from zzaimy.app import passwords as _pw_rules

        if password is None:
            raise HTTPException(400, "인증 없는 로컬 모드에서는 계정이 없습니다")
        uid = getattr(request.state, "user", "")
        dest = _local_back(back)
        # 「/settings#password」처럼 조각(#)이 붙은 자리면 결과 글(?ok=/err=)은 조각 앞에 — 뒤에 붙으면 배너가 안 보인다
        path, _, frag = dest.partition("#")
        sep = "&" if "?" in path else "?"

        def _go(kind: str, msg: str) -> RedirectResponse:
            return RedirectResponse(f"{path}{sep}{kind}={_q(msg)}" + (f"#{frag}" if frag else ""), status_code=303)

        bad = _pw_rules.problem(new_pw, current_pw)
        # 확인부터 쓰기까지 계정 잠금 안에서 — 그 사이 관리자 화면이 같은 계정을 고쳐도 서로 덮지 않는다
        with _org_lock.LOCK:
            if not _verify_pw(uid, current_pw):
                return _go("err", "현재 비밀번호가 맞지 않습니다")
            if bad:
                return _go("err", bad)
            if confirm and new_pw != confirm:
                return _go("err", "새 비밀번호 확인이 일치하지 않습니다")
            acct = accounts.get(uid)
            if acct is None:
                return _go("err", "계정을 찾을 수 없습니다")
            _set_pw(acct, new_pw)
            acct.pop("must_change", None)
            _save_accounts()
        return _go("ok", "비밀번호를 변경했습니다")

    # ---- 문서 작업 — 구글 독스를 화면 안에 두고 에이전트가 같은 문서를 읽고, 담당자가 누른 자리에만 쓴다(ADR-0029) ----

    def _usable_google(request: Request) -> set[str]:
        """이 담당자가 쓸 수 있는 구글 계정(본인·부서 공용, 관리자는 전부) — 남이 연결한 계정은 고를 수 없다."""
        from zzaimy.ingest import gdrive_files
        return gdrive_files.usable_accounts(db, getattr(request.state, "user", ""), getattr(request.state, "dept", "") or None,
                                            getattr(request.state, "role", ""))

    def _gdocs_accounts(request: Request) -> list[dict]:
        from zzaimy.ingest import gdocs, gdrive

        mine = _usable_google(request)
        return [{**a, "docs_ok": gdocs.has_docs_scope(a["email"])} for a in gdrive.list_accounts() if a["email"] in mine]

    def _gdocs_account(request: Request, account: str) -> str:
        if account and account not in _usable_google(request):
            raise HTTPException(403, "이 계정으로 쓸 수 있는 구글 계정이 아닙니다 — 설정에서 본인 학교 계정을 연결해 주세요")
        return account

    def _gdocs_page(request: Request, doc: str, account: str, **extra):
        from zzaimy.ingest import gdocs

        accounts_ = _gdocs_accounts(request)
        account = _gdocs_account(request, account) or (accounts_[0]["email"] if accounts_ else "")
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
        _gdocs_account(request, account)
        from zzaimy.app import access_guard as ag
        from zzaimy.ingest import gdocs

        q = question.strip()
        if not q:
            return _gdocs_page(request, doc, account, err="질문을 적어 주세요")
        owner = getattr(request.state, "user", "zzaimy")
        role, dept = _acct_role_dept(owner)
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
                kw["scope"] = ag.search_scope(dept, role, "" if _view_as(owner) else owner, Path(db_path).parent,
                                              grade=_gr.user_grade(accounts.get(owner), _view_as(owner)))
            answer = r_.answer(db, q, **kw)
            # 범위 밖 부서 자료를 가리키는 질문 — 대화 화면과 같은 안내(검색은 원래 범위 안에서만 돈다)
            scope_msg = ag.scope_note(q, dept, role, sorted({*(d.get("dept") or "" for d in db.department_counts()), *_all_depts()}),
                                      _extra_depts(owner) if role in _ap.STAFF_ROLES else ())
            if scope_msg:
                ag.audit(Path(db_path).parent, owner, "scope", q, dept, role)
                answer = scope_msg + "\n\n" + answer
        except Exception as e:
            from zzaimy.generate.client import describe_llm_error

            answer = describe_llm_error(e)
        answer = _scrub_internal(answer)
        return _gdocs_page(request, doc, account, question=q, answer=answer, draft_text=answer)

    @app.post("/gdocs/insert")
    def gdocs_insert(request: Request, doc: str = Form(""), account: str = Form(""), section: int = Form(0),
                     text: str = Form("")):
        from zzaimy.ingest import gdocs

        _gdocs_account(request, account)
        owner = getattr(request.state, "user", "zzaimy")
        try:
            r = gdocs.insert_into_section(account, doc, section, text, user=owner, data_dir=Path(db_path).parent,
                                          scrub=_scrub_internal)
        except (ValueError, PermissionError, FileNotFoundError, RuntimeError) as e:
            return RedirectResponse(f"/gdocs/work?{urlencode({'doc': doc, 'account': account, 'err': str(e)})}", status_code=303)
        msg = f"「{r['section']}」 아래에 {r['chars']}자를 넣었습니다"
        return RedirectResponse(f"/gdocs/work?{urlencode({'doc': doc, 'account': account, 'ok': msg})}", status_code=303)

    @app.post("/gdocs/replace")
    def gdocs_replace(request: Request, doc: str = Form(""), account: str = Form(""), old: str = Form(""),
                      new: str = Form("")):
        from zzaimy.ingest import gdocs

        _gdocs_account(request, account)
        owner = getattr(request.state, "user", "zzaimy")
        try:
            r = gdocs.replace_text(account, doc, old, new, user=owner, data_dir=Path(db_path).parent, scrub=_scrub_internal)
        except (ValueError, PermissionError, FileNotFoundError, RuntimeError) as e:
            return RedirectResponse(f"/gdocs/work?{urlencode({'doc': doc, 'account': account, 'err': str(e)})}", status_code=303)
        msg = f"{r['count']}곳을 바꿨습니다"
        return RedirectResponse(f"/gdocs/work?{urlencode({'doc': doc, 'account': account, 'ok': msg})}", status_code=303)

    def _project_by_name(owner: str, name: str) -> dict | None:
        from zzaimy.app.project_search import search as _ps

        want = (name or "").strip()
        if not want:
            return None
        hits = _ps(db, owner, want).get("projects", [])
        exact = [p for p in hits if p["name"].strip() == want]
        return (exact or hits or [None])[0]

    @app.post("/api/gimport/check")
    def gimport_check(request: Request):
        """화면을 열 때 내 드라이브 가져오기 폴더를 곧바로 한 번 확인한다(뒤에서). 주기(1분)를 기다리지 않게."""
        from zzaimy.app import gdrive_import as _gi
        if getattr(request.state, "role", "") == "student" or getattr(request.state, "view_as", None):
            return {"state": "none"}
        return {"state": _gi.check_now(db, processor, inbox_dir, getattr(request.state, "user", ""), **_gi._ctx)}

    @app.get("/api/gimport/recent")
    def gimport_recent(request: Request):
        """새로 가져와 아직 확인하지 않은 내 문서 — 화면 아래 알림이 쓴다."""
        if getattr(request.state, "view_as", None):
            return {"items": []}
        return {"items": [{"id": d["id"], "name": storage.title_of(d.get("filename") or "")}
                          for d in _drive_new(getattr(request.state, "user", ""))]}

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
        _gdocs_account(request, account)
        if session_id:
            chat_documents.owned(db, session_id, owner)
        name = (title or (proj or {}).get("name") or "새 문서").strip()[:60]
        if not session_id:
            session_id = db.create_chat_session(name, project_id=project_id, owner=owner)
        try:
            made = gdrive_files.auto_document(db, session_id, owner, name, project_name=(proj or {}).get("name"),
                                              email=account or None)
            _share_sync(proj)
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
        from zzaimy.app import chat_documents, gdocs_agent

        owner = getattr(request.state, "user", "zzaimy")
        link = chat_documents.binding(db, sid, owner)
        if not link:
            raise HTTPException(400, "연결된 문서가 없습니다")
        lines = gdocs_agent.apply_pending(db, sid, owner, link, data_dir=Path(db_path).parent, scrub=_scrub_internal)
        if lines:
            db.add_chat(sid, "assistant", "적용됨:\n" + "\n".join(f"- {ln}" for ln in lines))
        return {"ok": True, "applied": lines}

    # 개발 현황(/dev·/dev/flow·/dev/db·/dev/quality*·/dev/eval/*·/dev/reindex·/dev/route-all) — 독립 라우터
    # (app/dev_overview_routes). 개발 현황 도우미 묶음(_ov)은 앱마다 만들고 문서·보고·RAG 공간 라우터와 자동 재색인이 함께 쓴다.
    # 구글 드라이브 연결(/dev/gdrive/*) — 독립 라우터(app/dev_gdrive_routes)
    from zzaimy.app import dev_gdrive_routes, dev_overview_routes
    _ov = dev_overview_routes.make_overview_helpers(db, db_path, _DOCS_DIR)
    app.state.overview = _ov
    app.state.chat_sources = _chat_sources
    app.state.auto_route = _auto_route
    app.state.local_path = _LOCAL_PATH
    app.include_router(dev_overview_routes.router)
    app.include_router(dev_gdrive_routes.router)

    # 재색인 표시가 켜지면 앱이 알아서 돌린다 — 규정이 들어올 때마다 켜지는데 사람이 누르기를 기다리면 표시가 늘 떠 있다.
    # 바뀐 조각만 계산하고(52) 앱은 멈추지 않는다. 연달아 들어오는 반입이 끝나도록 표시가 몇 분 그대로일 때 돌리고,
    # 다른 무거운 작업이 돌면(66 의 잠금) 다음 차례에 다시 본다
    if password is not None and os.environ.get("ZZAIMY_AUTO_REINDEX", "1") != "0":
        import threading as _th_rx

        def _auto_reindex_loop():
            from zzaimy.app import reindex_state
            seen_at: dict = {}
            while True:
                time.sleep(300)
                try:
                    # 규정 조각 지문이 색인과 다르면 다시 만든다 — 연달아 바뀌는 중이면 한 차례(5분) 더 기다려 모아서
                    if not reindex_state.needed(db):
                        seen_at.clear()
                        continue
                    first = seen_at.setdefault("at", time.time())
                    if time.time() - first >= 180 and not app.state.overview.reindex_running():
                        logging.getLogger(__name__).info("규정 조각이 색인과 달라 자동으로 다시 만든다")
                        app.state.overview.start_reindex()
                except Exception as e:
                    logging.getLogger(__name__).warning("자동 재색인 시작 실패: %s", e)
        _th_rx.Thread(target=_auto_reindex_loop, name="auto-reindex", daemon=True).start()

    # LLM 연결 관리(/dev/llm/*)·데이터 공방(/dev/data*) — 독립 라우터(app/dev_data_routes). Label Studio 도우미는 앱마다 만든다
    # 데이터·학습(/dev/train)·도구·모델 연결(/dev/tools) — 독립 라우터(app/dev_train_routes). 함께 쓰는 도우미를 넘긴다
    # RAG 공간 관리(/dev/rag*) — 독립 라우터(app/dev_rag_routes). 개인정보 점검(/dev/pii*) — 독립 라우터(app/dev_pii_routes)
    from zzaimy.app import dev_data_routes, dev_pii_routes, dev_rag_routes, dev_train_routes
    _ls = dev_data_routes.make_ls_helpers(db)
    app.state.llm_redirect = dev_data_routes._llm_redirect
    app.state.ls_client = _ls.ls_client
    app.state.ls_status = _ls.ls_status
    app.state.grounded_cache = _ls.grounded_cache
    app.state.grounded_ls_status = _ls.grounded_ls_status
    app.state.grounded_status_note = dev_data_routes._grounded_status_note
    app.include_router(dev_data_routes.router)
    app.include_router(dev_train_routes.router)
    app.state.all_depts = lambda: _all_depts()     # 부서 목록 — 아래(라이브러리 폴더)에서 정의되므로 부를 때 찾는다
    app.state.reindex_running = _ov.reindex_running
    app.include_router(dev_rag_routes.router)
    app.include_router(dev_pii_routes.router)
    # 문서 반입(/dev/intake*) — 독립 라우터(app/dev_intake_routes). 문서·보고(/dev/docs·/dev/doc/·/dev/paper·
    # /dev/history·/dev/weekly*) — 독립 라우터(app/dev_docs_routes). 개발 현황 묶음(_ov)의 문서 도우미를 넘긴다
    from zzaimy.app import dev_docs_routes, dev_intake_routes
    app.state.db_path = db_path
    app.state.processor = processor
    app.state.inbox_dir = inbox_dir
    _docs = dev_docs_routes.make_docs_helpers(
        _DOCS_DIR, db_path, _dev_read=_ov.dev_read, _md_view=_md_view, _data_overview=_ov.data_overview,
        _dev_doc_list=_ov.dev_doc_list, _dev_papers=_ov.dev_papers, _PAPER_LABELS=_ov.paper_labels,
        _doc_last_changed=_ov.doc_last_changed, _dev_now_parts=_ov.dev_now_parts)
    app.state.docs = _docs
    app.include_router(dev_intake_routes.router)
    app.include_router(dev_docs_routes.router)
    # 모델 비교 결과(/dev/models/compare*) — VM 비교 실행이 남긴 파일을 읽기만 한다(app/model_compare)
    from zzaimy.app import model_compare
    model_compare.install(templates.env)
    app.include_router(model_compare.router)

    # 주간 보고 자동 작성 일정 — 작성 도우미는 문서·보고 라우터(app/dev_docs_routes)의 묶음(_docs)
    import threading as _weekly_threading
    _weekly_stop = _weekly_threading.Event()

    @app.on_event("startup")
    def _start_weekly_schedule():
        if os.environ.get("ZZAIMY_WEEKLY_AUTO", "1") == "0":
            return
        from zzaimy.app.weekly_schedule import run_schedule
        _weekly_stop.clear()
        def generate(stem, today):
            if not (_docs.weekly_dir() / f"{stem}.md").exists() and _docs.weekly_can_auto():
                _docs.weekly_kick(stem, today)
        _weekly_threading.Thread(target=run_schedule, args=(_weekly_stop, generate),
                                 daemon=True, name="weekly-schedule").start()

    @app.on_event("shutdown")
    def _stop_weekly_schedule():
        _weekly_stop.set()

    def _user_admin_status(request: Request) -> dict | None:
        """설정 화면의 '내 구글 계정' — 인증 없는 로컬 모드에는 계정이 없다."""
        if password is None:
            return None
        from zzaimy.app.user_admin import user_status

        uid = getattr(request.state, "user", "")
        return user_status(db, uid, accounts.get(uid, {}).get("dept") or None)

    from zzaimy.app import webhwp as _webhwp
    from zzaimy.app import webhwp_agent as _wa_files

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, ok: str = "", err: str = ""):
        """내 설정 — 프로필·비밀번호·구글 계정을 한 화면에서. 편집기는 기본 웹 한글 기안기(대화 문서 창에서 바꾼다)."""
        uid = getattr(request.state, "user", "") or ""
        acct = accounts.get(uid, {}) if password is not None else {}
        is_student = request.state.role == "student"          # 학생은 비밀번호 변경만(프로필·편집기·구글 계정 없음)
        gst = _gimport.status(db, uid) if uid and not is_student else {}
        return templates.TemplateResponse(
            request, "settings.html",
            ctx(request, {"s": db.profile_for(uid or None), "active_tab": "all", "uid": uid,
                          "acct_dept": acct.get("dept", ""), "acct_role": acct.get("role", request.state.role),
                          "has_password": password is not None, "is_student": is_student,
                          # 성명 — 사람 이름 한 곳(app/people)과 같은 값. 장부에 관리자가 정한 이름이 있으면 읽기만
                          "my_name": _person_name(uid or None, fallback=""),
                          "name_locked": password is not None and _people.name_locked(uid, accounts),
                          "google": None if is_student else _user_admin_status(request), "ok": ok, "err": err,
                          "gimport": gst, "gimport_folder": _gimport.FOLDER_NAME,
                          "gimport_minutes": _gimport.interval_minutes(), "gimport_stalled": _gimport.stalled(gst)}),
        )

    @app.post("/account/google/import")
    def account_google_import(request: Request):
        """지금 가져오기 — 내 드라이브 「ZZAIMY 가져오기」 폴더의 새·바뀐 파일을 라이브러리로."""
        from zzaimy.ingest import gdrive_files
        uid = getattr(request.state, "user", "") or ""
        email = (db.get_setting(f"google_account:{uid}", "") or "").strip() if uid else ""
        if not email or not gdrive_files.has_file_scope(email):
            return RedirectResponse("/settings?err=" + _q("구글 계정을 먼저 연결해 주세요") + "#myGoogle", status_code=303)
        try:
            got = _gimport.run_for_user(db, processor, inbox_dir, uid, email, dept=(accounts.get(uid, {}) or {}).get("dept", ""))
        except Exception as e:
            return RedirectResponse("/settings?err=" + _q(f"가져오지 못했습니다 — {e}") + "#myGoogle", status_code=303)
        msg = f"새로 가져온 문서 {len(got['imported'])}건" + (f" · 건너뜀 {len(got['errors'])}건" if got["errors"] else "")
        return RedirectResponse("/settings?ok=" + _q(msg) + "#myGoogle", status_code=303)

    @app.post("/settings")
    def settings_save(
        request: Request,
        name: str = Form(""),
    ):
        """설정 저장 — 성명만(호칭·개인 응답 지침은 뺐다, 지침은 프로젝트 「작성 원칙」)."""
        uid = getattr(request.state, "user", "") or ""
        if uid and password is not None and _people.name_locked(uid, accounts):
            # 관리자가 장부에 정한 이름은 본인이 바꾸지 않는다(사용자·계정 화면에서 관리자가)
            return RedirectResponse("/settings?err=" + _q("성명은 관리자가 정합니다"), status_code=303)
        if uid:
            prof = db.profile_for(uid) or {}
            db.set_profile(uid, name=name, call_me=prof.get("call_me", ""), instructions=prof.get("instructions", ""))
        else:                                               # 인증 없는 로컬 모드 — 계정이 없으니 설정 하나에
            db.set_setting("name", name.strip()[:4000])
        return RedirectResponse("/settings?ok=" + _q("저장했습니다"), status_code=303)

    def _ref_scope(request: Request) -> dict:
        return _req_viewer(request)

    @app.get("/api/projects/browse")
    def projects_browse(request: Request, q: str = "", status: str = "archived", year: str = "", unit: str = "",
                        for_project: int | None = None, sort: str = ""):
        """통합 프로젝트 찾기 JSON — 형식은 project_refs.browse 참조. 연결은 POST /project/{id}/refs."""
        from zzaimy.app import project_refs
        if for_project is not None:
            fp = db.get_project(for_project)
            if fp is None or (fp.get("owner") != request.state.user and request.state.role != "dev"):
                raise HTTPException(404)
        return project_refs.browse(db, _ref_scope(request), q, status if status in ("archived", "active", "all") else "archived",
                                   year, unit, for_project, sort=sort, shared=_shared_project_ids(request))

    def _can_edit_doc(request: Request, doc: dict | None) -> bool:
        return _may_edit_doc(request, doc)

    def _lib_back(key: str = "", **msg) -> RedirectResponse:
        qs = {"type": "all", **({"folder": key} if key else {}), **msg}
        return RedirectResponse("/?" + urlencode(qs), status_code=303)

    @app.post("/view-as")
    def view_as_set(request: Request, role: str = Form(""), dept: str = Form(""), grade: str = Form(""), back: str = Form("/")):
        """개발자 「테스트 보기」 켜기 — 고른 역할·부서로 화면·검색·대화를 본다. 개발자 계정만, 권한은 좁히기만."""
        if getattr(request.state, "real_role", request.state.role) != "dev":
            raise HTTPException(403, "개발자 계정 전용입니다")
        if role not in VIEW_AS_ROLES:
            raise HTTPException(400, "고를 수 없는 역할입니다")
        dept = (dept or "").strip()
        if dept and dept not in _all_depts():
            raise HTTPException(400, "없는 부서입니다")
        if grade and (role != "staff" or grade not in _gr.GRADES):
            raise HTTPException(400, "고를 수 없는 등급입니다")
        db.set_setting(f"view_as:{request.state.user}", _aj.dumps({"role": role, "dept": "" if role == "student" else dept,
                                                                    "grade": grade if role == "staff" else ""}))
        dest = _local_back(back) if not _local_back(back).startswith("/dev") else "/"
        return RedirectResponse("/chat" if role == "student" else dest, status_code=303)

    @app.post("/view-as/clear")
    def view_as_clear(request: Request, back: str = Form("/")):
        """테스트 보기 끄기 — 원래 개발자 권한으로."""
        if getattr(request.state, "real_role", request.state.role) != "dev":
            raise HTTPException(403, "개발자 계정 전용입니다")
        db.set_setting(f"view_as:{request.state.user}", "")
        dest = _local_back(back)
        return RedirectResponse(dest, status_code=303)

    templates.env.globals["view_as_roles"] = {r: _ROLES.get(r, r) for r in VIEW_AS_ROLES}
    templates.env.globals["view_as_depts"] = lambda: _all_depts()
    templates.env.globals["request_grade"] = _gr.request_top_grade                  # 메뉴 보이기용(겸직 포함)
    templates.env.globals["program_scope"] = lambda request: {"admin": "all", "head": "dept", "lead": "own", "clerk": "own"}.get(_gr.request_top_grade(request), "")   # 사이드바 「사업 관리」(일반·학생은 안 보임)
    templates.env.globals["view_as_grades"] = dict(_gr.GRADES)      # 테스트 보기의 교직원 등급(select name="grade", 비우면 기본 등급)

    def _all_depts() -> list[str]:
        """부서 목록 한 곳 — 부서 RAG·폴더 짝·계정·사용자 관리의 부서 공용(user_admin.known_depts)."""
        from zzaimy.app import user_admin as _ua
        return _ua.known_depts(db, accounts, Path(db_path).parent)

    def _scope_choices(request: Request) -> list[str]:
        """이 계정이 고를 수 있는 검색 범위 — 공통 RAG, 관리자는 모든 부서 RAG, 교직원은 소속 부서와 추가로 받은 부서의 RAG."""
        if request.state.role == "dev":
            return ["공통", *_all_depts()]
        mine = (getattr(request.state, "dept", "") or "").strip()
        out = ["공통", *([mine] if mine else [])]
        if request.state.role in _ap.STAFF_ROLES and not getattr(request.state, "view_as", None):
            out += [d for d in _extra_depts(getattr(request.state, "user", "")) if d not in out]
        return out

    @app.post("/library/folders")
    def library_folder_create(request: Request, name: str = Form(""), dept: str = Form("공통")):
        """라이브러리 폴더 만들기 — 검색 범위는 공통 RAG(기본) 또는 부서 RAG."""
        from zzaimy.app import library_folders as lf
        if dept not in _scope_choices(request):
            return _lib_back(err=f"고를 수 없는 공개 범위: {dept}")
        fid, err = lf.create(db, Path(db_path).parent, name, dept, by=request.state.user or "", allowed=_all_depts())
        if err:
            return _lib_back(err=err)
        return _lib_back(f"{lf.MADE}{fid}", ok=f"「{' '.join(name.split())}」 폴더를 만들었습니다 · 공개 {dept if dept != '공통' else '전체'}")

    @app.post("/library/folders/scope")
    def library_folder_scope(request: Request, key: str = Form(""), dept: str = Form("공통")):
        """폴더의 검색 범위 바꾸기 — 폴더의 문서가 함께 옮겨 간다. 관리자만."""
        from zzaimy.app import library_folders as lf
        if request.state.role != "dev":
            raise HTTPException(403, "관리자만 바꿀 수 있습니다")
        n, err = lf.set_dept(db, Path(db_path).parent, key, dept, allowed=_all_depts())
        if err:
            return _lib_back(key, err=err)
        return _lib_back(key, ok=f"공개 범위를 「{dept if dept != '공통' else '전체 공개'}」로 바꿨습니다 · 문서 {n:,}건")

    @app.post("/library/folders/delete")
    def library_folder_delete(request: Request, key: str = Form("")):
        """만든 폴더 지우기 — 문서는 그대로(폴더에서만 빠진다). 관리자만."""
        from zzaimy.app import library_folders as lf
        if request.state.role != "dev":
            raise HTTPException(403, "관리자만 지울 수 있습니다")
        n = lf.delete(db, key)
        if n < 0:
            return _lib_back(err="원본 폴더는 지울 수 없습니다 — 원본 보관소의 폴더입니다")
        return _lib_back(ok=f"폴더를 지웠습니다 · 문서 {n:,}건은 라이브러리에 그대로 있습니다")

    @app.post("/library/upload")
    def library_upload(request: Request, background: BackgroundTasks, files: list[UploadFile] = File(...),
                       folder: str = Form(""), dept: str = Form("공통"), doc_type: str = Form("auto"),
                       program: str = Form(""), new_program: str = Form(""), new_ministry: str = Form(""), year: str = Form("")):
        """라이브러리에 직접 올리기 — 프로젝트 없이. 폴더를 고르면 그 폴더의 검색 범위, 아니면 고른 범위(기본 공통 RAG)."""
        from zzaimy.app import library_folders as lf
        from zzaimy.app.access_policy import classify
        if request.state.role == "student":
            raise HTTPException(403, "학생 계정에서는 올릴 수 없습니다")
        if doc_type not in INBOX_TYPES:
            doc_type = "auto"
        data_dir = Path(db_path).parent
        scope = lf.folder_dept(db, data_dir, folder) if folder else (dept or "공통")
        if scope not in _scope_choices(request) and not (folder and scope == "공통"):
            return _lib_back(folder, err=f"이 계정은 「{scope}」 부서 RAG 에 올릴 수 없습니다")
        owner = getattr(request.state, "user", "zzaimy")
        # 사업 — 고른 기존 사업 또는 새 사업(장부에 등록). 그 사업 × 연도 묶음에 넣어 사업 목록·그래프에서 함께 보인다
        project_id, prog_label = None, ""
        if program == "_new":
            from zzaimy.app import program_register as _pr
            from zzaimy.app import paths as _paths
            program, prog_label, err = _pr.register(_paths.platform_dir() / "kg_external.json", new_program, new_ministry, by=owner)
            if err:
                return _lib_back(folder, err=err)
        elif program.startswith("program:"):
            from zzaimy.graph import kg_store as _ks
            _ks.ensure(db)
            with db._conn() as conn:
                row = conn.execute("SELECT label FROM kg_nodes WHERE id = ? AND type = 'program'", (program,)).fetchone()
            if not row:                                    # 그래프에 없는 사업 열쇠는 받지 않는다(새 사업은 「새 사업 등록」으로)
                return _lib_back(folder, err="없는 사업입니다 — 목록에서 고르거나 「새 사업 등록」으로 만듭니다")
            prog_label = row[0] or program.split(":", 1)[1]
        else:
            program = ""
        if program:
            from zzaimy.app import archive as _archive
            y = year if year.isdigit() and len(year) == 4 else None
            project_id = _archive.program_project(db, program, prog_label, None, y)
            if doc_type == "auto":
                doc_type = "grant"                  # 사업을 고른 문서는 사업 문서(사업 문서 검색에 든다)
        made, skipped = [], []
        for f in files:
            name = f.filename or "이름없음"
            suffix = Path(name).suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS:
                skipped.append(name)
                continue
            stored = inbox_dir / f"{uuid.uuid4().hex}{suffix}"
            with stored.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            d_dept, d_level = classify(doc_type, owner=owner, dept=scope)
            doc_id = db.add_document(filename=name, stored_path=str(stored), doc_type=doc_type, owner=owner,
                                     dept=d_dept, access_level=d_level, project_id=project_id)
            stored = storage.adopt_original(db, doc_id, stored)
            background.add_task(processor.process, db, doc_id, stored)
            made.append(doc_id)
        if folder.startswith(lf.MADE):
            lf.put(db, folder, made)
        msg = f"{len(made)}건을 올렸습니다 · 공개 {scope if scope != '공통' else '전체'}" + \
            (f" · 사업 {prog_label}" if prog_label else "") + " · 처리가 끝나면 검색에 들어갑니다"
        if skipped:
            msg += f" · 형식이 맞지 않아 건너뜀 {len(skipped)}건"
        return _lib_back(folder if folder.startswith(lf.MADE) else "", ok=msg)

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

    def _doc_scope(request: Request) -> dict:
        return _req_viewer(request)

    @app.post("/institution")
    def set_institution(request: Request, 대학명: str = Form(""), 주소: str = Form(""), 총장: str = Form(""), 대표전화: str = Form("")):
        """기관 정보 설정 — 문서함에서 인출한 값을 담당자가 바로잡거나 없는 값(총장 등)을 채운다. 빈 값은 인출값으로 돌아간다."""
        from zzaimy.app import institution

        for k, v in (("대학명", 대학명), ("주소", 주소), ("총장", 총장), ("대표전화", 대표전화)):
            institution.set_fact(db, k, v)
        return RedirectResponse("/criteria#institution", status_code=303)

    # 구글 드라이브 자동 가져오기(「ZZAIMY 가져오기」 폴더, 10분마다) — 구글 앱이 등록된 운영에서만 돈다
    if password is not None:
        _gimport.ensure_scheduler(db, processor, inbox_dir, dept_of=lambda u: (accounts.get(u, {}) or {}).get("dept", ""),
                                  rank=lambda u: {"staff": 0, "dev": 1}.get((accounts.get(u, {}) or {}).get("role", ""), 2))
    # 작업 문서 공유 점검 — 10분마다 공유가 켜졌거나 준 권한이 남은 프로젝트를 다시 맞춘다. 동료의 부서·역할이 바뀌거나 계정이 지워지고,
    # 근거 문서의 열람 등급이 바뀐 것도 여기서 거둔다(담당자가 화면을 열기만 기다리지 않게). 구글 앱이 등록된 운영에서만
    if password is not None:
        from zzaimy.ingest import drive_share as _ds_sweep, gdrive as _gd_sweep
        if _gd_sweep.configured() and os.environ.get("ZZAIMY_SHARE_SWEEP", "1") != "0":
            import threading as _th_sweep

            def _sweep_loop():
                time.sleep(120)
                while True:
                    try:
                        with db._conn() as _c:
                            _all = [dict(r) for r in _c.execute("SELECT * FROM projects ORDER BY id").fetchall()]
                        for pj in _all:
                            if pj.get("archived") and not _ds_sweep.has_grants(db, int(pj["id"])):
                                continue
                            if _ds_sweep.enabled(db, int(pj["id"])) or _ds_sweep.has_grants(db, int(pj["id"])):
                                _share_sync(pj, wait=True)
                        # 부서 드라이브 — 연결된 부서마다 부서 사람에게만 권한(구글을 새로 연결한 사람도 여기서 받는다)
                        from zzaimy.ingest import dept_drive as _dd
                        for _k, _v in db.all_settings().items():
                            if _k.startswith("google_root_by:") and (_v or "").strip():
                                _dd.sync(db, _k.split(":", 1)[1], accounts, Path(db_path).parent)
                    except Exception as e:
                        logging.getLogger(__name__).warning("작업 문서 공유 점검 실패: %s", e)
                    time.sleep(600)
            _th_sweep.Thread(target=_sweep_loop, name="drive-share-sweep", daemon=True).start()
    def _before_google_disconnect(user: str, email: str) -> bool:
        """구글 연결을 끊기 전에 그 계정이 준 작업 문서 보기 권한을 거둔다 — 토큰이 사라지면 거둘 길이 없다."""
        from zzaimy.ingest import drive_share as _dsx
        with db._conn() as _c:
            pids = [int(r[0]) for r in _c.execute("SELECT id FROM projects").fetchall()]
        ok = True
        for pid_ in pids:
            if _dsx.has_grants(db, pid_):
                ok = _dsx.revoke_by(db, pid_, email) and ok
        return ok
    app.state.before_google_disconnect = _before_google_disconnect
    # 에이전트 대화(/chat*) — 독립 라우터(app/chat_routes). 답 만들기·진행 표·근거 표와 문서 작업 도우미는 여기(앱마다)에서 만들어
    # 대화 묶음(app.state.chat)으로 넘긴다. 함께 쓰는 도우미가 다 정의된 뒤라 맨 끝에 둔다
    from zzaimy.app import chat_routes
    app.state.chat = chat_routes.make_chat_helpers(
        db=db,
        db_path=db_path,
        processor=processor,
        drafter=drafter,
        templates=templates,
        ctx=ctx,
        accounts=accounts,
        password=password,
        chat_topics=chat_topics,
        chat_revisions=chat_revisions,
        _chat_running=_chat_running,
        _chat_progress=_chat_progress,
        _chat_sources=_chat_sources,
        _chat_pages=_chat_pages,
        _RETRIEVAL_NOTE=_RETRIEVAL_NOTE,
        _owned_chat=_owned_chat,
        _can_read_chat=_can_read_chat,
        _reader_doc_ok=_reader_doc_ok,
        _criteria_docs=_criteria_docs,
        _viewer_sees=_viewer_sees,
        _extra_depts=_extra_depts,
        _local_back=_local_back,
        _share_sync=_share_sync,
        _chat_suggestions=_chat_suggestions,
        _intake_bundle=_intake_bundle,
        _link_past_materials=_link_past_materials,
        _link_past_projects=_link_past_projects,
        _remember_given=_remember_given,
        _schedule_answer=_schedule_answer,
        _set_options=_set_options,
        _title_from_bundle=_title_from_bundle,
        _link_criteria_kind=_link_criteria_kind,
        _all_depts=_all_depts,
        _chat_step=_chat_step,
        _scrub_internal=_scrub_internal,
        _session_viewer=_session_viewer,
        _view_as=_view_as,
        _auto_route=_auto_route,
        _identify_document=_identify_document,
        _can_edit_doc=_can_edit_doc,
    )
    app.include_router(chat_routes.router)
    # 프로젝트(/project*·/projects*) — 독립 라우터(app/project_routes). 묶음 반입·지난 사업 참조·문서 세 갈래·부서 공유 같은
    # 함께 쓰는 도우미는 여기(앱마다)에서 만들어 프로젝트 묶음(app.state.project)으로 넘긴다. 다 정의된 뒤라 맨 끝에 둔다
    from zzaimy.app import project_routes
    app.state.project = project_routes.make_project_helpers(
        db=db,
        templates=templates,
        ctx=ctx,
        _req_viewer=_req_viewer,
        _visible=_visible,
        _dept_peers=_dept_peers,
        _ref_scope=_ref_scope,
        _doc_groups=_doc_groups,
        _business_units=_business_units,
        _program_options=_program_options,
        _project_suggestions=_project_suggestions,
        _share_sync=_share_sync,
        _can_read_chat=_can_read_chat,
        _drive_doc_open=_drive_doc_open,
        _intake_bundle=_intake_bundle,
        _link_past_projects=_link_past_projects,
        _title_from_bundle=_title_from_bundle,
    )
    app.include_router(project_routes.router)
    # 문서 화면(/doc/{id})·문서 고치기·여러 문서(/docs/*) — 독립 라우터(app/doc_routes). 열람 범위·검색 범위·갈래 다시 정하기 같은
    # 함께 쓰는 도우미는 여기(앱마다)에서 만들어 문서 화면 묶음(app.state.doc_page)으로 넘긴다. 다 정의된 뒤라 맨 끝에 둔다
    from zzaimy.app import doc_routes
    app.state.may_edit_doc = _may_edit_doc          # 문서 고치기 권한(access_policy.can_edit) — 문서 라우터가 같은 판정을 쓴다
    app.state.doc_page = doc_routes.make_doc_page_helpers(
        db=db,
        db_path=db_path,
        processor=processor,
        drafter=drafter,
        templates=templates,
        ctx=ctx,
        CRITERIA_LIST=CRITERIA_LIST,
        _req_viewer=_req_viewer,
        _visible=_visible,
        _scope_choices=_scope_choices,
        _all_depts=_all_depts,
        _local_back=_local_back,
        _auto_route=_auto_route,
        _identify_document=_identify_document,
        _can_edit_doc=_can_edit_doc,
    )
    app.include_router(doc_routes.router)
    # 읽지 못한 문서 다시 하기(다시 읽기·파일 바꾸기·글자 읽기 맡기기·기안기로 다시 저장·지우기) — app/read_fix_routes
    from zzaimy.app import read_failure as _read_failure, read_fix_routes
    app.include_router(read_fix_routes.router)
    _read_failure.start_worker(db, processor)          # 글자 읽기 대기열에 남은 일이 있으면 이어서(없으면 띄우지 않는다)
    # 문서 파일 열기·내려받기·내보내기(/doc/{id}/original·view·export.* 등) — 독립 라우터(app/doc_file_routes). 뒤 작업 표는 묶음 안에(앱마다)
    from zzaimy.app import doc_file_routes
    app.state.doc_file = doc_file_routes.make_doc_file_helpers(
        db=db,
        db_path=db_path,
        templates=templates,
        accounts=accounts,
        password=password,
        _visible=_visible,
        _doc_scope=_doc_scope,
    )
    app.include_router(doc_file_routes.router)
    app.state.page_ctx = ctx
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
    # 공인 IP 장비에서 무인증·무암호화로 외부에 노출하지 않는다(risks.md §8).
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
