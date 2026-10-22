"""한글 파일 → 기안기 작업 양식 — hwp 는 기안기 서버(필터 서버 open→save)로 hwpx 로 바꾸고, 쓸 자리 누름틀을 박는다.

사업 공고가 준 양식(프로젝트·문서함의 hwp·hwpx 원본)을 그대로 기준 삼아 쓰기 위한 단계다(10-22 사용자 — 사업 양식이 있으면 그 양식대로).
일괄 수집(scripts/200)과 화면의 「기안기로 작업」 이 같은 함수를 쓴다.
"""
from __future__ import annotations


def to_hwpx(data: bytes, ext: str, http=None) -> bytes:
    """hwpx 는 그대로, hwp 는 기안기 서버로 — open(파일→문서 데이터) → save(HWPX) → 받기."""
    ext = (ext or "").lower()
    if ext == ".hwpx" or data[:2] == b"PK":
        return data
    import httpx
    from zzaimy.app import webhwp
    own = http is None
    http = http or httpx.Client(timeout=httpx.Timeout(240, connect=20))
    try:
        base = webhwp.base_url()
        r = http.post(f"{base}/webhwpctrl/open", files={"openFile": ("x.hwp", data)}, data={"format": "HWP", "args": ""}, timeout=240)
        r.raise_for_status()
        j = r.json()
        if not j.get("json"):
            raise RuntimeError("기안기가 이 한글 파일을 열지 못했습니다(옛 판·암호 문서일 수 있습니다)")
        s = http.post(f"{base}/webhwpctrl/save", headers={"AJAX": "true"},
                      files={"hwpj": ("hwpj.data", j["json"].encode("utf-8"))},
                      data={"id": j["id"], "filename": "x.hwpx", "format": "HWPX", "args": ""}, timeout=240)
        s.raise_for_status()
        sj = s.json()
        if not sj.get("result"):
            raise RuntimeError("기안기가 hwpx 로 저장하지 못했습니다")
        g = http.get(f"{base}/filterserver/get/{j['id']}/{sj['fileName']}", timeout=120)
        g.raise_for_status()
        if g.content[:2] != b"PK":
            raise RuntimeError("변환 결과가 hwpx 가 아닙니다")
        return g.content
    finally:
        if own:
            http.close()


def work_form(data: bytes, ext: str, http=None) -> tuple[bytes, list[dict]]:
    """한글 파일 → (필드를 박은 hwpx, 필드 목록)."""
    from zzaimy.app import webhwp_fields
    return webhwp_fields.fieldize(to_hwpx(data, ext, http))
