"""한글 양식에 누름틀 필드 박기 — 기안기에서 사람과 에이전트가 「어디에 쓸지」를 같은 이름으로 짚게 한다.

왜(10-22). 학교 양식은 서식 전체가 큰 테두리 표 안에 들어 있거나(절 제목이 표 칸 안), 번호 절 없이 라벨 칸만 있는 신청서·서명부가 많다.
문단 위치로 절을 찾는 채우기(hwpx_fill)는 이런 양식을 놓친다. 쓸 자리에 이름 붙은 누름틀을 미리 박아 두면
기안기 API(MoveToField·PutFieldText·Insert)로 표 안 제목 아래든 칸이든 같은 방법으로 쓴다.

필드 이름(사람이 읽는 꼴, 같은 이름이 겹치면 뒤에 「#2」):
- 「절:<제목>」 — 번호 붙은 절 제목 문단(표 칸 안 포함) 바로 다음에 새 빈 문단을 넣고 그 안에 필드. 본문·표·그림은 여기에 끼워 넣는다.
- 「칸:<라벨>」 — 같은 행에서 라벨 칸 바로 오른쪽의 빈 칸 첫 문단.
- 「표<n>:<머리 칸>」 — 머리행 + 빈 행으로 된 목록표의 첫 빈 행 칸(행 늘리기는 기안기 표 동작으로).
필드 XML 꼴은 기안기가 저장한 것을 그대로 따른다(CLICK_HERE, Direction = 안내 글). 이미 필드가 있는 양식은 그 필드를 그대로 두고 더하지 않는다.
"""
from __future__ import annotations

import io
import random
import re
import zipfile
from xml.sax.saxutils import escape

from zzaimy.app import section_context as sc

_SEC = re.compile(r"Contents/section\d+\.xml$")
_LEAF_P = re.compile(r"<hp:p\b[^>]*>(?:(?!<hp:p\b).)*?</hp:p>", re.S)
_TC = re.compile(r"<hp:tc\b.*?</hp:tc>", re.S)
_TR = re.compile(r"<hp:tr\b.*?</hp:tr>", re.S)
_TBL_OPEN = re.compile(r"<hp:tbl\b")


MIN_SLOT_W = 2800        # 쓸 자리로 볼 칸의 최소 폭(HWPUNIT, 약 10mm) — 이보다 좁은 칸은 도식·차트의 여백 칸

_LINESEG = re.compile(r"<hp:linesegarray>.*?</hp:linesegarray>", re.S)

# XML 1.0 이 받지 않는 글자 — 제어 문자(탭·줄바꿈 빼고)·짝 없는 대리 쌍·U+FFFE/FFFF. 하나라도 들어가면 한글이 파일을 열지 못한다
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
TEXT_MAX = 50_000                       # 한 번에 넣는 글의 상한(절 본문 여러 쪽 분량) — 넘는 글은 자른다


def clean_text(value, limit: int | None = TEXT_MAX) -> str:
    """문서에 넣을 글 — XML 이 받지 않는 글자를 빼고 줄바꿈을 \\n 하나로, 너무 길면 자른다. 이모지·특수문자는 그대로(이스케이프는 넣는 쪽이)."""
    s = "" if value is None else str(value)
    s = _BAD_XML.sub("", s.replace("\r\n", "\n").replace("\r", "\n"))
    return s[:limit] if limit else s


def clean_args(v, limit: int | None = TEXT_MAX):
    """도구 인자(사전·목록·글) 안의 글을 모두 clean_text 로 — 숫자·참거짓·None 은 그대로."""
    if isinstance(v, str):
        return clean_text(v, limit)
    if isinstance(v, dict):
        return {(clean_text(k, 200) if isinstance(k, str) else k): clean_args(x, limit) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return type(v)(clean_args(x, limit) for x in v)
    return v


def strip_lineseg_in(xml: str, pred) -> str:
    """pred(문단 xml) 가 참인 가장 안쪽 문단에서만 줄 배치 캐시(linesegarray)를 지운다.
    문서 전체에서 지우면 기안기가 상자·표·그림 둘레를 다시 배치하면서 글이 상자 밖으로 넘치고 그림 표가 늘어난다(10-22 실물 확인).
    글을 바꾸지 않은 문단은 원본 배치를 그대로 둔다."""
    out, pos = [], 0
    for m in _LEAF_P.finditer(xml):
        p = m.group(0)
        if "<hp:linesegarray>" in p and pred(p):
            out.append(xml[pos:m.start()])
            out.append(_LINESEG.sub("", p))
            pos = m.end()
    out.append(xml[pos:])
    return "".join(out)


def _text(frag: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", frag)).strip()


_PLACEHOLDER = re.compile(r"[ㅇ○◯0OoＯ\-/·.,:()\s원년월일시분명건개%~]*")


def _blankish(t: str) -> bool:
    """비었거나 예시 글자(ㅇㅇ·○○·000-00·0000. 00. 00.)만 있는 칸 — 쓸 자리로 본다."""
    return not t or bool(_PLACEHOLDER.fullmatch(t)) and bool(re.search(r"[ㅇ○◯0]", t))


def _wrap_placeholder(p: str, fxml_begin: str, fxml_end: str) -> str | None:
    """예시 글자가 한 run 의 한 <hp:t> 에만 있을 때 그 글자를 필드로 감싼다(필드에 쓰면 예시 글자가 바뀐다)."""
    ts = re.findall(r"<hp:t>[^<]*</hp:t>", p)
    if len(ts) != 1:
        return None
    return p.replace(ts[0], f"{fxml_begin}{ts[0]}{fxml_end}", 1)


_GUIDE = re.compile(r"【\s*(작성\s*방법|작성\s*요령|작성\s*가이드|증빙\s*자료|유의\s*사항|작성\s*예시)|작성\s*요령|작성\s*예시|※\s*작성|\(예시\)|<예시>")
_TOC = re.compile(r"(\s\d{1,3}$|·{3,}|…{2,}|\.{4,}|-{4,})")


def _is_heading(t: str) -> bool:
    num, title = sc.split_number(t)
    return (bool(num) and 2 <= len(title) <= 40 and not re.search(r"\d{4}\.\s*\d", t)
            and not _TOC.search(t) and not re.match(r"^\s*예\s*\)", t) and not _NOTE.search(title))


# 작성 안내 문장 — 「~기재」「~가능」「~작성」「~(반올림)」 으로 끝나는 번호 줄은 제목이 아니라 서식의 안내다
_NOTE = re.compile(r"(기재|가능|작성|기입|삭제|추가|반올림|생략|제출|첨부|유의|참고)\s*(할\s*것|하시오|함|바람|합니다|\))?\s*[.)]?\s*$")


_INSTRUCT = re.compile(r"작성|기재|기입|제출|유의|참고|하여야|해야|바랍니다|할\s*것|하시오|가능하|불가|해당\s*시|경우에는|으로\s*한다")


def _real_label(t: str) -> bool:
    """칸 라벨다운 글 — 한글 낱말이 있고, 숫자·퍼센트·예시 기호(ㅇㅇ·OO·○○·000)만이 아닌 것."""
    if not t or len(t) > 15 or not re.search(r"[가-힣]{2,}", t):
        return False
    core = re.sub(r"[\s\d.,%()\-·:~/]", "", t)
    return (not re.fullmatch(r"[ㅇOo○◯ㅁ0명원개년월일]*", core) and not re.search(r"ㅇㅇ|OO|○○|ㅁㅁ", t)
            and not re.search(r"목차|차례", t))


def _guide_spans(xml: str) -> list[tuple[int, int]]:
    """작성방법·작성요령·예시 상자(표) 범위 — 그 안의 번호 줄·칸은 쓸 자리가 아니다."""
    out = []
    for m in re.finditer(r"<hp:tbl\b(?:(?!<hp:tbl\b).)*?</hp:tbl>", xml, re.S):
        if _GUIDE.search(_text(m.group(0))):
            out.append((m.start(), m.end()))
    return out


def _inside(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


class _Ids:
    def __init__(self, xmls: list[str]):
        used = set(re.findall(r'\bid="(\d+)"', "".join(xmls)))
        self.used = used

    def new(self) -> str:
        while True:
            v = str(random.randint(1_000_000_000, 2_147_000_000))
            if v not in self.used:
                self.used.add(v)
                return v


def field_parts(name: str, hint: str, ids: _Ids) -> tuple[str, str]:
    """필드 여는 조각과 닫는 조각 — 사이에 글(<hp:t>)을 둔다."""
    whole = field_xml(name, hint, ids)
    a, b = whole.split("<hp:t/>", 1)
    return a, b


def field_xml(name: str, hint: str, ids: _Ids) -> str:
    bid, fid = ids.new(), ids.new()
    hint = hint[:60]
    cmd = f"Clickhere:set:{len(hint) + 42}:Direction:wstring:{len(hint)}:{hint} HelpState:wstring:0:  "
    return (f'<hp:ctrl><hp:fieldBegin id="{bid}" type="CLICK_HERE" name="{escape(name, {chr(34): "&quot;"})}" editable="1" dirty="0" '
            f'zorder="-1" fieldid="{fid}"><hp:parameters cnt="3" name=""><hp:integerParam name="Prop">9</hp:integerParam>'
            f'<hp:stringParam name="Command" xml:space="preserve">{escape(cmd)}</hp:stringParam>'
            f'<hp:stringParam name="Direction">{escape(hint)}</hp:stringParam></hp:parameters></hp:fieldBegin></hp:ctrl>'
            f'<hp:t/><hp:ctrl><hp:fieldEnd beginIDRef="{bid}" fieldid="{fid}"/></hp:ctrl>')


def _inject_into_para(p: str, fxml: str) -> str:
    """빈 문단의 첫 run 안에 필드를 넣는다(run 이 없거나 닫힌 꼴이면 run 을 만든다)."""
    m = re.search(r"<hp:run\b([^>]*?)/>", p)
    if m and p.find("<hp:run") == m.start():
        return p[:m.start()] + f"<hp:run{m.group(1)}>{fxml}</hp:run>" + p[m.end():]
    m = re.search(r"<hp:run\b[^>]*>", p)
    if m:
        return p[:m.end()] + fxml + p[m.end():]
    m = re.search(r"<hp:p\b[^>]*>", p)
    return p[:m.end()] + f'<hp:run charPrIDRef="0">{fxml}</hp:run>' + p[m.end():]


def _empty_para_after(p: str, fxml: str, ids: _Ids, body_cp: str = "0") -> str:
    """제목 문단과 같은 문단 모양의 새 빈 문단 + 필드. 글자 모양은 본문(charPr 0, 바탕글) — 제목의 굵고 큰 글자를 물려받으면
    쓴 본문이 제목처럼 보인다."""
    head = re.match(r"<hp:p\b[^>]*>", p).group(0)
    head = re.sub(r'\bid="\d+"', f'id="{ids.new()}"', head, count=1)
    head = re.sub(r'\bpageBreak="1"', 'pageBreak="0"', head)
    return f'{head}<hp:run charPrIDRef="{body_cp}">{fxml}</hp:run></hp:p>'


class _Names:
    def __init__(self):
        self.seen: dict[str, int] = {}

    def __call__(self, base: str) -> str:
        base = re.sub(r"\s+", " ", base).strip()[:40] or "칸"
        n = self.seen.get(base, 0) + 1
        self.seen[base] = n
        return base if n == 1 else f"{base}#{n}"


def fieldize_xml(xml: str, ids: _Ids, names: _Names) -> tuple[str, list[dict]]:
    out: list[dict] = []
    # 1) 표 — 라벨 칸 오른쪽 빈 칸, 목록표(머리행 + 빈 행) 첫 빈 행
    def do_table_rows(tbl_xml: str, tno: int) -> str:
        rows = _TR.findall(tbl_xml)
        texts = [[_text(c) for c in _TC.findall(r)] for r in rows]
        # 목록표 — 머리행(짧은 이름 칸)과 그 아래 빈 행 또는 예시 행(ㅇㅇ·○○·000 이 든 행)
        def example_row(r):
            return all(_blankish(c) for c in r) or any(re.search(r"ㅇㅇ|○○|◯◯|000", c) for c in r)
        listlike = (len(rows) >= 2 and len(texts[0]) >= 2 and all(texts[0]) and all(len(t) <= 12 for t in texts[0])
                    and not any(_blankish(t) and t for t in texts[0]) and all(example_row(r) for r in texts[1:]))
        new_rows = []
        for ri, r in enumerate(rows):
            cells = _TC.findall(r)
            ctexts = texts[ri]
            r2 = r
            for ci, c in enumerate(cells):
                if "<hp:tbl" in c or "fieldBegin" in c:
                    continue
                w = re.search(r'<hp:cellSz width="(\d+)"', c)
                if w and int(w.group(1)) < MIN_SLOT_W:            # 그림처럼 짠 표의 좁은 여백 칸 — 필드를 박으면 표가 늘어난다
                    continue
                target = None
                if listlike and ri == 1:
                    target = (names(f"표{tno}:{texts[0][ci] if ci < len(texts[0]) else ci + 1}"), texts[0][ci] if ci < len(texts[0]) else "")
                elif not ctexts[ci] and ci > 0 and _real_label(ctexts[ci - 1]) and not listlike:
                    target = (names(f"칸:{ctexts[ci - 1]}"), ctexts[ci - 1])
                if target:
                    pm = _LEAF_P.search(c)
                    if pm:
                        para = pm.group(0)
                        if ctexts[ci]:                       # 예시 글자(ㅇㅇㅇ) 칸 — 그 글자를 필드로 감싼다
                            a, b = field_parts(target[0], f"{target[1]} 입력", ids)
                            np_ = _wrap_placeholder(para, a, b)
                            if np_ is None:
                                continue
                        else:
                            np_ = _inject_into_para(para, field_xml(target[0], f"{target[1]} 입력", ids))
                        c2 = c[:pm.start()] + np_ + c[pm.end():]
                        r2 = r2.replace(c, c2, 1)
                        out.append({"name": target[0], "kind": "칸" if target[0].startswith("칸:") else "표"})
            new_rows.append((r, r2))
        for a, b in new_rows:
            if a != b:
                tbl_xml = tbl_xml.replace(a, b, 1)
        return tbl_xml

    # 가장 안쪽 표부터(표 안의 표) — 중첩 없는 표 조각만 골라 바꾼다
    tno = 0
    pos = 0
    pieces = []
    for m in re.finditer(r"<hp:tbl\b(?:(?!<hp:tbl\b).)*?</hp:tbl>", xml, re.S):
        tno += 1
        if _GUIDE.search(_text(m.group(0))):            # 작성방법·예시 상자 — 칸을 박지 않는다
            continue
        pieces.append((m.start(), m.end(), do_table_rows(m.group(0), tno)))
    if pieces:
        buf = []
        for a, b, rep in pieces:
            buf.append(xml[pos:a])
            buf.append(rep)
            pos = b
        buf.append(xml[pos:])
        xml = "".join(buf)
    # 2) 절 제목 — 번호 붙은 제목 문단(어디에 있든) 다음에 빈 문단 + 필드. 본문 글자 모양은 제목이 아닌 문단에서 가장 흔한 것
    from collections import Counter as _C
    cps = _C(re.search(r'charPrIDRef="(\d+)"', p).group(1) for p in _LEAF_P.findall(xml)
             if 'charPrIDRef="' in p and _text(p) and not _is_heading(_text(p)))
    body_cp = cps.most_common(1)[0][0] if cps else "0"

    guides = _guide_spans(xml)
    leaves = [(m.start(), m.end(), "" if "fieldBegin" in m.group(0) else _text(m.group(0)), "<hp:tbl" in m.group(0))
              for m in _LEAF_P.finditer(xml)]             # 이미 필드를 박은 칸은 빈 자리로 본다

    def slot_follows(i: int) -> bool:
        """제목 뒤가 쓸 자리인가 — 빈 문단·작성방법 상자·빈 표·다음 제목이 먼저 오면 쓸 자리, 긴 설명 글이 먼저 오면 안내문."""
        for a, b, t, _ in leaves[i + 1:i + 6]:
            if _inside(a, guides) or not t:
                return True
            if _is_heading(t):
                return True
            if len(t) >= 20 and _INSTRUCT.search(t):            # 작성·제출 지시 글이 먼저 오면 서식 안내의 번호 줄이다
                return False
        return True

    buf, pos = [], 0
    for i, (a, b, t, has_tbl) in enumerate(leaves):
        p = xml[a:b]
        if "fieldBegin" in p or has_tbl or not _is_heading(t) or _inside(a, guides) or not slot_follows(i):
            continue
        name = names(f"절:{t}")
        out.append({"name": name, "kind": "절"})
        at = b
        # 제목 바로 뒤 작성방법 상자가 있으면 쓸 자리는 그 상자 아래 — 읽는 차례가 「제목 → 안내 → 본문」
        while True:
            nxt = re.match(r"\s*<hp:p\b[^>]*>\s*<hp:run\b[^>]*>\s*<hp:tbl\b", xml[at:])
            g = next(((ga, gb) for ga, gb in guides if nxt and ga == at + nxt.end() - len("<hp:tbl")), None)
            if not g:
                break
            close = xml.find("</hp:p>", g[1])
            if close < 0:
                break
            at = close + len("</hp:p>")
        buf.append(xml[pos:at])
        buf.append(_empty_para_after(p, field_xml(name, "본문 입력", ids), ids, body_cp))
        pos = at
    buf.append(xml[pos:])
    return "".join(buf), out


MAX_UNZIPPED = 300 * 1024 * 1024        # 풀었을 때 전체 크기 한도 — 압축 폭탄 막기
MAX_RATIO = 200                          # 항목 하나의 압축률 한도(정상 hwpx 본문 XML 은 수십 배 안쪽)


def check_zip(zin: zipfile.ZipFile) -> None:
    """풀기 전에 크기·압축률을 잰다 — 작은 파일이 풀면서 메모리를 다 쓰지 않게."""
    total = 0
    for info in zin.infolist():
        total += info.file_size
        if info.compress_size and info.file_size > 1024 * 1024 and info.file_size / info.compress_size > MAX_RATIO:
            raise ValueError("한글 파일 구조가 비정상입니다(압축률이 지나치게 큼)")
    if total > MAX_UNZIPPED:
        raise ValueError("한글 파일이 너무 큽니다(풀었을 때 300MB 초과)")


def fieldize(data: bytes) -> tuple[bytes, list[dict]]:
    """hwpx 바이트 → 필드를 박은 hwpx 바이트와 필드 목록. 이미 필드가 있으면 손대지 않는다(그 목록만)."""
    try:
        zin = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("hwpx 파일이 아닙니다")
    check_zip(zin)
    names_ = zin.namelist()
    secs = [n for n in names_ if _SEC.search(n)]
    xmls = {n: zin.read(n).decode("utf-8") for n in secs}
    existing = re.findall(r'<hp:fieldBegin\b[^>]*type="CLICK_HERE"[^>]*name="([^"]*)"', "".join(xmls.values()))
    if existing:
        return data, [{"name": n, "kind": "기존"} for n in existing]
    ids, names = _Ids(list(xmls.values())), _Names()
    fields: list[dict] = []
    new = {}
    for n in sorted(secs, key=lambda s: int(re.search(r"(\d+)", s.rsplit("/", 1)[-1]).group(1))):
        x, f = fieldize_xml(xmls[n], ids, names)
        new[n] = strip_lineseg_in(x, lambda p: "fieldBegin" in p)    # 줄 배치 정보는 필드를 박은 문단만 지운다
        fields += f
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            body = new[info.filename].encode("utf-8") if info.filename in new else zin.read(info.filename)
            ct = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
            zout.writestr(info, body, compress_type=ct)
    return buf.getvalue(), fields
