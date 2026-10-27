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

import contextlib
import contextvars
import json
import re
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
    cached = _STATUS["value"]
    if cached is not None and now - _STATUS["at"] < (ttl if cached.get("ok") else min(ttl, 10.0)):   # 실패는 10초만 기억
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


DEFAULT_EDITOR = "webhwp"


def editor_of(db, user: str) -> str:
    """한글 작업을 열 편집기 — 고른 적 없으면 웹 한글 기안기. 대화 문서 창의 한글/구글 문서 단추가 바꾼다."""
    v = (db.get_setting(f"editor:{user}", "") or "").strip() if user else ""
    return v if v in EDITORS else DEFAULT_EDITOR


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


# 한 요청 안에서 여러 파일의 meta 를 읽을 때(문서함 목록의 권한 거르기) 미리 한 번에 읽어 둔 값 — preloaded_meta 안에서만 쓴다
_META_PRELOAD: contextvars.ContextVar = contextvars.ContextVar("webhwp_meta_preload", default=None)


# 요청 하나 동안의 meta 읽기 — 파일마다 따로 읽는 훑기가 화면 곳곳에 있어(채팅 문서 상태 5초·명령 확인 2초, 10-28 느린 요청 기록)
# 한 요청에서 BULK_AFTER 번을 넘게 읽으면 그때부터 한 번에 읽은 값을 쓴다. 쓰기·지우기는 db.SETTING_HOOKS 로 그 값에도 반영한다.
_META_REQ: contextvars.ContextVar = contextvars.ContextVar("webhwp_meta_req", default=None)
BULK_AFTER = 20
_PREFIX = "webhwp_meta:"


@contextlib.contextmanager
def request_scope():
    """요청 하나를 감싼다(main 의 미들웨어). 밖(배경 작업·스크립트)에서는 meta 가 지금처럼 파일마다 읽는다."""
    tok = _META_REQ.set({"n": 0, "pre": None, "db": None})
    try:
        yield
    finally:
        _META_REQ.reset(tok)


def _req_pre(db):
    st = _META_REQ.get()
    if st is None:
        return None
    if st["db"] is None:
        st["db"] = db
    elif st["db"] is not db:
        return None
    if st["pre"] is None:
        st["n"] += 1
        if st["n"] > BULK_AFTER:
            st["pre"] = db.settings_with_prefix(_PREFIX)
    return st["pre"]


def _on_setting(db, key: str, value) -> None:
    if not str(key).startswith(_PREFIX):
        return
    for pre in (_META_REQ.get() or {}).get("pre"), _META_PRELOAD.get():
        if pre is not None:
            if value is None:
                pre.pop(key, None)
            else:
                pre[key] = value


def meta(db, fid: int) -> dict:
    pre = _META_PRELOAD.get()
    if pre is None:
        pre = _req_pre(db)
    try:
        if pre is not None:
            return dict(json.loads(pre.get(f"webhwp_meta:{int(fid)}") or "{}"))
        return dict(json.loads(db.get_setting(f"webhwp_meta:{int(fid)}", "{}") or "{}"))
    except ValueError:
        return {}


@contextlib.contextmanager
def preloaded_meta(db):
    """이 안에서는 meta() 가 DB 를 파일마다 읽지 않고 한 번 읽은 값을 쓴다 — 읽기만 하는 훑기에서만(안에서 meta 를 고치면 안 보인다)."""
    tok = _META_PRELOAD.set(db.settings_with_prefix("webhwp_meta:"))
    try:
        yield
    finally:
        _META_PRELOAD.reset(tok)


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


def add(db, base: Path, dept: str, filename: str, data: bytes, *, owner: str, source: str = "", folder: str = "",
        project: int | None = None) -> int:
    """부서 디렉터리(그 안 폴더)에 한글 파일 하나를 들인다 — 같은 이름이 있으면 「(2)」 를 붙여 따로 둔다. 돌려주는 것은 files.id.
    project 는 이 파일이 작업 문서인 프로젝트(meta project) — 권한은 폴더 이름(「작업/<프로젝트 이름>」)이 아니라 이 값으로 정한다
    (webhwp_routes.work_projects — 같은 이름 프로젝트·이름 바꾸기·폴더 이름 바꾸기로 권한이 새지 않게)."""
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
    m = {"owner": owner, "dept": dept or COMMON, "source": source, "created": datetime.now().isoformat(timespec="seconds")}
    if project:
        m["project"] = int(project)
    db.set_setting(f"webhwp_meta:{fid}", json.dumps(m, ensure_ascii=False))
    return fid


def _versions(vdir: Path, p: Path) -> list[Path]:
    """이 파일의 앞 판만 — 「<이름> YYYYMMDD-HHMMSS[-n].<확장자>」. 이름이 앞부분만 같은 다른 파일(「계획」 과 「계획 2」)의 판은 잡지 않는다."""
    pat = re.compile(re.escape(p.stem) + r" \d{8}-\d{6}(?:-\d+)?" + re.escape(p.suffix) + "$")
    return sorted((x for x in vdir.iterdir() if pat.fullmatch(x.name)), key=lambda x: x.stat().st_mtime)


VERSION_GAP = 600        # 앞 판을 따로 남기는 최소 간격(초)
VERSION_KEEP = 30        # 파일마다 남기는 앞 판 수


def save(db, base: Path, fid: int, data: bytes, *, user: str, quiet: bool = False) -> Path:
    """기안기에서 고친 내용을 저장 — 앞 판은 .판/ 으로 옮기고 같은 자리에 쓴다. 고친 사람·시각을 남긴다.
    공동 편집 알림 줄에 「문서 저장」 을 남겨 열린 편집 화면이 새 판을 불러오게 한다(quiet=True 는 칸 글 써 넣기 — 화면은 이미 그 글을 가졌다)."""
    f = get(db, fid)
    if not f:
        raise FileNotFoundError("문서가 없습니다")
    p = Path(f["path"])
    if not data or data[:2] != b"PK" and data[:4] != b"\xd0\xcf\x11\xe0":
        raise ValueError("한글 파일 형식이 아닙니다")
    if p.exists():
        if p.read_bytes() == data:                          # 바뀐 것이 없는 자동 저장 — 쓰지도, 판을 남기지도 않는다
            return p
        vdir = p.parent / ".판"
        vdir.mkdir(exist_ok=True)
        olds = _versions(vdir, p)
        # 자동 저장은 몇 초마다 돌므로 판은 VERSION_GAP 마다 하나만 남긴다(그 사이 저장은 같은 자리에 덮어쓴다), 판은 최근 VERSION_KEEP 개
        recent = olds and (datetime.now() - datetime.fromtimestamp(olds[-1].stat().st_mtime)).total_seconds() < VERSION_GAP
        if recent:
            p.unlink()
        else:
            dest = vdir / f"{p.stem} {datetime.now():%Y%m%d-%H%M%S}{p.suffix}"
            n = 1
            while dest.exists():                            # 같은 초에 두 번 — 앞 판을 덮지 않게
                n += 1
                dest = vdir / f"{p.stem} {datetime.now():%Y%m%d-%H%M%S}-{n}{p.suffix}"
            shutil.move(str(p), str(dest))
            for old in _versions(vdir, p)[:-VERSION_KEEP]:
                old.unlink(missing_ok=True)
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
    if not quiet:
        try:
            from zzaimy.app import webhwp_coedit
            webhwp_coedit.emit(db, fid, "doc-saved", {"by": user, "saved": m["saved"]})
        except Exception:
            import logging
            logging.getLogger("zzaimy.app.webhwp").exception("저장 알림 실패")
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


def _at(*vals) -> datetime | None:
    """장부·meta 의 시각 글(시간대가 있든 없든)을 이 서버 시간대의 시각으로 — 읽을 수 없으면 None."""
    for v in vals:
        try:
            d = datetime.fromisoformat(str(v or "").strip())
        except ValueError:
            continue
        return d.astimezone()                               # 시간대 없는 값(meta 의 saved·created)은 서버 시각으로 본다
    return None


def when(d: datetime | None, now: datetime | None = None) -> str:
    """목록에 보일 짧은 시각 — 방금·N분 전·N시간 전(오늘)·어제·N일 전(1주 안)·날짜. 화면 스크립트(hwp-box.js rel)와 같은 규칙."""
    if d is None:
        return "—"
    now = (now or datetime.now()).astimezone()
    sec = (now - d).total_seconds()
    if sec < 60:
        return "방금"
    if sec < 3600:
        return f"{int(sec // 60)}분 전"
    days = (now.date() - d.date()).days
    if days == 0:
        return f"{int(sec // 3600)}시간 전"
    if days == 1:
        return "어제"
    if days < 7:
        return f"{days}일 전"
    return f"{d.month}월 {d.day}일" if d.year == now.year else f"{d.year}. {d.month}. {d.day}."


def _stamp(d: datetime | None) -> dict:
    """시각 한 칸 — at(기계 값, 스크립트가 다시 셈), when(짧은 말), full(마우스를 올리면 보이는 정확한 시각)."""
    if d is None:
        return {"at": "", "when": "—", "full": ""}
    return {"at": d.isoformat(timespec="seconds"), "when": when(d), "full": f"{d.year}년 {d.month}월 {d.day}일 {d:%H:%M}"}


def browse(db, base: Path, dept: str, folder: str = "", *, skip: set[int] | frozenset = frozenset(), me: str = "",
           accounts: dict | None = None) -> dict:
    """부서 안 한 폴더 — 하위 폴더(빈 폴더도)와 그 폴더의 문서. 디스크에 없는 장부 줄·skip(이 사람이 못 여는 것)은 뺀다.
    폴더 줄에는 안의 가장 최근 수정 시각, 문서 줄에는 수정 시각·수정한 사람(이름, 본인이면 me). 장부·meta·잠금은 한 번씩만 읽는다."""
    from zzaimy.app import webhwp_coedit
    here = folder_dir(base, dept, folder)
    cur = clean_folder(folder)
    folders = []
    if here.is_dir():
        for c in sorted(here.iterdir(), key=lambda x: x.name):
            if c.is_dir() and c.name not in HIDDEN and not c.name.startswith("."):
                n = sum(1 for x in c.rglob("*") if x.suffix.lower() in SUFFIXES and not any(p in HIDDEN for p in x.parts))
                folders.append({"name": c.name, "path": clean_folder(f"{cur}/{c.name}"), "count": n})
    holders = webhwp_coedit.struct_holders(db)
    names: dict[str, str] = {}

    def name_of(uid: str) -> str:
        if uid not in names:
            names[uid] = _display(db, uid, accounts)
        return names[uid]

    latest: dict[str, datetime] = {}                        # 하위 폴더 이름 → 안(아래 폴더까지)의 가장 최근 수정 시각
    files = []
    with preloaded_meta(db):
        rr = root(base).resolve()                           # 경로 풀기는 줄마다 한 번(부서·폴더를 따로 풀면 천여 개에서 1초 가까이 걸렸다)
        for r in db.list_files(kind="webhwp", limit=8000):
            if int(r["id"]) in skip:
                continue
            try:
                parts = Path(r["path"]).resolve().relative_to(rr).parts
            except (ValueError, OSError):
                continue
            if len(parts) < 2 or parts[0] != dept:
                continue
            fo = "/".join(parts[1:-1])
            if fo != cur and not (fo.startswith(cur + "/") if cur else bool(fo)):
                continue
            if not Path(r["path"]).exists():
                continue
            m = meta(db, r["id"])
            d = _at(m.get("saved"), m.get("created"), r.get("created_at"))
            if fo != cur:
                child = fo[len(cur) + 1:] if cur else fo
                child = child.split("/", 1)[0]
                if d and (child not in latest or d > latest[child]):
                    latest[child] = d
                continue
            by = m.get("saved_by") or m.get("owner") or ""
            files.append({"id": r["id"], "name": r["name"], "size": r["size"], "owner": m.get("owner", ""),
                          "locked": str(m.get("source", "")).startswith("양식:"),
                          "editing": name_of(holders[int(r["id"])]) if holders.get(int(r["id"])) else "",
                          "saved": (m.get("saved") or m.get("created") or r.get("created_at") or "")[:16].replace("T", " "),
                          "saved_by": m.get("saved_by", ""), "by": by, "by_name": name_of(by) if by else "",
                          "by_me": bool(me) and by == me, "_d": d, **_stamp(d)})
    files.sort(key=lambda x: x.pop("_d") or datetime.min.astimezone(), reverse=True)
    for fx in folders:
        fx.update(_stamp(latest.get(fx["name"])))
    crumbs, acc = [], []
    for seg in [x for x in cur.split("/") if x]:
        acc.append(seg)
        crumbs.append({"name": seg, "path": "/".join(acc)})
    return {"dept": dept, "folder": cur, "folders": folders, "files": files, "crumbs": crumbs}


def display_name(db, user: str, accounts: dict | None = None) -> str:
    """화면에 보일 사람 이름 — 공용 규칙(app/people.display_name: 계정 장부 이름 → 그 계정의 성명 설정 → 아이디).
    라우터 밖(에이전트·알림 흐름)에서도 쓰도록 계정 장부를 못 받으면 DB 옆 accounts.json 을 읽는다."""
    from zzaimy.app import people
    if not user:
        return ""
    if user in AGENTS:
        return AGENTS[user]
    if accounts is None:
        try:
            p = Path(db.path).parent / "accounts.json"
            accounts = json.loads(p.read_text()) if p.exists() else {}
        except (OSError, ValueError, TypeError):
            accounts = {}
    return people.display_name(user, accounts, db)


_display = display_name

# 사람 계정이 아닌 공동 편집 참여자 — 아이디 → 화면 이름. AURA(플랫폼 에이전트)와 Claude(논문 원고 쓰기, scripts/214·ADR-0072)
AGENTS = {"AURA": "AURA", "claude": "Claude"}


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


LOCK_TTL = 120     # 구조 잠금(문서 전체 편집 잠금) 유지 시간(초) — 편집 화면이 45초마다 늘린다. 창을 닫거나 끊기면 2분 뒤 저절로 풀린다


# 구조 잠금 — 칸(필드) 밖을 고치는 사람(본문·행 추가·페이지 설정, 필드 없는 문서는 문서 전체)이 쥔다.
# 칸 단위 임대와 같은 표(webhwp_coedit, 필드 이름 STRUCT)에 두어 동시에 잡아도 한 사람만 잡힌다.
def lock_holder(db, fid: int) -> dict:
    """지금 이 문서의 구조 잠금을 쥔 사람 — {"user", "at", "since"} 또는 {}(아무도 없거나 시간이 지남)."""
    from zzaimy.app import webhwp_coedit as ce
    cur = ce.locks(db, fid).get(ce.STRUCT)
    return {"user": cur["user"], "at": cur["until"] - LOCK_TTL, "since": cur["since"]} if cur else {}


def take_lock(db, fid: int, user: str) -> dict:
    """구조 잠금 잡기·늘리기 — 다른 사람이 잡고 있으면 {"ok": False, "by": 그 사람}."""
    from zzaimy.app import webhwp_coedit as ce
    r = ce.acquire(db, fid, ce.STRUCT, user, ttl=LOCK_TTL)
    return {"ok": True, "by": user} if r["ok"] else {"ok": False, "by": r["by"], "since": r.get("since", 0)}


def release_lock(db, fid: int, user: str) -> None:
    from zzaimy.app import webhwp_coedit as ce
    ce.release(db, fid, ce.STRUCT, user)


# ── 칸 단위 공유(예전 편집 화면·에이전트가 쓰던 이름) — 이제 webhwp_coedit 의 칸 글 표·알림 줄 위에서 돈다 ──
def live_put(db, fid: int, user: str, field: str, text: str) -> int:
    from zzaimy.app import webhwp_coedit as ce
    res = ce.apply_ops(db, fid, user, f"{user}:live", [{"seq": int(time.time() * 1e6), "field": field, "text": text}], force=True)
    return int(res[0].get("version") or 0) if res else 0


def live_since(db, fid: int, since: int, user: str) -> tuple[int, list[dict]]:
    """since(알림 번호) 뒤 다른 사람이 바꾼 칸 — (마지막 알림 번호, [{field, text, by, v}]). 같은 칸은 마지막 것만."""
    from zzaimy.app import webhwp_coedit as ce
    evs = ce.events_since(db, fid, int(since))
    last = max([int(since)] + [e["id"] for e in evs])
    out: dict[str, dict] = {}
    for e in evs:
        d = e["data"]
        if e["kind"] == "field-updated" and d.get("by") != user:
            out[d["field"]] = {"field": d["field"], "text": d["text"], "by": d["by"], "v": e["id"]}
    return last, sorted(out.values(), key=lambda x: x["v"])


def locked(db, fid: int) -> bool:
    """플랫폼이 만든 공통 양식(source 「양식:」) — 모두의 한글 완성본이 이 파일을 원본 서식으로 쓰므로 관리자만 고치고 지운다."""
    return str(meta(db, fid).get("source", "")).startswith("양식:")


def may_open(base: Path, f: dict | None, depts: list[str]) -> bool:
    return bool(f) and dept_of_path(base, f["path"]) in depts


def _register_hook() -> None:
    from zzaimy.app import db as _db
    if _on_setting not in _db.SETTING_HOOKS:
        _db.SETTING_HOOKS.append(_on_setting)


_register_hook()
