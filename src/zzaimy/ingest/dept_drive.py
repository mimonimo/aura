"""부서 드라이브 — 부서마다 「AURA · <부서>」 폴더 하나를 플랫폼이 만들고, 그 부서 사람(소속 + 추가 부서로 받은 교직원)의
학교 구글 계정에만 편집 권한을 준다(10-21 사용자: 부서 연결이 먼저, 그 부서에 연결된 계정만 접근).

- 연결: 관리자가 연결 계정(부서 공용 계정 또는 관리자 본인 계정)과 위치(공유 드라이브나 그 계정 드라이브의 폴더)를 고르면
  그 아래에 부서 폴더를 새로 만든다. 플랫폼이 만든 폴더라야 drive.file 범위로 권한을 주고 거둘 수 있다(이미 있는 폴더는 못 다룬다).
  설정: google_root:<부서>(폴더 id) · google_root_by:<부서>(만든 계정) · google_account_dept:<부서>(미연결 사용자가 쓰는 계정).
- 맞추기(sync): 목표 = 부서 사람 가운데 구글을 연결한 계정의 메일. 이 기능이 준 권한(gdept:<부서> = {메일: 권한 id})과 견주어
  주고 거둔다. 공유 알림 메일은 보내지 않는다. 사용자 부서가 바뀌거나 연결을 끊으면 다음 맞추기에서 거둔다.
- 공유 드라이브에 만들면 그 공유 드라이브의 구성원은 원래 모두 본다(구글 규칙) — 부서원만 보게 하려면 관리자만 구성원인 공유
  드라이브나 공용 계정의 드라이브에 만든다(화면 안내).
"""
from __future__ import annotations

import json
import logging
import threading
import time

from zzaimy.ingest import gdrive, gdrive_files

log = logging.getLogger(__name__)
_lock = threading.Lock()
STAFF = ("staff", "professor", "head")


def folder_name(dept: str) -> str:
    return f"AURA · {dept}"


def members(dept: str, accounts: dict, data_dir=None) -> list[str]:
    """부서 사람 — 소속 부서이거나 추가 부서로 받은 교직원(사용 중, 첫 비밀번호를 바꾼 계정)."""
    from zzaimy.app import access_policy
    out = []
    for u, a in accounts.items():
        a = a or {}
        if a.get("role", "staff") not in STAFF or a.get("active") is False or a.get("must_change"):
            continue
        if (a.get("dept") or "").strip() == dept or dept in access_policy.extra_depts(u, data_dir):
            out.append(u)
    return sorted(out)


def _granted(db, dept: str) -> dict[str, str]:
    try:
        return dict(json.loads(db.get_setting(f"gdept:{dept}", "{}") or "{}"))
    except ValueError:
        return {}


def state(db, dept: str, accounts: dict, data_dir=None) -> dict:
    """화면용 — 연결 여부, 폴더·만든 계정, 부서 사람 수, 권한을 받은 사람 수, 구글 미연결 사람, 마지막 맞추기 오류."""
    folder = (db.get_setting(f"google_root:{dept}", "") or "").strip()
    by = (db.get_setting(f"google_root_by:{dept}", "") or "").strip()
    mem = members(dept, accounts, data_dir)
    unlinked = [u for u in mem if not (db.get_setting(f"google_account:{u}", "") or "").strip()]
    try:
        last = json.loads(db.get_setting(f"gdept_status:{dept}", "{}") or "{}")
    except ValueError:
        last = {}
    return {"connected": bool(folder and by), "legacy": bool(folder and not by), "folder": folder, "by": by,
            "name": (db.get_setting(f"google_root_name:{dept}", "") or "").strip(),
            "members": mem, "unlinked": unlinked, "granted": len(_granted(db, dept)), "error": last.get("error", ""),
            "at": last.get("at", 0)}


def connect(db, dept: str, account: str, parent: str, http=None) -> str:
    """부서 폴더를 새로 만들고 부서 설정을 맞춘다 — 돌려주는 것은 폴더 id. 옛 연결의 권한은 맞추기에서 거둔다."""
    http = http or gdrive._http()
    r = http.post(f"{gdrive.API}/files", headers=gdrive_files._headers(account, http), params={"supportsAllDrives": "true"},
                  json={"name": folder_name(dept), "mimeType": gdrive.FOLDER, "parents": [parent or "root"]})
    if r.status_code != 200:
        raise PermissionError(f"부서 폴더를 만들지 못했습니다({r.status_code}) — 그 위치에 쓸 권한과 계정 허용을 확인해 주세요")
    fid = r.json()["id"]
    old_by = (db.get_setting(f"google_root_by:{dept}", "") or "").strip()
    if old_by and old_by != account:
        revoke_all(db, dept, http)                        # 옛 폴더에 준 권한은 그 계정으로 거둔다
    db.set_setting(f"google_root:{dept}", fid)
    db.set_setting(f"google_root_by:{dept}", account)
    db.set_setting(f"google_root_name:{dept}", folder_name(dept))
    db.set_setting(f"google_account_dept:{dept}", account)
    db.set_setting(f"gdept:{dept}", "{}")
    return fid


def revoke_all(db, dept: str, http=None) -> bool:
    by = (db.get_setting(f"google_root_by:{dept}", "") or "").strip()
    folder = (db.get_setting(f"google_root:{dept}", "") or "").strip()
    granted = _granted(db, dept)
    ok = True
    for email, pid in list(granted.items()):
        if _delete(by, folder, pid, http):
            granted.pop(email, None)
        else:
            ok = False
    db.set_setting(f"gdept:{dept}", json.dumps(granted))
    return ok


def _delete(by: str, folder: str, pid: str, http) -> bool:
    try:
        http = http or gdrive._http()
        r = http.delete(f"{gdrive.API}/files/{folder}/permissions/{pid}", headers=gdrive_files._headers(by, http),
                        params={"supportsAllDrives": "true"})
        return getattr(r, "status_code", 204) in (200, 204, 404)
    except Exception:
        return False


def sync(db, dept: str, accounts: dict, data_dir=None, http=None) -> dict:
    """부서 사람의 구글 계정에만 폴더 편집 권한 — 준 것과 목표를 맞춘다. 실패는 gdept_status 에 남기고 예외를 올리지 않는다."""
    folder = (db.get_setting(f"google_root:{dept}", "") or "").strip()
    by = (db.get_setting(f"google_root_by:{dept}", "") or "").strip()
    if not folder or not by:
        return {"added": 0, "removed": 0, "error": "부서 드라이브가 연결되지 않았습니다"}
    with _lock:
        target = {}
        for u in members(dept, accounts, data_dir):
            e = (db.get_setting(f"google_account:{u}", "") or "").strip().lower()
            if e and e != by.lower():
                target[e] = u
        granted = _granted(db, dept)
        added = removed = 0
        err = ""
        for email in [e for e in granted if e not in target]:
            if _delete(by, folder, granted[email], http):
                granted.pop(email)
                removed += 1
            else:
                err = err or "권한을 거두지 못했습니다 — 다음 맞추기에서 다시"
        todo = [e for e in target if e not in granted]
        if todo:
            try:
                if not gdrive_files.has_file_scope(by):
                    raise PermissionError("부서 폴더를 만든 계정의 허용이 없습니다 — 그 계정을 다시 연결해 주세요")
                http = http or gdrive._http()
                h = gdrive_files._headers(by, http)
                for email in todo:
                    r = http.post(f"{gdrive.API}/files/{folder}/permissions", headers=h,
                                  params={"sendNotificationEmail": "false", "supportsAllDrives": "true", "fields": "id"},
                                  json={"role": "writer", "type": "user", "emailAddress": email})
                    if r.status_code != 200:
                        raise RuntimeError(f"권한을 주지 못했습니다({r.status_code})")
                    granted[email] = r.json()["id"]
                    added += 1
                    db.set_setting(f"gdept:{dept}", json.dumps(granted))
            except Exception as e:
                err = err or str(e)[:200]
        db.set_setting(f"gdept:{dept}", json.dumps(granted))
        db.set_setting(f"gdept_status:{dept}", json.dumps({"at": time.time(), "error": err}, ensure_ascii=False))
        if err:
            log.warning("부서 %s 드라이브 권한 맞추기: %s", dept, err)
        return {"added": added, "removed": removed, "error": err}
