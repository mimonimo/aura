"""문서 저장 구조 — DB 가 원본이고 디스크는 DB 를 그대로 비추는 정리된 폴더다 (ADR-0030).

왜. 원본 파일을 한 폴더에 임의 이름으로 쌓으면 디스크만 보고는 무엇이 무엇인지 알 수 없다.
그래서 문서마다 사람이 읽을 수 있는 폴더 하나를 두고, 종류(반입·첨부·생성·보고)로
디렉터리를 나누며, 모든 파일을 DB 의 files 표에 기록한다.

  documents/반입/<연도>/<유형>/<접수번호> <제목>/원본.<확장자>   (+ 원본_imgs/ 추출 그림)
  documents/첨부/<연도>/대화-<번호>/<시각> <파일명>
  documents/생성/<연도>/<접수번호|대화-번호>/<시각> <종류>.<확장자>
  documents/보고/주간/<월요일>.md

부서·열람 등급·갈래는 나중에 바뀔 수 있어 폴더가 아니라 DB 로 관리한다. 접수번호는 한 번 쓰면 다시 쓰지 않으므로
폴더 이름의 열쇠로 안전하다. 제목이 반입 뒤 본문 제목으로 바뀌면 폴더 이름도 따라간다(rename_intake_dir).
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT_NAME = "documents"
KIND_DIRS = {"intake": "반입", "attachment": "첨부", "generated": "생성", "report": "보고"}
TYPE_DIRS = {"grant": "국고", "recruit": "채용", "admission": "입학", "auto": "행정", "regulation": "기준", "ocr": "추출"}
ORIGINAL = "원본"
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
MAX_TITLE = 60


def root(base: Path) -> Path:
    return Path(base) / ROOT_NAME


def safe_name(name: str, limit: int = MAX_TITLE) -> str:
    """폴더·파일 이름으로 안전한 제목 — 경로 문자를 빼고 공백을 하나로, 길이를 제한한다."""
    s = _BAD.sub(" ", name or "").strip()
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s[:limit].rstrip(" .") or "문서")


def _year(iso: str | None) -> str:
    return (iso or datetime.now().isoformat())[:4]


_EXT = re.compile(r"\.(hwpx?|pdf|docx?|xlsx?|pptx?|jpe?g|png|tiff?|bmp|webp|txt|md|zip)$", re.I)


def title_of(filename: str) -> str:
    """이름에서 확장자만 뗀다 — Path.stem 은 '2. 도심 캠퍼스 안내' 를 '2' 로 자른다.

    띄어쓰기 없이 '+' 나 '_' 로만 이어진 이름(누리집에서 내려받으면 공백이 그렇게 바뀐다)은 그 글자를
    공백으로 돌린다. 공백이 이미 있는 이름은 건드리지 않는다."""
    t = _EXT.sub("", (filename or "").strip())
    if t and " " not in t and ("+" in t or "_" in t):
        t = re.sub(r"[+_]+", " ", t).strip()
    return t


_SUFFIX = re.compile(r"\.[0-9A-Za-z]{1,8}$")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def _suffix(name: str) -> str:
    """이름·경로 끝의 확장자('.hwpx', 소문자). 없으면 ''.

    확장자 목록이 아니라 모양으로 본다 — 점 뒤가 영문·숫자 1~8자이고 영문이 한 자는 있어야 한다.
    그래서 처음 보는 형식도 그대로 통하고, '2. 도심 캠퍼스 안내'·'v1.2' 처럼 점이 든 제목은 확장자로 읽지 않는다."""
    last = re.split(r"[\\/]", name or "")[-1]
    m = _SUFFIX.search(last)
    if not m or m.start() == 0 or not re.search(r"[A-Za-z]", m.group()):
        return ""
    return m.group().lower()


def original_ext(doc: dict) -> str:
    """원본 파일의 실제 확장자('.hwpx' 꼴, 소문자). 모르면 ''.

    형식(미리보기 종류·MIME·변환기)은 표시 이름만으로 고르지 않는다. 반입 뒤 filename 은 확장자 없는 본문 제목이다.
    먼저 실제 파일이 놓인 자리(stored_path — 'dgx://<보관소 경로>' 처럼 'scheme://' 가 붙은 원격 보관 위치면 그 뒤 경로)의
    확장자, 그것이 없을 때만(확장자 없이 저장된 옛 기록) filename 의 확장자를 쓴다."""
    sp = str(doc.get("stored_path") or "")
    loc = _SCHEME.sub("", sp, count=1)
    return _suffix(loc) or _suffix(str(doc.get("filename") or ""))


def original_name(doc: dict) -> str:
    """원본을 사람에게 보일 이름 — 표시 이름(본문 제목) + 실제 확장자. 내려받기 이름과 형식 판단에 쓴다.

    형식은 이 이름이나 original_ext 로만 고른다 — filename(제목)을 그대로 kind_of·media_type·변환기에 넘기지 않는다.
    filename 이 다른 확장자를 달고 있으면 실제 파일 쪽 확장자로 바꾼다."""
    ext = original_ext(doc)
    name = str(doc.get("filename") or "").strip()
    cur = _suffix(name)
    if cur:
        if cur == ext:
            return name
        name = name[: -len(cur)].rstrip()
    return (name or ORIGINAL) + ext


def intake_dir(base: Path, doc: dict) -> Path:
    """반입 문서의 폴더 — 반입/<연도>/<유형>/<접수번호> <제목>."""
    title = safe_name(title_of(doc.get("filename") or ""))
    receipt = doc.get("receipt_no") or f"문서-{doc.get('id', 0)}"
    return (root(base) / KIND_DIRS["intake"] / _year(doc.get("created_at"))
            / TYPE_DIRS.get(doc.get("doc_type") or "auto", "행정") / f"{receipt} {title}")


def adopt_original(db, doc_id: int, src: Path, base: Path | None = None) -> Path:
    """방금 저장한 원본을 문서 폴더로 옮기고 stored_path 와 files 표를 맞춘다. 돌려주는 것은 새 경로."""
    doc = db.get_document(doc_id)
    if not doc:
        return Path(src)
    base = Path(base) if base is not None else Path(db.path).parent
    d = intake_dir(base, doc)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{ORIGINAL}{Path(src).suffix.lower()}"
    src = Path(src)
    if src.resolve() != dest.resolve():
        shutil.move(str(src), str(dest))
    db.set_stored_path(doc_id, str(dest))
    db.add_file("intake", str(dest), name=doc.get("filename") or dest.name, doc_id=doc_id,
                size=dest.stat().st_size if dest.exists() else 0)
    return dest


def rename_intake_dir(db, doc_id: int, base: Path | None = None) -> Path | None:
    """제목이 바뀐 뒤 폴더 이름을 따라 바꾼다(반입 폴더 안에 있을 때만). 바꾼 새 폴더를 돌려준다."""
    doc = db.get_document(doc_id)
    if not doc or not doc.get("stored_path"):
        return None
    base = Path(base) if base is not None else Path(db.path).parent
    cur = Path(doc["stored_path"])
    if not _under(cur, root(base) / KIND_DIRS["intake"]):
        return None
    want = intake_dir(base, doc)
    if cur.parent.resolve() == want.resolve():
        return cur.parent
    want.parent.mkdir(parents=True, exist_ok=True)
    if want.exists():
        return cur.parent                       # 같은 이름의 폴더가 이미 있으면 건드리지 않는다
    shutil.move(str(cur.parent), str(want))
    new_path = want / cur.name
    db.set_stored_path(doc_id, str(new_path))
    db.repath_files(str(cur.parent), str(want))
    db.repath_assets(doc_id, str(cur.parent), str(want))
    return want


def remove_intake_dir(db, doc: dict, base: Path | None = None) -> None:
    """문서 삭제 — 반입 폴더째 지운다(폴더 밖에 있던 옛 원본은 파일만)."""
    base = Path(base) if base is not None else Path(db.path).parent
    p = Path(doc.get("stored_path") or "")
    if _under(p, root(base) / KIND_DIRS["intake"]) and p.parent.exists():
        shutil.rmtree(p.parent, ignore_errors=True)
    elif p.exists():
        p.unlink()
    db.remove_files(doc_id=doc.get("id"))


def attachment_path(base: Path, session_id: int | None, filename: str) -> Path:
    """대화 첨부 — 첨부/<연도>/대화-<번호>/<시각> <파일명>."""
    now = datetime.now()
    d = root(base) / KIND_DIRS["attachment"] / f"{now:%Y}" / (f"대화-{session_id}" if session_id else "미분류")
    d.mkdir(parents=True, exist_ok=True)
    stem = safe_name(title_of(filename)) or "첨부"
    ext = _EXT.search(filename or "")
    return d / f"{now:%Y%m%d-%H%M%S} {stem}{(ext.group(0).lower() if ext else '')}"


def generated_path(base: Path, ref: str, kind: str, ext: str) -> Path:
    """생성한 파일 — 생성/<연도>/<접수번호|대화-번호>/<시각> <종류>.<확장자>."""
    now = datetime.now()
    d = root(base) / KIND_DIRS["generated"] / f"{now:%Y}" / safe_name(ref)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{now:%Y%m%d-%H%M%S} {safe_name(kind)}.{ext.lstrip('.')}"


def save_generated(db, data: bytes, *, ref: str, kind: str, ext: str, doc_id: int | None = None,
                   session_id: int | None = None, base: Path | None = None) -> Path:
    """생성한 파일을 저장하고 files 표에 남긴다."""
    base = Path(base) if base is not None else Path(db.path).parent
    p = generated_path(base, ref, kind, ext)
    p.write_bytes(data)
    db.add_file("generated", str(p), name=p.name, doc_id=doc_id, session_id=session_id, size=len(data))
    return p


def report_dir(base: Path, which: str = "주간") -> Path:
    d = root(base) / KIND_DIRS["report"] / which
    d.mkdir(parents=True, exist_ok=True)
    return d


def _under(p: Path, parent: Path) -> bool:
    try:
        return str(p.resolve()).startswith(str(parent.resolve()) + os.sep)
    except OSError:
        return False


def layout_summary(base: Path) -> dict:
    """화면·점검용 — 종류별 파일 수와 용량."""
    out: dict = {}
    for kind, name in KIND_DIRS.items():
        d = root(base) / name
        n = size = 0
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file():
                    n += 1
                    size += f.stat().st_size
        out[kind] = {"dir": str(d), "files": n, "bytes": size}
    return out
