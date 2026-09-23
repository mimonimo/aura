"""드라이브에 폴더·문서를 만든다 — 에이전트가 자동으로 작업 문서를 준비하기 위해(ADR-0029 개정, 2026-09-23).

사용자 지적: 문서 주소를 손으로 붙여 넣게 하면 너무 불편하다. 새 채팅에서 초안을 요청하면 드라이브에 프로젝트 폴더와
문서를 만들어 대화에 이어야 한다. 허용 범위에 drive.file 이 필요하다(이 앱이 만든 파일만 다룬다 — 사용자의 다른
파일은 여전히 읽기 전용 범위로만 본다).

폴더 규칙: ZZAIMY/<연도>/<프로젝트 이름 또는 대화 제목>/ . 같은 이름 폴더가 있으면 다시 만들지 않는다.
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


def ensure_folder(email: str, parts: list[str], http=None) -> str:
    """ZZAIMY/<연도>/<이름> 처럼 경로를 따라 폴더를 찾거나 만든다. 돌려주는 것은 마지막 폴더 id."""
    http = http or gdrive._http()
    parent = "root"
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


def project_parts(project_name: str | None, chat_title: str) -> list[str]:
    name = (project_name or chat_title or "대화").strip()[:60] or "대화"
    return [ROOT_FOLDER, f"{datetime.now():%Y}", name]


def auto_document(db, session_id: int, owner: str, title: str, project_name: str | None = None,
                  email: str | None = None, http=None) -> dict:
    """대화에 문서가 없으면 드라이브 폴더와 문서를 만들어 잇는다. 돌려주는 것은 {doc, account, folder}."""
    accounts = [a["email"] for a in gdrive.list_accounts() if gdocs.has_docs_scope(a["email"])]
    email = email or (accounts[0] if accounts else "")
    if not email:
        raise ValueError("허용된 구글 계정이 없습니다")
    if not has_file_scope(email):
        raise PermissionError("드라이브에 파일을 만들 권한이 없습니다 — 원천 관리에서 구글 계정 허용을 다시 해 주세요")
    http = http or gdrive._http()
    folder = ensure_folder(email, project_parts(project_name, title), http)
    doc_id = create_document(email, title, folder, http=http)
    db.set_setting(f"chat_google_doc:{session_id}", json.dumps({"doc": doc_id, "account": email}))
    return {"doc": doc_id, "account": email, "folder": folder, "url": f"https://docs.google.com/document/d/{doc_id}/edit"}
