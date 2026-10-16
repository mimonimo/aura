"""프로젝트 작업 문서의 부서 공유 — 같은 부서 동료의 학교 구글 계정에 작업 문서마다 「보기」 권한을 주고 거둔다.

폴더째 공유하지 않는다 — 한 폴더에 비공개 대화의 작업 문서, 동료가 못 보는 자료를 근거로 쓴 작업 문서가 함께 있기 때문이다.
어떤 문서를 누구에게 줄지는 호출하는 쪽(main._share_plan)이 대화 읽기와 같은 규칙으로 정해 {구글 파일 id: {메일}} 로 넘긴다.
이 모듈은 그 목표와 「이 기능이 준 권한」(설정 gshare:<프로젝트> = {"파일|메일": 권한 id}) 사이를 맞추기만 한다 —
목표에 없는 것은 거두고, 담당자가 드라이브에서 직접 건 공유는 건드리지 않는다. 권한 범위는 drive.file(플랫폼이 만든 파일)뿐이고
공유 알림 메일은 보내지 않는다. 기본은 켬, 담당자가 끈다. 프로젝트마다 잠금을 걸어 동시에 맞추다 기록을 잃지 않게 한다.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from zzaimy.ingest import gdrive, gdrive_files

log = logging.getLogger(__name__)

THROTTLE_S = 600                     # 프로젝트 화면을 열 때 맞추기 — 같은 프로젝트는 10분에 한 번
_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(project_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(int(project_id), threading.Lock())


def enabled(db, project_id: int) -> bool:
    return (db.get_setting(f"project_share:{project_id}", "on") or "on") != "off"


def set_enabled(db, project_id: int, on: bool) -> None:
    db.set_setting(f"project_share:{project_id}", "on" if on else "off")


def _granted(db, project_id: int) -> dict[str, str]:
    try:
        return dict(json.loads(db.get_setting(f"gshare:{project_id}", "{}") or "{}"))
    except ValueError:
        return {}


def has_grants(db, project_id: int) -> bool:
    return bool(_granted(db, project_id))


def state(db, project_id: int) -> dict:
    """화면용 — 켬/끔, 이 기능이 보기 권한을 준 사람 수(메일 기준)·문서 수, 마지막 맞춘 때·오류·못 받은 동료 수."""
    try:
        last = json.loads(db.get_setting(f"gshare_status:{project_id}", "{}") or "{}")
    except ValueError:
        last = {}
    g = _granted(db, project_id)
    return {"on": enabled(db, project_id), "shared": len({k.split("|", 1)[1] for k in g}), "files": len({k.split("|", 1)[0] for k in g}),
            **last}


def sync(db, project_id: int, owner_email: str, want: dict[str, set[str]], http=None, missing: int = 0) -> dict:
    """목표(want: 파일 id → 메일)와 이 기능이 준 권한을 맞춘다. 꺼져 있으면 목표는 비고 준 것을 모두 거둔다.
    거두기를 먼저 한다 — 실패해도 남은 권한은 기록에 남아 다음에 다시 거둔다(기록을 먼저 지우지 않는다).
    돌려주는 것 {added, removed, shared, error}. 실패는 gshare_status 에 남기고 예외를 올리지 않는다."""
    pid = int(project_id)
    with _lock(pid):
        owner = (owner_email or "").lower()
        target = set()
        if enabled(db, pid):
            target = {f"{fid}|{e.strip().lower()}" for fid, emails in (want or {}).items() if fid
                      for e in emails if e and e.strip() and e.strip().lower() != owner}
        granted = _granted(db, pid)
        added, removed, err = [], [], ""
        if target or granted:
            try:
                if not owner or not gdrive_files.has_file_scope(owner_email):
                    raise PermissionError("담당자 구글 계정이 연결되지 않아 공유를 맞출 수 없습니다")
                http = http or gdrive._http()
                h = gdrive_files._headers(owner_email, http)
                for key in [k for k in list(granted) if k not in target]:
                    fid = key.split("|", 1)[0]
                    r = http.delete(f"{gdrive.API}/files/{fid}/permissions/{granted[key]}", headers=h, params={"supportsAllDrives": "true"})
                    if getattr(r, "status_code", 204) not in (200, 204, 404):
                        raise RuntimeError(f"보기 권한을 거두지 못했습니다({r.status_code})")
                    granted.pop(key, None)
                    removed.append(key)
                    db.set_setting(f"gshare:{pid}", json.dumps(granted))      # 하나씩 바로 적는다 — 도중에 멈춰도 기록이 맞게
                for key in sorted(target - set(granted)):
                    fid, email = key.split("|", 1)
                    r = http.post(f"{gdrive.API}/files/{fid}/permissions", headers=h,
                                  params={"sendNotificationEmail": "false", "supportsAllDrives": "true", "fields": "id"},
                                  json={"role": "reader", "type": "user", "emailAddress": email})
                    if r.status_code != 200:
                        raise RuntimeError(f"보기 권한을 주지 못했습니다({r.status_code})")
                    granted[key] = r.json()["id"]
                    added.append(key)
                    db.set_setting(f"gshare:{pid}", json.dumps(granted))
            except Exception as e:                                         # 공유 실패가 문서 작성·화면을 막지 않는다 — 다음 맞추기에서 다시
                err = str(e)[:200]
                log.warning("프로젝트 %s 작업 문서 공유 맞추기 실패: %s", pid, err)
        db.set_setting(f"gshare:{pid}", json.dumps(granted))
        db.set_setting(f"gshare_status:{pid}", json.dumps({"at": time.time(), "error": err, "missing": int(missing)}, ensure_ascii=False))
        return {"added": added, "removed": removed, "shared": len({k.split("|", 1)[1] for k in granted}), "error": err}


def due(db, project_id: int) -> bool:
    """화면을 열 때 맞출 때가 됐는가(10분에 한 번)."""
    try:
        at = float(json.loads(db.get_setting(f"gshare_status:{project_id}", "{}") or "{}").get("at") or 0)
    except ValueError:
        at = 0
    return time.time() - at >= THROTTLE_S
