"""웹 한글 기안기 — 한컴 웹한글기안기 V2 를 플랫폼 문서 편집기로 쓴다(구글 문서와 골라 쓰기, 10-21 사용자).

- 기안기 서버는 운영 VM 의 Tomcat(127.0.0.1:8090, ~/opt/webhwp — 설치 스크립트·순서는 그 아래 README.txt)이고, 브라우저는
  플랫폼 주소 아래 /webhwpctrl/ 로 들어간다(webhwp_routes 의 프록시 — 로그인한 교직원·관리자만, nginx 설정 없이).
- 문서는 부서 디렉터리에 둔다: documents/기안기/<부서>/<제목>.hwpx — 나중에 여러 사람이 같은 부서 문서를 함께 고치는 자리.
  저장할 때마다 바로 앞 판을 documents/기안기/<부서>/.판/<제목> <시각>.hwpx 로 옮겨 둔다(되돌리기).
  부서가 없는 계정의 문서는 「공통」. 볼 수 있는 부서 = 소속 부서 + 추가 부서(관리자는 전부), 「공통」 은 교직원 모두.
- 파일 장부는 files 표(kind="webhwp") — 경로에 부서가 들어 있고, 올린 사람·제목은 설정 webhwp_meta:<id> 에.
- 편집기 고르기: 설정 editor:<계정> = "gdocs"(기본) | "webhwp". 한글 완성본을 만들 때 기안기를 고른 사람은 기안기 링크를 받는다.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from zzaimy.app import storage

DIR_NAME = "기안기"
COMMON = "공통"
EDITORS = {"gdocs": "구글 문서", "webhwp": "웹 한글 기안기"}
SUFFIXES = (".hwp", ".hwpx")
_STATUS: dict = {"at": 0.0, "value": None}


def base_url() -> str:
    """기안기 서버 주소(플랫폼 서버에서 보는) — 바꾸려면 ZZAIMY_WEBHWP_URL."""
    return (os.environ.get("ZZAIMY_WEBHWP_URL") or "http://127.0.0.1:8090").rstrip("/")


def status(http=None, ttl: float = 60.0) -> dict:
    """기안기 서버가 떠 있는가 — version.txt 를 1분마다 한 번 묻는다. {"ok", "version", "error"}."""
    now = time.time()
    if _STATUS["value"] is not None and now - _STATUS["at"] < ttl:
        return _STATUS["value"]
    try:
        import httpx
        r = (http or httpx).get(f"{base_url()}/webhwpctrl/version.txt", timeout=3)
        v = r.text.strip() if r.status_code == 200 else ""
        val = {"ok": bool(v), "version": v[:40], "error": "" if v else f"응답 {r.status_code}"}
    except Exception as e:
        val = {"ok": False, "version": "", "error": type(e).__name__}
    _STATUS.update(at=now, value=val)
    return val


def editor_of(db, user: str) -> str:
    v = (db.get_setting(f"editor:{user}", "") or "").strip() if user else ""
    return v if v in EDITORS else "gdocs"


def set_editor(db, user: str, editor: str) -> None:
    if user and editor in EDITORS:
        db.set_setting(f"editor:{user}", editor)


def root(base: Path) -> Path:
    return storage.root(base) / DIR_NAME


def dept_dir(base: Path, dept: str) -> Path:
    d = root(base) / (storage.safe_name(dept) or COMMON)
    d.mkdir(parents=True, exist_ok=True)
    return d


def depts_for(role: str, dept: str, also=(), known=()) -> list[str]:
    """이 사람이 쓰는 부서 디렉터리 — 관리자는 알려진 부서 전부, 교직원은 소속·추가 부서, 모두 「공통」 을 함께."""
    if role == "dev":
        out = list(known)
    elif role in ("staff", "professor", "head"):
        out = [d for d in ((dept or "").strip(), *also) if d]
    else:
        return []
    seen, res = set(), []
    for d in [*out, COMMON]:
        if d and d not in seen:
            seen.add(d)
            res.append(d)
    return res


def dept_of_path(base: Path, path: str) -> str:
    try:
        rel = Path(path).resolve().relative_to(root(base).resolve())
    except (ValueError, OSError):
        return ""
    return rel.parts[0] if len(rel.parts) >= 2 else ""


def meta(db, fid: int) -> dict:
    try:
        return dict(json.loads(db.get_setting(f"webhwp_meta:{int(fid)}", "{}") or "{}"))
    except ValueError:
        return {}


HIDDEN = (".판", ".휴지통")


def clean_folder(folder: str) -> str:
    """부서 안 폴더 경로(「2026/공문」)를 안전한 꼴로 — 이름마다 safe_name, 빈 칸·. ·.. ·숨김 폴더는 버린다. 잘못된 길은 ValueError."""
    parts = []
    for seg in (folder or "").replace("\\", "/").split("/"):
        seg = seg.strip()
        if not seg:
            continue
        if seg in (".", "..") or seg.startswith("."):
            raise ValueError("폴더 이름이 올바르지 않습니다")
        parts.append(storage.safe_name(seg) or "폴더")
    if len(parts) > 6:
        raise ValueError("폴더는 여섯 단계까지 만들 수 있습니다")
    return "/".join(parts)


def folder_dir(base: Path, dept: str, folder: str = "") -> Path:
    d = dept_dir(base, dept)
    f = clean_folder(folder)
    return d / f if f else d


def folder_of(base: Path, path: str) -> str:
    """파일이 부서 안 어느 폴더에 있나 — 부서 바로 아래면 빈 문자열."""
    try:
        rel = Path(path).resolve().relative_to(root(base).resolve())
    except (ValueError, OSError):
        return ""
    return "/".join(rel.parts[1:-1])


def make_folder(base: Path, dept: str, parent: str, name: str) -> str:
    sub = clean_folder(f"{parent}/{name}")
    if not sub or sub == clean_folder(parent):
        raise ValueError("폴더 이름을 적어 주세요")
    folder_dir(base, dept, sub).mkdir(parents=True, exist_ok=True)
    return sub


def add(db, base: Path, dept: str, filename: str, data: bytes, *, owner: str, source: str = "", folder: str = "") -> int:
    """부서 디렉터리(그 안 폴더)에 한글 파일 하나를 들인다 — 같은 이름이 있으면 「(2)」 를 붙여 따로 둔다. 돌려주는 것은 files.id."""
    ext = Path(filename or "").suffix.lower()
    if ext not in SUFFIXES:
        raise ValueError("한글 파일(.hwp·.hwpx)만 올릴 수 있습니다")
    stem = storage.safe_name(storage.title_of(filename)) or "문서"
    d = folder_dir(base, dept, folder)
    d.mkdir(parents=True, exist_ok=True)
    p, n = d / f"{stem}{ext}", 2
    while p.exists():
        p, n = d / f"{stem} ({n}){ext}", n + 1
    p.write_bytes(data)
    fid = db.add_file("webhwp", str(p), name=p.name, size=len(data))
    db.set_setting(f"webhwp_meta:{fid}", json.dumps({"owner": owner, "dept": dept or COMMON, "source": source,
                                                    "created": datetime.now().isoformat(timespec="seconds")},
                                                   ensure_ascii=False))
    return fid


def save(db, base: Path, fid: int, data: bytes, *, user: str) -> Path:
    """기안기에서 고친 내용을 저장 — 앞 판은 .판/ 으로 옮기고 같은 자리에 쓴다. 고친 사람·시각을 남긴다."""
    f = get(db, fid)
    if not f:
        raise FileNotFoundError("문서가 없습니다")
    p = Path(f["path"])
    if not data or data[:2] != b"PK" and data[:4] != b"\xd0\xcf\x11\xe0":
        raise ValueError("한글 파일 형식이 아닙니다")
    if p.exists():
        vdir = p.parent / ".판"
        vdir.mkdir(exist_ok=True)
        shutil.move(str(p), str(vdir / f"{p.stem} {datetime.now():%Y%m%d-%H%M%S}{p.suffix}"))
    # 기안기는 HWPX 로 돌려준다 — 옛 hwp 를 고쳤으면 확장자를 hwpx 로 바꿔 쓴다(장부도 따라)
    if data[:2] == b"PK" and p.suffix.lower() == ".hwp":
        p = p.with_suffix(".hwpx")
    p.write_bytes(data)
    if str(p) != f["path"]:
        with db._conn() as conn:
            conn.execute("UPDATE files SET path = ?, name = ?, size = ? WHERE id = ?", (str(p), p.name, len(data), int(fid)))
    else:
        with db._conn() as conn:
            conn.execute("UPDATE files SET size = ? WHERE id = ?", (len(data), int(fid)))
    m = meta(db, fid)
    m.update(saved_by=user, saved=datetime.now().isoformat(timespec="seconds"))
    db.set_setting(f"webhwp_meta:{int(fid)}", json.dumps(m, ensure_ascii=False))
    return p


def get(db, fid: int) -> dict | None:
    with db._conn() as conn:
        r = conn.execute("SELECT * FROM files WHERE id = ? AND kind = 'webhwp'", (int(fid),)).fetchone()
    return dict(r) if r else None


def listing(db, base: Path, depts: list[str]) -> list[dict]:
    """부서마다 문서 수 — 화면 왼쪽 부서 목록용. [{"dept", "count"}]."""
    rows = db.list_files(kind="webhwp", limit=5000)
    count = {d: 0 for d in depts}
    for r in rows:
        d = dept_of_path(base, r["path"])
        if d in count and Path(r["path"]).exists():
            count[d] += 1
    return [{"dept": d, "count": count[d]} for d in depts]


def browse(db, base: Path, dept: str, folder: str = "") -> dict:
    """부서 안 한 폴더 — 하위 폴더(빈 폴더도)와 그 폴더의 문서. 디스크에 없는 장부 줄은 뺀다."""
    here = folder_dir(base, dept, folder)
    folders = []
    if here.is_dir():
        for c in sorted(here.iterdir(), key=lambda x: x.name):
            if c.is_dir() and c.name not in HIDDEN and not c.name.startswith("."):
                n = sum(1 for x in c.rglob("*") if x.suffix.lower() in SUFFIXES and not any(p in HIDDEN for p in x.parts))
                folders.append({"name": c.name, "path": clean_folder(f"{folder}/{c.name}"), "count": n})
    files = []
    for r in db.list_files(kind="webhwp", limit=5000):
        p = Path(r["path"])
        if dept_of_path(base, r["path"]) != dept or folder_of(base, r["path"]) != clean_folder(folder) or not p.exists():
            continue
        m = meta(db, r["id"])
        files.append({"id": r["id"], "name": r["name"], "size": r["size"], "owner": m.get("owner", ""),
                      "saved": (m.get("saved") or m.get("created") or r.get("created_at") or "")[:16].replace("T", " "),
                      "saved_by": m.get("saved_by", "")})
    files.sort(key=lambda x: x["saved"], reverse=True)
    crumbs, acc = [], []
    for seg in [x for x in clean_folder(folder).split("/") if x]:
        acc.append(seg)
        crumbs.append({"name": seg, "path": "/".join(acc)})
    return {"dept": dept, "folder": clean_folder(folder), "folders": folders, "files": files, "crumbs": crumbs}


def trash(db, base: Path, fid: int, *, user: str) -> None:
    """문서 지우기 — 부서의 .휴지통/ 으로 옮긴다(되살릴 수 있게). 장부에서는 뺀다."""
    f = get(db, fid)
    if not f:
        raise FileNotFoundError("문서가 없습니다")
    p = Path(f["path"])
    if p.exists():
        t = root(base) / dept_of_path(base, f["path"]) / ".휴지통"
        t.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(t / f"{p.stem} {datetime.now():%Y%m%d-%H%M%S} {user}{p.suffix}"))
    db.remove_files(path=f["path"])


def may_open(base: Path, f: dict | None, depts: list[str]) -> bool:
    return bool(f) and dept_of_path(base, f["path"]) in depts
