"""기안기 작업본에 쓰기(서버 쪽) — 에이전트 도구가 브라우저 없이 한글 파일(hwpx)의 누름틀 필드에 값을 넣고 절 본문(문단·표)을 쓴다.

- fields(data): 필드 목록과 지금 값 — [{"name", "kind"(절·칸·표·기타), "text"}]
- set_fields(data, {이름: 값}): 칸·표 필드 값 바꾸기(줄바꿈은 한 칸 안 줄 나눔)
- write_section(data, 이름, items, written): 절 필드가 든 문단 바로 뒤에 문단·표를 넣는다. items = [("text", 글) | ("table", 행렬)].
  앞서 같은 절에 넣은 문단(written[이름] 의 문단 id)은 지우고 새로 넣는다 — 「다시 써 줘」 가 덧붙이지 않게.
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


def _kind(name: str) -> str:
    return "절" if name.startswith("절:") else "칸" if name.startswith("칸:") else "표" if re.match(r"표\d+:", name) else "기타"


def _span(xml: str, bid: str, start: int) -> tuple[int, int] | None:
    """필드 여는 ctrl 끝 ~ 닫는 ctrl 시작(그 사이가 필드 값)."""
    m = re.search(rf'<hp:ctrl><hp:fieldEnd\b[^>]*beginIDRef="{bid}"[^>]*/></hp:ctrl>', xml[start:])
    return (start, start + m.start()) if m else None


def fields(data: bytes) -> list[dict]:
    _, secs = _open(data)
    out = []
    for n in sorted(secs):
        xml = secs[n]
        for m in _BEGIN.finditer(xml):
            sp = _span(xml, m.group(1), m.end())
            text = re.sub(r"<[^>]+>", "", xml[sp[0]:sp[1]]).strip() if sp else ""
            name = _unq(m.group(2))
            out.append({"name": name, "kind": _kind(name), "text": text})
    return out


def _value_xml(text: str) -> str:
    lines = wf.clean_text(text).split("\n")
    return "<hp:t>" + "<hp:lineBreak/>".join(escape(x) for x in lines) + "</hp:t>"


def set_fields(data: bytes, values: dict[str, str]) -> tuple[bytes, list[str]]:
    """칸·표 필드 값 바꾸기 — 돌려주는 것은 (새 바이트, 넣은 필드 이름). 없는 이름은 건너뛴다."""
    zin, secs = _open(data)
    if not isinstance(values, dict):
        raise ValueError("values 는 {필드 이름: 값} 이다")
    want = {str(k): ("" if v is None else wf.clean_text(v)) for k, v in values.items() if k}
    done: list[str] = []
    new = {}
    for n, xml in secs.items():
        pos, buf = 0, []
        for m in _BEGIN.finditer(xml):
            name = _unq(m.group(2))
            if name not in want or name in done:
                continue
            sp = _span(xml, m.group(1), m.end())
            if not sp:
                continue
            buf.append(xml[pos:sp[0]])
            buf.append(_value_xml(want[name]))
            pos = sp[1]
            done.append(name)
        if buf:
            buf.append(xml[pos:])
            names = set(done)
            # 값이 바뀐 필드의 문단만 줄 배치 캐시를 지운다(편집기가 그 문단만 다시 배치)
            new[n] = wf.strip_lineseg_in("".join(buf), lambda p: any(_unq(b.group(2)) in names for b in _BEGIN.finditer(p)))
    return (_pack(zin, new) if new else data), done


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

    def pp(self, left: int, hang: int) -> str:
        key = (left, hang)
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
        """줄마다 (문단 모양 id, 글) — 단계는 절 제목(base_level) 아래부터 센다. rel_min 은 본문 전체의 가장 얕은 단계
        (표·도식으로 끊긴 글 덩이마다 따로 세면 같은 「2)」 가 덩이마다 다르게 들여써진다)."""
        out, cont_left = [], 0
        if rel_min is None:
            rel_min = min((lv for lv in (marker_level(x) for x in lines) if lv is not None), default=base_level + 1)
        for ln in lines:
            lv = marker_level(ln)
            if lv is None:
                out.append((self.pp(cont_left, 0) if cont_left else self.base, ln.strip()))
                continue
            depth = max(0, lv - min(rel_min, base_level + 1)) + 1          # 절 제목 바로 아래 단계가 한 글자 들여쓰기
            mk = re.match(r"^\s*(\S+)", ln).group(1)
            hang = int(self.ch * (len(mk) * 0.55 + 0.5))                 # 부호 폭(대략 반각 글자) + 빈칸
            left = depth * self.ch
            cont_left = left + hang
            out.append((self.pp(left, hang), mk + " " + ln.strip()[len(mk):].strip()))
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


def _drop_empty_form_tables(tail: str) -> str:
    """본문이 표를 들고 왔을 때, 같은 절(다음 절 필드 앞까지)에 남은 양식의 빈 표(머리줄 말고는 글자 없는 표)를 지운다 —
    본문 표 밑에 같은 꼴 빈 표가 겹쳐 남지 않게. 글자가 한 칸이라도 든 표는 손대지 않는다."""
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
        if len(rows) < 2:
            continue
        body_txt = "".join(re.findall(r"<hp:t\b[^>]*>([^<]*)</hp:t>", "".join(rows[1:])))
        if re.sub(r"\s+", "", body_txt):
            continue
        if re.sub(r"\s+", "", "".join(re.findall(r"<hp:t\b[^>]*>([^<]*)</hp:t>", frag[:frag.find("<hp:tbl")]))):
            continue                                       # 표 앞에 글이 같이 든 문단은 두고
        cut.append((a, b))
    for a, b in reversed(cut):
        tail = tail[:a] + tail[b:]
    return tail


def write_section(data: bytes, name: str, items: list[tuple[str, object]], written: dict[str, list[str]] | None = None
                  ) -> tuple[bytes, list[str]]:
    """절 필드 다음에 문단·표를 넣는다 — (새 바이트, 넣은 문단 id). 앞서 넣은 것은 지운다."""
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
    old_ids = set((written or {}).get(name, []))
    ids = wf._Ids([header, *secs.values()])
    new: dict[str, str] = {}
    put: list[str] = []
    diagrams: list[str] = []
    diagram_pids: set[str] = set()
    for n, xml in secs.items():
        hit = next((m for m in _BEGIN.finditer(xml) if _unq(m.group(2)) == name), None)
        if not hit:
            continue
        if old_ids:                                          # 앞서 이 절에 넣은 문단(표 문단 포함)을 지운다
            for pid in old_ids:
                m = re.search(rf'<hp:p id="{pid}"[^>]*>', xml)
                if not m:
                    continue
                depth, i = 0, m.start()
                for t in re.finditer(r"<hp:p\b|</hp:p>", xml[m.start():]):
                    depth += 1 if t.group(0) != "</hp:p>" else -1
                    if depth == 0:
                        xml = xml[:m.start()] + xml[m.start() + t.end():]
                        break
            hit = next((m for m in _BEGIN.finditer(xml) if _unq(m.group(2)) == name), None)
        # 본문 첫 줄은 절 필드 안에 넣는다 — 빈 필드의 안내 글(「본문 입력」)이 본문 위에 남지 않게. 나머지는 그 문단 뒤에
        items = list(items)
        first = ""
        if items and items[0][0] == "text":
            lines = hf.split_lines(str(items[0][1]))
            if lines:
                first, rest = lines[0], "\n".join(lines[1:])
                items[0] = ("text", rest)
        sp = _span(xml, hit.group(1), hit.end())
        if sp:
            xml = xml[:sp[0]] + _value_xml(first) + xml[sp[1]:]
            hit = next((m for m in _BEGIN.finditer(xml) if _unq(m.group(2)) == name), None)
        pb = _para_bounds(xml, hit.start())
        if not pb:
            continue
        head_text = re.sub(r"<[^>]+>", "", name)[2:] if name.startswith("절:") else ""
        head_level = marker_level(head_text) if marker_level(head_text) is not None else 0
        # 새 문단의 글자는 그 자리(필드가 든 문단)의 글자 모양을 잇는다 — 양식 칸이 11pt 면 덧붙는 줄도 11pt
        here = re.search(r'<hp:run\b[^>]*\bcharPrIDRef="(\d+)"', xml[pb[0]:pb[1]])
        cp = here.group(1) if here else cp
        levels = _LevelStyles(header, pp, hangs.char_height(cp))
        bf, margin = hf._table_template(xml, solid)
        if bf is None:
            header, bf = hf._inject_border_fill(header)
            margin = margin or '<hp:cellMargin left="510" right="510" top="141" bottom="141"/>'
        width = _cell_width(xml, hit.start()) or hf._text_width_hu(xml)
        chunks = []
        form_marks = bool(para_styles.pick("1) 예") or para_styles.pick("가) 예"))
        all_lines = ([first] if first else []) + [ln for k, v in items if k == "text" for ln in hf.split_lines(str(v))]
        rel = min((lv for lv in (marker_level(x) for x in all_lines) if lv is not None), default=head_level + 1)
        if first and not form_marks and pb:
            # 필드 안의 첫 줄도 뒤 줄과 같은 단계 모양으로(첫 줄만 왼쪽에 붙어 보이지 않게)
            lp0, line0 = levels.for_lines([first], head_level, rel)[0]
            p_open = re.match(r"<hp:p\b[^>]*>", xml[pb[0]:])
            if p_open:
                new_open = re.sub(r'paraPrIDRef="\d+"', f'paraPrIDRef="{lp0}"', p_open.group(0), count=1)
                xml = xml[:pb[0]] + new_open + xml[pb[0] + len(p_open.group(0)):]
                pb = (pb[0], pb[1] + len(new_open) - len(p_open.group(0)))
        for kind, val in items:
            if kind == "text":
                lines = hf.split_lines(str(val)) if str(val).strip() else []
                if form_marks:
                    # 양식에 단계별 본문 예시가 있으면 그 모양(서식 작성자의 뜻)을 따른다
                    for line in lines:
                        pid = ids.new()
                        lp, lc = para_styles.pick(line) or (pp, cp)
                        if hf.marker_of(line) and len(line) > hf._ParaStyles.SHORT:
                            lp, tab_hu = hangs.hanging(lp, lc, line[:hf._MARK.match(line).end()].strip())
                            px = hf.marker_paragraph_xml(line, lp, lc, tab_hu)
                        else:
                            px = hf.paragraph_xml(line, lp, lc)
                        chunks.append(px.replace('<hp:p id="0"', f'<hp:p id="{pid}"', 1))
                        put.append(pid)
                else:
                    # 예시가 없는 양식 — 공문서 단계 규칙으로 들여쓰기·내어쓰기
                    for lp, line in levels.for_lines(lines, head_level, rel):
                        pid = ids.new()
                        chunks.append(hf.paragraph_xml(line, lp, cp).replace('<hp:p id="0"', f'<hp:p id="{pid}"', 1))
                        put.append(pid)
            elif kind == "diagram" and str(val).strip():
                # 도식은 표시 문단으로 넣고 다 쓴 뒤 그 자리를 도식 표로 바꾼다(webhwp_style.diagram_table — header 모양 등록이 한 곳에서)
                pid = ids.new()
                diagrams.append(str(val))
                diagram_pids.add(pid)
                chunks.append(hf.paragraph_xml(f"⟦도식{len(diagrams) - 1}⟧", pp, cp).replace('<hp:p id="0"', f'<hp:p id="{pid}"', 1))
                put.append(pid)
            elif kind == "table" and isinstance(val, list) and val:
                rows = [[str(c) for c in r] for r in val]
                pid = ids.new()
                chunks.append(hf.table_xml(rows, pp, cp, width, bf, margin, int(ids.new()), None, roles)
                              .replace('<hp:p id="0"', f'<hp:p id="{pid}"', 1))
                put.append(pid)
        # 줄 배치 캐시는 글이 바뀐 필드 문단에서만 지운다(새 문단·표는 처음부터 없다). 문서 전체에서 지우면 상자 밖으로 글이 넘친다
        field_p = hf.strip_linesegs(xml[pb[0]:pb[1]])[0]
        head_ = xml[:pb[0]] + field_p + "".join(chunks)
        tail_ = xml[pb[1]:]
        if any(k == "table" for k, _ in items):
            tail_ = _drop_empty_form_tables(tail_)
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


def parse_body(text: str) -> list[tuple[str, object]]:
    """에이전트가 쓴 절 본문(글 + 마크다운 표 | a | b | + ```도식 블록) → items. 표 구분 줄(|---|)은 버린다."""
    items: list[tuple[str, object]] = []
    lines = wf.clean_text(text).split("\n")
    buf: list[str] = []
    table: list[list[str]] = []
    fence: list[str] | None = None

    def flush_text():
        if buf and any(x.strip() for x in buf):
            items.append(("text", "\n".join(x for x in buf if x.strip())))
        buf.clear()

    for ln in lines:
        s = ln.strip()
        if fence is not None:                               # ```도식 … ``` 블록
            if s.startswith("```"):
                items.append(("diagram", "\n".join(fence)))
                fence = None
            else:
                fence.append(ln)
            continue
        if re.match(r"^```\s*(도식|diagram|flow)", s):
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
            table.append([c.strip() for c in s.strip("|").split("|")])
            continue
        if table:
            items.append(("table", table))
            table = []
        buf.append(ln)
    if table:
        items.append(("table", table))
    if fence:
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
        begins = list(_BEGIN.finditer(xml))
        prev_end = 0
        for k, m in enumerate(begins):
            sp = _span(xml, m.group(1), m.end())
            nxt = begins[k + 1].start() if k + 1 < len(begins) else len(xml)
            head = txt(xml[prev_end:m.start()])[-before:]
            tail = txt(xml[(sp[1] if sp else m.end()):nxt])[:after]
            out.setdefault(_unq(m.group(2)), (head + ("\n[이 자리에 쓴다]\n" if head or tail else "") + tail).strip())
            prev_end = sp[1] if sp else m.end()
    return out


def section_context(data: bytes, name: str, before: int = 900, after: int = 300) -> str:
    """한 필드의 앞뒤 양식 글(section_contexts 참고). 필드가 없으면 빈 글."""
    return section_contexts(data, before, after).get(name, "")
