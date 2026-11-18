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
_INPUT_HINT = re.compile(r"[가-힣A-Za-z·()]{1,12}(?: [가-힣A-Za-z·()]{1,12}){0,3} 입력")
_HEADING = re.compile(r"(?:[0-9]{1,2}|[Ⅰ-Ⅻ]|[가-하])[.)]\s*\S.{0,24}")


def _secs(data: bytes) -> list[str]:
    z = zipfile.ZipFile(io.BytesIO(data))
    names = sorted((n for n in z.namelist() if _SEC.search(n)), key=lambda n: int(_SEC.search(n).group(1)))
    return [z.read(n).decode("utf-8", "replace") for n in names]


def _txt(x: str) -> str:
    return re.sub(r"<[^>]+>", " ", "".join(re.findall(r"<hp:t\b[^>]*>((?:[^<]|<hp:(?:tab|lineBreak|fwSpace|nbSpace|hyphen)\b[^>]*/>)*)</hp:t>", x))).strip()


def _cell_width(tc: str) -> int:
    m = re.search(r'<hp:cellSz\b[^>]*\bwidth="(\d+)"', tc)
    return int(m.group(1)) if m else 0


def _char_size(xml: str) -> int:
    return 1000                                              # 10pt 기준 — 글자 모양을 따라가지 않고 보수적으로


def issues(data: bytes) -> dict[str, list[str]]:
    """{갈래: [어디·무엇]} — 비었으면 깨끗하다."""
    out: dict[str, list[str]] = {k: [] for k in ("ditto", "empty_rows", "narrow", "placeholder", "picture_mark", "off_style", "toc_zero", "dup_table",
                                                     "wide", "tall_row", "unsplit")}
    try:
        secs = _secs(data)
    except Exception:
        return out
    off = _off_chars(data)
    tno = 0
    prev_tbl = ""
    for xml in secs:
        bw, bh = _body_box(xml)
        for tbl in re.finditer(r"<hp:tbl\b.*?</hp:tbl>", xml, flags=re.S):
            tno += 1
            body = tbl.group(0)
            _head_t = _txt((re.findall(r"<hp:tr>(.*?)</hp:tr>", body, flags=re.S) or [""])[0])[:20]
            sz = re.search(r'<hp:sz width="(\d+)"[^>]*height="(\d+)"', body)
            if sz and bw and int(sz.group(1)) > bw * 1.03 and xml.rfind("<hp:tc", 0, tbl.start()) <= xml.rfind("</hp:tc>", 0, tbl.start()):
                out["wide"].append(f"표{tno}(「{_head_t}」) 폭 {int(sz.group(1)) * 100 // bw}%")      # 본문 폭을 넘어 오른쪽으로 나감
            if bh:
                hs = [int(h) for rs, h in re.findall(r'<hp:cellSpan colSpan="\d+" rowSpan="(\d+)"/><hp:cellSz width="\d+" height="(\d+)"', body)
                      if rs == "1"]                       # 세로로 합친 칸은 원래 키가 크다 — 한 행짜리 칸만
                if hs and max(hs) > bh * 0.92:
                    out["tall_row"].append(f"표{tno}(「{_head_t}」) 칸 높이가 쪽의 {max(hs) * 100 // bh}%")   # 쪽에서 못 나눠 아래가 잘림
                if sz and 'pageBreak="NONE"' in body[:400] and int(sz.group(2)) > bh:
                    out["unsplit"].append(f"표{tno}(「{_head_t}」) 쪽보다 큰데 나누지 않게 됨")
            rows = re.findall(r"<hp:tr>(.*?)</hp:tr>", body, flags=re.S)
            whole = re.sub(r"\s+", "", _txt(body))
            if len(whole) > 30 and whole == prev_tbl and "<hp:tbl" not in body[6:]:   # 바로 앞 표와 글이 통째로 같다 — 같은 표를 두 번 넣음(11-16 14차)
                out["dup_table"].append(f"표{tno}(「{_txt(rows[0])[:20] if rows else ''}」)")
            prev_tbl = whole
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
        for m in re.finditer(r'<hp:t>([^<]{2,120}?)<hp:tab\b[^>]*\bleader="[1-9]\d*"[^>]*/>\s*0</hp:t>', flat):
            out["toc_zero"].append(m.group(1).strip()[:20])          # 목차 쪽 번호가 서식의 「0」 그대로
        for t in texts:
            for m in _PLACE.finditer(t):
                out["placeholder"].append(t[max(0, m.start() - 10):m.end() + 10].strip())
            if not _PLACE.search(t) and _INPUT_HINT.fullmatch(t.strip()):     # 「반영 방안 입력」 처럼 칸 글 전체가 입력 안내(만든 양식의 빈 칸)
                out["placeholder"].append(t.strip())
            for m in _PIC.finditer(t):
                out["picture_mark"].append(m.group(0))
        if off:                                              # 서식 규격 밖 글자(검정 아닌 색·기울임) — 견본 글 자리의 값·본문
            for r in re.finditer(r'<hp:run\b[^>/]*\bcharPrIDRef="(\d+)"[^>/]*>(.*?)</hp:run>', flat, flags=re.S):
                if r.group(1) in off:
                    t = "".join(re.findall(r"<hp:t\b[^>]*>([^<]*)</hp:t>", r.group(2))).strip()
                    if t and not _PLACE.search(t) and not _HEADING.fullmatch(t):   # 짧은 번호 제목(「1. 요약」)은 양식 제목 색일 뿐
                        out["off_style"].append(t[:15])
    return out


def table_problems(body: str, sec_xml: str, char_hu: int | None = None) -> list[str]:
    """표 하나(<hp:tbl>…</hp:tbl>)의 보기 문제 — 표를 쓰거나 고친 도구가 바로 그 표를 검사해 돌려준다(11-16 사용자: 표를 채운 자리에서 보기 좋은지 검사).
    폭 넘침·쪽보다 높은 칸·쪽 나눔 막힌 큰 표·좁은 열의 긴 글(세로로 접힘)·빈 행·글자로 남은 「〃」「←」."""
    out: list[str] = []
    bw, bh = _body_box(sec_xml)
    sz = re.search(r'<hp:sz width="(\d+)"[^>]*height="(\d+)"', body)
    if sz and bw and int(sz.group(1)) > bw * 1.03:
        out.append(f"폭이 본문의 {int(sz.group(1)) * 100 // bw}%라 오른쪽으로 넘친다 — widths 로 줄인다")
    if bh:
        hs = [int(h) for rs, h in re.findall(r'<hp:cellSpan colSpan="\d+" rowSpan="(\d+)"/><hp:cellSz width="\d+" height="(\d+)"', body) if rs == "1"]
        if hs and max(hs) > bh * 0.92:
            out.append(f"한 칸 높이가 쪽의 {max(hs) * 100 // bh}%라 쪽에서 잘린다 — 긴 글은 행을 나눠 넣거나 표 밖 글로 뺀다")
        if sz and 'pageBreak="NONE"' in body[:400] and int(sz.group(2)) > bh:
            out.append("쪽보다 큰데 쪽 나눔이 막혀 통째로 넘어간다")
    rows = re.findall(r"<hp:tr>(.*?)</hp:tr>", body, flags=re.S)
    ch = char_hu or _char_size(sec_xml)
    narrow, marks, run, worst = [], 0, 0, 0
    for i, tr in enumerate(rows):
        cells = re.findall(r"<hp:tc\b.*?</hp:tc>", tr, flags=re.S)
        texts = [_txt(c) for c in cells]
        marks += sum(1 for t in texts if t.strip() in ("〃", "″", "←", "◀"))
        if i > 0 and cells and not any(texts):
            run += 1; worst = max(worst, run)
        else:
            run = 0
        for c, t in zip(cells, texts):
            w = _cell_width(c)
            if len(t) > 4 and 0 < w < 2 * ch + 600 and "<hp:tbl" not in c:
                narrow.append(t[:8])
    if narrow:
        out.append(f"좁은 열에 긴 글 {len(narrow)}칸(「{narrow[0]}」…)이 세로로 접힌다 — widths 로 그 열을 넓히거나 열을 합친다")
    if worst >= 2:
        out.append(f"빈 행 {worst}줄이 이어진다 — 채우거나 delete_rows")
    if marks:
        out.append(f"「〃」「←」 {marks}칸이 글자로 남았다 — 합치기 표시라 set_cells 로 다시 넣으면 합쳐진다")
    return out


def _body_box(xml: str) -> tuple[int, int]:
    """구역의 본문 폭·높이(HWPUNIT) — 용지에서 여백·머리말·꼬리말을 뺀 값. 못 읽으면 (0, 0)."""
    pg = re.search(r'<hp:pagePr\b[^>]*\bwidth="(\d+)"[^>]*\bheight="(\d+)"', xml)
    mg = re.search(r'<hp:margin\b([^>]*)/>', xml)
    if not pg or not mg:
        return 0, 0
    g = {k: int(v) for k, v in re.findall(r'(\w+)="(\d+)"', mg.group(1))}
    w = int(pg.group(1)) - g.get("left", 0) - g.get("right", 0)
    h = int(pg.group(2)) - g.get("top", 0) - g.get("bottom", 0) - g.get("header", 0) - g.get("footer", 0)
    land = 'landscape="NARROWLY"' in xml[:pg.end() + 200] or 'landscape="1"' in xml[:pg.end() + 200]
    return (h, w) if land and h > w else (max(w, 0), max(h, 0))


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
         "toc_zero": "목차 쪽 번호가 0 그대로(본문을 다 쓴 뒤 hwp_toc_pages 로 실제 쪽에 맞춘다)",
         "dup_table": "바로 앞 표와 글이 똑같은 표(같은 표를 두 번 넣음 — 하나를 지운다)",
         "wide": "본문 폭보다 넓은 표(오른쪽으로 넘침 — hwp_table_edit widths 로 줄이거나 열을 나눈다)",
         "tall_row": "쪽 높이에 가까운 칸(쪽에서 못 나눠 아래가 잘림 — 긴 글은 행을 나눠 넣거나 글로 뺀다)",
         "unsplit": "쪽보다 큰데 쪽 나눔을 막은 표(통째로 넘어가거나 잘림 — 행 단위로 나뉘게 한다)",
         "off_style": "서식 규격 밖 글자(파랑·빨강·기울임 — 견본 글 꼴을 물려받음, 같은 글로 다시 쓰면 본문 꼴이 된다)"}


def _key(x: str) -> str:
    """견줄 열쇠 — 표 번호(「표12」)는 앞에서 표를 넣고 빼면 밀리므로 떼고 내용으로만 견준다(11-13 8차: 표를 그림으로 바꾼 뒤 번호가 밀려
    원래 있던 목차 표의 빈 행을 「새로 생긴 것」 으로 보고 지움)."""
    return re.sub(r"^표\d+", "표", x)


def new_issues(before: dict[str, list[str]], after: dict[str, list[str]]) -> dict[str, list[str]]:
    """이번 요청에서 새로 생겼거나 늘어난 것만."""
    out = {}
    for k, v in after.items():
        old = [_key(x) for x in (before.get(k) or [])]
        fresh = []
        for x in v:
            if _key(x) in old:
                old.remove(_key(x))
            else:
                fresh.append(x)
        if fresh:
            out[k] = fresh
    return out


def report(found: dict[str, list[str]], limit: int = 4) -> str:
    return "\n".join(f"- {LABEL.get(k, k)}: " + "; ".join(v[:limit]) + (f" 외 {len(v) - limit}곳" if len(v) > limit else "")
                     for k, v in found.items() if v)
