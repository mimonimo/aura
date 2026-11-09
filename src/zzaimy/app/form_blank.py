"""지난 문서를 양식으로 쓸 때 옛 값 비우기(양식화) — 구조·표 머리·라벨·작성방법은 두고, 표 칸에 채워진 값(수치·날짜·세부 내역)만 비운다.

왜(11-09). 결과보고서 초안 요청에서 에이전트가 학교의 지난 실적보고서(1차년도 예결산이 다 채워진 파일)를 양식으로 골랐고,
그 안의 옛 수치(예산 2,100백만원·집행 1,902백만원 등)가 새 문서에 그대로 남았다. 「1차년도」→「2차년도」 바꾸기까지 하자
옛 해 실적이 새 해 값처럼 보였다. 수치 대조기도 양식 글을 근거로 쳐서 옛 값을 걸러내지 못했다.
완성본을 양식으로 쓸 때는 값을 비운 사본으로 시작하고, 옛 값은 근거 자료(문서함 검색)로만 쓴다.

판정은 일반 규칙이다 — 표 칸 글이 「값」(숫자·금액·비율·날짜만, 또는 숫자 든 세부 내역 줄)인 칸이 비어 있지 않은 칸의
상당 부분이면 「채워진 문서」. 예시 글자(○○·00%·0000. 00.)는 빈 양식의 자리표시라 값으로 치지 않는다.
"""
from __future__ import annotations

import io
import re
import zipfile

_SEC = re.compile(r"Contents/section\d+\.xml$")
_T = re.compile(r"<hp:t>(?:(?!</hp:t>).)*?</hp:t>|<hp:t/>", re.S)
_LINESEG = re.compile(r"<hp:linesegarray>.*?</hp:linesegarray>", re.S)
_ROW0 = re.compile(r'<hp:cellAddr\b[^>]*\browAddr="0"')

# 값으로 보는 칸 글 — 숫자를 하나 이상 품고, 숫자·단위·구분 기호로만 된 것(「12.6」「2,100」「60.8%」「’22.4.」「157,159천원」「8명」)
_VALUE = re.compile(r"[\d\s,.%()~～’'‘\-+=/×xX:*원천백만억명건개회점월일년분시호]+")
# 예시 글자만 있는 칸(빈 양식의 자리표시) — 「00%」「0,000」「○○」「0000. 00. 00.」
_PLACEHOLDER = re.compile(r"[0○◯ㅇOo\s,.%()~\-/:원천백만명건개년월일]+")
_BULLET = re.compile(r"^\s*[◦○•▪·\-‣※∙ㆍ]\s*")

MIN_VALUES = 20        # 값 칸이 이만큼 안 되면 채워진 문서로 보지 않는다(서식의 배점·예시 몇 칸)
FILLED_SHARE = 0.4     # 비어 있지 않은 칸 가운데 값 칸의 몫이 이 이상이면 「채워진 문서」


def _text(frag: str) -> str:
    return re.sub(r"<[^>]+>", "", re.sub(r"<hp:lineBreak\s*/>", "\n", frag))


def _leaf_cells(xml: str) -> list[tuple[int, int]]:
    """안에 표가 없는 칸(<hp:tc>…</hp:tc>)의 자리 — 겹친 표는 가장 안쪽 칸만."""
    out, stack = [], []
    for m in re.finditer(r"<hp:tc\b|</hp:tc>", xml):
        if m.group(0) != "</hp:tc>":
            stack.append(m.start())
        elif stack:
            a = stack.pop()
            body = xml[a:m.end()]
            if "<hp:tbl" not in body[5:]:
                out.append((a, m.end()))
    return out


def is_value(text: str) -> bool:
    """칸 글이 채워진 값인가 — 숫자만 된 값, 또는 숫자 든 세부 내역 줄이 절반 넘는 칸. 예시 글자는 값이 아니다."""
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t or not re.search(r"\d", t):
        return False
    if _VALUE.fullmatch(t):
        return not (_PLACEHOLDER.fullmatch(t) and not re.search(r"[1-9]", t))
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    detail = [ln for ln in lines if _BULLET.match(ln) and re.search(r"\d", ln)]
    return len(lines) >= 1 and len(detail) * 2 > len(lines)


def _cells(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for n in z.namelist():
            if _SEC.search(n):
                xml = z.read(n).decode("utf-8", "replace")
                for a, b in _leaf_cells(xml):
                    yield n, xml[a:b]


def stats(data: bytes) -> tuple[int, int]:
    """(비어 있지 않은 칸 수, 그 가운데 값 칸 수) — 머리 행(행 번호 0)은 세지 않는다."""
    filled = values = 0
    for _n, cell in _cells(data):
        if _ROW0.search(cell):
            continue
        t = _text(cell).strip()
        if not t:
            continue
        filled += 1
        values += is_value(t)
    return filled, values


def looks_filled(data: bytes) -> bool:
    """값이 채워진 완성본인가(빈 양식이 아니라)."""
    try:
        filled, values = stats(data)
    except Exception:
        return False
    return values >= MIN_VALUES and values >= FILLED_SHARE * max(filled, 1)


def blank_values(data: bytes) -> tuple[bytes, int]:
    """표 칸의 값만 비운 사본과 비운 칸 수 — 머리 행·라벨 칸·작성방법·예시 글자·표 밖 글은 그대로. 값이 없으면 원본 그대로."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    n_blank = 0
    with zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            raw = zin.read(info.filename)
            if _SEC.search(info.filename):
                xml = raw.decode("utf-8")
                parts, last = [], 0
                for a, b in _leaf_cells(xml):
                    cell = xml[a:b]
                    if _ROW0.search(cell) or not is_value(_text(cell).strip()):
                        continue
                    first = [True]

                    def keep_first(m):
                        if first[0]:
                            first[0] = False
                            return "<hp:t></hp:t>"
                        return ""
                    new = _LINESEG.sub("", _T.sub(keep_first, re.sub(r"<hp:lineBreak\s*/>", "", cell)))
                    parts.append(xml[last:a] + new)
                    last = b
                    n_blank += 1
                if parts:
                    raw = ("".join(parts) + xml[last:]).encode("utf-8")
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
            zi.external_attr = info.external_attr
            zout.writestr(zi, raw)
    return (out.getvalue(), n_blank) if n_blank else (data, 0)
