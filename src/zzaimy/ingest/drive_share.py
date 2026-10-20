"""프로젝트 작업 문서의 공유 — 프로젝트를 공유받은 사람(app/project_access)의 학교 구글 계정에 작업 문서마다
「보기」(공유 보기) 또는 「편집」(함께 작업) 권한을 주고 거둔다.

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


def _granted(db, project_id: int) -> dict[str, dict]:
    """이 기능이 준 권한 {"파일|메일": {"id": 권한 id, "by": 준 구글 계정}} — 거둘 때는 준 계정으로 거둔다(담당자 구글이 바뀌어도)."""
    try:
        raw = dict(json.loads(db.get_setting(f"gshare:{project_id}", "{}") or "{}"))
    except ValueError:
        return {}
    return {k: (v if isinstance(v, dict) else {"id": str(v), "by": ""}) for k, v in raw.items()}


def _email_of(key: str) -> str:
    """기록 열쇠 「파일|메일」(보기) 또는 「파일|메일|writer」(편집)의 메일."""
    return key.split("|", 2)[1] if "|" in key else key


def has_grants(db, project_id: int) -> bool:
    return bool(_granted(db, project_id))


def state(db, project_id: int) -> dict:
    """화면용 — 켬/끔, 이 기능이 보기 권한을 준 사람 수(메일 기준)·문서 수, 마지막 맞춘 때·오류·못 받은 동료 수."""
    try:
        last = json.loads(db.get_setting(f"gshare_status:{project_id}", "{}") or "{}")
    except ValueError:
        last = {}
    g = _granted(db, project_id)
    return {"on": enabled(db, project_id), "shared": len({_email_of(k) for k in g}), "files": len({k.split("|", 1)[0] for k in g}),
            **last}


def _revoke(db, pid: int, granted: dict[str, dict], keys: list[str], fallback_by: str, http) -> tuple[list[str], str]:
    """keys 의 권한을 준 계정으로 거둔다. 거둔 것은 바로 기록에서 빼고, 못 거둔 것은 남긴다(다음에 다시). (거둔 키, 첫 오류)."""
    removed, err = [], ""
    for key in keys:
        g = granted.get(key) or {}
        by = g.get("by") or fallback_by
        fid = key.split("|", 1)[0]
        try:
            if not by or not gdrive_files.has_file_scope(by):
                raise PermissionError("권한을 준 구글 계정의 허용이 없어 거두지 못했습니다 — 그 계정을 다시 연결해 주세요")
            http = http or gdrive._http()
            r = http.delete(f"{gdrive.API}/files/{fid}/permissions/{g.get('id')}", headers=gdrive_files._headers(by, http),
                            params={"supportsAllDrives": "true"})
            if getattr(r, "status_code", 204) not in (200, 204, 404):
                raise RuntimeError(f"보기 권한을 거두지 못했습니다({r.status_code})")
        except Exception as e:
            err = err or str(e)[:200]
            continue
        granted.pop(key, None)
        removed.append(key)
        db.set_setting(f"gshare:{pid}", json.dumps(granted))          # 하나씩 바로 적는다 — 도중에 멈춰도 기록이 맞게
    return removed, err


def sync(db, project_id: int, owner_email: str, want, http=None, missing: int = 0) -> dict:
    """목표와 이 기능이 준 권한을 맞춘다. want 는 {파일 id: {메일}} 또는 {파일 id: {메일: "reader"|"writer"}} 또는 (목표, 못 받는 사람 수)를 돌려주는 함수 —
    함수면 잠금 안에서 불러 그때 상태로 정한다(계획과 맞추기 사이에 비공개로 바뀐 것이 끼지 않게). 꺼져 있으면 목표는 비고 준 것을 모두 거둔다.
    거두기를 먼저, 준 계정으로 한다 — 못 거둔 권한은 기록에 남아 다음 맞추기·점검에서 다시 거둔다.
    돌려주는 것 {added, removed, shared, error}. 실패는 gshare_status 에 남기고 예외를 올리지 않는다."""
    pid = int(project_id)
    with _lock(pid):
        if callable(want):
            want, missing = want()
        owner = (owner_email or "").lower()
        target = set()
        if enabled(db, pid):
            for fid, emails in (want or {}).items():
                roles = emails if isinstance(emails, dict) else {e: "reader" for e in emails}
                for e, role in roles.items():
                    e = (e or "").strip().lower()
                    if fid and e and e != owner:
                        target.add(f"{fid}|{e}|writer" if role == "writer" else f"{fid}|{e}")
        granted = _granted(db, pid)
        added, removed, err = [], [], ""
        stale = [k for k in list(granted) if k not in target]
        if stale:
            removed, err = _revoke(db, pid, granted, stale, owner_email, http)
        todo = sorted(target - set(granted))
        if todo:
            try:
                if not owner or not gdrive_files.has_file_scope(owner_email):
                    raise PermissionError("담당자 구글 계정이 연결되지 않아 공유할 수 없습니다")
                http = http or gdrive._http()
                h = gdrive_files._headers(owner_email, http)
                for key in todo:
                    fid, email, role = (key.split("|") + ["reader"])[:3]
                    r = http.post(f"{gdrive.API}/files/{fid}/permissions", headers=h,
                                  params={"sendNotificationEmail": "false", "supportsAllDrives": "true", "fields": "id"},
                                  json={"role": "writer" if role == "writer" else "reader", "type": "user", "emailAddress": email})
                    if r.status_code != 200:
                        raise RuntimeError(f"보기 권한을 주지 못했습니다({r.status_code})")
                    granted[key] = {"id": r.json()["id"], "by": owner}
                    added.append(key)
                    if role == "writer":
                        # 편집자가 다른 사람에게 다시 공유하지 못하게 — 이 기능이 기록하지 않는 권한이 생기면 거둘 수 없다(10-21 보안 검토)
                        http.patch(f"{gdrive.API}/files/{fid}", headers=h, params={"supportsAllDrives": "true"},
                                   json={"writersCanShare": False})
                    db.set_setting(f"gshare:{pid}", json.dumps(granted))
            except Exception as e:                                     # 주기 실패는 문서 작업을 막지 않는다 — 다음 맞추기에서 다시
                err = err or str(e)[:200]
        if err:
            log.warning("프로젝트 %s 작업 문서 공유 맞추기: %s", pid, err)
        db.set_setting(f"gshare:{pid}", json.dumps(granted))
        db.set_setting(f"gshare_status:{pid}", json.dumps({"at": time.time(), "error": err, "missing": int(missing)}, ensure_ascii=False))
        return {"added": added, "removed": removed, "shared": len({_email_of(k) for k in granted}), "error": err}


def revoke_by(db, project_id: int, email: str, http=None) -> bool:
    """이 구글 계정이 준 권한을 모두 거둔다(연결을 끊기 전에). 다 거뒀으면 True."""
    pid = int(project_id)
    with _lock(pid):
        granted = _granted(db, pid)
        keys = [k for k, g in granted.items() if (g.get("by") or "").lower() == (email or "").lower()]
        if not keys:
            return True
        _removed, err = _revoke(db, pid, granted, keys, email, http)
        db.set_setting(f"gshare:{pid}", json.dumps(granted))
        return not err


def due(db, project_id: int) -> bool:
    """화면을 열 때 맞출 때가 됐는가(10분에 한 번)."""
    try:
        at = float(json.loads(db.get_setting(f"gshare_status:{project_id}", "{}") or "{}").get("at") or 0)
    except ValueError:
        at = 0
    return time.time() - at >= THROTTLE_S
