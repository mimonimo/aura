"""교내 NAS 연동 — 읽기 전용 계정으로 공유 폴더의 문서를 가져와 기준 문서·문서 추출로 자동 반입한다.

- NAS 에는 아무것도 쓰지 않는다(목록·열기·읽기만). 가져온 파일은 로컬 inbox 에 저장하고 수동 업로드와 같은 처리 경로를 탄다.
- 원천(계정 포함): data/platform/nas_sources.json (0600). 반입 상태: data/platform/nas_state.json.
- 같은 파일(크기·수정 시각 같음)은 다시 가져오지 않고, 내용 해시가 같은 파일은 다른 이름이어도 한 번만 반입한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

BACKENDS = {
    "local": "서버 경로 (마운트된 공유 폴더)",
    "smb": "SMB 공유 (NAS·Windows·Synology, 읽기 전용 계정)",
    "gdrive": "구글 드라이브 (읽기 전용, 허용한 구글 계정)",
}
TARGETS = {"regulation": "기준 문서", "ocr": "문서 추출"}
SECTORS = {"common": "공통", "grant": "국고사업", "recruit": "채용", "admission": "입학"}
DEFAULT_EXTENSIONS = [".pdf", ".hwp", ".hwpx", ".doc", ".docx", ".xls", ".xlsx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"]
MAX_FILE_BYTES = 200 * 1024 * 1024

_sources_path: Path | None = None
_state_path: Path | None = None
_lock = threading.Lock()
_runs: dict[str, dict] = {}
_scheduler_started = False


def configure(sources_path: Path, state_path: Path) -> None:
    global _sources_path, _state_path
    _sources_path, _state_path = Path(sources_path), Path(state_path)


def _read_json(path: Path | None, default):
    if path and path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return default


def _write_json(path: Path | None, data) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


# ---------------- 원천 정의 ----------------

def list_sources() -> list[dict]:
    return _read_json(_sources_path, {"sources": []}).get("sources", [])


def get(sid: str) -> dict | None:
    return next((s for s in list_sources() if s["id"] == sid), None)


def _save_sources(sources: list[dict]) -> None:
    _write_json(_sources_path, {"sources": sources})


def public(src: dict) -> dict:
    c = {k: v for k, v in src.items() if k != "password"}
    c["has_password"] = bool(src.get("password"))
    c["backend_label"] = BACKENDS.get(src.get("backend", ""), src.get("backend", ""))
    c["target_label"] = TARGETS.get(src.get("target", ""), src.get("target", ""))
    c["sector_label"] = SECTORS.get(src.get("sector", "common"), src.get("sector", ""))
    c["run"] = _runs.get(src["id"], {})
    return c


def list_public() -> list[dict]:
    return [public(s) for s in list_sources()]


def _norm_exts(text: str) -> list[str]:
    exts = []
    for tok in re.split(r"[,\s]+", text or ""):
        tok = tok.strip().lower()
        if not tok:
            continue
        if not tok.startswith("."):
            tok = "." + tok
        exts.append(tok)
    return exts or list(DEFAULT_EXTENSIONS)


TYPE_GROUPS = [
    ("pdf", "PDF", [".pdf"]), ("hwp", "한글", [".hwp", ".hwpx"]), ("word", "워드", [".doc", ".docx"]),
    ("excel", "엑셀", [".xls", ".xlsx"]), ("image", "이미지", [".png", ".jpg", ".jpeg", ".tif", ".tiff"]),
]


def _parse_since(text: str) -> float:
    """'YYYY-MM-DD' → epoch. 비었거나 형식이 틀리면 0(제한 없음)."""
    text = (text or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.strptime(text, "%Y-%m-%d").timestamp()
    except ValueError:
        return 0.0


def _norm_excludes(text: str) -> list[str]:
    return [t.strip() for t in re.split(r"[,\s]+", text or "") if t.strip()]


def _passes_filters(src: dict, rel: str, size: int, mtime: float) -> bool:
    exts = set(src.get("extensions") or DEFAULT_EXTENSIONS)
    if Path(rel).suffix.lower() not in exts:
        return False
    parts = Path(rel).parts[:-1]
    if any(p in parts for p in (src.get("exclude") or [])):
        return False
    if src.get("since") and mtime < _parse_since(src["since"]):
        return False
    max_mb = int(src.get("max_mb") or 0)
    if max_mb and size > max_mb * 1024 * 1024:
        return False
    return True


def add(name: str, backend: str, root: str, target: str, sector: str = "common", username: str = "",
        password: str = "", domain: str = "", extensions: str = "", recursive: bool = True,
        auto: bool = False, interval_min: int = 60, since: str = "", exclude: str = "",
        max_mb: int = 0) -> dict:
    if backend not in BACKENDS:
        raise ValueError("연결 방식이 올바르지 않습니다")
    if target not in TARGETS:
        raise ValueError("저장 위치가 올바르지 않습니다")
    if sector not in SECTORS:
        raise ValueError("업무 영역이 올바르지 않습니다")
    name, root = name.strip()[:60], root.strip()
    if not name or not root:
        raise ValueError("이름과 경로를 적어 주세요")
    if backend == "smb" and not (root.startswith("\\\\") or root.startswith("//")):
        raise ValueError(r"SMB 경로는 \\서버\공유\폴더 형식이어야 합니다")
    if backend == "gdrive":
        from zzaimy.ingest import gdrive

        root = gdrive.folder_id(root)              # 폴더 주소를 넣어도 ID 로 저장
        if not username.strip():
            raise ValueError("허용한 구글 계정(이메일)을 골라 주세요")
    src = {
        "id": secrets.token_hex(4), "name": name, "backend": backend, "root": root,
        "target": target, "sector": sector if target == "regulation" else "common",
        "username": username.strip(), "password": password, "domain": domain.strip(),
        "extensions": _norm_exts(extensions), "recursive": bool(recursive),
        "since": since.strip() if _parse_since(since) else "", "exclude": _norm_excludes(exclude),
        "max_mb": max(0, int(max_mb or 0)),
        "auto": bool(auto), "interval_min": max(5, int(interval_min or 60)),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "last_run": "", "last_summary": "",
    }
    sources = list_sources() + [src]
    _save_sources(sources)
    return src


def update(sid: str, **fields) -> dict:
    sources = list_sources()
    src = next((s for s in sources if s["id"] == sid), None)
    if src is None:
        raise ValueError("없는 폴더입니다")
    for k in ("name", "root", "username", "domain"):
        if k in fields and fields[k] is not None and str(fields[k]).strip():
            src[k] = str(fields[k]).strip()[:200]
    if fields.get("target") in TARGETS:
        src["target"] = fields["target"]
    if fields.get("sector") in SECTORS:
        src["sector"] = fields["sector"] if src["target"] == "regulation" else "common"
    if "extensions" in fields and fields["extensions"] is not None:
        src["extensions"] = _norm_exts(fields["extensions"])
    for k in ("recursive", "auto"):
        if k in fields and fields[k] is not None:
            src[k] = bool(fields[k])
    if fields.get("interval_min"):
        src["interval_min"] = max(5, int(fields["interval_min"]))
    if "since" in fields and fields["since"] is not None:
        src["since"] = fields["since"].strip() if _parse_since(fields["since"]) else ""
    if "exclude" in fields and fields["exclude"] is not None:
        src["exclude"] = _norm_excludes(fields["exclude"])
    if "max_mb" in fields and fields["max_mb"] is not None:
        src["max_mb"] = max(0, int(fields["max_mb"] or 0))
    pw = fields.get("password")
    if pw == "clear":
        src["password"] = ""
    elif pw:
        src["password"] = pw
    _save_sources(sources)
    return src


def delete(sid: str) -> None:
    _save_sources([s for s in list_sources() if s["id"] != sid])
    state = _read_json(_state_path, {})
    if sid in state:
        del state[sid]
        _write_json(_state_path, state)


def _touch_run(src: dict, summary: str) -> None:
    sources = list_sources()
    for s in sources:
        if s["id"] == src["id"]:
            s["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            s["last_summary"] = summary
    _save_sources(sources)


# ---------------- 백엔드 (읽기 전용) ----------------

class LocalBackend:
    """서버에서 보이는 경로 — 관리자가 마운트한 공유나 rsync 사본."""

    def __init__(self, root: str):
        self.root = Path(root).expanduser()

    def check(self) -> None:
        if not self.root.is_dir():
            raise FileNotFoundError(f"경로가 없습니다: {self.root}")

    def walk(self, recursive: bool = True) -> Iterator[tuple[str, int, float]]:
        base = self.root
        stack = [base]
        while stack:
            d = stack.pop()
            with os.scandir(d) as it:
                for e in sorted(it, key=lambda x: x.name):
                    if e.name.startswith((".", "~$")):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        if recursive:
                            stack.append(Path(e.path))
                    elif e.is_file(follow_symlinks=False):
                        st = e.stat()
                        yield str(Path(e.path).relative_to(base)), st.st_size, st.st_mtime

    def read(self, rel: str) -> bytes:
        return (self.root / rel).read_bytes()

    def listdir(self, rel: str = "") -> tuple[list[dict], list[tuple[str, int, float]]]:
        """한 단계만: (하위 폴더, 파일). 찾아보기용."""
        d = self.root / rel if rel else self.root
        dirs, files = [], []
        with os.scandir(d) as it:
            for e in sorted(it, key=lambda x: x.name):
                if e.name.startswith((".", "~$")):
                    continue
                if e.is_dir(follow_symlinks=False):
                    dirs.append({"name": e.name})
                elif e.is_file(follow_symlinks=False):
                    st = e.stat()
                    files.append((e.name, st.st_size, st.st_mtime))
        return dirs, files


class SmbBackend:
    r"""SMB/CIFS 공유 — smbprotocol 의 smbclient 로 목록·읽기만 한다(쓰기 API 는 쓰지 않는다)."""

    def __init__(self, root: str, username: str = "", password: str = "", domain: str = ""):
        unc = root.replace("/", "\\").rstrip("\\")
        m = re.match(r"^\\\\([^\\]+)\\(.+)$", unc)
        if not m:
            raise ValueError(r"SMB 경로는 \\서버\공유\폴더 형식이어야 합니다")
        self.server, self.unc = m.group(1), unc
        self.username, self.password, self.domain = username, password, domain

    def _client(self):
        try:
            import smbclient
        except ImportError as e:
            raise RuntimeError("smbprotocol 패키지가 없습니다 — scripts/80_install_smb.sh 로 설치") from e
        user = f"{self.domain}\\{self.username}" if self.domain and self.username else (self.username or None)
        smbclient.register_session(self.server, username=user, password=self.password or None,
                                   connection_timeout=10)
        return smbclient

    def check(self) -> None:
        smb = self._client()
        smb.stat(self.unc)

    def walk(self, recursive: bool = True) -> Iterator[tuple[str, int, float]]:
        smb = self._client()
        stack = [self.unc]
        while stack:
            d = stack.pop()
            for e in sorted(smb.scandir(d), key=lambda x: x.name):
                if e.name.startswith((".", "~$")):
                    continue
                if e.is_dir():
                    if recursive:
                        stack.append(e.path)
                elif e.is_file():
                    st = e.stat()
                    rel = e.path[len(self.unc):].lstrip("\\").replace("\\", "/")
                    yield rel, int(st.st_size), float(st.st_mtime)

    def read(self, rel: str) -> bytes:
        smb = self._client()
        path = self.unc + "\\" + rel.replace("/", "\\")
        with smb.open_file(path, mode="rb") as f:
            return f.read()

    def listdir(self, rel: str = "") -> tuple[list[dict], list[tuple[str, int, float]]]:
        smb = self._client()
        d = self.unc + ("\\" + rel.replace("/", "\\") if rel else "")
        dirs, files = [], []
        for e in sorted(smb.scandir(d), key=lambda x: x.name):
            if e.name.startswith((".", "~$")):
                continue
            if e.is_dir():
                dirs.append({"name": e.name})
            elif e.is_file():
                st = e.stat()
                files.append((e.name, int(st.st_size), float(st.st_mtime)))
        return dirs, files


def backend_for(src: dict):
    if src["backend"] == "local":
        return LocalBackend(src["root"])
    if src["backend"] == "gdrive":
        from zzaimy.ingest.gdrive import GDriveBackend

        return GDriveBackend(src["root"], src.get("username", ""))
    return SmbBackend(src["root"], src.get("username", ""), src.get("password", ""), src.get("domain", ""))


def browse(src: dict, rel: str = "") -> dict:
    """폴더 찾아보기 — 한 단계의 하위 폴더와 파일 종류별 개수(하위 폴더 포함 여부에 따라)를 돌려준다."""
    try:
        b = backend_for(src)
        b.check()
        dirs, files = b.listdir(rel)
        counts = {k: 0 for k, _l, _e in TYPE_GROUPS}
        other = 0
        entries = files
        if src.get("recursive", True):
            base = Path(rel) if rel else None
            entries = []
            for r, size, mtime in b.walk(True):
                if base is not None and not Path(r).parts[:len(base.parts)] == base.parts:
                    continue
                entries.append((r, size, mtime))
        for name, size, mtime in entries:
            ext = Path(name).suffix.lower()
            hit = False
            for key, _label, exts in TYPE_GROUPS:
                if ext in exts:
                    counts[key] += 1
                    hit = True
            if not hit:
                other += 1
        return {"ok": True, "rel": rel, "dirs": dirs[:200], "counts": counts, "other": other,
                "n_files": len(entries), "error": ""}
    except Exception as e:
        return {"ok": False, "rel": rel, "dirs": [], "counts": {}, "other": 0, "n_files": 0,
                "error": f"{type(e).__name__}: {e}"[:200]}


def plan(src: dict, limit: int = 20) -> dict:
    """반입 미리보기 — 무엇이 새로 들어오고 무엇이 그대로인지 크기·수정 시각으로만 판단(읽지 않음)."""
    try:
        b = backend_for(src)
        b.check()
        entries = list(b.walk(src.get("recursive", True)))
    except Exception as e:
        return {"ok": False, "new": 0, "same": 0, "skipped": 0, "sample": [], "error": f"{type(e).__name__}: {e}"[:200]}
    mine = _state().get(src.get("id", ""), {})
    out = {"ok": True, "new": 0, "same": 0, "skipped": 0, "sample": [], "error": ""}
    for rel, size, mtime in entries:
        if not _passes_filters(src, rel, size, mtime):
            out["skipped"] += 1
            continue
        prev = mine.get(rel)
        if prev and prev.get("size") == size and abs(prev.get("mtime", 0) - mtime) < 1 and prev.get("doc_id"):
            out["same"] += 1
        else:
            out["new"] += 1
            if len(out["sample"]) < limit:
                out["sample"].append({"path": rel, "size": size})
    return out


def probe(src: dict, limit: int = 30) -> dict:
    """연결 확인 — 목록만 읽는다. 실패해도 예외 없이 사유만."""
    try:
        b = backend_for(src)
        b.check()
        n_all = n_match = 0
        sample: list[str] = []
        for rel, size, mtime in b.walk(src.get("recursive", True)):
            n_all += 1
            if _passes_filters(src, rel, size, mtime):
                n_match += 1
                if len(sample) < limit:
                    sample.append(rel)
            if n_all >= 5000:
                break
        return {"ok": True, "n_all": n_all, "n_match": n_match, "sample": sample, "error": ""}
    except Exception as e:
        return {"ok": False, "n_all": 0, "n_match": 0, "sample": [], "error": f"{type(e).__name__}: {e}"[:200]}


# ---------------- 반입 ----------------

def _state() -> dict:
    return _read_json(_state_path, {})


def _known_hashes(state: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for items in state.values():
        for it in items.values():
            if it.get("sha1") and it.get("doc_id"):
                out[it["sha1"]] = it["doc_id"]
    return out


def sync(src: dict, db, processor, inbox_dir: Path, limit: int | None = None,
         progress: dict | None = None) -> dict:
    """원천 하나를 반입한다. NAS 는 읽기만, 파일은 로컬 inbox 로. 결과는 항목별로 남긴다."""
    result = {"scanned": 0, "new": 0, "same": 0, "dup": 0, "failed": 0, "items": [], "error": ""}
    inbox_dir = Path(inbox_dir)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    try:
        b = backend_for(src)
        b.check()
        entries = list(b.walk(src.get("recursive", True)))
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"[:200]
        return result
    with _lock:
        state = _state()
        mine = state.setdefault(src["id"], {})
        hashes = _known_hashes(state)
    candidates = [(rel, size, mtime) for rel, size, mtime in entries if _passes_filters(src, rel, size, mtime)]
    result["scanned"] = len(candidates)
    for i, (rel, size, mtime) in enumerate(candidates):
        if progress is not None:
            progress["progress"] = f"{i + 1}/{len(candidates)}"
        prev = mine.get(rel)
        if prev and prev.get("size") == size and abs(prev.get("mtime", 0) - mtime) < 1 and prev.get("doc_id"):
            result["same"] += 1
            continue
        if limit is not None and result["new"] >= limit:
            break
        item = {"path": rel, "size": size, "status": "", "doc_id": None, "error": ""}
        try:
            if size > MAX_FILE_BYTES:
                raise ValueError("파일이 너무 큽니다(200MB 초과)")
            data = b.read(rel)
            sha1 = hashlib.sha1(data).hexdigest()
            if sha1 in hashes and db.get_document(hashes[sha1]):
                item.update(status="dup", doc_id=hashes[sha1])
                result["dup"] += 1
                mine[rel] = {"size": size, "mtime": mtime, "sha1": sha1, "doc_id": hashes[sha1],
                             "at": datetime.now().strftime("%Y-%m-%d %H:%M"), "dup": True}
            else:
                stored = inbox_dir / f"{uuid.uuid4().hex}{Path(rel).suffix.lower()}"
                stored.write_bytes(data)
                doc_id = db.add_document(filename=Path(rel).name, stored_path=str(stored),
                                         doc_type=src["target"], sector=src.get("sector", "common"))
                try:
                    processor.process(db, doc_id, stored)
                except Exception as e:  # 처리 실패는 문서 상태에 남기고 반입은 계속한다
                    db.update_document(doc_id, status="failed", error=f"{type(e).__name__}: {e}"[:300])
                item.update(status="new", doc_id=doc_id)
                result["new"] += 1
                hashes[sha1] = doc_id
                mine[rel] = {"size": size, "mtime": mtime, "sha1": sha1, "doc_id": doc_id,
                             "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
        except Exception as e:
            item.update(status="failed", error=f"{type(e).__name__}: {e}"[:200])
            result["failed"] += 1
        result["items"].append(item)
        with _lock:
            state = _state()
            state[src["id"]] = mine
            _write_json(_state_path, state)
    summary = f"검사 {result['scanned']} · 새로 {result['new']} · 그대로 {result['same']} · 중복 {result['dup']} · 실패 {result['failed']}"
    _touch_run(src, summary)
    result["summary"] = summary
    return result


def run_status(sid: str) -> dict:
    return _runs.get(sid, {})


def start_sync(src: dict, db, processor, inbox_dir: Path) -> bool:
    """백그라운드 반입 시작. 이미 실행 중이면 False."""
    st = _runs.get(src["id"])
    if st and st.get("running"):
        return False
    st = {"running": True, "started": datetime.now().strftime("%Y-%m-%d %H:%M"), "progress": "준비", "last": None}
    _runs[src["id"]] = st

    def go() -> None:
        try:
            st["last"] = sync(src, db, processor, inbox_dir, progress=st)
        except Exception as e:  # 예상 밖 실패도 화면에 남긴다
            st["last"] = {"error": f"{type(e).__name__}: {e}"[:200], "items": [], "summary": "실패"}
        finally:
            st["running"] = False

    threading.Thread(target=go, daemon=True, name=f"nas-sync-{src['id']}").start()
    return True


def ensure_scheduler(db, processor, inbox_dir: Path, tick: int = 300) -> None:
    """자동 반입 — 켜진 원천을 주기마다 확인해 간격이 지났으면 반입한다(한 번에 하나씩)."""
    global _scheduler_started
    if _scheduler_started or os.environ.get("ZZAIMY_NAS_AUTO") == "0":
        return
    if not any(s.get("auto") for s in list_sources()):
        return
    _scheduler_started = True

    def loop() -> None:
        while True:
            time.sleep(tick)
            for src in list_sources():
                if not src.get("auto"):
                    continue
                last = src.get("last_run") or ""
                due = True
                if last:
                    try:
                        due = (datetime.now() - datetime.strptime(last, "%Y-%m-%d %H:%M")).total_seconds() >= src["interval_min"] * 60
                    except ValueError:
                        due = True
                if due and not (_runs.get(src["id"]) or {}).get("running"):
                    try:
                        sync(src, db, processor, inbox_dir)
                    except Exception:
                        pass

    threading.Thread(target=loop, daemon=True, name="nas-scheduler").start()


def install_hint() -> str:
    return "smbprotocol 은 오프라인 휠로 설치합니다: pip install --no-index --find-links data/tmp/wheels smbprotocol"


def copy_local_sample(src_dir: Path, dst_dir: Path) -> None:  # 테스트·시연용
    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
