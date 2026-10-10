"""사무 문서(한글·워드·엑셀·PPT)의 열람 PDF — 반입 때 한 번 만들어 문서 폴더에 두고 문서 화면 '원문' 탭에 띄운다.

사용자 지시 2026-09-27: hwp·hwpx 처럼 브라우저가 못 여는 파일은 반입하면서 변환해 두고, 문서 보기 본문에 PDF 로 보이게.
리눅스에는 hwp → PDF 직변환기가 없다(LibreOffice 에 hwp5 필터 없음). 한글은 우리 변환기로 docx(독스 편집용으로도 쓰는 것)를
만들고 그 docx 를 LibreOffice 가 PDF 로 그린다. 워드·엑셀·PPT 는 LibreOffice 가 바로 그린다. 원본은 한 번만 읽는다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

OFFICE_EXTS = {".hwp", ".hwpx", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".odt", ".ods", ".odp"}
VIEW_NAME = "열람.pdf"
SHEET_EXTS = {".xlsx", ".xls", ".ods"}
SOFFICE_TIMEOUT = 300


def is_office(path: str | Path) -> bool:
    return Path(str(path)).suffix.lower() in OFFICE_EXTS


def view_path(doc: dict) -> Path:
    source = Path(doc.get("stored_path") or "")
    return source.parent / ("열람-시트전체-v1.pdf" if source.suffix.lower() in SHEET_EXTS else VIEW_NAME)


def soffice() -> str | None:
    """렌더러 — 환경변수 ZZAIMY_SOFFICE, 홈에 root 없이 푼 전체판(~/opt/lo/opt/libreoffice*/program/soffice, Calc·Impress 포함),
    그다음 PATH 의 soffice(배포판 writer-nogui 만이면 엑셀·PPT 는 못 그린다)."""
    env = os.environ.get("ZZAIMY_SOFFICE", "").strip()
    if env and Path(env).exists():
        return env
    home = sorted(Path.home().glob("opt/lo/opt/libreoffice*/program/soffice"))
    if home:
        return str(home[-1])
    return shutil.which("soffice") or shutil.which("libreoffice")


def docx_for(src: Path) -> tuple[bytes, str] | None:
    """한글은 docx 로(독스 열람본과 같은 변환기). 돌려주는 것은 (바이트, 확장자). 못 바꾸면 None.
    줄 간격은 LibreOffice 용 '고정'(exact) — 독스용 배수(docs)와 다르다(hwpx_docx.Converter 참조)."""
    ext = src.suffix.lower()
    if ext == ".hwpx":
        from zzaimy.ingest import hwpx_docx

        data, _ = hwpx_docx.convert(src, line_rule="exact")
        return data, ".docx"
    if ext == ".hwp":
        try:
            from zzaimy.ingest import hwp5_docx

            data, _ = hwp5_docx.convert(src, line_rule="exact")
            return data, ".docx"
        except Exception:
            from zzaimy.ingest import hwp_html

            data, _ = hwp_html.convert(src)             # (html 바이트, 통계) — 예전에는 튜플째 돌려 쓰기에서 깨졌다
            return data, ".html"
    return None


def to_pdf(src: Path, out_dir: Path, timeout: int = SOFFICE_TIMEOUT) -> Path | None:
    """LibreOffice 로 PDF 를 만든다. 사용자 프로필은 임시 폴더(동시 실행·잠금 충돌 방지)."""
    exe = soffice()
    if not exe:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="zz-soffice-"))
    try:
        env = {"HOME": str(profile), "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "ko_KR.UTF-8"}
        pdf_filter = ('pdf:calc_pdf_Export:{"SinglePageSheets":{"type":"boolean","value":"true"}}'
                      if src.suffix.lower() in SHEET_EXTS else "pdf")
        cmd = [exe, "--headless", "--norestore", f"-env:UserInstallation=file://{profile}/profile",
               "--convert-to", pdf_filter, "--outdir", str(out_dir), str(src)]
        subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        out = out_dir / (src.stem + ".pdf")
        return out if out.exists() and out.stat().st_size > 0 else None
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def convert_file(src: Path, target: Path, progress=None) -> Path | None:
    """원본 파일 하나를 열람 PDF(target)로 그린다. 한글은 docx 를 거친다. progress(step) 는 'convert'(한글 → docx)·
    'render'(LibreOffice 로 PDF) 순으로 불린다 — 기다림 화면의 단계 표시용. 못 만들면 None."""
    src = Path(src)
    work = Path(tempfile.mkdtemp(prefix="zz-view-"))
    try:
        if progress:
            progress("convert")
        conv = docx_for(src)
        if conv is not None:
            data, ext = conv
            stage = work / (src.stem + ext)
            stage.write_bytes(data)
        else:
            stage = work / src.name
            shutil.copyfile(src, stage)
        if progress:
            progress("render")
        pdf = to_pdf(stage, work)
        if pdf is None:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(pdf), str(target))
        return target
    finally:
        shutil.rmtree(work, ignore_errors=True)


def render(db, doc: dict, force: bool = False, progress=None) -> Path | None:
    """문서의 열람 PDF — 있으면 그것, 없으면 지금 만든다. 만들면 files 장부(kind=view)에 적는다."""
    src = Path(doc.get("stored_path") or "")
    if not src.exists() or not is_office(src):
        return None
    target = view_path(doc)
    if target.exists() and target.stat().st_size > 0 and not force:
        return target
    out = convert_file(src, target, progress=progress)
    if out is None:
        return None
    try:
        db.add_file("view", str(target), name=VIEW_NAME, doc_id=int(doc["id"]), size=target.stat().st_size)
    except Exception:
        pass
    return target
