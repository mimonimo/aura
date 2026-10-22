"""기안기 문서 내려받기 형식·다른 이름으로 저장·이름 바꾸기 — 규칙만 두고 화면 경로는 webhwp_export_routes.

내려받기 형식은 기안기 필터 서버(webhwp_convert.convert)로 바꾼다. 실제 필터 서버(13.82)에 hwpx 를 넣어 확인한 것만 둔다(10-22):
HWPX·HWP(옛 한글, OLE 꼴)·PDF·OOXML(Word docx — 필터 이름은 DOCX 가 아니라 OOXML, DOCX·MSWORD 는 거절).
"""
from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from pathlib import Path

# 형식 키 → (필터 이름, 확장자, 내려받기 MIME, 화면 이름)
FORMATS: dict[str, tuple[str, str, str, str]] = {
    "hwpx": ("HWPX", ".hwpx", "application/hwp+zip", "한글(hwpx)"),
    "hwp": ("HWP", ".hwp", "application/x-hwp", "한글(hwp)"),
    "pdf": ("PDF", ".pdf", "application/pdf", "PDF"),
    "docx": ("OOXML", ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "Word(docx)"),
}


def _looks_like(fmt: str, data: bytes) -> bool:
    """변환 결과가 그 형식인지 — 필터 서버가 오류 쪽(HTML 등)을 돌려줘도 파일로 내보내지 않게."""
    if fmt == "hwpx":
        return data[:2] == b"PK"
    if fmt == "hwp":
        return data[:4] == b"\xd0\xcf\x11\xe0"
    if fmt == "pdf":
        return data[:5] == b"%PDF-"
    if fmt == "docx":
        if data[:2] != b"PK":
            return False
        try:
            return "word/document.xml" in zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return False
    return False


def export(path: Path, fmt: str, http=None) -> tuple[bytes, str, str]:
    """저장된 판을 fmt 로 — (바이트, 내려받을 이름, MIME). 같은 형식이면 바꾸지 않고 그대로."""
    from zzaimy.app import webhwp_convert
    if fmt not in FORMATS:
        raise ValueError("내려받을 수 없는 형식입니다")
    flt, ext, mime, _ = FORMATS[fmt]
    data = path.read_bytes()
    src = "HWPX" if data[:2] == b"PK" else "HWP"
    if src.lower() == fmt:
        out = data
    else:
        out = webhwp_convert.convert(data, src, flt, "x" + ext, http)
        if not _looks_like(fmt, out):
            raise RuntimeError(f"{FORMATS[fmt][3]} 로 바꾸지 못했습니다")
    return out, path.stem + ext, mime


def clean_title(name: str) -> str:
    """사람이 적은 문서 이름 → 파일 이름 줄기(확장자·경로 문자 뺌). 비면 ValueError."""
    from zzaimy.app import storage
    s = (name or "").strip()
    s = re.sub(r"\.(hwpx|hwp)$", "", s, flags=re.I).strip(" .")
    if not s:
        raise ValueError("이름을 적어 주세요")
    return storage.safe_name(s, 80)


def rename(db, fid: int, name: str) -> Path:
    """이름 바꾸기 — 같은 폴더 안에서 파일과 앞 판(.판/)을 함께 옮기고 장부(files 이름·경로)를 고친다. 확장자는 그대로.
    같은 폴더에 같은 이름이 있으면 「(2)」 를 붙인다."""
    from zzaimy.app import webhwp
    f = webhwp.get(db, fid)
    if not f:
        raise FileNotFoundError("문서가 없습니다")
    p = Path(f["path"])
    stem = clean_title(name)
    if stem == p.stem:
        return p
    dest, n = p.with_name(stem + p.suffix), 2
    while dest.exists():
        dest, n = p.with_name(f"{stem} ({n}){p.suffix}"), n + 1
    if p.exists():
        shutil.move(str(p), str(dest))
    vdir = p.parent / ".판"
    if vdir.is_dir():
        pat = re.compile(re.escape(p.stem) + r"( \d{8}-\d{6}(?:-\d+)?" + re.escape(p.suffix) + ")$")
        for v in list(vdir.iterdir()):
            m = pat.fullmatch(v.name)
            if m and not (vdir / (dest.stem + m.group(1))).exists():
                v.rename(vdir / (dest.stem + m.group(1)))
    with db._conn() as conn:
        conn.execute("UPDATE files SET path = ?, name = ? WHERE id = ?", (str(dest), dest.name, int(fid)))
    return dest


def link_drive(db, fid: int, up: dict, parent: str, email: str) -> None:
    """드라이브에 만든 문서의 부서 작업본에 드라이브 자리를 남긴다 — 뒤에 「드라이브에 올리기(최종본)」 가 같은 폴더로 가고 앞 판은 휴지통으로."""
    from zzaimy.app import webhwp
    m = webhwp.meta(db, fid)
    m["drive"] = {"id": up["id"], "name": up.get("name", ""), "parent": parent, "account": email}
    m["drive_out"] = {"id": up["id"], "url": up.get("url", ""), "account": email}
    db.set_setting(f"webhwp_meta:{int(fid)}", json.dumps(m, ensure_ascii=False))
