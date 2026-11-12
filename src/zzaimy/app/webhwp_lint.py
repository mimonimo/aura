"""한글 작업본 품질 점검(11-12) — 에이전트가 요청을 끝내기 전에 이번에 생긴 지저분한 곳을 찾는다.

사용자 판정(특성화 재현 3차): 「표 양식이 넘치거나 「〃」 이 줄마다 찍히고, 늘린 행이 비고, 견본 글·그림 자리 표시가 남아 지저분하다 — 항상 원본 수준이 나와야」.
고치는 판단은 에이전트가 한다. 이 모듈은 파일을 읽어 문제 목록만 낸다(쓰기 없음). 요청 시작 때와 끝날 때를 견줘 새로 생긴 것만 되짚게 한다.

점검(표마다·문서 전체):
- ditto: 표 칸 글이 「〃」 뿐 — 위 칸과 합칠 자리
- empty_rows: 자료 행이 통째로 빈 채 2줄 넘게 이어짐 — 늘리고 못 채운 행
- narrow: 글이 4자 넘는데 열 폭이 글자 두 개도 안 되는 칸 — 글자 단위로 접혀 세로로 늘어짐
- placeholder: 견본 글(OOO·○○○·「본문 입력」) 이 남음
- picture_mark: 「[그림 …]」 자리 표시가 글로 남음(그림을 넣지 않음)
"""
from __future__ import annotations

import io
import re
import zipfile

_SEC = re.compile(r"Contents/section(\d+)\.xml$")
_PLACE = re.compile(r"OOO|○○○|본문 입력")
_PIC = re.compile(r"\[그림 [^\]]{1,80}\]")


def _secs(data: bytes) -> list[str]:
    z = zipfile.ZipFile(io.BytesIO(data))
    names = sorted((n for n in z.namelist() if _SEC.search(n)), key=lambda n: int(_SEC.search(n).group(1)))
    return [z.read(n).decode("utf-8", "replace") for n in names]


def _txt(x: str) -> str:
    return "".join(re.findall(r"<hp:t\b[^>]*>([^<]*)</hp:t>", x)).strip()


def _cell_width(tc: str) -> int:
    m = re.search(r'<hp:cellSz\b[^>]*\bwidth="(\d+)"', tc)
    return int(m.group(1)) if m else 0


def _char_size(xml: str) -> int:
    return 1000                                              # 10pt 기준 — 글자 모양을 따라가지 않고 보수적으로


def issues(data: bytes) -> dict[str, list[str]]:
    """{갈래: [어디·무엇]} — 비었으면 깨끗하다."""
    out: dict[str, list[str]] = {k: [] for k in ("ditto", "empty_rows", "narrow", "placeholder", "picture_mark", "off_style")}
    try:
        secs = _secs(data)
    except Exception:
        return out
    off = _off_chars(data)
    tno = 0
    for xml in secs:
        for tbl in re.finditer(r"<hp:tbl\b.*?</hp:tbl>", xml, flags=re.S):
            tno += 1
            body = tbl.group(0)
            rows = re.findall(r"<hp:tr>(.*?)</hp:tr>", body, flags=re.S)
            head = _txt(rows[0])[:20] if rows else ""
            where = f"표{tno}(「{head}」)"
            ditto = run = worst = 0
            narrow = []
            for i, tr in enumerate(rows):
                cells = re.findall(r"<hp:tc\b.*?</hp:tc>", tr, flags=re.S)
                texts = [_txt(c) for c in cells]
                ditto += sum(1 for t in texts if t in ("〃", "″", "\""))
                if i > 0 and cells and not any(texts):
                    run += 1
                    worst = max(worst, run)
                else:
                    run = 0
                for c, t in zip(cells, texts):
                    w = _cell_width(c)
                    if len(t) > 4 and 0 < w < 2 * _char_size(xml) + 600 and "<hp:tbl" not in c:
                        narrow.append(t[:8])
            if ditto >= 2:
                out["ditto"].append(f"{where} 「〃」 칸 {ditto}개")
            if worst >= 2:
                out["empty_rows"].append(f"{where} 빈 행 {worst}줄 연속")
            if narrow:
                out["narrow"].append(f"{where} 좁은 열에 긴 글 {len(narrow)}칸(「{narrow[0]}」…)")
        flat = re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", xml, flags=re.S)
        texts = re.findall(r"<hp:t\b[^>]*>([^<]*)</hp:t>", flat)
        for t in texts:
            for m in _PLACE.finditer(t):
                out["placeholder"].append(t[max(0, m.start() - 10):m.end() + 10].strip())
            for m in _PIC.finditer(t):
                out["picture_mark"].append(m.group(0))
        if off:                                              # 서식 규격 밖 글자(검정 아닌 색·기울임) — 견본 글 자리의 값·본문
            for r in re.finditer(r'<hp:run\b[^>/]*\bcharPrIDRef="(\d+)"[^>/]*>(.*?)</hp:run>', flat, flags=re.S):
                if r.group(1) in off:
                    t = "".join(re.findall(r"<hp:t\b[^>]*>([^<]*)</hp:t>", r.group(2))).strip()
                    if t and not _PLACE.search(t):
                        out["off_style"].append(t[:15])
    return out


def _off_chars(data: bytes) -> set[str]:
    """검정이 아닌 색이거나 기울인 글자 모양 id."""
    try:
        hdr = zipfile.ZipFile(io.BytesIO(data)).read("Contents/header.xml").decode("utf-8", "replace")
    except Exception:
        return set()
    out = set()
    for m in re.finditer(r'<hh:charPr\b[^>]*\bid="(\d+)"[^>]*>(?:(?!</hh:charPr>).)*</hh:charPr>', hdr, flags=re.S):
        col = re.search(r'\btextColor="#([0-9A-Fa-f]{6})"', m.group(0))
        if (col and col.group(1).upper() != "000000") or "<hh:italic" in m.group(0):
            out.add(m.group(1))
    return out


def counts(found: dict[str, list[str]]) -> dict[str, int]:
    return {k: len(v) for k, v in found.items()}


LABEL = {"ditto": "「〃」 이 글자로 남은 표(위 칸과 합칠 자리)", "empty_rows": "늘리고 채우지 않은 빈 행",
         "narrow": "열이 좁아 글자 단위로 접힌 칸(열 폭을 넓히거나 다른 열을 줄인다)", "placeholder": "견본 글(OOO·○○○·본문 입력)",
         "picture_mark": "그림 자리 표시가 글로 남음(hwp_picture 로 그 그림을 넣는다)",
         "off_style": "서식 규격 밖 글자(파랑·빨강·기울임 — 견본 글 꼴을 물려받음, 같은 글로 다시 쓰면 본문 꼴이 된다)"}


def new_issues(before: dict[str, list[str]], after: dict[str, list[str]]) -> dict[str, list[str]]:
    """이번 요청에서 새로 생겼거나 늘어난 것만."""
    out = {}
    for k, v in after.items():
        old = list(before.get(k) or [])
        fresh = []
        for x in v:
            if x in old:
                old.remove(x)
            else:
                fresh.append(x)
        if fresh:
            out[k] = fresh
    return out


def report(found: dict[str, list[str]], limit: int = 4) -> str:
    return "\n".join(f"- {LABEL.get(k, k)}: " + "; ".join(v[:limit]) + (f" 외 {len(v) - limit}곳" if len(v) > limit else "")
                     for k, v in found.items() if v)
