"""기안기 작업본에 쓰기(서버 쪽) — 에이전트 도구가 브라우저 없이 한글 파일(hwpx)의 누름틀 필드에 값을 넣고 절 본문(문단·표)을 쓴다.

- fields(data): 필드 목록과 지금 값 — [{"name", "kind"(절·칸·입력·표·기타), "text"}]
- set_fields(data, {이름: 값}): 칸·표 필드 값 바꾸기(줄바꿈은 진짜 문단 나눔 — 필드가 여러 문단에 걸친다)
- split_tables(data): 글을 써 넣는 표의 쪽 나눔을 「나눔」 으로 — 칸이 쪽보다 길어지면 넘친 글이 잘려 안 보였다(11-11)
- normalize_breaks(data): 필드 안 줄 나눔(lineBreak)을 문단 나눔으로 — 기안기 GetFieldText 가 줄 나눔을 돌려주지 않아
  공동 편집 한 번에 줄이 모두 붙고, 한 문단이 된 긴 절은 양식 모드에서 한 글자마다 문단 길이만큼 다시 배치된다(ADR-0070)
- write_section(data, 이름, items, written): 절 본문을 쓴다. items = [("text", 글) | ("table", 행렬) | ("diagram", 글)].
  글 줄은 모두 절 필드 안에 문단으로 넣는다(양식 모드에서 사람이 고치고 공동 편집하는 자리). 표·도식은 필드 밖 문단으로 두고,
  표 뒤에 오는 글은 표 바로 뒤의 이어 쓰기 필드(「이어:절이름 (2)」, cont_name)에 넣는다 — 칸 글 동기화가 필드 글을 통째로
  읽고 쓰므로 표를 필드 안에 두면 지워진다(ADR-0070 덧붙임 10-26).
  앞서 같은 절에 쓴 것(필드 안 문단, 이어 쓰기 필드, written[이름] 의 표 문단)은 지우고 새로 넣는다 — 「다시 써 줘」 가 덧붙이지 않게.
  문단·표 모양은 서식 보존 채우기(hwpx_fill)와 같은 함수로 만든다(서식의 기본 문단·글자 모양, 실선 테두리).
돌려주는 것은 새 hwpx 바이트(와 절 쓰기는 이번에 넣은 문단 id 목록). 파일 저장·판 남기기는 webhwp.save 가 한다.
"""
from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape

from zzaimy.app import webhwp_fields as wf
from zzaimy.ingest import hwpx_fill as hf

_SEC = re.compile(r"Contents/section\d+\.xml$")
_BEGIN = re.compile(r'<hp:ctrl><hp:fieldBegin\b[^>]*\bid="(\d+)"[^>]*type="CLICK_HERE"[^>]*\bname="([^"]*)"[^>]*>.*?</hp:fieldBegin></hp:ctrl>', re.S)


def _open(data: bytes) -> tuple[zipfile.ZipFile, dict[str, str]]:
    zin = zipfile.ZipFile(io.BytesIO(data))
    wf.check_zip(zin)
    return zin, {n: zin.read(n).decode("utf-8") for n in zin.namelist() if _SEC.search(n)}


def _pack(zin: zipfile.ZipFile, new: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            body = new[info.filename].encode("utf-8") if info.filename in new else zin.read(info.filename)
            zout.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def _unq(name: str) -> str:
    return name.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


# 이어 쓰기 필드 — 절 본문이 표로 끊긴 뒤의 글을 담는다. 이름은 「이어:」 + 절 필드 이름 + 「 (n)」(n 은 2부터).
# 「절:」 로 시작하지 않으므로 절 목록·빈 절 세기에는 나오지 않고, section_of 로 어느 절의 것인지 안다
CONT = "이어:"
_CONT_RE = re.compile(r"^이어:(.+) \((\d+)\)$", re.S)


def cont_name(section: str, k: int) -> str:
    return f"{CONT}{section} ({k})"


def is_cont(name: str) -> bool:
    return bool(_CONT_RE.match(name or ""))


def section_of(name: str) -> str:
    """필드 이름 → 그 필드가 속한 절 이름(이어 쓰기 필드면 원래 절, 아니면 그대로)."""
    m = _CONT_RE.match(name or "")
    return m.group(1) if m else name


def _kind(name: str) -> str:
    if is_cont(name):
        return "이어"
    if name.startswith("입력:"):                            # 사람 칸(날짜·서명·결재) — 에이전트는 물어 받은 값만
        return "입력"
    return "절" if name.startswith("절:") else "칸" if name.startswith("칸:") else "표" if re.match(r"표\d+:", name) else "기타"


_END = re.compile(r'<hp:ctrl><hp:fieldEnd\b[^>]*beginIDRef="(\d+)"[^>]*/></hp:ctrl>')
_ENDS: tuple = (None, {})


def _span(xml: str, bid: str, start: int) -> tuple[int, int] | None:
    """필드 여는 ctrl 끝 ~ 닫는 ctrl 시작(그 사이가 필드 값). 닫는 ctrl 자리는 xml 하나에 한 번만 훑어 둔다 —
    필드마다 xml[start:] 를 잘라 찾던 것이 942칸·5MB 절에서 써 넣기 한 번에 0.5초였다."""
    global _ENDS
    c = _ENDS
    if c[0] is not xml:
        ends: dict[str, list[int]] = {}
        for m in _END.finditer(xml):
            ends.setdefault(m.group(1), []).append(m.start())
        c = _ENDS = (xml, ends)
    for e in c[1].get(bid, ()):
        if e >= start:
            return (start, e)
    return None


def fields(data: bytes) -> list[dict]:
    """필드 목록. 이어 쓰기 필드는 따로 내지 않고 그 글을 원래 절의 글 뒤에 잇는다(에이전트가 빈 절로 보지 않게)."""
    _, secs = _open(data)
    out: list[dict] = []
    at: dict[str, dict] = {}
    for n in sorted(secs):
        xml = secs[n]
        for m in _BEGIN.finditer(xml):
            sp = _span(xml, m.group(1), m.end())
            text = re.sub(r"<[^>]+>", "", _PARAMS.sub("", xml[sp[0]:sp[1]])).strip() if sp else ""
            if text and _is_own_hint(m.group(0), text):
                text = ""                                  # 칸 안 작성 안내를 감싼 필드 — 안내 글 그대로면 아직 안 쓴 칸이다
            name = _unq(m.group(2))
            if name.startswith("문단:"):                       # 문서 전체 공동 편집용 칸(webhwp_fields.wrap_rest) — 쓸 자리가 아니다
                continue
            own = at.get(section_of(name)) if is_cont(name) else None
            if own is not None:
                own["text"] = "\n".join(x for x in (own["text"], text) if x)
                continue
            x = {"name": name, "kind": _kind(name), "text": text}
            at.setdefault(name, x)
            out.append(x)
    return out


def _is_own_hint(begin: str, text: str) -> bool:
    """필드 글이 그 필드의 안내문(Direction) 그대로인가 — 양식 칸의 「~을 요약 기술」 을 감싼 필드(webhwp_fields._instruction_cell)."""
    d = re.search(r'<hp:stringParam name="Direction">([^<]*)</hp:stringParam>', begin)
    hint = re.sub(r"\s+", " ", _unq(d.group(1))).strip() if d else ""
    t = re.sub(r"\s+", " ", _unq(text)).strip()
    # 짧은 안내(「본문 입력」「합계 입력」)도 글이 안내와 똑같으면 빈 칸이다 — 10자 이상만 보던 때는 기안기가 저장한 빈 절이
    # 「본문 입력」 을 글로 가져 에이전트가 쓴 절로 봤다(11-12 특성화 재현: 빈 절 수가 줄고 다음 절 안내가 틀림)
    return len(hint) >= 2 and (t == hint or (len(hint) >= 60 and t.startswith(hint)))


def field_values(data: bytes) -> dict[str, str]:
    """필드마다 지금 값 — 공동 편집이 칸 글과 견줄 꼴(줄 나눔·문단 나눔은 \n, 글자 참조는 풀고, 앞뒤 공백도 그대로). 같은 이름은 앞의 것."""
    _, secs = _open(data)
    out: dict[str, str] = {}
    for n in sorted(secs):
        xml = secs[n]
        for m in _BEGIN.finditer(xml):
            name = _unq(m.group(2))
            if name in out:
                continue
            sp = _span(xml, m.group(1), m.end())
            frag = xml[sp[0]:sp[1]] if sp else ""
            frag = _PARAMS.sub("", frag)                   # 칸 안에 든 칸(칸째 붙여넣기)의 설정값은 글이 아니다 — 「9Clickhere:set:…」 가 글로 읽혔다(10-26)
            frag = re.sub(r"<hp:lineBreak\s*/>|</hp:p>\s*<hp:p\b[^>]*>", "\n", frag)
            frag = re.sub(r"<hp:tab\b[^>]*/>", "\t", frag)
            out[name] = _unq(re.sub(r"<[^>]+>", "", frag))
    return out


_OBJ = re.compile(r"<hp:(tbl|pic|equation|container|ole|rect|ellipse|line|arc|polygon|curve|connectLine|textart|video)\b")


def rich_fields(data: bytes) -> set[str]:
    """글 말고 개체(표·그림·수식·도형)가 든 필드 이름 — 공동 편집에서 문단 단위로 나눠 고치지 않고 칸째 임대한다(ADR-0070 문단 단위 덧붙임).
    기안기 GetFieldText 는 표·그림을 글로 돌려주지 않아 문단 번호가 화면과 어긋난다."""
    _, secs = _open(data)
    out: set[str] = set()
    for n in sorted(secs):
        xml = secs[n]
        for m in _BEGIN.finditer(xml):
            sp = _span(xml, m.group(1), m.end())
            if sp and _OBJ.search(xml, sp[0], sp[1]):
                out.add(_unq(m.group(2)))
    return out


_TOK = re.compile(r"<hp:p\b[^>]*>|</hp:p>|<hp:run\b[^>]*>|</hp:run>|<hp:t(?=[\s>])[^>]*>")
_PARAMS = re.compile(r"<hp:parameters\b.*?</hp:parameters>", re.S)
_LB = re.compile(r"<hp:lineBreak\s*/>")


def _opens_at(xml: str, positions: list[int]) -> list[tuple[str, str, str]]:
    """자리마다 그 자리를 품은 가장 안쪽 문단·글자 묶음(run)·글(t)의 여는 태그 — 한 번 훑어서(자리는 차례대로)."""
    out, ps, runs, t = [], [], [], ""
    toks = _TOK.finditer(xml)
    nxt = next(toks, None)
    for at in positions:
        while nxt is not None and nxt.start() < at:
            g = nxt.group(0)
            if g.startswith("<hp:p"):
                ps.append(g)
            elif g == "</hp:p>":
                ps and ps.pop()
            elif g.startswith("<hp:run"):
                runs.append(g); t = ""
            elif g == "</hp:run>":
                runs and runs.pop(); t = ""
            else:
                t = g
            nxt = next(toks, None)
        out.append((ps[-1] if ps else "", runs[-1] if runs else "", t or "<hp:t>"))
    return out


def _cont(p_open: str, run_open: str, t_open: str, ids: "wf._Ids", made: set) -> str:
    """문단을 여기서 나눈다 — 닫고, 같은 모양의 새 문단(새 id, 쪽·단 나눔 없음)을 연다."""
    pid = ids.new()
    made.add(pid)
    p2 = re.sub(r'\bid="\d+"', f'id="{pid}"', p_open, count=1)
    p2 = re.sub(r'\b(pageBreak|columnBreak)="1"', r'\1="0"', p2)
    return "</hp:t></hp:run></hp:p>" + p2 + (run_open or '<hp:run charPrIDRef="0">') + t_open


def _value_xml(text: str, split=None) -> str:
    """필드 값 — 여러 줄이면 split(j)(j 번째 줄의 새 문단을 여는 조각)으로 잇는다. split 이 없으면(문단 자리를 모름) 줄 나눔."""
    lines = wf.clean_text(text, None).split("\n")
    out = escape(lines[0])
    for j, x in enumerate(lines[1:], 1):
        out += (split(j) if split else "<hp:lineBreak/>") + escape(x)
    return "<hp:t>" + out + "</hp:t>"


_P_OPEN = re.compile(r"<hp:p\b[^>]*>")


def _line_sources(frag: str, text: str) -> list[int]:
    """새 값의 줄마다 모양을 물려받을 옛 문단 번호(0 = 필드가 든 문단) — 옛 필드 값(frag)의 줄 글과 견줘 같은 줄은 제 모양,
    새로 끼운 줄은 바로 앞 줄의 모양. 사람이 AURA 가 쓴 절에서 한 줄을 고쳐도 다른 줄의 단계 들여쓰기가 지워지지 않게."""
    import difflib
    olds = [_unq(re.sub(r"<[^>]+>", "", x)) for x in _P_OPEN.split(frag)]
    new = wf.clean_text(text, None).split("\n")
    src = [0] * len(new)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, olds, new, autojunk=False).get_opcodes():
        for j in range(j1, j2):
            if tag in ("equal", "replace") and i2 > i1:
                src[j] = min(i1 + (j - j1), i2 - 1)
            else:
                src[j] = src[j - 1] if j else 0
    return src


def _strip_made(xml: str, made: set) -> str:
    """나누거나 새로 만든 문단의 줄 배치 캐시만 지운다(편집기가 그 문단만 다시 배치)."""
    if not made:
        return xml
    return wf.strip_lineseg_in(xml, lambda p: (re.match(r'<hp:p\b[^>]*\bid="(\d+)"', p) or [None, None])[1] in made)


def _normalize_xml(xml: str, ids: "wf._Ids") -> tuple[str, int]:
    """한 절 xml 의 필드 안 줄 나눔을 문단 나눔으로. 표·그림이 든 필드는 그대로 둔다. (새 xml, 바꾼 수)."""
    hits = []
    if "<hp:lineBreak" not in xml:
        return xml, 0
    for m in _BEGIN.finditer(xml):
        sp = _span(xml, m.group(1), m.end())
        if not sp:
            continue
        frag = xml[sp[0]:sp[1]]
        if "<hp:lineBreak" not in frag or "<hp:tbl" in frag or "<hp:pic" in frag:
            continue
        hits += [sp[0] + b.start() for b in _LB.finditer(frag)]
    if not hits:
        return xml, 0
    hits.sort()
    opens = _opens_at(xml, hits)
    made: set = set()
    out, pos = [], 0
    for at, (p, r, t) in zip(hits, opens):
        if not p:
            continue
        made.add((re.match(r'<hp:p\b[^>]*\bid="(\d+)"', p) or [None, None])[1])
        m = _LB.match(xml, at)
        out.append(xml[pos:at]); out.append(_cont(p, r, t, ids, made)); pos = m.end()
    out.append(xml[pos:])
    return _strip_made("".join(out), made), len(hits)


def normalize_breaks(data: bytes) -> tuple[bytes, int]:
    """필드 안 줄 나눔(lineBreak) → 문단 나눔. 바꿀 것이 없으면 같은 바이트. (새 바이트, 바꾼 수)."""
    zin, secs = _open(data)
    ids = wf._Ids(list(secs.values()))
    new, n = {}, 0
    for name, xml in secs.items():
        x2, k = _normalize_xml(xml, ids)
        if k:
            new[name], n = x2, n + k
    return (_pack(zin, new) if new else data), n


_TBL_OPEN = re.compile(r"<hp:tbl\b[^>]*>")
_FIELD_NAME = re.compile(r'<hp:fieldBegin\b[^>]*type="CLICK_HERE"[^>]*\bname="([^"]*)"')
LONG_CELL = 500          # 이만큼 넘는 글이 든 칸이 있는 표도 — 사람이 칸에 길게 쓴 문서


def _long_cell(tbl: str) -> bool:
    for c in re.findall(r"<hp:tc\b.*?</hp:tc>", tbl, re.S):
        if len("".join(re.findall(r"<hp:t\b[^>]*>([^<]*)</hp:t>", c))) > LONG_CELL:
            return True
    return False


def _split_tables_xml(xml: str) -> tuple[str, int]:
    """글을 써 넣는 표(문단 칸 말고 절·칸·입력·표 필드가 든 표, 또는 긴 글이 든 칸이 있는 표)의 쪽 나눔을 「나눔」(TABLE)으로.
    「셀 단위로 나눔」(CELL)·「나누지 않음」(NONE)이면 한 칸이 쪽보다 길어질 때 쪽 아래로 넘친 글이 잘려 보이지 않는다(11-11 사용자 보고)."""
    hits = []
    for a, b in wf._all_tables(xml):
        m = _TBL_OPEN.match(xml, a)
        if not m:
            continue
        tag = m.group(0)
        pb = re.search(r'\bpageBreak="(\w+)"', tag)
        if pb and pb.group(1) == "TABLE":
            continue
        seg = xml[a:b]
        writes = any(not wf.is_para(n) for n in _FIELD_NAME.findall(seg))
        if not (writes or _long_cell(seg)):
            continue
        new = re.sub(r'\bpageBreak="\w+"', 'pageBreak="TABLE"', tag, count=1) if pb else tag[:-1].rstrip("/") + ' pageBreak="TABLE"' + (" />" if tag.endswith("/>") else ">")
        hits.append((a, m.end(), new))
    for a, e, new in sorted(hits, reverse=True):
        xml = xml[:a] + new + xml[e:]
    return xml, len(hits)


def split_tables(data: bytes) -> tuple[bytes, int]:
    """글을 써 넣는 표가 쪽 경계에서 칸 안 글까지 나뉘게 한다. 바꿀 것이 없으면 같은 바이트. (새 바이트, 바꾼 표 수)."""
    zin, secs = _open(data)
    new, n = {}, 0
    for name, xml in secs.items():
        x2, k = _split_tables_xml(xml)
        if k:
            new[name], n = x2, n + k
    return (_pack(zin, new) if new else data), n


_FRAG_TOK = re.compile(r"<[^>]+>|[^<]+")
_PLAIN_FRAG_TAGS = re.compile(r"<(/?)(hp:t|hp:run|hp:p|hp:linesegarray|hp:lineseg)\b[^>]*?(/?)>")
_ENT = re.compile(r"&(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);|.", re.S)


def _frag_text(frag: str) -> str:
    """field_values 와 같은 셈으로 필드 조각의 글."""
    frag = _PARAMS.sub("", frag)
    f = re.sub(r"<hp:lineBreak\s*/>|</hp:p>\s*<hp:p\b[^>]*>", "\n", frag)
    f = re.sub(r"<hp:tab\b[^>]*/>", "\t", f)
    return _unq(re.sub(r"<[^>]+>", "", f))


def _patch_span(frag: str, new: str, p_open: str, r_open: str, ids, made: set) -> str | None:
    """필드 값을 통째로 다시 쓰지 않고 바뀐 글자 범위만 그 자리에서 바꾼다 — 글자 묶음(run)마다 든 서식(색·크기·글꼴·굵게)을 지킨다(10-26).
    통째로 다시 쓰면 줄마다 첫 글자 모양으로 돌아가 사람이 칸 안에서 바꾼 서식이 파일에서 지워졌다.
    글·문단만 든 조각(표·개체·줄 나눔·탭 없음)에서만 한다. 못 하면 None(부르는 쪽이 예전처럼 통째로)."""
    if not frag:
        return None
    for m in re.finditer(r"<[^>]+>", frag):
        if not _PLAIN_FRAG_TAGS.fullmatch(m.group(0)):
            return None
    frag = re.sub(r"<hp:t(\b[^>]*?)/>", r"<hp:t\1></hp:t>", frag)          # 빈 글 태그도 열고 닫는 꼴로 — 자를 자리가 생기게
    old = _frag_text(frag)
    new = wf.clean_text(new, None)
    if old == new:
        return frag
    # 글자마다 (글자, 조각 안 시작 자리, 끝 자리), 문단 나눔은 그 문단 마지막 글 태그 닫는 자리로
    atoms: list[tuple[str, int, int]] = []
    in_t, last_t_end, pending_break = False, None, False
    for m in _FRAG_TOK.finditer(frag):
        tok = m.group(0)
        if tok.startswith("<"):
            if tok.startswith("<hp:t") and not tok.startswith("<hp:tab"):
                in_t = True
            elif tok == "</hp:t>":
                in_t = False
                last_t_end = m.start()
            elif tok.startswith("<hp:p"):
                if pending_break:
                    if last_t_end is None:
                        return None
                    atoms.append(("\n", last_t_end, last_t_end))
                    pending_break = False
            elif tok == "</hp:p>":
                pending_break = True
            continue
        if not in_t:
            if tok.strip():
                return None
            continue
        for e in _ENT.finditer(tok):
            atoms.append((_unq(e.group(0)), m.start() + e.start(), m.start() + e.end()))
    if "".join(a[0] for a in atoms) != old or last_t_end is None:
        return None
    import difflib

    def anchor(i: int) -> int:
        """글을 넣을 자리 — 바로 앞 글자 뒤(편집기처럼 앞 글자의 모양을 잇는다). 앞 글자가 없으면(칸·문단 처음) 그 자리 글자 앞."""
        if i > 0 and atoms[i - 1][0] != "\n":
            return atoms[i - 1][2]
        return atoms[i][1] if i < len(atoms) else last_t_end

    def next_t_open_end(at: int) -> int | None:
        m = re.compile(r"<hp:t(?=[\s>])[^>]*>").search(out, at)
        return m.end() if m else None
    out = frag
    ops = [o for o in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if o[0] != "equal"]
    for _tag, i1, i2, j1, j2 in reversed(ops):          # 뒤에서부터 — 앞 자리 값이 그대로 맞다
        for k in range(i2 - 1, i1 - 1, -1):             # 지우기는 글자 하나씩 — 글자 묶음(run) 경계·서식은 남는다
            ch, st, en = atoms[k]
            if ch == "\n":                              # 문단 나눔 지우기 = 두 문단 잇기(뒤 문단의 첫 글은 앞 문단 끝 묶음으로)
                nx = next_t_open_end(st)
                if nx is None:
                    return None
                out = out[:st] + out[nx:]
            else:
                out = out[:st] + out[en:]
        if j2 > j1:
            at = anchor(i1)
            head = out[:at]
            po = ([x.group(0) for x in _P_OPEN.finditer(head)] or [p_open])[-1]
            ro = ([x.group(0) for x in re.finditer(r"<hp:run\b[^>]*>", head)] or [r_open])[-1]
            lines = new[j1:j2].split("\n")
            ins = escape(lines[0])
            for x in lines[1:]:
                ins += _cont(po, ro, "<hp:t>", ids, made) + escape(x)
            out = head + ins + out[at:]
    out = re.sub(r"<hp:linesegarray>.*?</hp:linesegarray>", "", out, flags=re.S)
    return out if _frag_text(out) == new else None


def set_fields(data: bytes, values: dict[str, str]) -> tuple[bytes, list[str]]:
    """칸·표 필드 값 바꾸기 — 돌려주는 것은 (새 바이트, 넣은 필드 이름). 없는 이름은 건너뛴다."""
    zin, secs = _open(data)
    if not isinstance(values, dict):
        raise ValueError("values 는 {필드 이름: 값} 이다")
    want = {str(k): ("" if v is None else wf.clean_text(v, None)) for k, v in values.items() if k}
    done: list[str] = []
    new = {}
    ids = wf._Ids(list(secs.values()))
    for n, xml in secs.items():
        hits = []
        for m in _BEGIN.finditer(xml):
            name = _unq(m.group(2))
            if name not in want or name in done:
                continue
            sp = _span(xml, m.group(1), m.end())
            if not sp:
                continue
            hits.append((m, sp, name))
            done.append(name)
        if not hits:
            continue
        # 여러 줄 값은 필드가 든 문단과 같은 모양의 문단으로 나눈다(줄 나눔이 아니라) — 기안기 PutFieldText(\r\n)와 같은 꼴
        opens = _opens_at(xml, [m.start() for m, _, _ in hits])
        made: set = set()
        pos, buf = 0, []
        for (m, sp, name), (p, r, t) in zip(hits, opens):
            buf.append(xml[pos:sp[0]])
            pid = (re.match(r'<hp:p\b[^>]*\bid="(\d+)"', p) or [None, None])[1] if p else None
            if pid:
                made.add(pid)
            split = None
            if p:
                frag = xml[sp[0]:sp[1]]
                opens = [(q.group(0), (re.compile(r"<hp:run\b[^>]*>").search(frag, q.end()) or [""])[0])
                         for q in _P_OPEN.finditer(frag)] if "<hp:tbl" not in frag else []
                src = _line_sources(frag, want[name]) if opens else []

                def split(j, p=p, r=r, opens=opens, src=src):
                    i = src[j] if j < len(src) else 0
                    po, ro = opens[i - 1] if 0 < i <= len(opens) else (p, r)
                    return _cont(po, ro or r, "<hp:t>", ids, made)
            patched = _patch_span(xml[sp[0]:sp[1]], want[name], p, r, ids, made) if p else None
            buf.append(patched if patched is not None else _value_xml(want[name], split))
            pos = sp[1]
        buf.append(xml[pos:])
        x2 = "".join(buf)
        # 값이 바뀐 필드의 문단(나눈 문단 포함)만 줄 배치 캐시를 지운다. 닫는 필드가 있던 문단(앞 판에서 여러 문단이던 필드)도
        ends = {m.group(1) for m, _, _ in hits}
        x2 = wf.strip_lineseg_in(x2, lambda q: (re.match(r'<hp:p\b[^>]*\bid="(\d+)"', q) or [None, None])[1] in made
                                 or any(e in q for e in (f'beginIDRef="{b}"' for b in ends)))
        new[n] = _normalize_xml(x2, ids)[0] if "<hp:lineBreak" in x2 else x2     # 같은 절의 다른 필드에 남은 줄 나눔도 함께 문단으로
    inked = {k for k in done if str(want.get(k) or "").strip()}
    if new and inked:
        new = _ink_fields(zin, new, inked)
    return (_pack(zin, new) if new else data), done


def _ink_fields(zin: zipfile.ZipFile, new: dict[str, str], names: set[str]) -> dict[str, str]:
    """값을 넣은 필드 안 글은 검정으로 — 서식 견본 글(파랑·빨강) 모양을 물려받아 완성본 값이 색 글씨로 남던 것(11-12).
    필드 밖 글·서식이 일부러 색을 준 제목은 건드리지 않는다. header 가 바뀌면 함께 담는다."""
    from zzaimy.app import webhwp_style as ws
    try:
        hdr0 = zin.read("Contents/header.xml").decode("utf-8")
    except KeyError:
        return new
    H = ws._Header(hdr0)
    out = dict(new)
    for n, xml in new.items():
        if n == "Contents/header.xml":
            continue
        buf, pos = [], 0
        for m in _BEGIN.finditer(xml):
            if _unq(m.group(2)) not in names or m.start() < pos:
                continue
            sp = _span(xml, m.group(1), m.end())
            if not sp:
                continue
            frag = re.sub(r'(<hp:run\b[^>]*\bcharPrIDRef=")(\d+)', lambda q: q.group(1) + H.char_plain(q.group(2)), xml[sp[0]:sp[1]])
            # 필드를 연 글 조각(fieldBegin 이 든 run)도 — 값 첫 글자가 그 run 에 이어진다
            run_open = xml.rfind("<hp:run", 0, m.start())
            if run_open >= pos:
                head = re.sub(r'(<hp:run\b[^>]*\bcharPrIDRef=")(\d+)', lambda q: q.group(1) + H.char_plain(q.group(2)), xml[run_open:sp[0]], count=1)
                buf.append(xml[pos:run_open] + head)
            else:
                buf.append(xml[pos:sp[0]])
            buf.append(frag)
            pos = sp[1]
        if buf:
            buf.append(xml[pos:])
            out[n] = "".join(buf)
    if H.xml != hdr0:
        out["Contents/header.xml"] = H.xml
    return out


def _para_bounds(xml: str, at: int) -> tuple[int, int] | None:
    """at 을 품은 가장 안쪽 문단 <hp:p …>…</hp:p> 의 범위."""
    for m in wf._LEAF_P.finditer(xml):
        if m.start() <= at < m.end():
            return m.start(), m.end()
    return None


def _cell_width(xml: str, at: int) -> int | None:
    """at 이 표 칸 안이면 그 칸의 폭(HWPUNIT) — 칸 안 절에 넣는 표는 칸 폭에 맞춘다."""
    tc = xml.rfind("<hp:tc", 0, at)
    end = xml.rfind("</hp:tc>", 0, at)
    if tc == -1 or end > tc:
        return None
    m = re.search(r'<hp:cellSz width="(\d+)"', xml[tc:xml.find("</hp:tc>", at) + 8])
    return int(m.group(1)) - 600 if m else None


# 공문서 항목 단계 — 1. → 가. → 1) → 가) → (1) → (가) → ① . 한 단계 내려갈 때마다 한 글자씩 들여 쓴다(행정업무운영 편람의 항목 구분)
_LEVELS = (r"\d+\.", r"[가-하]\.", r"\d+\)", r"[가-하]\)", r"\(\d+\)", r"\([가-하]\)", r"[①-⑳]")
_LEVEL_RE = [re.compile(r"^\s*" + p + r"\s") for p in _LEVELS]


_HANGUL_SEQ = "가나다라마바사아자차카타파하"
_MARK_TOKEN = re.compile(r"^(\s*)(\(?)(\d{1,2}|[가-하]|[①-⑳])([.)])(\s)")


def _marker_value(tok: str) -> int | None:
    if tok.isdigit():
        return int(tok)
    if tok in _HANGUL_SEQ:
        return _HANGUL_SEQ.index(tok) + 1
    if "①" <= tok <= "⑳":
        return ord(tok) - ord("①") + 1
    return None


def _marker_text(level: int, n: int) -> str:
    """단계 번호(_LEVELS 의 차례)와 순번 → 부호 글(「1.」「가)」「(1)」「①」)."""
    letter = _HANGUL_SEQ[(n - 1) % len(_HANGUL_SEQ)]
    return [f"{n}.", f"{letter}.", f"{n})", f"{letter})", f"({n})", f"({letter})", chr(ord("①") + min(n, 20) - 1)][level]


def marker_pattern(text: str) -> list[int]:
    """본문 글에 나온 항목 부호 단계를 깊이 차례로(처음 나온 차례) — 표·도식 줄은 빼고. [2, 3, 4] 는 1) → 가) → (1)."""
    out: list[int] = []
    for ln in _plain_lines(text):
        lv = marker_level(ln)
        if lv is not None and lv not in out and _small_number(ln):
            out.append(lv)
    return out


def _plain_lines(text: str):
    fence = False
    for ln in (text or "").split("\n"):
        s = ln.strip()
        if s.startswith("```"):
            fence = not fence
            continue
        if fence or (s.startswith("|") and s.endswith("|")):
            continue
        yield ln


def _small_number(ln: str) -> bool:
    """「2026. 3. ~」 같은 날짜 줄은 항목 부호가 아니다 — 숫자 부호는 두 자리까지만."""
    m = _MARK_TOKEN.match(ln)
    return not m or not m.group(3).isdigit() or len(m.group(3)) <= 2


def unify_markers(text: str, pattern: list[int] | None) -> tuple[str, list[int]]:
    """한 문서 안 항목 부호를 하나로 — 이 본문의 부호 단계(처음 나온 차례)를 문서가 쓰는 단계(pattern)로 깊이별로 바꾼다
    (어떤 절은 1) 가) (1), 어떤 절은 1. 가. (1) 로 섞이던 것, 11-10 대화 190). 순번은 그대로(가 → 1, 2 → 나 처럼 갈래가 바뀌어도 차례 유지).
    pattern 이 없으면 이 본문의 꼴을 돌려준다(첫 절이 문서의 꼴을 정한다). 표·도식 줄은 손대지 않는다."""
    mine = marker_pattern(text)
    if not pattern or not mine or mine == pattern[:len(mine)]:
        return text, (pattern or mine)
    remap = {lv: pattern[i] for i, lv in enumerate(mine) if i < len(pattern)}
    out, fence = [], False
    for ln in (text or "").split("\n"):
        s = ln.strip()
        if s.startswith("```"):
            fence = not fence
        if fence or s.startswith("```") or (s.startswith("|") and s.endswith("|")):
            out.append(ln)
            continue
        lv = marker_level(ln)
        m = _MARK_TOKEN.match(ln)
        if lv is not None and lv in remap and remap[lv] != lv and m and _small_number(ln):
            n = _marker_value(m.group(3))
            if n:
                ln = m.group(1) + _marker_text(remap[lv], n) + m.group(5) + ln[m.end():]
        out.append(ln)
    return "\n".join(out), pattern


def marker_level(line: str) -> int | None:
    for i, rx in enumerate(_LEVEL_RE):
        if rx.match(line + " "):
            return i
    return None


class _LevelStyles:
    """항목 단계별 문단 모양 — 바탕 문단 모양을 복제해 왼쪽 여백(단계 × 한 글자)과 내어쓰기(부호 + 빈칸 폭)를 준 paraPr 를 header 에
    한 번씩 등록한다. 둘째 줄은 내용 시작 자리에 맞는다. 부호 없는 줄은 바로 앞 항목의 내용 자리에서 시작한다."""

    def __init__(self, header: str, base_pp: str, char_hu: int):
        self.header, self.base, self.ch = header, base_pp, max(char_hu, 800)
        self.cache: dict[tuple[int, int], str] = {}
        self.added: list[str] = []

    LINE_MIN = 160                     # 공문서 본문 줄 간격(%) — 바탕 모양이 이보다 좁으면 넓힌다
    TOP_GAP_PT = 4                     # 첫째 단계 항목 위 문단 간격(pt) — 「가.」「나.」 덩이가 붙어 보이지 않게

    def pp(self, left: int, hang: int, gap: int = 0) -> str:
        key = (left, hang, gap)
        if key in self.cache:
            return self.cache[key]
        m = re.search(rf'<hh:paraPr\b[^>]*\bid="{self.base}"[^>]*>.*?</hh:paraPr>', self.header, re.S)
        if not m:
            return self.base
        ids = [int(x) for x in re.findall(r'<hh:paraPr\b[^>]*\bid="(\d+)"', self.header)]
        new_id = str(max(ids) + 1)
        body = re.sub(r'(<hh:paraPr\b[^>]*\bid=")\d+(")', rf"\g<1>{new_id}\2", m.group(0), count=1)
        body = re.sub(r'<hh:align horizontal="[A-Z]+"', '<hh:align horizontal="JUSTIFY"', body, count=1)

        def margins(block: str, div: int) -> str:
            block = re.sub(r'<hc:intent value="-?\d+"', f'<hc:intent value="{-(hang // div)}"', block, count=1)
            if gap:
                block = re.sub(r'<hc:prev value="-?\d+"', f'<hc:prev value="{gap * 200 // div}"', block, count=1)
            ls = re.search(r'<hh:lineSpacing type="PERCENT" value="(\d+)"', block)
            if ls and int(ls.group(1)) < self.LINE_MIN:
                block = block[:ls.start(1)] + str(self.LINE_MIN) + block[ls.end(1):]
            return re.sub(r'<hc:left value="-?\d+"', f'<hc:left value="{(left + hang) // div}"', block, count=1)
        case = re.search(r"<hp:case\b.*?</hp:case>", body, re.S)
        if case:
            body = body[:case.start()] + margins(case.group(0), 2) + body[case.end():]
        dflt = re.search(r"<hp:default>.*?</hp:default>", body, re.S)
        if dflt:
            body = body[:dflt.start()] + margins(dflt.group(0), 1) + body[dflt.end():]
        if not case and not dflt:
            body = margins(body, 1)
        close = self.header.find("</hh:paraProperties>")
        self.header = self.header[:close] + body + self.header[close:]
        cnt = re.search(r'(<hh:paraProperties\b[^>]*\bitemCnt=")(\d+)(")', self.header)
        if cnt:
            self.header = self.header[:cnt.start(2)] + str(int(cnt.group(2)) + 1) + self.header[cnt.end(2):]
        self.cache[key] = new_id
        return new_id

    def for_lines(self, lines: list[str], base_level: int, rel_min: int | None = None) -> list[tuple[str, str]]:
        """줄마다 (문단 모양 id, 글). 깊이는 부호가 처음 나온 차례로 정한다(marker_depths) — 「1)」 아래 「가.」 처럼 편람 차례와
        다르게 써도 아래 항목은 늘 위 항목보다 들여 쓴다. 깊이는 글 덩이(표·도식으로 끊긴)를 건너 이어 센다(같은 부호는 같은 깊이)."""
        out, cont_left = [], 0
        stack = getattr(self, "_stack", None)
        if stack is None:
            stack = self._stack = []
        for ln in lines:
            lv = marker_level(ln)
            if lv is None:
                out.append((self.pp(cont_left, 0) if cont_left else self.base, ln.strip()))
                continue
            depth = _push_depth(stack, lv)
            mk = re.match(r"^\s*(\S+)", ln).group(1)
            hang = int(self.ch * (len(mk) * 0.55 + 0.5))                 # 부호 폭(대략 반각 글자) + 빈칸
            left = depth * self.ch
            cont_left = left + hang
            gap = self.TOP_GAP_PT if depth == 1 and out else 0
            out.append((self.pp(left, hang, gap), mk + " " + ln.strip()[len(mk):].strip()))
        return out


def _push_depth(stack: list[int], lv: int) -> int:
    """부호 단계 lv 의 깊이(1부터) — 이미 나온 부호면 그 깊이로 돌아가고, 처음 나온 부호면 지금 항목 아래로 한 단계 내려간다."""
    if lv in stack:
        del stack[stack.index(lv) + 1:]
    else:
        stack.append(lv)
    return len(stack)


def marker_depths(lines: list[str]) -> list[int]:
    """줄마다 항목 깊이(부호 없는 줄은 바로 앞 항목의 깊이, 맨 앞이면 0)."""
    stack: list[int] = []
    out, cur = [], 0
    for ln in lines:
        lv = marker_level(ln)
        if lv is not None:
            cur = _push_depth(stack, lv)
        out.append(cur)
    return out


def _top_paras(xml: str, end: int):
    """xml[:end] 안 맨 바깥 문단(<hp:p>…</hp:p>)의 (시작, 끝) — 표 칸 안 문단은 건너뛴다."""
    depth, start = 0, None
    for t in re.finditer(r"<hp:p\b|</hp:p>", xml[:end]):
        if t.group(0) != "</hp:p>":
            if depth == 0:
                start = t.start()
            depth += 1
        else:
            depth -= 1
            if depth == 0 and start is not None:
                yield start, t.end()
            if depth < 0:
                return


def _cell_texts(row: str) -> list[str]:
    return [wf._text(re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", c, flags=re.S))
            for c in re.findall(r"<hp:tc\b.*?</hp:tc>", row, re.S)]


def _head_cells(frag: str) -> list[str]:
    """표의 머리줄 글(첫 두 줄 — 두 줄 머리 「졸업이수 학점 / 교양·전공·합계」 도) — 빈 칸은 뺀다."""
    rows = re.findall(r"<hp:tr\b.*?</hp:tr>", frag, re.S)[:2]
    return [t for r in rows for t in _cell_texts(r) if t]


def _hkey(t: str) -> str:
    return re.sub(r"[\s\W_]+|\d+\)$", "", str(t))


def same_head(form_head: list[str], body_head: list[str]) -> bool:
    """본문 표 머리줄이 서식 표 머리줄과 같은 표인가 — 본문 머리 칸의 6할 이상이 서식 머리 칸과 같거나 서로 품는다."""
    fk = [k for k in (_hkey(x) for x in form_head) if k]
    bk = [k for k in (_hkey(x) for x in body_head) if k]
    if len(bk) < 2 or not fk:
        return False
    hit = sum(1 for b in bk if any(b == f or (len(b) >= 2 and len(f) >= 2 and (b in f or f in b)) for f in fk))
    return hit >= 0.6 * len(bk)


def section_form_heads(data: bytes, name: str) -> list[list[str]]:
    """절(다음 절 필드 앞까지)에 든 서식 표들의 머리줄 — 본문 쓰기 지시서에 「이 표를 채운다(새로 만들지 않는다)」 로 건넨다."""
    _, secs = _open(data)
    for xml in secs.values():
        hit = _find(xml, name)
        if not hit:
            continue
        pb = _para_bounds(xml, hit.start())
        tail = xml[pb[1] if pb else hit.end():]            # 필드가 든 문단 다음부터(맨 바깥 문단 단위로 센다)
        nxt = next((m.start() for m in _BEGIN.finditer(tail) if _unq(m.group(2)).startswith("절:")), len(tail))
        out = []
        for a, b in _top_paras(tail, nxt):
            if b > nxt:
                break
            frag = tail[a:b]
            if "<hp:tbl" in frag and any(_kind(_unq(m.group(2))) == "입력" for m in _BEGIN.finditer(frag)):
                break                                       # 사람 칸(기안자·결재란 등, 입력:)을 품은 표 — 절 본문 영역이 끝났다. 끝 절이면 다음 절 필드가 없어
                                                            # 결문의 결재 표까지 「이 절의 서식 표」 로 넘겨 본문에 결재 표를 다시 쓰던 일(11-10 대화 192)
            if "<hp:tbl" not in frag or len(re.findall(r"<hp:tr\b", frag)) < 2:
                continue
            h = _head_cells(frag)
            if len(h) >= 2:
                out.append(h[:16])
        return out
    return []


def _drop_empty_form_tables(tail: str, form_sid: str | None = None, cols: set[int] | None = None,
                            keep_sid: str | None = None, heads: list[list[str]] | None = None) -> str:
    """본문이 표를 들고 왔을 때, 같은 절(다음 절 필드 앞까지)에 남은 양식의 빈 표를 지운다 — 본문 표 밑에 같은 꼴 빈 표가 겹쳐 남지 않게.
    「빈 틀」은 사용자 글이 있는가로 가른다: 코드로 짠 양식의 표는 양식 글에 「양식 틀」 표시가 있어(webhwp_fields.mark_form_text)
    라벨·2단 머리줄·합계 행 이름이 있어도 사용자 글이 없으면 지운다. 표시가 없는 표(학교 서식)는 머리줄 말고 글자가 한 칸이라도 들면 둔다.
    cols — 본문이 넣은 표들의 열 수. 빈 틀 가운데 열 수가 같은 것이 있으면 그것만 지운다(한 절에 표가 둘인 양식 — 지표 표와 SWOT 격자 —
    에서 본문이 지표 표만 쓰면 SWOT 틀은 남게). 같은 것이 없으면 본문 표가 양식 표를 다른 꼴로 바꾼 것이라 빈 틀을 모두 지운다.
    머리줄 없는 라벨·값 격자와 「양식 틀 고정」(keep_sid — 결재란·서명란) 표는 지우지 않는다."""
    nxt = next((m.start() for m in _BEGIN.finditer(tail) if _unq(m.group(2)).startswith("절:")), len(tail))
    nxt = min(nxt, len(tail))
    cut = []
    for a, b in _top_paras(tail, nxt):
        if b > nxt:
            break
        frag = tail[a:b]
        if "<hp:tbl" not in frag:
            continue
        rows = re.findall(r"<hp:tr\b.*?</hp:tr>", frag, re.S)
        if len(rows) < 2 or len(re.findall(r"<hp:tbl\b", frag)) > 1:      # 한 줄 상자·표 안의 표는 두고
            continue
        if keep_sid is not None and re.search(rf'<hp:p\b[^>]*\bstyleIDRef="{keep_sid}"', frag):
            continue
        judged = wf.user_text_in_table(frag, form_sid)
        # 본문이 이 서식 표와 같은 머리줄의 표를 들고 왔다 — 서식 표의 칸(견본 라벨·빈 칸)을 본문 표가 채운 것이니 서식 표를 둔 채 같은 표가
        # 두 번 남지 않게 본문 표로 바꾼다(11-10 대화 190: 새 <표> 아래 견본 표 「OO학과 00명」이 그대로). 사용자가 쓴 표(judged)는 두고
        matched = bool(heads) and not judged and any(same_head(_head_cells(frag), h) for h in heads)
        if matched:
            m = re.search(r'<hp:tbl\b[^>]*\bcolCnt="(\d+)"', frag)
            cut.append((a, b, -1 if not m else int(m.group(1))))
            continue
        if judged:
            continue
        first = [wf._text(re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", c, flags=re.S))
                 for c in re.findall(r"<hp:tc\b.*?</hp:tc>", rows[0], re.S)]
        if judged is False and not (len(first) >= 2 and all(first)):
            continue                                       # 머리줄 없는 라벨·값 격자(개요·항목 표)는 본문 표로 바뀌는 자리가 아니다 — 칸을 채운다
        if judged is None:
            body_txt = "".join(re.findall(r"<hp:t\b[^>]*>([^<]*)</hp:t>", "".join(rows[1:])))
            if re.sub(r"\s+", "", body_txt):
                continue
        if re.sub(r"\s+", "", "".join(re.findall(r"<hp:t\b[^>]*>([^<]*)</hp:t>", frag[:frag.find("<hp:tbl")]))):
            continue                                       # 표 앞에 글이 같이 든 문단은 두고
        m = re.search(r'<hp:tbl\b[^>]*\bcolCnt="(\d+)"', frag)
        cut.append((a, b, int(m.group(1)) if m else 0))
    if cols and any(n in cols for _a, _b, n in cut):
        keep_matched = [x for x in cut if heads and any(same_head(_head_cells(tail[x[0]:x[1]]), h) for h in heads)]
        cut = [x for x in cut if x[2] in cols or x in keep_matched]
    for a, b, _n in reversed(cut):
        tail = tail[:a] + tail[b:]
    return tail


_PX = re.compile(r"(<hp:p\b[^>]*>)(<hp:run\b[^>]*>)<hp:t>(.*)</hp:t></hp:run></hp:p>$", re.S)


def _find(xml: str, name: str):
    return next((m for m in _BEGIN.finditer(xml) if _unq(m.group(2)) == name), None)


def _drop_conts(xml: str, name: str) -> str:
    """이 절의 이어 쓰기 필드를 문단째 뗀다 — 여는 필드가 든 문단부터 닫는 필드가 든 문단까지(사람이 그 안에 문단을 더했어도)."""
    while True:
        m = next((m for m in _BEGIN.finditer(xml) if is_cont(_unq(m.group(2))) and section_of(_unq(m.group(2))) == name), None)
        if not m:
            return xml
        pb = _para_bounds(xml, m.start())
        if not pb:
            return xml
        sp = _span(xml, m.group(1), m.end())
        end = xml.find("</hp:p>", sp[1]) + 7 if sp else pb[1]
        xml = xml[:pb[0]] + xml[max(end, pb[1]):]


def _drop_paras(xml: str, pids) -> str:
    """문단 id 로 문단(표 문단은 표째)을 뗀다."""
    for pid in pids:
        m = re.search(rf'<hp:p id="{pid}"[^>]*>', xml)
        if not m:
            continue
        depth = 0
        for t in re.finditer(r"<hp:p\b|</hp:p>", xml[m.start():]):
            depth += 1 if t.group(0) != "</hp:p>" else -1
            if depth == 0:
                xml = xml[:m.start()] + xml[m.start() + t.end():]
                break
    return xml


def _sample_char(header: str, cp: str) -> bool:
    """글자 모양이 서식 견본·안내 글 꼴인가 — 검정이 아닌 글자 색이거나 기울임."""
    m = re.search(rf'<hh:charPr\b[^>]*\bid="{cp}"[^>]*>(?:(?!</hh:charPr>).)*</hh:charPr>', header, re.S)
    if not m:
        return False
    col = re.search(r'\btextColor="#([0-9A-Fa-f]{6})"', m.group(0))
    return bool((col and col.group(1).upper() != "000000") or "<hh:italic" in m.group(0))


def _body_char(sections, header: str) -> str | None:
    """양식 본문 글자 모양 id — 표·상자 밖 최상위 문단 가운데 긴 문장(굵지 않은 것)에 가장 많이 쓴 글자 모양. 없으면 None."""
    from collections import Counter
    bold = {m.group(1) for m in re.finditer(r"<hh:charPr\b[^>]*\bid=\"(\d+)\"[^>]*>(?:(?!</hh:charPr>).)*<hh:bold\b", header, re.S)}
    # 검정이 아닌 글자(파란 작성방법·빨간 안내)는 본문 본보기가 아니다
    bold |= {m.group(1) for m in re.finditer(r'<hh:charPr\b[^>]*\bid="(\d+)"[^>]*\btextColor="#(?!000000)[0-9A-Fa-f]{6}"', header)}
    seen: Counter = Counter()
    for s_ in sections:
        for (a, b), t in zip(s_.paras, s_.texts):
            block = s_.xml[a:b]
            if len(t or "") <= hf._ParaStyles.SHORT or "<hp:tbl" in block or "<hp:pic" in block or wf._is_heading(t):
                continue
            m = re.search(r'<hp:run\b[^>]*\bcharPrIDRef="(\d+)"', block)
            if m and m.group(1) not in bold:
                seen[m.group(1)] += 1
    return seen.most_common(1)[0][0] if seen else None


# 자리표시 칸 — 말줄임(… ... ⋯ ⋮)·줄표·「중략」「이하 생략」. 작년 양식 표의 「… | … | …」 행을 본문 표가 그대로 옮겨 남겼다(11-10 <표 5>)
_FILLER = re.compile(r"^(?:\.{2,}|…+|⋯+|⋮+|[-–—·ㆍ]+|중략|\(?이하(?:생략|동일|중략)\)?|등)$")
_ELLIPSIS = re.compile(r"^(?:\.{2,}|…+|⋯+|⋮+|중략|\(?이하(?:생략|동일|중략)\)?)$")


def drop_filler_rows(rows: list[list[str]]) -> list[list[str]]:
    """머리줄 아래에서 모든 칸이 말줄임·줄표·빈칸뿐인 행(번호 칸 하나는 있어도)을 뺀다 — 말줄임이 한 칸이라도 있어야 자리표시 행으로 본다
    (그냥 빈 행·「-」 만 든 값 행은 둔다)."""
    out = []
    for i, r in enumerate(rows):
        cells = [re.sub(r"\s+", "", str(c)) for c in r]
        rest = cells[1:] if cells and re.fullmatch(r"\d{1,3}\.?", cells[0] or "") else cells
        if (i and any(_ELLIPSIS.match(c) for c in cells if c)
                and all((not c) or _FILLER.match(c) for c in rest)):
            continue
        out.append(r)
    return out


def write_section(data: bytes, name: str, items: list[tuple[str, object]], written: dict[str, list[str]] | None = None
                  ) -> tuple[bytes, list[str]]:
    """절 본문 쓰기 — (새 바이트, 넣은 문단 id). 글 줄은 절 필드 안 문단으로, 표·도식은 필드 밖, 표 뒤 글은 이어 쓰기 필드로.
    앞서 쓴 것(필드 안 글·이어 쓰기 필드·written 의 문단)은 지운다."""
    zin, secs = _open(data)
    items = [(k, wf.clean_args(v)) for k, v in (items or []) if k in ("text", "table", "diagram")]
    header = zin.read("Contents/header.xml").decode("utf-8")
    pp, cp = hf._default_refs(header)
    solid = hf._solid_border_ids(header)
    # 서식 보존 채우기와 같은 서식 고르기 — 양식 본문 문단의 부호별 모양(1)·가) 단계 들여쓰기·글자), 두 줄 넘는 부호 문단의
    # 내어쓰기(새 paraPr 등록), 표는 양식 표의 머리 행·라벨 열·본문 칸 모양(테두리·음영·정렬·글자)
    sec_objs = [hf._Section(n, secs[n], solid) for n in sorted(secs)]
    roles = hf._role_styles(sec_objs, header)
    para_styles = hf._ParaStyles(sec_objs, header)
    hangs = hf._HangRegistry(header)
    body_cp = _body_char(sec_objs, header)
    old_ids = set((written or {}).get(name, []))
    ids = wf._Ids([header, *secs.values()])
    new: dict[str, str] = {}
    put: list[str] = []
    diagrams: list[str] = []
    diagram_pids: set[str] = set()
    # 글 덩이(이웃한 글은 하나로)와 표·도식 — 첫 글 덩이는 절 필드, 표·도식 뒤 글 덩이는 이어 쓰기 필드가 된다
    segs: list[tuple[str, object]] = []
    for kind, val in items:
        if kind == "text":
            lines = hf.split_lines(str(val)) if str(val).strip() else []
            if segs and segs[-1][0] == "text":
                segs[-1][1].extend(lines)
            elif lines:
                segs.append(("text", list(lines)))
        elif (kind == "diagram" and str(val).strip()) or (kind == "table" and isinstance(val, list) and val):
            segs.append((kind, val))
    for n, xml in secs.items():
        hit = _find(xml, name)
        if not hit:
            continue
        # 앞서 쓴 것을 지운다 — 절 필드 안(여러 문단)을 비우고, 이 절의 이어 쓰기 필드와 앞서 넣은 문단(표·도식, 예전 꼴의 필드 밖 글)을 뗀다.
        # 필드 안부터 비운다(필드 안 문단 id 도 written 에 있어, 먼저 떼면 닫는 필드가 든 문단이 사라진다)
        sp = _span(xml, hit.group(1), hit.end())
        if sp:
            xml = xml[:sp[0]] + "<hp:t></hp:t>" + xml[sp[1]:]
        xml = _drop_conts(xml, name)
        if old_ids:
            xml = _drop_paras(xml, old_ids)
        hit = _find(xml, name)
        pb = _para_bounds(xml, hit.start()) if hit else None
        if not pb:
            continue
        head_text = re.sub(r"<[^>]+>", "", name)[2:] if name.startswith("절:") else ""
        head_level = marker_level(head_text) if marker_level(head_text) is not None else 0
        # 새 문단의 글자는 그 자리(필드가 든 문단)의 글자 모양을 잇는다 — 양식 칸이 11pt 면 덧붙는 줄도 11pt
        here = re.search(r'<hp:run\b[^>]*\bcharPrIDRef="(\d+)"', xml[pb[0]:pb[1]])
        cp = here.group(1) if here else cp
        # 다만 그 자리가 제목 문단의 큰 글자를 물려받았으면(제목 바로 뒤에 박은 필드) 양식 본문 글자로 — 새 본문만 커 보이던 것(11-09)
        if body_cp and hangs.char_height(cp) > hangs.char_height(body_cp) + 100:
            cp = body_cp
        # 그 자리가 서식 견본·작성방법 글(파랑·빨강·기울임)이면 본문 글자로 — 절 본문 전체가 파란 기울임으로 들어갔다(11-12 특성화 재현)
        if body_cp and _sample_char(header, cp):
            cp = body_cp
        bf, margin = hf._table_template(xml, solid)
        if bf is None:
            header, bf = hf._inject_border_fill(header)
            margin = margin or '<hp:cellMargin left="510" right="510" top="141" bottom="141"/>'
        levels = _LevelStyles(header, pp, hangs.char_height(cp))      # 테두리 모양을 넣은 뒤의 header 에서(끝에 levels.header 로 바꿔 담는다)
        width = _cell_width(xml, hit.start()) or hf._text_width_hu(xml)
        form_marks = bool(para_styles.pick("1) 예") or para_styles.pick("가) 예"))
        all_lines = [ln for k, v in segs if k == "text" for ln in v]
        rel = min((lv for lv in (marker_level(x) for x in all_lines) if lv is not None), default=head_level + 1)

        def paras(lines: list[str]) -> list[str]:
            """줄마다 문단 XML(id="0") — 양식에 단계별 본문 예시가 있으면 그 모양(서식 작성자의 뜻), 없으면 공문서 단계 규칙."""
            if form_marks:
                out = []
                for line in lines:
                    lp, lc = para_styles.pick(line) or (pp, cp)
                    if hf.marker_of(line) and len(line) > hf._ParaStyles.SHORT:
                        lp, tab_hu = hangs.hanging(lp, lc, line[:hf._MARK.match(line).end()].strip())
                        out.append(hf.marker_paragraph_xml(line, lp, lc, tab_hu))
                    else:
                        out.append(hf.paragraph_xml(line, lp, lc))
                return out
            return [hf.paragraph_xml(line, lp, cp) for lp, line in levels.for_lines(lines, head_level, rel)]

        def joined(pxs: list[str]) -> str:
            """필드 안에서 줄마다 문단을 나누는 조각 — 문단 모양·글자 모양은 줄마다 제 것."""
            s_ = ""
            for px in pxs:
                pid = ids.new()
                put.append(pid)
                m_ = _PX.match(px.replace('<hp:p id="0"', f'<hp:p id="{pid}"', 1))
                s_ += "</hp:t></hp:run></hp:p>" + m_.group(1) + m_.group(2) + "<hp:t>" + m_.group(3)
            return s_

        # 첫 글 덩이는 절 필드 안에 — 첫 줄은 필드가 든 문단에(빈 필드의 안내 글이 본문 위에 남지 않게), 나머지 줄은 필드 안 새 문단
        first = segs[0][1] if segs and segs[0][0] == "text" else []
        rest_segs = segs[1:] if first else segs
        value = "<hp:t></hp:t>"
        if first:
            if form_marks:
                tail_px = paras(first[1:])
            else:
                all_px = paras(first)
                tail_px = all_px[1:]
                # 필드 안의 첫 줄도 뒤 줄과 같은 단계 모양으로(첫 줄만 왼쪽에 붙어 보이지 않게)
                lp0 = re.search(r'paraPrIDRef="(\d+)"', all_px[0]).group(1)
                p_open = re.match(r"<hp:p\b[^>]*>", xml[pb[0]:])
                new_open = re.sub(r'paraPrIDRef="\d+"', f'paraPrIDRef="{lp0}"', p_open.group(0), count=1)
                xml = xml[:pb[0]] + new_open + xml[pb[0] + len(p_open.group(0)):]
            value = "<hp:t>" + escape(wf.clean_text(first[0], None)) + joined(tail_px) + "</hp:t>"
        hit = _find(xml, name)
        sp = _span(xml, hit.group(1), hit.end())
        if sp:
            xml = xml[:sp[0]] + value + xml[sp[1]:]
            hit = _find(xml, name)
            sp = _span(xml, hit.group(1), hit.end())
        pb = (pb[0], xml.find("</hp:p>", sp[1]) + 7 if sp else _para_bounds(xml, hit.start())[1])   # 필드가 끝나는 문단까지
        chunks = []
        k_ = 1
        for kind, val in rest_segs:
            if kind == "text":
                # 표 뒤 글 — 표 바로 뒤의 이어 쓰기 필드 안에(양식 모드에서 고칠 수 있게)
                k_ += 1
                pxs = paras(val)
                a_, b_ = wf.field_parts(cont_name(name, k_), "본문 이어 쓰기", ids)
                pid = ids.new()
                put.append(pid)
                m_ = _PX.match(pxs[0].replace('<hp:p id="0"', f'<hp:p id="{pid}"', 1))
                chunks.append(m_.group(1) + m_.group(2) + a_ + "<hp:t>" + m_.group(3) + joined(pxs[1:]) + "</hp:t>" + b_ + "</hp:run></hp:p>")
            elif kind == "diagram":
                # 도식은 표시 문단으로 넣고 다 쓴 뒤 그 자리를 도식 표로 바꾼다(webhwp_style.diagram_table — header 모양 등록이 한 곳에서)
                pid = ids.new()
                diagrams.append(str(val))
                diagram_pids.add(pid)
                chunks.append(hf.paragraph_xml(f"⟦도식{len(diagrams) - 1}⟧", pp, cp).replace('<hp:p id="0"', f'<hp:p id="{pid}"', 1))
                put.append(pid)
            else:
                rows = drop_filler_rows([[str(c) for c in r] for r in val])
                pid = ids.new()
                chunks.append(hf.table_xml(rows, pp, cp, width, bf, margin, int(ids.new()), None, roles, char_hu=hangs.char_height(cp))
                              .replace('<hp:p id="0"', f'<hp:p id="{pid}"', 1))
                put.append(pid)
        # 줄 배치 캐시는 글이 바뀐 필드 문단에서만 지운다(새 문단·표는 처음부터 없다). 문서 전체에서 지우면 상자 밖으로 글이 넘친다
        field_p = hf.strip_linesegs(xml[pb[0]:pb[1]])[0]
        head_ = xml[:pb[0]] + field_p + "".join(chunks)
        tail_ = xml[pb[1]:]
        if any(k == "table" for k, _ in items):
            ncols = {max((len(r) for r in v if isinstance(r, (list, tuple))), default=0)
                     for k, v in items if k == "table" and isinstance(v, list) and v}
            heads = [[str(c) for c in v[0]] for k, v in items if k == "table" and isinstance(v, list) and v]
            tail_ = _drop_empty_form_tables(tail_, wf.form_style_id(header), ncols,
                                            keep_sid=wf.form_style_id(header, wf.FORM_KEEP_STYLE), heads=heads)
        new[n] = head_ + tail_
        if levels.cache:
            header = levels.header
        break
    if hangs.changed:                                     # 내어쓰기 문단 모양을 header 에 등록(채우기와 같은 방식)
        for body in re.findall(r'<hh:paraPr\b[^>]*\bid="(?:%s)"[^>]*>.*?</hh:paraPr>' % "|".join(hangs.cache.values()), hangs.header, re.S):
            close = header.find("</hh:paraProperties>")
            header = header[:close] + body + header[close:]
        cnt = re.search(r'(<hh:paraProperties\b[^>]*\bitemCnt=")(\d+)(")', header)
        if cnt:
            header = header[:cnt.start(2)] + str(int(cnt.group(2)) + len(hangs.cache)) + header[cnt.end(2):]
    if not new:
        raise KeyError(f"절 필드 「{name}」 을 찾지 못했습니다")
    out = _pack(zin, new)
    if header != zin.read("Contents/header.xml").decode("utf-8"):    # 테두리 모양을 새로 넣었으면 header 도 바꿔 담는다
        zin2 = zipfile.ZipFile(io.BytesIO(out))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zout:
            for info in zin2.infolist():
                body = header.encode("utf-8") if info.filename == "Contents/header.xml" else zin2.read(info.filename)
                zout.writestr(info, body, compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
        out = buf.getvalue()
    if diagrams:
        from zzaimy.app import webhwp_style
        out = webhwp_style.place_diagrams(out, diagrams, width)
    if put and set(put) - diagram_pids:
        # 새 표 — 양식에 음영 있는 표가 없으면 학교 표 기본 모양(webhwp_style.HOUSE) 통째로, 있으면 양식 선·음영은 두고
        # 머리줄 굵게·짧은 값 가운데·칸 여백만(양식을 따른 표도 글이 선에 붙지 않게)
        from zzaimy.app import webhwp_style
        out = webhwp_style.apply(out, only_paras=set(put) - diagram_pids,
                                 opts=None if "head" not in roles else {"keep_borders": True})
    return out, put


def _guide_box(frag: str) -> str | None:
    """맨 바깥 문단이 작성방법 안내 상자(한 줄 표, 안내 표시 글, 표 밖 글 없음)면 그 글, 아니면 None."""
    if "<hp:tbl" not in frag or len(re.findall(r"<hp:tbl\b", frag)) > 1 or len(re.findall(r"<hp:tr\b", frag)) != 1:
        return None
    # 필드가 든 상자는 쓸 자리다 — 다만 구조 저장이 안내 글 문단을 「문단:n」 으로 감싼 것은 안내 그대로다(11-10 대화 191)
    bare = re.sub(r'<hp:ctrl>\s*<hp:fieldBegin\b[^>]*name="문단:\d+".*?</hp:fieldBegin>\s*</hp:ctrl>', "", frag, flags=re.S)
    if _BEGIN.search(bare):
        return None
    outside = re.sub(r"<hp:tbl\b.*</hp:tbl>", "", frag, flags=re.S)
    if wf._text(outside).strip():
        return None
    txt = wf._text(frag).strip()
    return txt if wf._GUIDE.search(txt) else None


def drop_guides(data: bytes, names) -> tuple[bytes, dict[str, str]]:
    """다 쓴 절의 작성방법 안내 상자를 뗀다 — (새 바이트, {절 필드: 뗀 안내 글}). 절 필드 문단 바로 앞(빈 문단은 건너뛰고, 제목·글이
    나오면 멈춤)의 안내 상자만. 필드가 표 칸 안에 있으면(배치 표) 건드리지 않는다. 아직 안 쓴 절의 안내는 남는다(사람·에이전트가 본다).
    뗀 글은 부른 쪽이 작업본 기록(meta guides)에 둔다 — 고쳐 쓰기·맡기기가 계속 읽는다(webhwp_agent.Session.section_ctx)."""
    names = [n for n in dict.fromkeys(names or []) if n]
    if not names:
        return data, {}
    zin, secs = _open(data)
    new: dict[str, str] = {}
    got: dict[str, str] = {}
    for n, xml in secs.items():
        changed = False
        for name in names:
            if name in got:
                continue
            hit = _find(xml, name)
            if not hit:
                continue
            pb = _para_bounds(xml, hit.start())
            if not pb:
                continue
            head = xml[:pb[0]]
            if len(re.findall(r"<hp:p\b", head)) != head.count("</hp:p>"):
                continue                                    # 표 칸 안의 필드
            cut, texts = [], []
            for a, b in reversed(list(_top_paras(xml, pb[0]))):
                frag = xml[a:b]
                g = _guide_box(frag)
                if g:
                    cut.append((a, b))
                    texts.append(g)
                    continue
                if "<hp:tbl" in frag or "<hp:pic" in frag or wf._text(frag).strip() or _BEGIN.search(frag):
                    break
            if not cut:
                continue
            for a, b in cut:                                # 뒤에서부터(reversed 로 모았다)
                xml = xml[:a] + xml[b:]
            got[name] = "\n".join(reversed(texts))
            changed = True
        # 절 필드에 붙지 않은 안내 — (1) 바로 뒤가 칸·표 필드 묶음(표)이면 그 묶음의 필드가 다 찼을 때, (2) 바로 뒤가 제목인 위 단계 안내
        # (「2. 주요 프로그램」 처럼 제 필드 없이 아래 절들을 묶는 제목)면 그 상자부터 다음 위 단계 안내까지의 필드(아래 절 전부)가 다 찼을 때 뗀다
        filled = set(names)

        def _fields_in(frag: str) -> list[str]:
            return [_unq(m.group(2)) for m in _BEGIN.finditer(frag) if not is_cont(_unq(m.group(2)))]
        while True:
            paras = list(_top_paras(xml, len(xml)))
            boxes = []                                      # (시작, 끝, 안내 글, 「묶음」 이면 그 필드 목록 / 위 단계면 None)
            for i, (a, b) in enumerate(paras):
                g = _guide_box(xml[a:b])
                if not g:
                    continue
                nxt = next((xml[c:d] for c, d in paras[i + 1:] if wf._text(xml[c:d]).strip() or "<hp:tbl" in xml[c:d] or _BEGIN.search(xml[c:d])), "")
                if _guide_box(nxt):
                    continue
                inside = _fields_in(nxt)
                if inside and not inside[0].startswith("절:"):
                    boxes.append((a, b, g, inside))         # 칸·표 묶음 안내
                elif not inside:
                    boxes.append((a, b, g, None))           # 위 단계 안내
            drop = None
            tops = [x for x in boxes if x[3] is None]
            for a, b, g, inside in boxes:
                if inside is None:
                    later = [t[0] for t in tops if t[0] > a]
                    inside = _fields_in(xml[b:later[0] if later else len(xml)])
                    if not any(x.startswith("절:") for x in inside):
                        continue
                if inside and all(x in filled for x in inside):
                    drop = (a, b, g, inside[0])
                    break
            if not drop:
                break
            a, b, g, first = drop
            xml = xml[:a] + xml[b:]
            got["위:" + first] = (got.get("위:" + first, "") + "\n" + g).strip()
            changed = True
        if changed:
            new[n] = xml
    return (_pack(zin, new) if new else data), got


_DUP_MARK = re.compile(r"(?m)^(\s*)((?:\d{1,2}|[가-하])[.)]|\((?:\d{1,2}|[가-하])\)|[①-⑳])\s+\2(?=\s)")


def drop_double_markers(text: str) -> str:
    """줄 머리에 같은 항목 부호가 두 번(「가. 가. 목적」) 오면 하나로 — 지시서의 「가. 목적(…)」 요지를 서브가 부호째 옮겨 쓰던 일(11-10 대화 192)."""
    return _DUP_MARK.sub(r"\1\2", text or "")


def drop_field_tables(text: str, labels) -> str:
    """본문의 마크다운 표 가운데 머리줄이 문서에 이미 있는 칸 이름(기안자·검토자·결재권자 같은 사람·라벨 칸)으로 된 표를 뺀다 —
    결문의 결재 표를 본문에 흉내 내어 다시 쓰던 일(11-10 대화 192). 머리 칸의 6할 이상이 칸 이름이고 둘 이상 맞을 때만."""
    keys = {re.sub(r"[\s\W_]+", "", str(x)) for x in labels or [] if str(x).strip()}
    if not keys:
        return text
    lines, out, i = (text or "").split("\n"), [], 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("|") and s.endswith("|"):
            j = i
            while j < len(lines) and lines[j].strip().startswith("|") and lines[j].strip().endswith("|"):
                j += 1
            head = [c.strip() for c in s.strip("|").split("|")]
            hit = sum(1 for c in head if re.sub(r"[\s\W_]+", "", c) in keys)
            if hit >= 2 and hit >= 0.6 * len([c for c in head if c]):
                if out and re.match(r"^\s*<표[^>]*>", out[-1]):
                    out.pop()                                # 그 표 제목 줄도
                i = j
                continue
            out.extend(lines[i:j])
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def drop_heading_echo(text: str, field: str) -> str:
    """본문 첫 줄이 절 제목을 그대로 되풀이하면 뺀다 — 제목은 양식의 절 머리에 이미 있다(「2. 성과지표 총괄표」 가 두 번 보이던 것).
    공백·기호·마크다운 # 를 뺀 글이 같을 때만 — 제목을 바꿔 쓴 줄(○○○○ → 과제 이름)은 그대로 둔다."""
    def key(s: str) -> str:
        return re.sub(r"[\s\W_]+", "", s)

    def bare(s: str) -> str:                               # 번호(1.1.·1.·가.·1)·(1)·①)와 마크다운 # 를 뗀 제목 — 「1.1. 정원 감축 계획」 아래 「1. 정원 감축 계획」(11-10 대화 190)
        s = re.sub(r"^\s*#+\s*", "", s)
        return key(re.sub(r"^\s*(?:(?:\d+\.)+\d*|[가-하]\.|\(?\d+\)|\(?[가-하]\)|[①-⑳]|[IVXⅠ-Ⅹ]+\.)\s*", "", s))
    raw_name = field.split(":", 1)[-1]
    name = key(raw_name)
    lines = text.split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is None or not name or (key(lines[first]) != name and not (len(bare(raw_name)) >= 2 and bare(lines[first]) == bare(raw_name))):
        return text
    rest = lines[first + 1:]
    while rest and not rest[0].strip():
        rest.pop(0)
    return "\n".join(rest) if any(x.strip() for x in rest) else text


_EMPH = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")


def drop_emphasis(text: str) -> str:
    """마크다운 굵게(**글**·__글__) 표시를 떼고 글만 남긴다 — 한글 문서에는 표시가 글자 그대로 들어갔다(10-30 「**합계** **500**」).
    문서 서식은 굵게를 쓰지 않는다(house_style). 홑 별표(각주·목록 머리)는 그대로."""
    return _EMPH.sub(lambda m: m.group(2), text or "")


def parse_body(text: str) -> list[tuple[str, object]]:
    """에이전트가 쓴 절 본문(글 + 마크다운 표 | a | b | + ```도식 블록) → items. 표 구분 줄(|---|)은 버린다."""
    items: list[tuple[str, object]] = []
    lines = drop_emphasis(wf.clean_text(text, breaks=False)).split("\n")   # 「<br>」 은 칸·줄로 나눈 뒤 바꾼다(표 줄이 끊기지 않게)
    buf: list[str] = []
    table: list[list[str]] = []
    fence: list[str] | None = None
    plain_fence = False                                     # 이름표 없는 ``` 덩이 — 화살표가 있으면 도식, 없으면 표시 줄만 뗀 글

    def flush_text():
        if buf and any(x.strip() for x in buf):
            items.append(("text", "\n".join(x for x in buf if x.strip())))
        buf.clear()

    for ln in lines:
        s = ln.strip()
        if fence is not None:                               # ```도식 … ``` 블록
            if s.startswith("```"):
                if plain_fence and not any(a in "\n".join(fence) for a in ("↓", "→", "->", "⇒", "▶")):
                    buf.extend(fence)                       # 그림이 아닌 덩이 — 글로(표시 줄 「```」 은 버린다)
                else:
                    items.append(("diagram", "\n".join(fence)))
                fence = None
            else:
                fence.append(ln)
            continue
        if re.match(r"^```\s*(도식|diagram|flow)", s) or re.fullmatch(r"```\s*\w*", s):
            # 이름표 없는 「```」 도 덩이로 — 글자 그대로 문서에 들어갔다(10-30 「``` [사업 신청] ↓ [선정 평가]」)
            plain_fence = not re.match(r"^```\s*(도식|diagram|flow)", s)
            if not plain_fence:
                flush_text()
            if table:
                items.append(("table", table))
                table = []
            fence = []
            continue
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            flush_text()
            if re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", s):
                continue
            table.append([wf.HTML_BREAK.sub("\n", c).strip() for c in s.strip("|").split("|")])   # 칸 안 「<br>」 → 칸 안 줄바꿈
            continue
        if table:
            items.append(("table", table))
            table = []
        buf.append(wf.HTML_BREAK.sub("\n", ln))
    if table:
        items.append(("table", table))
    if fence:
        if plain_fence and not any(a in "\n".join(fence) for a in ("↓", "→", "->", "⇒", "▶")):
            buf.extend(fence)
        else:
            items.append(("diagram", "\n".join(fence)))
    flush_text()
    return items



def section_contexts(data: bytes, before: int = 900, after: int = 300) -> dict[str, str]:
    """필드마다 앞뒤의 양식 글 — 제목·작성방법 상자·예시처럼 그 자리를 쓸 때 볼 안내(앞 필드 끝 ~ 이 필드, 이 필드 끝 ~ 다음 필드).
    서브 에이전트(절 맡기기)가 그 절의 양식 글·작성방법만 보고 쓰게 하고, 상태 글이 절마다 작성방법이 요구한 요소를 보이는 데 쓴다."""
    def txt(frag: str) -> str:
        frag = re.sub(r"</hp:p>|<hp:lineBreak/>", "\n", frag)
        t = re.sub(r"<[^>]+>", "", frag)
        t = t.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
        return "\n".join(ln.strip() for ln in t.splitlines() if ln.strip())
    _, secs = _open(data)
    out: dict[str, str] = {}
    for n in sorted(secs):
        xml = secs[n]
        allb = list(_BEGIN.finditer(xml))
        begins = [m for m in allb if not is_cont(_unq(m.group(2)))]       # 이어 쓰기 필드는 제 절의 글이라 자리 경계가 아니다
        prev_end = 0
        for k, m in enumerate(begins):
            sp = _span(xml, m.group(1), m.end())
            nxt = begins[k + 1].start() if k + 1 < len(begins) else len(xml)
            if k:
                prev_end = max([prev_end] + [s_[1] for c in allb if begins[k - 1].start() < c.start() < m.start()
                                             and (s_ := _span(xml, c.group(1), c.end()))])
            head = txt(xml[prev_end:m.start()])[-before:]
            tail = txt(xml[(sp[1] if sp else m.end()):nxt])[:after]
            out.setdefault(_unq(m.group(2)), (head + ("\n[이 자리에 쓴다]\n" if head or tail else "") + tail).strip())
            prev_end = sp[1] if sp else m.end()
    return out


def section_body(data: bytes, name: str, pids=()) -> str:
    """절의 지금 글(문서 차례대로) — 절 필드 안 글, 그 뒤에 넣은 표(pids, 칸 글을 한 줄로)·이어 쓰기 필드 글. 절 필드가 없으면 빈 글."""
    _, secs = _open(data)
    for n in sorted(secs):
        xml = secs[n]
        hit = _find(xml, name)
        if not hit:
            continue
        pb = _para_bounds(xml, hit.start())
        a = pb[0] if pb else hit.start()
        sp = _span(xml, hit.group(1), hit.end())
        b = xml.find("</hp:p>", sp[1]) + 7 if sp else (pb[1] if pb else hit.end())
        for m in _BEGIN.finditer(xml):
            if is_cont(_unq(m.group(2))) and section_of(_unq(m.group(2))) == name and m.start() > a:
                s_ = _span(xml, m.group(1), m.end())
                if s_:
                    b = max(b, xml.find("</hp:p>", s_[1]) + 7)
        for pid in pids:
            m = re.search(rf'<hp:p id="{re.escape(str(pid))}"[^>]*>', xml)
            if not m or m.start() < a:
                continue
            depth = 0
            for t in re.finditer(r"<hp:p\b|</hp:p>", xml[m.start():]):
                depth += 1 if t.group(0) != "</hp:p>" else -1
                if depth == 0:
                    b = max(b, m.start() + t.end())
                    break
        frag = re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", xml[a:b], flags=re.S)
        frag = re.sub(r"<hp:tbl\b.*?</hp:tbl>", lambda t: " ".join(re.sub(r"<[^>]+>", " ", t.group(0)).split()), frag, flags=re.S)
        frag = re.sub(r"</hp:p>|<hp:lineBreak\s*/>", "\n", frag)
        return "\n".join(ln.strip() for ln in _unq(re.sub(r"<[^>]+>", "", frag)).split("\n") if ln.strip())
    return ""


def section_context(data: bytes, name: str, before: int = 900, after: int = 300) -> str:
    """한 필드의 앞뒤 양식 글(section_contexts 참고). 필드가 없으면 빈 글."""
    return section_contexts(data, before, after).get(name, "")


_CMD = re.compile(r'(<hp:stringParam name="Command"[^>]*>)Clickhere:set:\d+:Direction:wstring:\d+:(.*?) HelpState:wstring:(\d+):(.*?)(</hp:stringParam>)', re.S)


def quiet_hints(data: bytes, only: set[str] | None = None) -> tuple[bytes, list[str]]:
    """밑에 이미 내용(표 글·그림·글 문단)이 들어간 빈 절 필드의 안내 글(「본문 입력」)을 지운다 — (새 바이트, 지운 필드 이름).
    표·그림으로 채운 절도 절 필드 자체는 비어 있어 기안기가 빨간 「본문 입력」 을 띄워, 다 쓴 문서가 「영역 지정만 된」 꼴로 보였다(11-13 4차, 사용자 지적).
    필드는 그대로 둔다(공동 편집 칸·나중에 쓸 자리) — 안내 글만 비운다.
    only: 실제로 채운 절(쓰기 기록·표·그림·칸 값을 넣은 절)만 — 서식의 견본 표·작성방법 상자도 글이라, 아직 안 쓴 절의 안내까지 지우면 안 된다."""
    zin, secs = _open(data)
    changed: dict[str, str] = {}
    done: list[str] = []
    for n, xml in secs.items():
        begins = [m for m in _BEGIN.finditer(xml) if _unq(m.group(2)).startswith("절:") and not is_cont(_unq(m.group(2)))]
        if not begins:
            continue
        out, last = [], 0
        for i, m in enumerate(begins):
            sp = _span(xml, m.group(1), m.end())
            own = re.sub(r"<[^>]+>", "", _PARAMS.sub("", xml[sp[0]:sp[1]])).strip() if sp else ""
            hint = re.search(r'<hp:stringParam name="Direction">([^<]*)</hp:stringParam>', m.group(0))
            if not hint or not hint.group(1).strip() or (own and own != _unq(hint.group(1)).strip()):
                continue
            if only is not None and _unq(m.group(2)) not in only:
                continue
            end = begins[i + 1].start() if i + 1 < len(begins) else len(xml)
            region = xml[(sp[1] if sp else m.end()):end]
            region_txt = re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", region, flags=re.S)
            has = ("<hp:pic" in region or
                   any(t.strip() for t in re.findall(r"<hp:t\b[^>]*>([^<]*)</hp:t>", region_txt)))
            if not has:
                continue
            seg = m.group(0)
            seg2 = seg.replace(hint.group(0), '<hp:stringParam name="Direction"></hp:stringParam>')

            def _cmd(mm):
                rest = f"Direction:wstring:0: HelpState:wstring:{mm.group(3)}:{mm.group(4)}"
                return f"{mm.group(1)}Clickhere:set:{len(rest)}:{rest}{mm.group(5)}"
            seg2 = _CMD.sub(_cmd, seg2)
            out.append(xml[last:m.start()] + seg2)
            last = m.end()
            done.append(_unq(m.group(2)))
        if out:
            out.append(xml[last:])
            changed[n] = "".join(out)
    if not changed:
        return data, []
    return _pack(zin, changed), done


def _sec_order(secs: dict[str, str]) -> list[str]:
    return sorted(secs, key=lambda n: int(m.group(1)) if (m := re.search(r"(\d+)\.xml$", n)) else 0)


def section_before(data: bytes, sec: str, offset: int) -> str | None:
    """문서의 한 자리(구역, 글자 위치) 바로 앞의 절 필드 이름 — 그 자리를 품은 절. 표·그림·칸 값을 넣은 곳이 어느 절인지 적는 데 쓴다."""
    _, secs = _open(data)
    last = None
    for n in _sec_order(secs):
        for m in _BEGIN.finditer(secs[n]):
            if n == sec and m.start() >= offset:
                return last
            name = _unq(m.group(2))
            if name.startswith("절:") and not is_cont(name):
                last = name
        if n == sec:
            return last
    return last


def section_of_field(data: bytes, name: str) -> str | None:
    """필드가 든 절(절 필드면 그 자신)."""
    if name.startswith("절:"):
        return name
    _, secs = _open(data)
    for n in _sec_order(secs):
        for m in _BEGIN.finditer(secs[n]):
            if _unq(m.group(2)) == name:
                return section_before(data, n, m.start())
    return None


def section_of_text(data: bytes, text: str) -> str | None:
    """이 글이 처음 나오는 문단이 든 절."""
    key = re.sub(r"\s+", "", text or "")[:40]
    if not key:
        return None
    _, secs = _open(data)
    for n in _sec_order(secs):
        xml = secs[n]
        for pm in re.finditer(r"<hp:p\b.*?</hp:p>", xml, flags=re.S):
            if key in re.sub(r"\s+", "", "".join(re.findall(r"<hp:t\b[^>]*>([^<]*)</hp:t>", pm.group(0)))):
                return section_before(data, n, pm.start())
    return None


def drop_empty_box(data: bytes, name: str) -> tuple[bytes, bool]:
    """절 필드 바로 뒤(빈 문단은 건너뜀)의 빈 작성 상자(칸 하나짜리 빈 표)를 뗀다 — (새 바이트, 뗐나).
    서식이 「4-4. 기타 …」 제목 아래 빈 상자만 두고 그 안에 쓰게 한 자리(11-13 8차). 본문을 절 필드에 쓰면 상자가 빈 채 아래에 남아,
    처음 쓰기 전에 뗀다. 글·그림·안쪽 표·필드가 든 상자, 칸이 둘 넘는 표는 건드리지 않는다. 필드가 표 칸 안이면 그대로 둔다."""
    zin, secs = _open(data)
    for n, xml in secs.items():
        hit = _find(xml, name)
        if not hit:
            continue
        pb = _para_bounds(xml, hit.start())
        if not pb:
            return data, False
        head = xml[:pb[0]]
        if len(re.findall(r"<hp:p\b", head)) != head.count("</hp:p>"):
            return data, False                              # 표 칸 안의 필드
        tail = xml[pb[1]:]
        for a, b in _top_paras(tail, len(tail)):
            frag = tail[a:b]
            if "<hp:tbl" not in frag:
                if wf._text(frag).strip() or "<hp:pic" in frag or _BEGIN.search(frag):
                    return data, False
                continue                                    # 빈 문단
            tbl = frag[frag.find("<hp:tbl"):]
            if (frag.count("<hp:tbl") == 1 and len(re.findall(r"<hp:tc\b", tbl)) == 1 and not wf._text(frag).strip()
                    and "<hp:pic" not in frag and not _BEGIN.search(frag)):
                secs[n] = xml[:pb[1] + a] + xml[pb[1] + b:]
                return _pack(zin, secs), True
            return data, False
        return data, False
    return data, False
