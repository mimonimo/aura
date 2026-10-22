"""한글 문서함 ↔ 구글 드라이브 — 기본 저장은 부서 디렉터리, 드라이브는 필요할 때 연결된 학교 계정으로(10-22 사용자).

- 가져오기: 내 드라이브의 한글 파일(hwp·hwpx)을 골라 부서 디렉터리(그 폴더)로 받아 온다 → 기안기로 편집.
  받은 파일의 메타에 드라이브 원본 id·이름·폴더를 남긴다(source 「drive:<id>」).
- 올리기(최종본): 지금 판을 드라이브에 올린다. 앱 권한(drive.file)으로는 남이 만든 파일을 덮어쓸 수 없으므로 새 파일로 올리되,
  가져온 원본이 있던 폴더에 먼저 시도하고 안 되면 「AURA 한글/<부서>」 폴더에. 같은 문서를 다시 올리면 앞서 올린 판은 휴지통으로(되돌릴 수 있음).
- 계정은 그 사람이 연결한 학교 계정(gdrive_files.account_for) — 남의 계정으로 드라이브를 보지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

HWP_MIME = {".hwp": "application/x-hwp", ".hwpx": "application/hwp+zip"}
FOLDER = "AURA 한글"


def _http():
    import httpx
    return httpx.Client(timeout=httpx.Timeout(300, connect=20))


def list_files(email: str, q: str = "", page: str = "", http=None) -> dict:
    """내 드라이브의 한글 파일 — 최근 고친 순 50개. q 는 이름 낱말."""
    from zzaimy.ingest import gdrive, gdrive_files
    http = http or _http()
    cond = ["trashed = false", "(name contains '.hwp' or name contains '.hwpx' or mimeType = 'application/x-hwp' "
                               "or mimeType = 'application/haansofthwp' or mimeType = 'application/hwp+zip')"]
    if q.strip():
        cond.append("name contains '" + q.strip().replace("\\", "").replace("'", "\\'")[:60] + "'")
    params = {"q": " and ".join(cond), "pageSize": 50, "orderBy": "modifiedTime desc", "supportsAllDrives": "true",
              "includeItemsFromAllDrives": "true", "fields": "nextPageToken, files(id,name,mimeType,modifiedTime,size,parents)"}
    if page:
        params["pageToken"] = page
    r = http.get(f"{gdrive.API}/files", headers=gdrive_files._headers(email, http), params=params)
    if r.status_code != 200:
        raise RuntimeError(f"드라이브 목록을 읽지 못했습니다({r.status_code})")
    body = r.json()
    files = [f for f in body.get("files") or [] if Path(f["name"]).suffix.lower() in HWP_MIME]
    return {"files": files, "next": body.get("nextPageToken", "")}


FOLDER_MIME = "application/vnd.google-apps.folder"
_ID = __import__("re").compile(r"[A-Za-z0-9_-]{10,128}")


def drives(email: str, http=None) -> list[dict]:
    """연결된 계정이 볼 수 있는 공유 드라이브 — 파인더 왼쪽 목록."""
    from zzaimy.ingest import gdrive, gdrive_files
    http = http or _http()
    r = http.get(f"{gdrive.API}/drives", headers=gdrive_files._headers(email, http), params={"pageSize": 50, "fields": "drives(id,name)"})
    if r.status_code != 200:
        return []
    return [{"id": d["id"], "name": d["name"]} for d in r.json().get("drives") or []]


def browse(email: str, folder: str = "", drive: str = "", q: str = "", http=None) -> dict:
    """드라이브 폴더 하나의 하위 폴더와 한글 파일(파인더처럼 폴더를 따라 내려간다).
    folder 가 없으면 내 드라이브 맨 위(root) 또는 공유 드라이브 맨 위. q 가 있으면 폴더와 상관없이 이름 검색(한글 파일만)."""
    from zzaimy.ingest import gdrive, gdrive_files
    http = http or _http()
    for v in (folder, drive):
        if v and not _ID.fullmatch(v):
            raise ValueError("드라이브 id 가 올바르지 않습니다")
    if q.strip():
        return {"folders": [], **list_files(email, q, http=http)}
    parent = folder or drive or "root"
    params = {"q": f"'{parent}' in parents and trashed = false", "pageSize": 200, "orderBy": "folder,name",
              "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
              "fields": "files(id,name,mimeType,modifiedTime,size)"}
    if drive:
        params.update({"corpora": "drive", "driveId": drive})
    r = http.get(f"{gdrive.API}/files", headers=gdrive_files._headers(email, http), params=params)
    if r.status_code != 200:
        raise RuntimeError(f"드라이브 폴더를 읽지 못했습니다({r.status_code})")
    items = r.json().get("files") or []
    return {"folders": [{"id": f["id"], "name": f["name"]} for f in items if f.get("mimeType") == FOLDER_MIME],
            "files": [f for f in items if Path(f["name"]).suffix.lower() in HWP_MIME]}


def import_file(db, base: Path, email: str, file_id: str, *, dept: str, folder: str, owner: str, http=None) -> int:
    """드라이브 파일 하나를 부서 디렉터리로 — 돌려주는 것은 한글 문서함 id."""
    from zzaimy.app import webhwp
    from zzaimy.ingest import gdrive, gdrive_files
    http = http or _http()
    h = gdrive_files._headers(email, http)
    m = http.get(f"{gdrive.API}/files/{file_id}", headers=h, params={"fields": "id,name,parents,size", "supportsAllDrives": "true"})
    if m.status_code != 200:
        raise RuntimeError(f"드라이브 파일을 찾지 못했습니다({m.status_code})")
    meta = m.json()
    if Path(meta["name"]).suffix.lower() not in HWP_MIME:
        raise ValueError("한글 파일(.hwp·.hwpx)만 가져올 수 있습니다")
    if int(meta.get("size") or 0) > 100 * 1024 * 1024:
        raise ValueError("100MB 보다 큰 파일은 가져올 수 없습니다")
    r = http.get(f"{gdrive.API}/files/{file_id}", headers=h, params={"alt": "media", "supportsAllDrives": "true"})
    if r.status_code != 200:
        raise RuntimeError(f"내려받지 못했습니다({r.status_code})")
    fid = webhwp.add(db, base, dept, meta["name"], r.content, owner=owner, source=f"drive:{file_id}", folder=folder)
    m2 = webhwp.meta(db, fid)
    m2["drive"] = {"id": file_id, "name": meta["name"], "parent": (meta.get("parents") or [""])[0], "account": email}
    db.set_setting(f"webhwp_meta:{fid}", json.dumps(m2, ensure_ascii=False))
    return fid


def upload_final(db, fid: int, email: str, *, dept: str, http=None) -> dict:
    """지금 판을 드라이브에 최종본으로 — 원본이 있던 폴더(되면) 또는 「AURA 한글/<부서>」. 앞서 올린 판은 휴지통으로."""
    from zzaimy.app import webhwp
    from zzaimy.ingest import gdrive, gdrive_files
    http = http or _http()
    f = webhwp.get(db, fid)
    if not f:
        raise FileNotFoundError("문서가 없습니다")
    p = Path(f["path"])
    data, name = p.read_bytes(), p.name
    m = webhwp.meta(db, fid)
    src = m.get("drive") or {}
    up = None
    if src.get("parent"):
        try:
            up = gdrive_files.upload_file(email, data, name, HWP_MIME.get(p.suffix.lower(), "application/octet-stream"),
                                          src["parent"], http=http, reuse=False)
        except Exception:
            up = None                                    # 앱 권한으로 그 폴더에 못 쓰면 우리 폴더로
    if up is None:
        folder = gdrive_files.ensure_folder(email, [FOLDER, dept or webhwp.COMMON], http=http)
        up = gdrive_files.upload_file(email, data, name, HWP_MIME.get(p.suffix.lower(), "application/octet-stream"),
                                      folder, http=http, reuse=False)
    old = (m.get("drive_out") or {}).get("id")
    if old and old != up["id"]:
        try:
            http.patch(f"{gdrive.API}/files/{old}", headers=gdrive_files._headers(email, http),
                       params={"supportsAllDrives": "true"}, json={"trashed": True})
        except Exception:
            pass
    m["drive_out"] = {"id": up["id"], "url": up["url"], "account": email}
    db.set_setting(f"webhwp_meta:{int(fid)}", json.dumps(m, ensure_ascii=False))
    return up
