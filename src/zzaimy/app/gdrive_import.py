"""구글 드라이브 자동 가져오기 — 계정을 연결한 사람의 드라이브 「ZZAIMY 가져오기」 폴더에 넣은 파일을 라이브러리로 가져온다.

드라이브 전체가 아니라 이 폴더 하나만 본다(개인 파일이 섞이지 않게, 2026-10-06 사용자 결정). 새 파일·바뀐 파일만 가져오고,
라이브러리 업로드와 같은 처리(판독·분류)를 거쳐 그 사람의 문서로 들어간다. 구글 문서·시트·슬라이드는 docx·xlsx·pptx 로 내보내 가져온다.
가져온 기록은 구글 메일 기준 — 설정 gimport:<메일>:<파일 id> = 수정 시각|문서 번호(여러 계정이 같은 구글 계정에 이어져도 한 번만 들인다).
상태는 gimport_status:<계정>.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

FOLDER_NAME = "ZZAIMY 가져오기"
INTERVAL = 600                       # 10분마다
MAX_BYTES = 100_000_000
SUFFIXES = {".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".png", ".jpg", ".jpeg", ".csv"}
EXPORT = {                            # 구글 고유 형식 → 내보낼 형식
    "application/vnd.google-apps.document": (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "application/vnd.google-apps.spreadsheet": (".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "application/vnd.google-apps.presentation": (".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
}
_lock = threading.Lock()


def bound_users(db) -> list[tuple[str, str]]:
    """(계정, 연결한 구글 메일) — 본인이 연결한 것만(부서 공용 계정은 가져오기 대상이 아니다)."""
    out = []
    with db._conn() as conn:
        for r in conn.execute("SELECT key, value FROM settings WHERE key LIKE 'google_account:%'").fetchall():
            user, email = str(r[0]).split(":", 1)[1], (r[1] or "").strip()
            if user and email:
                out.append((user, email))
    return out


def status(db, user: str) -> dict:
    try:
        return json.loads(db.get_setting(f"gimport_status:{user}", "") or "{}")
    except ValueError:
        return {}


def _list(email: str, folder_id: str, http) -> list[dict]:
    from zzaimy.ingest import gdrive, gdrive_files
    files, token = [], None
    while True:
        params = {"q": f"'{folder_id}' in parents and trashed = false", "pageSize": 200,
                  "fields": "nextPageToken, files(id,name,mimeType,modifiedTime,size)", "supportsAllDrives": "true"}
        if token:
            params["pageToken"] = token
        r = http.get(f"{gdrive.API}/files", headers=gdrive_files._headers(email, http), params=params)
        if r.status_code != 200:
            raise RuntimeError(f"폴더 목록을 읽지 못했습니다({r.status_code})")
        body = r.json()
        files += body.get("files") or []
        token = body.get("nextPageToken")
        if not token:
            return files


def _download(email: str, f: dict, http) -> tuple[bytes, str]:
    from zzaimy.ingest import gdrive, gdrive_files
    name = f["name"]
    if f["mimeType"] in EXPORT:
        ext, mime = EXPORT[f["mimeType"]]
        r = http.get(f"{gdrive.API}/files/{f['id']}/export", headers=gdrive_files._headers(email, http), params={"mimeType": mime})
        if not name.lower().endswith(ext):
            name += ext
    else:
        r = http.get(f"{gdrive.API}/files/{f['id']}", headers=gdrive_files._headers(email, http),
                     params={"alt": "media", "supportsAllDrives": "true"})
    if r.status_code != 200:
        raise RuntimeError(f"내려받지 못했습니다({r.status_code})")
    return r.content, name


def run_for_user(db, processor, inbox_dir: Path, user: str, email: str, dept: str = "", http=None) -> dict:
    """한 사람의 가져오기 폴더를 훑어 새·바뀐 파일을 들인다. {imported, skipped, errors, folder_id}."""
    from zzaimy.app import storage
    from zzaimy.app.access_policy import classify
    from zzaimy.ingest import gdrive, gdrive_files

    http = http or gdrive._http()
    folder_id = gdrive_files.ensure_folder(email, [FOLDER_NAME], http=http)
    imported, skipped, errors = [], 0, []
    for f in _list(email, folder_id, http):
        if f["mimeType"] == gdrive.FOLDER:
            continue
        key = f"gimport:{email}:{f['id']}"
        seen = (db.get_setting(key, "") or "").split("|", 1)[0]
        if seen and seen == f.get("modifiedTime"):
            skipped += 1
            continue
        name = f["name"]
        suffix = EXPORT.get(f["mimeType"], (Path(name).suffix.lower(), ""))[0]
        if suffix not in SUFFIXES:
            errors.append(f"{name}: 가져올 수 없는 형식")
            db.set_setting(key, f"{f.get('modifiedTime')}|")              # 같은 파일을 매번 다시 보지 않게
            continue
        if int(f.get("size") or 0) > MAX_BYTES:
            errors.append(f"{name}: 100MB 초과")
            db.set_setting(key, f"{f.get('modifiedTime')}|")
            continue
        try:
            data, name = _download(email, f, http)
        except RuntimeError as e:
            errors.append(f"{name}: {e}")
            continue
        stored = Path(inbox_dir) / f"{uuid.uuid4().hex}{suffix}"
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_bytes(data)
        doc_dept, level = classify("auto", owner=user, uploader_dept=dept or "")
        doc_id = db.add_document(filename=name, stored_path=str(stored), doc_type="auto", owner=user, dept=doc_dept, access_level=level)
        stored = storage.adopt_original(db, doc_id, stored)
        try:
            processor.process(db, doc_id, stored)
        except Exception as e:                                         # 판독 실패도 문서는 남는다(문서 화면에서 다시)
            log.warning("가져온 문서 처리 실패 doc=%s: %s", doc_id, e)
        db.set_setting(key, f"{f.get('modifiedTime')}|{doc_id}")
        imported.append({"doc_id": doc_id, "name": name})
    st = status(db, user)
    st.update({"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "folder_id": folder_id, "email": email,
               "imported_total": int(st.get("imported_total") or 0) + len(imported),
               "last_imported": [x["name"] for x in imported][:5] or st.get("last_imported", []), "errors": errors[:5]})
    db.set_setting(f"gimport_status:{user}", json.dumps(st, ensure_ascii=False))
    return {"imported": imported, "skipped": skipped, "errors": errors, "folder_id": folder_id}


def run_all(db, processor, inbox_dir: Path, dept_of=None, rank=None) -> dict:
    """연결된 구글 계정마다 한 번 — 같은 메일에 여러 계정이 이어져 있으면 rank(계정) 가 작은 계정이 문서 주인(담당자 먼저)."""
    from zzaimy.ingest import gdrive_files
    if not _lock.acquire(blocking=False):
        return {"busy": True}
    try:
        out = {}
        by_email: dict[str, list[str]] = {}
        for user, email in bound_users(db):
            by_email.setdefault(email, []).append(user)
        for email, users in by_email.items():
            user = sorted(users, key=lambda u: (rank(u) if rank else 0, u))[0]
            if not gdrive_files.has_file_scope(email):
                continue
            try:
                got = run_for_user(db, processor, inbox_dir, user, email, dept=(dept_of(user) if dept_of else ""))
                out[user] = len(got["imported"])
            except Exception as e:
                log.warning("드라이브 가져오기 실패 %s: %s", user, e)
                st = status(db, user)
                st.update({"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "errors": [str(e)[:200]]})
                db.set_setting(f"gimport_status:{user}", json.dumps(st, ensure_ascii=False))
        return out
    finally:
        _lock.release()


_started = False


def ensure_scheduler(db, processor, inbox_dir: Path, dept_of=None, rank=None) -> None:
    """앱이 뜰 때 한 번 — 구글 앱이 등록돼 있을 때만 10분마다 돈다."""
    global _started
    from zzaimy.ingest import gdrive
    if _started or not gdrive.configured():
        return
    _started = True

    def loop():
        time.sleep(60)
        while True:
            try:
                run_all(db, processor, inbox_dir, dept_of, rank)
            except Exception as e:
                log.warning("드라이브 가져오기 주기 실패: %s", e)
            time.sleep(INTERVAL)
    threading.Thread(target=loop, name="gdrive-import", daemon=True).start()
