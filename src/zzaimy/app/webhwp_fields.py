"""한글 양식에 누름틀 필드 박기 — 기안기에서 사람과 에이전트가 「어디에 쓸지」를 같은 이름으로 짚게 한다.

왜(10-22). 학교 양식은 서식 전체가 큰 테두리 표 안에 들어 있거나(절 제목이 표 칸 안), 번호 절 없이 라벨 칸만 있는 신청서·서명부가 많다.
문단 위치로 절을 찾는 채우기(hwpx_fill)는 이런 양식을 놓친다. 쓸 자리에 이름 붙은 누름틀을 미리 박아 두면
기안기 API(MoveToField·PutFieldText·Insert)로 표 안 제목 아래든 칸이든 같은 방법으로 쓴다.

필드 이름(사람이 읽는 꼴, 같은 이름이 겹치면 뒤에 「#2」):
- 「절:<제목>」 — 번호 붙은 절 제목 문단(표 칸 안 포함) 바로 다음에 새 빈 문단을 넣고 그 안에 필드. 본문·표·그림은 여기에 끼워 넣는다.
  코드로 짠 양식은 만든 쪽이 잎 절 목록(slots)을 넘겨 짐작 없이 잎 절에만 박는다(10-25).
- 「칸:<라벨>」 — 같은 행에서 라벨 칸 바로 오른쪽의 빈 칸 첫 문단.
- 「표<n>:<머리 칸>」 — 머리행 + 빈 행으로 된 목록표의 첫 빈 행 칸(행 늘리기는 기안기 표 동작으로).
- 「입력:<라벨>」 — 코드로 짠 양식이 사람 칸으로 정한 칸(날짜·서명·결재·달성률). 에이전트는 담당자에게 물어 받은 값만 넣는다.
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
TEXT_MAX = 1_000_000                    # 한 칸·한 번에 넣는 글의 상한(절 하나 약 600쪽 분량) — 50,000자일 때 30쪽 넘는 절의 끝이 잘렸다(ADR-0070)
DOC_TEXT_MAX = 8_000_000                # 한 문서 칸 글 합계 상한(공동 편집 표) — 넘는 고침은 받지 않고 알린다


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


class Slots:
    """만든 쪽(webhwp_templates.build)이 아는 제목 차례 — [(제목 글, 잎 절인가)…]. 문서 차례대로 제목 문단을 하나씩 짚고
    잎 절에만 절 필드를 박는다. 글 꼴로 짐작하지 않으므로 번호 없는 절(「사업 요약」)·로마 숫자 잎 장·붙임·첨부에도 박히고,
    아래 절이 있는 위 제목에는 박히지 않는다. 섹션이 여럿이어도 차례를 잇는다."""

    def __init__(self, heads):
        self.heads = [(re.sub(r"\s+", " ", str(t)).strip(), bool(leaf)) for t, leaf in heads]
        self.at = 0

    def take(self, text: str) -> bool | None:
        """이 문단 글이 다음 제목이면 잎 절인지(참·거짓), 아니면 None."""
        if self.at < len(self.heads) and text == self.heads[self.at][0]:
            self.at += 1
            return self.heads[self.at - 1][1]
        return None


def fieldize_xml(xml: str, ids: _Ids, names: _Names, slots: Slots | None = None,
                 people: dict | None = None, heads: dict | None = None) -> tuple[str, list[dict]]:
    out: list[dict] = []
    # 1) 표 — 라벨 칸 오른쪽 빈 칸, 목록표(머리행 + 빈 행) 첫 빈 행
    def do_table_rows(tbl_xml: str, tno: int) -> str:
        placed = len(out)
        tbl_xml = _table_rows(tbl_xml, tno)
        if len(out) == placed and heads and tno in heads:
            tbl_xml = _spec_list_row(tbl_xml, tno, *heads[tno])
        return tbl_xml

    def _spec_list_row(tbl_xml: str, tno: int, head_rows: int, cols: list[str]) -> str:
        """만든 쪽이 머리줄 수·열 이름을 아는 목록표 — 짐작으로 필드가 하나도 안 박힌 표(2단 머리줄, 12자 넘는 머리)의
        첫 본문 행 빈 칸에 「표n:<열 이름>」. 행 이름(라벨)이 든 칸·좁은 칸은 건너뛴다."""
        rows = _TR.findall(tbl_xml)
        if head_rows >= len(rows):
            return tbl_xml
        r = rows[head_rows]
        r2 = r
        for c in _TC.findall(r):
            col = re.search(r'colAddr="(\d+)"', c)
            w = re.search(r'<hp:cellSz width="(\d+)"', c)
            if not col or _text(c) or "fieldBegin" in c or (w and int(w.group(1)) < MIN_SLOT_W):
                continue
            ci = int(col.group(1))
            head = cols[ci] if ci < len(cols) and cols[ci] else str(ci + 1)
            person = (people or {}).get(tno, False)
            name = names(f"입력:{head}" if person is None or person and head in person else f"표{tno}:{head}")
            pm = _LEAF_P.search(c)
            if not pm:
                continue
            c2 = c[:pm.start()] + _inject_into_para(pm.group(0), field_xml(name, f"{head} 입력", ids)) + c[pm.end():]
            r2 = r2.replace(c, c2, 1)
            out.append({"name": name, "kind": "입력" if name.startswith("입력:") else "표"})
        return tbl_xml.replace(r, r2, 1)

    def _table_rows(tbl_xml: str, tno: int) -> str:
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
                person = (people or {}).get(tno, False)      # 사람 칸 — None 이면 표 전체, 글 묶음이면 그 라벨·머리만
                if listlike and ri == 1:
                    head = texts[0][ci] if ci < len(texts[0]) else ""
                    mine = person is None or person and head in person
                    target = (names(f"입력:{head}" if mine else f"표{tno}:{head or ci + 1}"), head)
                elif not ctexts[ci] and ci > 0 and _real_label(ctexts[ci - 1]) and not listlike:
                    lab = ctexts[ci - 1]
                    mine = person is None or person and lab in person
                    target = (names(f"입력:{lab}" if mine else f"칸:{lab}"), lab)
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
                        out.append({"name": target[0], "kind": target[0].split(":", 1)[0] if target[0].startswith(("칸:", "입력:")) else "표"})
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
    tables = [(m.start(), m.end()) for m in re.finditer(r"<hp:tbl\b.*?</hp:tbl>", xml, re.S)]
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
        if slots is not None:                           # 만든 쪽이 아는 제목 차례 — 짐작하지 않는다(표 칸 안 글은 제목이 아니다)
            if "fieldBegin" in p or has_tbl or not t or _inside(a, tables) or not slots.take(t):
                continue
        elif "fieldBegin" in p or has_tbl or not _is_heading(t) or _inside(a, guides) or not slot_follows(i):
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


# ── 양식 틀 표시 ── 코드로 짠 양식의 표에서 양식이 미리 적은 글(머리줄·라벨·행 이름)이 든 문단에 붙이는 문단 스타일.
# 본문에 표를 쓰면 같은 절의 빈 양식 표를 지우는데(webhwp_write), 「빈 틀」은 사용자 글이 있는가로 가른다 — 이 스타일이 아닌 문단의 글과
# 누름틀에 쓴 글이 사용자 글이다. 스타일은 한글이 저장해도 남고, 빈 칸에 친 글은 그 칸 문단(바탕글)을 따르므로 사용자 글로 읽힌다.
FORM_STYLE = "양식 틀"
FORM_KEEP_STYLE = "양식 틀 고정"       # 결재란·서명란 — 비어 있어도 본문 표로 바뀌는 자리가 아니다(지우지 않는다)


def form_style_id(header: str, name: str = FORM_STYLE) -> str | None:
    m = re.search(r'<hh:style\b[^>]*\bid="(\d+)"[^>]*\bname="%s"' % re.escape(name), header)
    return m.group(1) if m else None


def _ensure_form_style(header: str, name: str = FORM_STYLE) -> tuple[str, str]:
    sid = form_style_id(header, name)
    if sid is not None:
        return header, sid
    ids_ = [int(x) for x in re.findall(r'<hh:style\b[^>]*\bid="(\d+)"', header)]
    sid = str(max(ids_) + 1 if ids_ else 0)
    tag = (f'<hh:style id="{sid}" type="PARA" name="{name}" engName="Form Frame" paraPrIDRef="0" charPrIDRef="0" '
           f'nextStyleIDRef="0" langID="1042" lockForm="0"/>')
    header = header.replace("</hh:styles>", tag + "</hh:styles>", 1)
    header = re.sub(r'(<hh:styles\b[^>]*\bitemCnt=")(\d+)(")', lambda m: f"{m.group(1)}{int(m.group(2)) + 1}{m.group(3)}", header, count=1)
    return header, sid


def mark_form_text(data: bytes, keep: set[int] | None = None) -> bytes:
    """표 칸 안의 글 있는 문단(누름틀이 없는 것)에 「양식 틀」 스타일을 붙인다 — 양식을 만든 직후에만 부른다.
    keep — 문서 차례 표 번호(1부터, 작성방법 상자 포함) 가운데 결재란·서명란처럼 늘 남길 표. 그 표는 「양식 틀 고정」.
    작성방법 상자는 건너뛴다(지우는 대상이 아니다)."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    header, sid = _ensure_form_style(zin.read("Contents/header.xml").decode("utf-8"))
    if keep:
        header, kid = _ensure_form_style(header, FORM_KEEP_STYLE)
    count = [0]

    def mark_para(p: str, style: str) -> str:
        if "fieldBegin" in p or not _text(re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", p, flags=re.S)):
            return p
        return re.sub(r'(<hp:p\b[^>]*\bstyleIDRef=")\d+', rf"\g<1>{style}", p, count=1)

    def mark_tbl(m: re.Match) -> str:
        t = m.group(0)
        count[0] += 1
        if _GUIDE.search(_text(t)):
            return t
        style = kid if keep and count[0] in keep else sid
        return _LEAF_P.sub(lambda pm: mark_para(pm.group(0), style), t)
    new = {"Contents/header.xml": header}
    for n in zin.namelist():
        if _SEC.search(n):
            new[n] = re.sub(r"<hp:tbl\b(?:(?!<hp:tbl\b).)*?</hp:tbl>", mark_tbl, zin.read(n).decode("utf-8"), flags=re.S)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            body = new[info.filename].encode("utf-8") if info.filename in new else zin.read(info.filename)
            zout.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def user_text_in_table(tbl: str, form_sid: str | None) -> bool | None:
    """양식 표에 사용자 글이 있는가 — 「양식 틀」 표시가 없는 표면 None(판정 못 함). 표시가 있으면 그 스타일이 아닌 문단에
    예시 글자(○○·0000)가 아닌 글이 하나라도 있으면 참."""
    if form_sid is None or not re.search(rf'<hp:p\b[^>]*\bstyleIDRef="{form_sid}"', tbl):
        return None
    # 이 함수가 받는 form_sid 는 「양식 틀」 하나 — 「양식 틀 고정」 표는 부르는 쪽(webhwp_write)이 먼저 거른다
    for m in _LEAF_P.finditer(tbl):
        p = m.group(0)
        if re.search(rf'^<hp:p\b[^>]*\bstyleIDRef="{form_sid}"', p):
            continue
        t = _text(re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", p, flags=re.S))
        if t and not _blankish(t):
            return True
    return False


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


def fieldize(data: bytes, slots=None, people: dict | None = None, heads: dict | None = None) -> tuple[bytes, list[dict]]:
    """hwpx 바이트 → 필드를 박은 hwpx 바이트와 필드 목록. 이미 필드가 있으면 손대지 않는다(그 목록만).
    slots — 만든 쪽이 아는 제목 차례 [(제목 글, 잎 절인가)…](코드로 짠 공통 양식). 주면 그 잎 절에만 절 필드를 박고,
    없으면(학교 실물 서식) 글 꼴로 제목을 짐작한다.
    people — {표 번호(문서 차례, 1부터): 사람 칸 라벨 묶음 또는 None(표 전체)}. 날짜·서명·결재·달성률처럼 사람이 정하는 칸은
    「입력:<라벨>」 — 에이전트는 담당자에게 물어 받은 값만 넣는다(webhwp_agent.fill).
    heads — {표 번호: (머리줄 수, 열 이름들)}. 짐작으로 필드가 하나도 안 박힌 목록표(2단 머리줄 등)의 첫 본문 행에 「표n:<열>」."""
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
    order = Slots(slots) if slots is not None else None
    fields: list[dict] = []
    new = {}
    for n in sorted(secs, key=lambda s: int(re.search(r"(\d+)", s.rsplit("/", 1)[-1]).group(1))):
        x, f = fieldize_xml(xmls[n], ids, names, order, people, heads)
        new[n] = strip_lineseg_in(x, lambda p: "fieldBegin" in p)    # 줄 배치 정보는 필드를 박은 문단만 지운다
        fields += f
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            body = new[info.filename].encode("utf-8") if info.filename in new else zin.read(info.filename)
            ct = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
            zout.writestr(info, body, compress_type=ct)
    return buf.getvalue(), fields


# ── 문서 전체 공동 편집(10-26) — 칸 밖 문단도 이름 없는 칸(「문단:n」)으로 감싼다 ──
# 칸(누름틀) 안만 여럿이 실시간으로 고칠 수 있어, 제목·소제목·안내 글·표 칸은 「문서 틀 고치기」 로 한 사람씩만 고쳤다.
# 사용자 — 「칸 있는 곳만 수정하는 게 아니잖아. 전체 영역에 대한 수정이 가능해야 하고, 표기도 떠야」.
# 글만 든 문단(표 칸 안 포함)을 안내 글 없는 칸으로 감싸면 칸 공동 편집(임대·바뀐 부분·이름표)이 문서 전체에 그대로 듣는다.
# 그림·표·머리말 같은 개체가 든 문단, 머리말·꼬리말·각주·글상자 안, 이미 칸 안인 문단은 건너뛴다(그런 고침은 여전히 문서 틀).
PARA = "문단:"
_PLAIN_TAGS = {"hp:p", "hp:run", "hp:t", "hp:tab", "hp:lineBreak", "hp:linesegarray", "hp:lineseg", "hp:nbSpace",
               "hp:fwSpace", "hp:hyphen", "hp:markpenBegin", "hp:markpenEnd"}
_TAG = re.compile(r"<(hp:[A-Za-z]+)\b")
_NO_WRAP = re.compile(r"<(hp:header|hp:footer|hp:footNote|hp:endNote|hp:drawText)\b.*?</\1>", re.S)


def is_para(name: str) -> bool:
    return str(name or "").startswith(PARA)


def _plain(p: str) -> bool:
    return all(t in _PLAIN_TAGS for t in _TAG.findall(p))


def _wrap_para(p: str, begin: str, end: str) -> str:
    p = _LINESEG.sub("", p)
    m = re.search(r"<hp:run\b[^>]*?/>", p)
    if m and p.find("<hp:run") == m.start():                    # 닫힌 꼴 run 하나뿐인 빈 문단
        p = p[:m.start()] + re.sub(r"/>$", ">", m.group(0)) + "</hp:run>" + p[m.end():]
    first = re.search(r"<hp:run\b[^>]*>", p)
    if not first:
        head = re.match(r"<hp:p\b[^>]*>", p)
        return p[:head.end()] + f'<hp:run charPrIDRef="0">{begin}<hp:t/>{end}</hp:run>' + p[head.end():]
    p = p[:first.end()] + begin + p[first.end():]
    last = p.rfind("</hp:run>")
    return p[:last] + end + p[last:]


def wrap_rest_xml(xml: str, ids: _Ids, start: int) -> tuple[str, int]:
    """한 섹션 — 감싼 문단 수와 함께. start 는 이어 붙일 「문단:n」 번호."""
    skip = [(m.start(), m.end()) for m in _NO_WRAP.finditer(xml)]
    out, pos, open_, n = [], 0, 0, start
    for m in _LEAF_P.finditer(xml):
        p = m.group(0)
        b, e = p.count("<hp:fieldBegin"), p.count("<hp:fieldEnd")
        inside = open_ > 0
        open_ = max(0, open_ + b - e)
        if inside or b or e or _inside(m.start(), skip) or not _plain(p):
            continue
        n += 1
        begin, end = field_parts(f"{PARA}{n}", "", ids)
        out.append(xml[pos:m.start()])
        out.append(_wrap_para(p, begin, end))
        pos = m.end()
    out.append(xml[pos:])
    return "".join(out), n - start


def count_rest(data: bytes) -> int:
    """감쌀 문단 수(바꾸지 않고 세기만)."""
    _, k = wrap_rest(data, dry=True)
    return k


def wrap_rest(data: bytes, limit: int | None = None, dry: bool = False) -> tuple[bytes, int]:
    """hwpx → 칸 밖 글 문단을 모두 「문단:n」 칸으로 감싼 hwpx 와 감싼 수. 감쌀 것이 없거나 limit 을 넘으면 그대로(수는 셈)."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    check_zip(zin)
    secs = sorted([n for n in zin.namelist() if _SEC.search(n)], key=lambda s: int(re.search(r"(\d+)", s.rsplit("/", 1)[-1]).group(1)))
    xmls = {n: zin.read(n).decode("utf-8") for n in secs}
    used = [int(x) for x in re.findall(r'name="' + PARA + r'(\d+)"', "".join(xmls.values()))]
    ids, k0 = _Ids(list(xmls.values())), max(used, default=0)
    new, k = {}, k0
    # 같은 이름 칸이 둘이면(칸째 복사해 붙여 넣음) 하나만 이름을 지키고 나머지는 새 「문단:n」 으로 — 같은 이름이면 칸 글이 엉뚱한 칸에 들어간다.
    # 지키는 쪽은 다른 칸 안에 들어가지 않은 것(원래 자리), 없으면 앞의 것. 붙여 넣은 복사본은 붙인 칸 안에 들어가 있다
    tok = re.compile(r'(<hp:fieldBegin\b[^>]*?\s)name="([^"]*)"|<hp:fieldEnd\b')
    occ: dict[str, list[tuple[str, int, bool]]] = {}
    for n in secs:
        depth = 0
        for m in tok.finditer(xmls[n]):
            if m.group(0).startswith("<hp:fieldEnd"):
                depth = max(0, depth - 1)
                continue
            if "CLICK_HERE" in m.group(1):
                occ.setdefault(m.group(2), []).append((n, m.start(), depth > 0))
            depth += 1
    rename_at: set[tuple[str, int]] = set()
    for name, lst in occ.items():
        if len(lst) < 2:
            continue
        keep = next((o for o in lst if not o[2]), lst[0])
        rename_at |= {(o[0], o[1]) for o in lst if o is not keep}
    for n in secs:
        def rename(m, n=n):
            nonlocal k
            if (n, m.start()) not in rename_at:
                return m.group(0)
            k += 1
            return m.group(1) + f'name="{PARA}{k}"'
        x = re.sub(r'(<hp:fieldBegin\b[^>]*?\s)name="([^"]*)"', rename, xmls[n])
        new[n], c = wrap_rest_xml(x, ids, k)
        k += c
    total = k - k0
    if dry or not total or (limit is not None and total > limit):
        return data, total
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            body = new[info.filename].encode("utf-8") if info.filename in new else zin.read(info.filename)
            ct = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
            zout.writestr(info, body, compress_type=ct)
    return buf.getvalue(), total
