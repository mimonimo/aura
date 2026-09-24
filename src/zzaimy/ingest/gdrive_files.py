"""드라이브에 폴더·문서를 만든다 — 에이전트가 자동으로 작업 문서를 준비하기 위해(ADR-0029 개정, 2026-09-23).

사용자 지적: 문서 주소를 손으로 붙여 넣게 하면 너무 불편하다. 새 채팅에서 초안을 요청하면 드라이브에 프로젝트 폴더와
문서를 만들어 대화에 이어야 한다. 허용 범위에 drive.file 이 필요하다(이 앱이 만든 파일만 다룬다 — 사용자의 다른
파일은 여전히 읽기 전용 범위로만 본다).

폴더 규칙: ZZAIMY/<연도>/<프로젝트 이름>/ , 프로젝트가 없으면 ZZAIMY/<연도>/기타/ . 같은 이름 폴더가 있으면 다시 만들지 않는다.
"""

from __future__ import annotations

import json
from datetime import datetime

from zzaimy.ingest import gdocs, gdrive

FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
ROOT_FOLDER = "ZZAIMY"


def has_file_scope(email: str) -> bool:
    for a in gdrive.list_accounts():
        if a["email"] == email:
            return FILE_SCOPE in (a.get("scopes") or [])
    return False


def _headers(email: str, http) -> dict:
    return {"Authorization": f"Bearer {gdrive.access_token(email, http)}"}


def _find_folder(email: str, name: str, parent: str, http) -> str | None:
    safe = name.replace("\\", "\\\\").replace("'", "\\'")
    q = f"name = '{safe}' and mimeType = '{gdrive.FOLDER}' and '{parent}' in parents and trashed = false"
    r = http.get(f"{gdrive.API}/files", headers=_headers(email, http),
                 params={"q": q, "fields": "files(id,name)", "pageSize": 5, "supportsAllDrives": "true"})
    if r.status_code != 200:
        return None
    files = r.json().get("files") or []
    return files[0]["id"] if files else None


def ensure_folder(email: str, parts: list[str], http=None, root: str = "root") -> str:
    """ZZAIMY/<연도>/<이름> 처럼 경로를 따라 폴더를 찾거나 만든다. 돌려주는 것은 마지막 폴더 id.

    root 는 시작 폴더다. 기본은 내 드라이브 최상위, 부서 공용 드라이브(공유 드라이브·공유 폴더)가 있으면 그 id."""
    http = http or gdrive._http()
    parent = root or "root"
    for name in parts:
        fid = _find_folder(email, name, parent, http)
        if not fid:
            r = http.post(f"{gdrive.API}/files", headers=_headers(email, http),
                          params={"supportsAllDrives": "true"},
                          json={"name": name, "mimeType": gdrive.FOLDER, "parents": [parent]})
            if r.status_code != 200:
                raise PermissionError("드라이브에 폴더를 만들 권한이 없습니다 — 계정 허용을 다시 해 주세요(파일 만들기 범위)")
            fid = r.json()["id"]
        parent = fid
    return parent


def create_document(email: str, title: str, folder_id: str | None = None, body: str = "", http=None) -> str:
    """구글 독스를 만들고(제목·머리글) 폴더로 옮긴다. 돌려주는 것은 문서 id."""
    http = http or gdrive._http()
    h = _headers(email, http)
    r = http.post(gdocs.DOCS_API, headers=h, json={"title": title})
    if r.status_code != 200:
        raise PermissionError("구글 독스를 만들지 못했습니다 — 계정 허용(문서 편집 범위)을 확인해 주세요")
    doc_id = r.json()["documentId"]
    text = f"{title}\n" + (body + "\n" if body else "")
    reqs = [{"insertText": {"location": {"index": 1}, "text": text}},
            {"updateParagraphStyle": {"range": {"startIndex": 1, "endIndex": 1 + len(title) + 1},
                                      "paragraphStyle": {"namedStyleType": "TITLE"}, "fields": "namedStyleType"}}]
    http.post(f"{gdocs.DOCS_API}/{doc_id}:batchUpdate", headers=h, json={"requests": reqs})
    if folder_id:
        http.patch(f"{gdrive.API}/files/{doc_id}", headers=h,
                   params={"addParents": folder_id, "removeParents": "root", "supportsAllDrives": "true"}, json={})
    return doc_id


OTHER_FOLDER = "기타"


def project_parts(project_name: str | None, chat_title: str = "") -> list[str]:
    """폴더 경로 — 프로젝트가 있으면 ZZAIMY/<연도>/<프로젝트>, 없으면 ZZAIMY/<연도>/기타 (대화마다 폴더를 만들면 드라이브가 어지럽다)."""
    name = (project_name or "").strip()[:60] or OTHER_FOLDER
    return [ROOT_FOLDER, f"{datetime.now():%Y}", name]


# ---- 계정·부서 체계 (계정이 여럿이 될 때) ----
# 플랫폼 계정마다 자기 구글 계정을 잇는다(허용을 누른 사람에게 자동으로 묶인다: settings google_account:<user>).
# 부서 공용 자리(공유 드라이브·공유 폴더 id)는 settings google_root:<부서> 에 두면 그 아래에 만든다 — 담당자가 바뀌어도
# 문서가 개인 드라이브에 갇히지 않는다. 부서 공용 계정은 google_account_dept:<부서>.

def bind_account(db, user: str, email: str) -> None:
    if user and email:
        db.set_setting(f"google_account:{user}", email)


def account_for(db, user: str | None, dept: str | None = None) -> str:
    """이 담당자가 쓸 구글 계정 — 본인 계정 → 부서 공용 계정 → (허용 계정이 하나뿐이면) 그것. 없으면 빈 문자열."""
    ok = {a["email"] for a in gdrive.list_accounts() if gdocs.has_docs_scope(a["email"])}
    for key in ((f"google_account:{user}" if user else ""), (f"google_account_dept:{dept}" if dept else "")):
        if key:
            email = (db.get_setting(key, "") or "").strip()
            if email in ok:
                return email
    return next(iter(ok)) if len(ok) == 1 else ""


def root_for(db, dept: str | None) -> str:
    """부서 공용 드라이브 자리(폴더 id). 없으면 내 드라이브 최상위."""
    return (db.get_setting(f"google_root:{dept}", "") or "root").strip() if dept else "root"


def move_document(email: str, doc_id: str, folder_id: str, http=None) -> dict:
    """문서를 다른 폴더로 옮긴다(부모 교체). 이 앱이 만든 파일이어야 한다."""
    http = http or gdrive._http()
    h = _headers(email, http)
    r = http.get(f"{gdrive.API}/files/{doc_id}", headers=h, params={"fields": "parents", "supportsAllDrives": "true"})
    parents = ",".join((r.json().get("parents") or [])) if r.status_code == 200 else ""
    r = http.patch(f"{gdrive.API}/files/{doc_id}", headers=h,
                   params={"addParents": folder_id, "removeParents": parents, "supportsAllDrives": "true", "fields": "id,parents"},
                   json={})
    if r.status_code == 403:
        raise PermissionError("이 문서를 옮길 권한이 없습니다 — 플랫폼이 만든 문서만 옮길 수 있습니다")
    if r.status_code != 200:
        raise RuntimeError(f"문서 옮기기 실패({r.status_code})")
    return {"ok": True, "folder": folder_id}


def move_to_project(db, session_id: int, project_name: str | None, dept: str | None = None, http=None) -> dict | None:
    """대화에 이어진 문서를 프로젝트 폴더로(프로젝트가 없어지면 기타로) 옮긴다. 이어진 문서가 없으면 None."""
    raw = db.get_setting(f"chat_google_doc:{session_id}", "") or ""
    if not raw:
        return None
    link = json.loads(raw)
    http = http or gdrive._http()
    folder = ensure_folder(link["account"], project_parts(project_name), http, root=root_for(db, dept))
    move_document(link["account"], link["doc"], folder, http=http)
    return {"doc": link["doc"], "folder": folder, "project": project_name or OTHER_FOLDER}


def auto_document(db, session_id: int, owner: str, title: str, project_name: str | None = None,
                  email: str | None = None, dept: str | None = None, http=None) -> dict:
    """대화에 문서가 없으면 드라이브 폴더와 문서를 만들어 잇는다. 돌려주는 것은 {doc, account, folder}."""
    email = email or account_for(db, owner, dept)
    if not email:
        raise ValueError("이 계정에 이어진 구글 계정이 없습니다 — 원천 관리에서 구글 계정 허용을 해 주세요")
    if not has_file_scope(email):
        raise PermissionError("드라이브에 파일을 만들 권한이 없습니다 — 원천 관리에서 구글 계정 허용을 다시 해 주세요")
    http = http or gdrive._http()
    folder = ensure_folder(email, project_parts(project_name, title), http, root=root_for(db, dept))
    doc_id = create_document(email, title, folder, http=http)
    db.set_setting(f"chat_google_doc:{session_id}", json.dumps({"doc": doc_id, "account": email}))
    return {"doc": doc_id, "account": email, "folder": folder, "url": f"https://docs.google.com/document/d/{doc_id}/edit"}


def rename_document(email: str, doc_id: str, title: str, http=None) -> dict:
    """드라이브 문서 이름을 바꾼다(구글 독스 제목도 같이 바뀐다). 이 앱이 만든 파일이거나 사용자가 연 파일이어야 한다."""
    http = http or gdrive._http()
    title = (title or "").strip()[:120]
    if not title:
        raise ValueError("새 이름을 적어 주세요")
    r = http.patch(f"{gdrive.API}/files/{doc_id}", headers=_headers(email, http),
                   params={"supportsAllDrives": "true", "fields": "id,name"}, json={"name": title})
    if r.status_code == 403:
        raise PermissionError("이 문서의 이름을 바꿀 권한이 없습니다 — 플랫폼이 만든 문서만 바꿀 수 있습니다")
    if r.status_code != 200:
        raise RuntimeError(f"이름 바꾸기 실패({r.status_code})")
    return {"ok": True, "title": r.json().get("name", title)}

