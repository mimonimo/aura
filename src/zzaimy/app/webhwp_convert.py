"""한글 파일 → 기안기 작업 양식 — hwp 는 기안기 서버(필터 서버 open→save)로 hwpx 로 바꾸고, 쓸 자리 누름틀을 박는다.

사업 공고가 준 양식(프로젝트·문서함의 hwp·hwpx 원본)을 그대로 기준 삼아 쓰기 위한 단계다(10-22 사용자 — 사업 양식이 있으면 그 양식대로).
일괄 수집(scripts/200)과 화면의 「기안기로 작업」 이 같은 함수를 쓴다. 다른 형식으로 내려받기(webhwp_export)도 같은 변환 길(convert)을 쓴다.
"""
from __future__ import annotations

import os


class TooBig(RuntimeError):
    """기안기 변환 서버가 받지 못하는 큰 파일(웹 한글 기안기 업로드 한도, 운영 실측 약 56MB)."""


def upload_max() -> int:
    """기안기 변환 서버가 받는 한 파일 크기 상한(바이트) — 설치마다 다를 수 있어 설정(ZZAIMY_WEBHWP_UPLOAD_MAX_MB)으로."""
    try:
        return int(float(os.environ.get("ZZAIMY_WEBHWP_UPLOAD_MAX_MB", "55")) * 1024 * 1024)
    except ValueError:
        return 55 * 1024 * 1024


def convert(data: bytes, src_format: str, out_format: str, out_name: str, http=None) -> bytes:
    """기안기 필터 서버로 형식 바꾸기 — open(파일→문서 데이터, src_format) → save(out_format) → 받기.

    src_format 은 HWP·HWPX, out_format 은 필터 서버가 받는 이름(HWPX·HWP·PDF·OOXML 등). 실패하면 RuntimeError."""
    import httpx
    from zzaimy.app import webhwp
    own = http is None
    http = http or httpx.Client(timeout=httpx.Timeout(240, connect=20))
    src_ext = ".hwp" if src_format.upper() == "HWP" else ".hwpx"
    if len(data) > upload_max():
        raise TooBig(f"파일이 {len(data) // (1024 * 1024)}MB 로 한글 변환 한도(약 {upload_max() // (1024 * 1024)}MB)를 넘습니다")
    try:
        base = webhwp.base_url()
        try:
            r = http.post(f"{base}/webhwpctrl/open", files={"openFile": ("x" + src_ext, data)},
                          data={"format": src_format.upper(), "args": ""}, timeout=240)
            if r.status_code in (413, 500) and len(data) > 20 * 1024 * 1024:
                # 기안기가 큰 파일을 받지 않으면 500(MaxUploadSizeExceeded)을 낸다 — 연결 문제가 아니라 크기 문제다
                raise TooBig(f"파일이 {len(data) // (1024 * 1024)}MB 로 한글 변환 서버가 받지 않았습니다")
            r.raise_for_status()
            j = r.json()
        except httpx.HTTPError as e:
            raise RuntimeError(f"한글 변환 서버가 응답하지 않습니다({type(e).__name__}) — 잠시 뒤 다시 하거나 관리자에게 알립니다") from e
        if not j.get("json"):
            raise RuntimeError("기안기가 이 한글 파일을 열지 못했습니다(옛 판·암호 문서일 수 있습니다)")
        try:
            s = http.post(f"{base}/webhwpctrl/save", headers={"AJAX": "true"},
                          files={"hwpj": ("hwpj.data", j["json"].encode("utf-8"))},
                          data={"id": j["id"], "filename": out_name, "format": out_format, "args": ""}, timeout=240)
            s.raise_for_status()
            sj = s.json()
        except httpx.HTTPError as e:
            raise RuntimeError(f"한글 변환 서버가 응답하지 않습니다({type(e).__name__}) — 잠시 뒤 다시 하거나 관리자에게 알립니다") from e
        if not sj.get("result"):
            raise RuntimeError(f"기안기가 {out_format} 로 저장하지 못했습니다")
        try:
            g = http.get(f"{base}/filterserver/get/{j['id']}/{sj['fileName']}", timeout=120)
            g.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(f"변환한 파일을 받지 못했습니다({type(e).__name__})") from e
        return g.content
    finally:
        if own:
            http.close()


def to_hwpx(data: bytes, ext: str, http=None) -> bytes:
    """hwpx 는 그대로, hwp 는 기안기 서버로 — open(파일→문서 데이터) → save(HWPX) → 받기."""
    ext = (ext or "").lower()
    if ext == ".hwpx" or data[:2] == b"PK":
        return data
    out = convert(data, "HWP", "HWPX", "x.hwpx", http)
    if out[:2] != b"PK":
        raise RuntimeError("변환 결과가 hwpx 가 아닙니다")
    return out


def work_form(data: bytes, ext: str, http=None, blank: bool = False, report: dict | None = None) -> tuple[bytes, list[dict]]:
    """한글 파일 → (필드를 박은 hwpx, 필드 목록).
    blank: 값이 채워진 완성본(지난 문서)이면 표 칸의 값을 비운 사본으로(form_blank — 옛 값이 새 문서에 남지 않게).
    report 를 주면 비운 칸 수를 report["blanked"] 에 적는다."""
    from zzaimy.app import webhwp_fields
    hx = to_hwpx(data, ext, http)
    if blank:
        from zzaimy.app import form_blank
        if form_blank.looks_filled(hx):
            hx, n = form_blank.blank_values(hx)
            if report is not None:
                report["blanked"] = n
    return webhwp_fields.fieldize(hx)
