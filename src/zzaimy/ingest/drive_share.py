"""프로젝트 작업 폴더의 부서 공유 — 같은 부서 동료의 학교 구글 계정에 프로젝트 폴더 「보기」 권한을 주고 거둔다.

폴더(ZZAIMY/<연도>/<프로젝트>)에 권한을 주면 안의 작업 문서가 모두 따라간다(드라이브 권한 상속). 켜고 끄는 것은 프로젝트 담당자,
기본은 켬 — 프로젝트 대화가 기본으로 부서 공개인 것과 같게(docs/notes/2026-10-15-access-control.md 「같은 부서 공유」).
거둘 때는 이 기능이 준 권한만 거둔다(설정 gshare:<프로젝트> 에 적은 권한 id) — 담당자가 드라이브에서 직접 건 공유는 건드리지 않는다.
권한 범위는 drive.file 이라 플랫폼이 만든 폴더만 다룬다. 공유 알림 메일은 보내지 않는다.
"""
from __future__ import annotations

import json
import logging
import time

from zzaimy.ingest import gdrive, gdrive_files

log = logging.getLogger(__name__)

THROTTLE_S = 600                     # 프로젝트 화면을 열 때 맞추기 — 같은 프로젝트는 10분에 한 번


def enabled(db, project_id: int) -> bool:
    return (db.get_setting(f"project_share:{project_id}", "on") or "on") != "off"


def set_enabled(db, project_id: int, on: bool) -> None:
    db.set_setting(f"project_share:{project_id}", "on" if on else "off")


def _granted(db, project_id: int) -> dict[str, str]:
    try:
        return dict(json.loads(db.get_setting(f"gshare:{project_id}", "{}") or "{}"))
    except ValueError:
        return {}


def state(db, project_id: int) -> dict:
    """화면용 — 켬/끔, 이 기능이 보기 권한을 준 동료 메일 수, 마지막 맞춘 때·오류."""
    try:
        last = json.loads(db.get_setting(f"gshare_status:{project_id}", "{}") or "{}")
    except ValueError:
        last = {}
    return {"on": enabled(db, project_id), "shared": len(_granted(db, project_id)), **last}


def sync(db, project: dict, owner_email: str, peer_emails: list[str], dept: str | None = None, http=None) -> dict:
    """켜져 있으면 동료 메일마다 폴더 보기 권한을 주고(없는 것만), 동료가 아니게 된 메일의 권한은 거둔다. 꺼져 있으면 준 것을 모두 거둔다.
    돌려주는 것 {added, removed, shared, folder}. 실패는 gshare_status 에 남기고 예외를 올리지 않는다(문서 작업을 막지 않게)."""
    pid = int(project["id"])
    want = sorted({e.strip().lower() for e in peer_emails if e and e.strip().lower() != (owner_email or "").lower()}) if enabled(db, pid) else []
    granted = _granted(db, pid)
    added, removed = [], []
    folder = ""
    try:
        if not owner_email or not gdrive_files.has_file_scope(owner_email):
            raise PermissionError("담당자 구글 계정이 연결되지 않아 공유할 수 없습니다")
        http = http or gdrive._http()
        folder = gdrive_files.project_folder_for(db, owner_email, project, dept, http=http)
        h = gdrive_files._headers(owner_email, http)
        for email in want:
            if email in granted:
                continue
            r = http.post(f"{gdrive.API}/files/{folder}/permissions", headers=h,
                          params={"sendNotificationEmail": "false", "supportsAllDrives": "true", "fields": "id"},
                          json={"role": "reader", "type": "user", "emailAddress": email})
            if r.status_code != 200:
                raise RuntimeError(f"보기 권한을 주지 못했습니다({r.status_code})")
            granted[email] = r.json()["id"]
            added.append(email)
        for email in [e for e in list(granted) if e not in want]:
            r = http.delete(f"{gdrive.API}/files/{folder}/permissions/{granted[email]}", headers=h,
                            params={"supportsAllDrives": "true"})
            if getattr(r, "status_code", 204) not in (200, 204, 404):
                raise RuntimeError(f"보기 권한을 거두지 못했습니다({r.status_code})")
            granted.pop(email, None)
            removed.append(email)
        err = ""
    except Exception as e:                                         # 공유 실패가 문서 작성·화면을 막지 않는다
        err = str(e)[:200]
        log.warning("프로젝트 %s 폴더 공유 맞추기 실패: %s", pid, err)
    db.set_setting(f"gshare:{pid}", json.dumps(granted))
    db.set_setting(f"gshare_status:{pid}", json.dumps({"at": time.time(), "error": err, "missing": 0}, ensure_ascii=False))
    return {"added": added, "removed": removed, "shared": len(granted), "folder": folder, "error": err}


def due(db, project_id: int) -> bool:
    """화면을 열 때 맞출 때가 됐는가(10분에 한 번)."""
    try:
        at = float(json.loads(db.get_setting(f"gshare_status:{project_id}", "{}") or "{}").get("at") or 0)
    except ValueError:
        at = 0
    return time.time() - at >= THROTTLE_S
