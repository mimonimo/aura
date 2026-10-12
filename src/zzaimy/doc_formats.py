"""문서 형식 한 곳 — 원본 처리 대상 형식(DOC_EXT)·사진·압축 형식, 확장자로 형식을 알 수 없는 파일의 실제 형식 판별.

원본 보관소 화면·반입 점검(scripts/175)·DGX 목록(165)·DGX 처리(167)·VM 동기화(170)가 모두 여기서 가져간다.
앱 의존이 없어 DGX 의 가벼운 가상환경에서도 불러온다(olefile 이 없으면 옛 오피스·한글 이진 판별만 빠진다).
"""
from __future__ import annotations

from pathlib import Path

# 처리 대상 문서 형식 — 반입 처리기(app/pipeline)가 본문 글자를 읽을 수 있는 형식
#   한글·PDF·오피스: hwp hwpx pdf docx xlsx pptx / 옛 오피스(LibreOffice 로 새 형식으로): xls doc ppt
#   매크로·서식 파일: xlsm(openpyxl) pptm(python-pptx) xlt(옛 엑셀 서식, LibreOffice)
#   열린 문서·서식 있는 글: ods rtf(LibreOffice) / 글: csv txt / 웹 문서: html htm mht(태그 뺀 본문) / 메일: eml(제목·본문)
DOC_EXT = ("hwp", "hwpx", "pdf", "docx", "xlsx", "pptx", "xls", "doc", "ppt",
           "xlsm", "pptm", "xlt", "ods", "rtf", "csv", "txt", "html", "htm", "mht", "eml")
IMAGE_EXT = ("jpg", "jpeg", "png", "bmp", "gif", "tif", "tiff", "heic", "webp")
ARCHIVE_EXT = ("zip", "7z", "rar", "egg", "alz")

# 브라우저가 내려받다 만 파일·웹 페이지 저장 부속은 이름 끝이 「다운로드」다(예: 「보고서.pdf다운로드」, 「x.js.다운로드」)
DOWNLOAD_TAIL = "다운로드"

# 파일 머리로 판별한 실제 형식 가운데 문서인 것 → 원본 장부의 형식. 글 파일(.txt 판별)은 넣지 않는다 — 확장자 없는
# 글 파일은 웹 페이지 부속(스크립트·스타일)과 구별할 수 없다
SNIFF_DOC = {".pdf": "pdf", ".hwp": "hwp", ".hwp3": "hwp", ".hml": "hwp", ".hwpx": "hwpx", ".docx": "docx",
             ".xlsx": "xlsx", ".pptx": "pptx", ".xls": "xls", ".doc": "doc", ".ppt": "ppt"}

_OLE = b"\xd0\xcf\x11\xe0"


def file_ext(name: str) -> str:
    """파일 이름의 확장자(소문자, 점 없이). 없으면 빈 값."""
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def needs_sniff(name: str, ext: str | None = None) -> bool:
    """확장자로 형식을 알 수 없는 파일 — 확장자 없음·이름 끝 「다운로드」. 파일 머리로 실제 형식을 봐야 한다."""
    e = file_ext(name) if ext is None else (ext or "").lower().lstrip(".")
    return not e or name.endswith(DOWNLOAD_TAIL)


def sniff_suffix(file_path: Path) -> str | None:
    """파일 머리로 실제 형식(확장자)을 판별한다 — 모르면 None. 확장자와 내용이 다른 원본을
    「형식이 아닙니다」로 버리지 않고 실제 형식으로 읽기 위해."""
    try:
        with Path(file_path).open("rb") as fh:
            head = fh.read(2048)
    except OSError:
        return None
    if not head.strip(b"\x00 \r\n\t"):
        return ".empty"
    if b"%PDF-" in head[:1024]:
        return ".pdf"
    if head.startswith(b"HWP Document File"):
        return ".hwp3"                                   # 한글 3.0(옛 이진 형식)
    body = head.lstrip(b"\xef\xbb\xbf \r\n\t")
    if body.startswith(b"<?xml") and b"<HWPML" in head:
        return ".hml"
    if head.startswith(b"PK"):
        import zipfile
        try:
            with zipfile.ZipFile(file_path) as zf:
                names = zf.namelist()
                mime = zf.read("mimetype").decode("ascii", "ignore") if "mimetype" in names else ""
        except (zipfile.BadZipFile, OSError, KeyError):
            return None
        if "hwp" in mime or any(n.startswith("Contents/") for n in names):
            return ".hwpx"
        for prefix, ext in (("word/", ".docx"), ("xl/", ".xlsx"), ("ppt/", ".pptx")):
            if any(n.startswith(prefix) for n in names):
                return ext
        return None
    if head.startswith(_OLE):
        try:
            import olefile
            with olefile.OleFileIO(str(file_path)) as ole:
                streams = {"/".join(e) for e in ole.listdir()}
        except Exception:
            return None
        if streams & {"EncryptionInfo", "EncryptedPackage"}:
            return ".encrypted"
        if "FileHeader" in streams:
            return ".hwp"
        if streams & {"Workbook", "Book"}:
            return ".xls"
        if "WordDocument" in streams:
            return ".doc"
        if "PowerPoint Document" in streams:
            return ".ppt"
        return None
    if b"\x00" not in head and not body.lower().startswith((b"<!doctype", b"<html", b"<?xml")):
        try:
            head.decode("utf-8")
            return ".txt"                                # 글 파일에 한글·오피스 확장자를 붙인 것
        except UnicodeDecodeError:
            pass
    return None


def sniff_doc_ext(file_path: Path) -> str:
    """확장자로 알 수 없는 파일의 실제 문서 형식(원본 장부의 형식 값) — 문서가 아니면 빈 값."""
    return SNIFF_DOC.get(sniff_suffix(file_path) or "", "")
