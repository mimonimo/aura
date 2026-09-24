"""구글 드라이브 원천 — 읽기 전용으로 폴더의 문서를 가져와 공유 폴더(NAS)와 같은 반입 경로를 태운다.

1단계(2026-09-22, ADR-0028): 드라이브에는 아무것도 쓰지 않는다. 목록·내려받기·내보내기(구글 독스 → docx,
시트 → xlsx, 프레젠테이션 → pdf)만 한다. 가져온 파일은 로컬 inbox 로 들어가 업로드와 같은 처리(마스킹 → 조각 → 색인)를 받는다.

인증은 담당자가 자기 구글 계정으로 한 번 허용하는 OAuth 2.0 이다. 클라이언트 ID·비밀은 관리자가 화면에서 넣고
`data/platform/gdrive_oauth.json`(0600)에, 계정별 갱신 토큰은 `gdrive_tokens.json`(0600)에 둔다. 값은 화면에 되돌려 보이지 않는다.
허용 범위(SCOPES)는 지금 읽기 전용 하나다. 2단계(독스 편집·댓글)는 범위를 넓히고 다시 허용받는다.

구글 클라이언트 라이브러리 없이 httpx 로 REST 를 직접 부른다 — 의존성을 늘리지 않고 오프라인 VM 에 설치할 것이 없다.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlencode

# drive.readonly: 폴더 목록·내려받기(1단계). documents: 문서 작업 화면에서 같은 문서를 읽고 고치기(2단계, ADR-0029).
# drive.file: 이 앱이 만든 폴더·문서만 만들고 고친다(에이전트가 작업 문서를 자동으로 준비, gdrive_files).
SCOPES = ["https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/documents",
          "https://www.googleapis.com/auth/drive.file"]
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
USERINFO = "https://www.googleapis.com/oauth2/v2/userinfo"
FOLDER = "application/vnd.google-apps.folder"
# 구글 고유 형식 → 내보내기 형식과 확장자. 그 밖의 구글 형식(그림·폼·사이트)은 건너뛴다.
EXPORTS = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
}
FIELDS = "nextPageToken,files(id,name,mimeType,size,modifiedTime)"
_lock = threading.Lock()
_pending_states: dict[str, float] = {}


def _data_dir() -> Path:
    return Path(os.environ.get("ZZAIMY_DATA_DIR", "data/platform"))


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, path)
    path.chmod(0o600)


# ---------------- 클라이언트(앱) 설정 ----------------

def client_config() -> dict:
    return _read(_data_dir() / "gdrive_oauth.json", {})


def set_client(client_id: str, client_secret: str) -> None:
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise ValueError("클라이언트 ID 는 '….apps.googleusercontent.com' 이어야 합니다")
    if not client_secret:
        raise ValueError("클라이언트 비밀을 적어 주세요")
    with _lock:
        _write(_data_dir() / "gdrive_oauth.json", {"client_id": client_id, "client_secret": client_secret,
                                                   "set_at": time.strftime("%Y-%m-%d %H:%M")})


def configured() -> bool:
    c = client_config()
    return bool(c.get("client_id") and c.get("client_secret"))


def public_status() -> dict:
    """화면에 보여 줄 상태 — 비밀값 없이."""
    c = client_config()
    cid = c.get("client_id", "")
    return {"configured": configured(), "client_id_hint": (cid[:12] + "…") if cid else "",
            "set_at": c.get("set_at", ""), "accounts": list_accounts(), "scopes": SCOPES}


# ---------------- 계정 토큰 ----------------

def _tokens() -> dict:
    return _read(_data_dir() / "gdrive_tokens.json", {})


def list_accounts() -> list[dict]:
    return [{"email": k, "granted_at": v.get("granted_at", ""), "scopes": v.get("scopes", [])}
            for k, v in sorted(_tokens().items())]


def revoke(email: str) -> None:
    with _lock:
        t = _tokens()
        t.pop(email, None)
        _write(_data_dir() / "gdrive_tokens.json", t)


def auth_url(redirect_uri: str) -> str:
    """담당자를 구글 허용 화면으로 보낸다. state 는 되돌아올 때 대조한다(10분)."""
    c = client_config()
    if not configured():
        raise ValueError("구글 클라이언트 ID·비밀이 아직 없습니다")
    state = secrets.token_urlsafe(24)
    with _lock:
        now = time.time()
        for k in [k for k, v in _pending_states.items() if now - v > 600]:
            _pending_states.pop(k, None)
        _pending_states[state] = now
    q = {"client_id": c["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
         "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "state": state,
         "include_granted_scopes": "true"}
    return f"{AUTH_URL}?{urlencode(q)}"


def _http():
    import httpx

    # 큰 독스(사업계획서 95쪽, 표 124개)는 documents.get 에 30초가 넘는다(실측 2026-09-24) — 읽기 제한을 넉넉히
    return httpx.Client(timeout=httpx.Timeout(180, connect=30))


def exchange_code(code: str, state: str, redirect_uri: str, http=None) -> str:
    """허용 화면에서 돌아온 코드를 토큰으로 바꾸고 계정(이메일) 아래에 보관한다. 돌려주는 것은 이메일."""
    with _lock:
        if state not in _pending_states:
            raise ValueError("허용 요청이 만료됐거나 일치하지 않습니다 — 다시 시도해 주세요")
        _pending_states.pop(state, None)
    c = client_config()
    http = http or _http()
    r = http.post(TOKEN_URL, data={"code": code, "client_id": c["client_id"], "client_secret": c["client_secret"],
                                   "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    if r.status_code != 200:
        raise ValueError(f"토큰 교환 실패({r.status_code}): {r.text[:160]}")
    tok = r.json()
    if not tok.get("refresh_token"):
        raise ValueError("갱신 토큰이 오지 않았습니다 — 구글 계정 설정에서 이 앱의 접근 권한을 지우고 다시 허용해 주세요")
    email = _whoami(tok["access_token"], http) or f"account-{secrets.token_hex(3)}"
    with _lock:
        t = _tokens()
        t[email] = {"refresh_token": tok["refresh_token"], "access_token": tok.get("access_token", ""),
                    "expires_at": time.time() + int(tok.get("expires_in", 0)) - 60,
                    "scopes": (tok.get("scope") or " ".join(SCOPES)).split(),
                    "granted_at": time.strftime("%Y-%m-%d %H:%M")}
        _write(_data_dir() / "gdrive_tokens.json", t)
    return email


def _whoami(token: str, http) -> str:
    """토큰 주인의 이메일 — 허용 범위에 이메일 조회가 없어도 드라이브 계정 정보(about.user)로 안다."""
    h = {"Authorization": f"Bearer {token}"}
    try:
        r = http.get(f"{API}/about", headers=h, params={"fields": "user(emailAddress)"})
        if r.status_code == 200 and (r.json().get("user") or {}).get("emailAddress"):
            return r.json()["user"]["emailAddress"]
        u = http.get(USERINFO, headers=h)
        if u.status_code == 200 and u.json().get("email"):
            return u.json()["email"]
    except Exception:
        pass
    return ""


def relabel_accounts(http=None) -> list[tuple[str, str]]:
    """'account-…' 로 저장된 계정을 실제 이메일로 바꾼다(허용 직후 이메일을 못 받았던 것). 돌려주는 것은 (전, 후) 목록."""
    http = http or _http()
    changed: list[tuple[str, str]] = []
    for key in [k for k in _tokens() if k.startswith("account-")]:
        try:
            email = _whoami(access_token(key, http), http)
        except ValueError:
            continue
        if email and email != key:
            with _lock:
                t = _tokens()
                t[email] = t.pop(key)
                _write(_data_dir() / "gdrive_tokens.json", t)
            changed.append((key, email))
    return changed


def access_token(email: str, http=None) -> str:
    """계정의 접근 토큰 — 만료됐으면 갱신 토큰으로 새로 받는다."""
    t = _tokens()
    acc = t.get(email)
    if not acc:
        raise ValueError(f"허용된 구글 계정이 아닙니다: {email or '(없음)'} — 먼저 계정 허용을 해 주세요")
    if acc.get("access_token") and time.time() < float(acc.get("expires_at", 0)):
        return acc["access_token"]
    c = client_config()
    http = http or _http()
    r = http.post(TOKEN_URL, data={"refresh_token": acc["refresh_token"], "client_id": c.get("client_id", ""),
                                   "client_secret": c.get("client_secret", ""), "grant_type": "refresh_token"})
    if r.status_code != 200:
        raise ValueError(f"토큰 갱신 실패({r.status_code}) — 계정 허용을 다시 해 주세요")
    tok = r.json()
    with _lock:
        t = _tokens()
        t.setdefault(email, acc).update(access_token=tok["access_token"],
                                        expires_at=time.time() + int(tok.get("expires_in", 3600)) - 60)
        _write(_data_dir() / "gdrive_tokens.json", t)
    return tok["access_token"]


# ---------------- 원천 백엔드 (nas_sync 와 같은 계약: check · walk · read · listdir) ----------------

_FOLDER_URL = re.compile(r"folders/([A-Za-z0-9_-]{4,})")
_FILE_URL = re.compile(r"/d/([A-Za-z0-9_-]{4,})")


def folder_id(root: str) -> str:
    """폴더 ID 또는 드라이브 URL → 폴더 ID. 'root' 는 내 드라이브 최상위."""
    s = (root or "").strip()
    m = _FOLDER_URL.search(s) or _FILE_URL.search(s)
    if m:
        return m.group(1)
    if "?" in s or "/" in s:
        raise ValueError("드라이브 폴더 주소(…/folders/ID) 또는 폴더 ID 를 적어 주세요")
    return s or "root"


def _mtime(iso: str) -> float:
    try:
        return time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except (ValueError, TypeError):
        return 0.0


class GDriveBackend:
    """드라이브 폴더 하나 — 읽기 전용. 상대 경로는 폴더 이름 경로이고 구글 형식 파일은 내보내기 확장자를 붙여 이름 짓는다."""

    def __init__(self, root: str, account: str = "", http=None):
        self.folder = folder_id(root)
        self.account = (account or "").strip()
        self._http = http
        self._ids: dict[str, tuple[str, str]] = {}      # 상대 경로 → (파일 id, 구글 형식)

    def _client(self):
        if self._http is None:
            self._http = _http()
        return self._http

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {access_token(self.account, self._client())}"}

    def _get(self, url: str, **params):
        r = self._client().get(url, headers=self._headers(), params=params)
        if r.status_code == 401:
            raise PermissionError("구글 접근 권한이 없습니다 — 계정 허용을 다시 해 주세요")
        if r.status_code == 404:
            raise FileNotFoundError("드라이브에서 폴더나 파일을 찾지 못했습니다 — 공유 여부와 ID 를 확인해 주세요")
        if r.status_code != 200:
            raise RuntimeError(f"드라이브 응답 {r.status_code}: {r.text[:120]}")
        return r

    def check(self) -> None:
        if not self.account:
            raise ValueError("허용한 구글 계정을 골라 주세요")
        self._get(f"{API}/files/{self.folder}", fields="id,name,mimeType", supportsAllDrives="true")

    def _children(self, fid: str) -> list[dict]:
        out: list[dict] = []
        token = None
        while True:
            params = {"q": f"'{fid}' in parents and trashed = false", "fields": FIELDS, "pageSize": 200,
                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "orderBy": "name"}
            if token:
                params["pageToken"] = token
            data = self._get(f"{API}/files", **params).json()
            out.extend(data.get("files", []))
            token = data.get("nextPageToken")
            if not token:
                return out

    @staticmethod
    def _entry_name(f: dict) -> str | None:
        mime = f.get("mimeType", "")
        if mime == FOLDER:
            return None
        if mime.startswith("application/vnd.google-apps."):
            exp = EXPORTS.get(mime)
            if not exp:
                return None            # 그림·폼·사이트 같은 것은 문서가 아니다
            name = f["name"]
            return name if name.lower().endswith(exp[1]) else name + exp[1]
        return f["name"]

    def walk(self, recursive: bool = True) -> Iterator[tuple[str, int, float]]:
        stack = [(self.folder, "")]
        while stack:
            fid, prefix = stack.pop()
            for f in self._children(fid):
                if f.get("name", "").startswith((".", "~$")):
                    continue
                if f.get("mimeType") == FOLDER:
                    if recursive:
                        stack.append((f["id"], f"{prefix}{f['name']}/"))
                    continue
                name = self._entry_name(f)
                if not name:
                    continue
                rel = prefix + name
                self._ids[rel] = (f["id"], f.get("mimeType", ""))
                yield rel, int(f.get("size") or 0), _mtime(f.get("modifiedTime", ""))

    def listdir(self, rel: str = "") -> tuple[list[dict], list[tuple[str, int, float]]]:
        fid = self._resolve_folder(rel)
        dirs, files = [], []
        for f in self._children(fid):
            if f.get("name", "").startswith((".", "~$")):
                continue
            if f.get("mimeType") == FOLDER:
                dirs.append({"name": f["name"]})
                continue
            name = self._entry_name(f)
            if name:
                self._ids[(rel + "/" if rel else "") + name] = (f["id"], f.get("mimeType", ""))
                files.append((name, int(f.get("size") or 0), _mtime(f.get("modifiedTime", ""))))
        return dirs, files

    def _resolve_folder(self, rel: str) -> str:
        fid = self.folder
        for part in [p for p in (rel or "").split("/") if p]:
            hit = next((f for f in self._children(fid) if f.get("mimeType") == FOLDER and f.get("name") == part), None)
            if not hit:
                raise FileNotFoundError(f"폴더가 없습니다: {rel}")
            fid = hit["id"]
        return fid

    def read(self, rel: str) -> bytes:
        if rel not in self._ids:
            folder, _, name = rel.rpartition("/")
            self.listdir(folder)
        if rel not in self._ids:
            raise FileNotFoundError(f"파일이 없습니다: {rel}")
        fid, mime = self._ids[rel]
        if mime.startswith("application/vnd.google-apps."):
            exp = EXPORTS[mime]
            return self._get(f"{API}/files/{fid}/export", mimeType=exp[0]).content
        return self._get(f"{API}/files/{fid}", alt="media", supportsAllDrives="true").content
