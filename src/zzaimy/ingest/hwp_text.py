"""한글 문서(.hwp 바이너리 / .hwpx)에서 텍스트를 추출한다.

ADR-0014 열린 항목("네이티브 .hwp 읽기") 해소: 인제스트에서 .hwp/.hwpx를
텍스트로 읽는 경로다. 생성·렌더가 아니라 읽기(파싱)이므로 폰트·조판 문제와
무관하다.

- .hwpx: 표준 ZIP+XML. 라이브러리 없이 `Contents/section*.xml`의 `<hp:t>`를
  긁는다.
- .hwp (HWP 5.0 바이너리): pyhwp(TextTransform). pyhwp는 `six`에 의존한다.

한계: 표 셀 내용은 pyhwp 텍스트 변환에서 `<표>` 자리표시로 접힌다. 표 구조·셀
내용이 필요한 계열은 인제스트의 표 파서(lattice)와 함께 쓴다. 스캔 PDF처럼
텍스트가 비면 호출측이 OCR 경로로 보낸다.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

_HP_T = re.compile(r"<hp:t>(.*?)</hp:t>", re.S)
_TAG = re.compile(r"<[^>]+>")
_ENT = {"&#13;": "\n", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"'}


def _unescape(s: str) -> str:
    for k, v in _ENT.items():
        s = s.replace(k, v)
    return s


class EncryptedHwpxError(ValueError):
    """배포용(암호화) HWPX — 본문 섹션이 암호화돼 키 없이 추출 불가."""


def extract_hwpx(path: str | Path) -> str:
    """.hwpx 본문 텍스트. 섹션 순서대로 문단 텍스트를 잇는다.

    배포용(암호화) HWPX는 섹션이 XML이 아니라 암호화 스트림이라 추출할 수
    없다 — 조용히 빈 문자열을 주지 않고 EncryptedHwpxError로 명확히 알린다.
    """
    parts: list[str] = []
    with zipfile.ZipFile(path) as z:
        names = sorted(
            n for n in z.namelist()
            if re.search(r"Contents/section\d+\.xml$", n)
        )
        for n in names:
            raw = z.read(n)
            head = raw[:64].lstrip()
            if not head.startswith(b"<"):
                # 정상 HWPX 섹션은 XML(‘<’)로 시작. 아니면 배포용 암호화 스트림.
                raise EncryptedHwpxError(f"배포용/암호화 HWPX로 보임: {Path(path).name}")
            xml = raw.decode("utf-8", "replace")
            for chunk in _HP_T.findall(xml):
                parts.append(_unescape(_TAG.sub("", chunk)))
    return "\n".join(parts).strip()


def extract_hwp(path: str | Path) -> str:
    """.hwp(HWP 5.0 바이너리) 본문 텍스트. pyhwp TextTransform 사용."""
    from hwp5.hwp5txt import TextTransform      # 지연 임포트(선택 의존)
    from hwp5.xmlmodel import Hwp5File

    h = Hwp5File(str(path))
    buf = io.BytesIO()
    TextTransform().transform_hwp5_to_text(h, buf)
    return buf.getvalue().decode("utf-8", "replace").strip()


def extract_text(path: str | Path) -> str:
    """확장자로 분기해 텍스트를 추출한다. 지원하지 않으면 ValueError."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".hwpx":
        return extract_hwpx(p)
    if ext == ".hwp":
        return extract_hwp(p)
    raise ValueError(f"지원하지 않는 형식: {ext} (pdf는 ingest의 PDF 파서로)")
