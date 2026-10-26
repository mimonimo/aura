"""한글 작업본 편집 도구 — 대화 에이전트가 기안기 기능(페이지 설정·표 만들기·표 편집·쪽 번호·머리말·글자·문단·각주·그림)을 쓰게 한다.

두 갈래.
- 파일 직접 고치기(A): 서버가 hwpx XML 을 고친다. 기안기가 열려 있지 않아도 된다. 고친 문단만 줄 배치 캐시(linesegarray)를 지우고
  (webhwp_fields.strip_lineseg_in 과 같은 규칙), header 의 모양 목록은 실제 항목 수로 itemCnt 를 다시 센다(webhwp_style._Header).
  고치기 전에 지금 파일을 판(.판/)으로 남기고, 고친 결과가 자가 점검(hwpx_fill.check)을 통과할 때만 저장한다 — 실패하면 파일은 그대로.
- 열린 기안기에 명령 보내기(B): 명령 줄(설정 webhwp_cmd:<id>)에 넣으면 편집 화면이 1~2초마다 가져가 기안기 동작(CreateAction·Run)으로
  실행하고 결과를 돌려준다. 동작은 기안기에서 실제로 확인한 허용 목록(LIVE_ACTIONS)만 — 파일 열기·저장·인쇄·외부 주소는 없다.

판단(무엇을 어디에 얼마나)은 에이전트가 하고, 이 모듈은 실행·관문·기록만 한다. 단위는 사람 기준 — 표의 행·열은 1부터, 길이는 mm.
HWPUNIT = mm × 283.465.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from zzaimy.app import webhwp
from zzaimy.app import webhwp_fields as wf
from zzaimy.app import webhwp_native as _nat
from zzaimy.app import webhwp_style as ws
from zzaimy.export import house_style as hs
from zzaimy.ingest import hwpx_fill as hf

MM = 283.465                                      # HWPUNIT / mm


def hu(mm: float) -> int:
    return int(round(float(mm) * MM))


def mm_of(v: int) -> float:
    return round(int(v) / MM, 1)


class EditError(ValueError):
    """에이전트에게 돌려줄 까닭 — 파일은 바꾸지 않는다."""


class ArgError(EditError):
    """인자를 읽다가 생각지 못한 꼴(글 자리에 사전, 숫자 자리에 글 등)을 만났다 — 까닭을 돌려주고 파일은 그대로. 무작위 시험이 이것을 고장으로 센다."""


def _guarded(fn):
    """연산 입구 — 인자 안의 글을 문서에 넣을 수 있는 꼴로(webhwp_fields.clean_args), 생각지 못한 예외는 ArgError 로.
    연산은 바이트 → 바이트라 실패하면 들어온 파일이 그대로 남는다."""
    import functools

    @functools.wraps(fn)
    def run(data, *args):
        args = tuple(wf.clean_args(x) if isinstance(x, (dict, list, str)) else x for x in args)
        try:
            return fn(data, *args)
        except EditError:
            raise
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, zipfile.BadZipFile) as e:
            raise ArgError(f"인자를 읽지 못했다({type(e).__name__}: {str(e)[:80]}) — 도구 설명의 꼴대로 다시 준다.") from e
    return run


# 보안(자원): 한 표·한 파일의 크기 상한 — 도구를 되풀이 불러 표나 파일을 끝없이 키우지 못하게
MAX_ROWS = 300            # 표 하나의 행
MAX_COLS = 30             # 표 하나의 열
MAX_CELLS = 3000          # 표 하나의 칸
MAX_FILE = 30 * 1024 * 1024   # 고친 뒤 hwpx 크기
MAX_UNZIPPED = 120 * 1024 * 1024  # 열 때 풀린 전체 크기


def _int(v, name: str, lo: int, hi: int, default: int | None = None) -> int:
    """숫자 인자 — 비었으면 default, 글·소수·범위 밖이면 까닭."""
    if v in (None, "") and default is not None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise EditError(f"{name} 는 숫자다({lo}~{hi}).")
    if f != int(f) or not lo <= int(f) <= hi:
        raise EditError(f"{name} 는 {lo}~{hi} 사이의 정수다.")
    return int(f)


# ── 용지·여백 ──
PAPERS = {"A4": (210, 297), "A3": (297, 420), "A5": (148, 210), "B4": (257, 364), "B5": (182, 257),
          "Letter": (215.9, 279.4), "Legal": (215.9, 355.6)}
# 여백 프리셋(mm) — 보통은 한글 새 문서 기본값, 좁게·넓게는 학교 문서에서 흔한 두 꼴
MARGIN_PRESETS = {
    "보통": {"top": 20, "bottom": 15, "left": 30, "right": 30, "header": 15, "footer": 15},
    "좁게": {"top": 10, "bottom": 10, "left": 15, "right": 15, "header": 10, "footer": 10},
    "넓게": {"top": 25, "bottom": 20, "left": 35, "right": 35, "header": 15, "footer": 15},
}
_MARGIN_KEYS = ("top", "bottom", "left", "right", "header", "footer", "gutter")
_GUTTER_TYPES = {"왼쪽": "LEFT_ONLY", "맞쪽": "LEFT_RIGHT", "위쪽": "TOP_BOTTOM", "LEFT_ONLY": "LEFT_ONLY",
                 "LEFT_RIGHT": "LEFT_RIGHT", "TOP_BOTTOM": "TOP_BOTTOM"}
ALIGNS = {"가운데": "CENTER", "왼쪽": "LEFT", "오른쪽": "RIGHT", "양쪽": "JUSTIFY", "배분": "DISTRIBUTE",
          "CENTER": "CENTER", "LEFT": "LEFT", "RIGHT": "RIGHT", "JUSTIFY": "JUSTIFY", "DISTRIBUTE": "DISTRIBUTE"}
VALIGNS = {"위": "TOP", "가운데": "CENTER", "아래": "BOTTOM", "TOP": "TOP", "CENTER": "CENTER", "BOTTOM": "BOTTOM"}
PAGE_NUM_POS = {"위 왼쪽": "TOP_LEFT", "위 가운데": "TOP_CENTER", "위 오른쪽": "TOP_RIGHT",
                "아래 왼쪽": "BOTTOM_LEFT", "아래 가운데": "BOTTOM_CENTER", "아래 오른쪽": "BOTTOM_RIGHT",
                "바깥 위": "OUTSIDE_TOP", "바깥 아래": "OUTSIDE_BOTTOM", "안쪽 위": "INSIDE_TOP", "안쪽 아래": "INSIDE_BOTTOM"}
PAGE_NUM_FMT = {"1": "DIGIT", "①": "CIRCLED_DIGIT", "I": "ROMAN_CAPITAL", "i": "ROMAN_SMALL", "A": "LATIN_CAPITAL",
                "a": "LATIN_SMALL", "가": "HANGUL_SYLLABLE", "一": "IDEOGRAPH"}


# ── 문서 열기·담기 ──
_SEC = re.compile(r"Contents/section(\d+)\.xml$")


class _Hdr(ws._Header):
    """webhwp_style 의 header 더하기(같은 모양은 한 번만) + 글꼴·줄 간격·여백·채움 복제."""

    def font_id(self, face: str) -> str:
        """글꼴 이름 → 글꼴 id(언어 묶음마다 같은 번호가 되게 없으면 모든 묶음에 더한다)."""
        key = ("font", face)
        if key in self.cache:
            return self.cache[key]
        groups = list(re.finditer(r'<hh:fontface\b[^>]*>.*?</hh:fontface>', self.xml, re.S))
        hit = None
        for g in groups:
            m = re.search(rf'<hh:font\b[^>]*\bid="(\d+)"[^>]*\bface="{re.escape(escape(face, {chr(34): "&quot;"}))}"', g.group(0))
            if m:
                hit = m.group(1)
                break
        if hit is not None and all(re.search(rf'<hh:font\b[^>]*\bid="{hit}"[^>]*\bface="{re.escape(face)}"', g.group(0)) for g in groups):
            self.cache[key] = hit
            return hit
        new_id = str(max([int(x) for g in groups for x in re.findall(r'<hh:font\b[^>]*\bid="(\d+)"', g.group(0))] or [-1]) + 1)
        out, pos = [], 0
        for g in groups:
            body = g.group(0)
            font = (f'<hh:font id="{new_id}" face="{escape(face, {chr(34): "&quot;"})}" type="TTF" isEmbedded="0">'
                    '<hh:typeInfo familyType="FCAT_GOTHIC" weight="6" proportion="4" contrast="0" strokeVariation="1" armStyle="1" '
                    'letterform="1" midline="1" xHeight="1"/></hh:font>')
            body = body.replace("</hh:fontface>", font + "</hh:fontface>")
            n = len(re.findall(r"<hh:font\b", body))
            body = re.sub(r'(<hh:fontface\b[^>]*\bfontCnt=")\d+', rf"\g<1>{n}", body, count=1)
            out.append(self.xml[pos:g.start()] + body)
            pos = g.end()
        out.append(self.xml[pos:])
        self.xml = "".join(out)
        self.cache[key] = new_id
        return new_id

    def char_ex(self, base: str, *, pt: float | None = None, face: str = "", bold: bool | None = None) -> str:
        key = ("cpx", base, pt, face, bold)
        if key in self.cache:
            return self.cache[key]
        m = re.search(rf'<hh:charPr\b[^>]*?\bid="{base}"[^>]*>.*?</hh:charPr>', self.xml, re.S)
        if not m:
            return base
        fid = self.font_id(face) if face else ""
        body = re.sub(r'\bid="\d+"', f'id="{self._next_id("charPr")}"', m.group(0), count=1)
        if pt:
            body = re.sub(r'\bheight="\d+"', f'height="{int(round(float(pt) * 100))}"', body, count=1)
        if fid:
            body = re.sub(r"<hh:fontRef\b[^>]*/>", f'<hh:fontRef hangul="{fid}" latin="{fid}" hanja="{fid}" japanese="{fid}" '
                          f'other="{fid}" symbol="{fid}" user="{fid}"/>', body, count=1)
        if bold is True and "<hh:bold/>" not in body:
            body = body.replace("<hh:underline", "<hh:bold/><hh:underline", 1)
        elif bold is False:
            body = body.replace("<hh:bold/>", "")
        new_id = self._append("charProperties", body)
        self.cache[key] = new_id
        return new_id

    def para_ex(self, base: str, *, align: str = "", line: int | None = None, left: int | None = None,
                indent: int | None = None) -> str:
        """문단 모양 복제 — align(가로 정렬), line(줄 간격 %), left·indent(HWPUNIT, 내어쓰기는 음수)."""
        key = ("ppx", base, align, line, left, indent)
        if key in self.cache:
            return self.cache[key]
        m = re.search(rf'<hh:paraPr\b[^>]*?\bid="{base}"[^>]*>.*?</hh:paraPr>', self.xml, re.S)
        if not m:
            return base
        body = re.sub(r'\bid="\d+"', f'id="{self._next_id("paraPr")}"', m.group(0), count=1)
        if align:
            body = re.sub(r'(<hh:align horizontal=")[A-Z_]+', rf"\g<1>{align}", body, count=1)

        def fix(block: str, div: int) -> str:
            if line is not None:
                block = re.sub(r'(<hh:lineSpacing type=")[A-Z_]+(" value=")-?\d+', rf"\g<1>PERCENT\g<2>{int(line)}", block, count=1)
            if left is not None:
                block = re.sub(r'(<hc:left value=")-?\d+', rf"\g<1>{int(left) // div}", block, count=1)
            if indent is not None:
                block = re.sub(r'(<hc:intent value=")-?\d+', rf"\g<1>{int(indent) // div}", block, count=1)
            return block
        # 한글은 HwpUnitChar 판(case)에 반값을, 기본(default)에 온값을 쓴다(webhwp_write._LevelStyles 와 같은 규칙)
        case = re.search(r"<hp:case\b.*?</hp:case>", body, re.S)
        if case:
            body = body[:case.start()] + fix(case.group(0), 2) + body[case.end():]
        dflt = re.search(r"<hp:default>.*?</hp:default>", body, re.S)
        if dflt:
            body = body[:dflt.start()] + fix(dflt.group(0), 1) + body[dflt.end():]
        if not case and not dflt:
            body = fix(body, 1)
        new_id = self._append("paraProperties", body)
        self.cache[key] = new_id
        return new_id

    def fill_clone(self, base: str, color: str) -> str:
        """테두리 모양 복제에 음영만 바꾼다(color 'none' 이면 음영 없앰)."""
        key = ("fill", base, color)
        if key in self.cache:
            return self.cache[key]
        m = re.search(rf'<hh:borderFill\b[^>]*?\bid="{base}"[^>]*>.*?</hh:borderFill>', self.xml, re.S)
        if not m:
            return self.border(ws.HOUSE["inner"], ws.HOUSE["inner"], ws.HOUSE["inner"], ws.HOUSE["inner"], color)
        body = re.sub(r'\bid="\d+"', f'id="{self._next_id("borderFill")}"', m.group(0), count=1)
        body = re.sub(r"<hc:fillBrush>.*?</hc:fillBrush>|<hh:fillInfo>.*?</hh:fillInfo>|<hh:fillInfo/>", "", body, flags=re.S)
        if color and color != "none":
            body = body.replace("</hh:borderFill>", f'<hc:fillBrush><hc:winBrush faceColor="{color}" hatchColor="#999999" alpha="0"/></hc:fillBrush></hh:borderFill>')
        new_id = self._append("borderFills", body)
        self.cache[key] = new_id
        return new_id


class _Doc:
    """hwpx 하나 — 구역 XML·header·매니페스트를 열고, 바뀐 것만 다시 담는다(항목 순서·압축 방식 유지)."""

    def __init__(self, data: bytes):
        try:
            self.zin = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise EditError("hwpx 파일이 아니다(옛 hwp 는 기안기에서 한 번 저장하면 hwpx 가 된다).")
        if len(data) > MAX_FILE or sum(i.file_size for i in self.zin.infolist()) > MAX_UNZIPPED:
            raise EditError("문서가 너무 커서 이 도구로 고치지 않는다(상한 초과).")
        try:
            wf.check_zip(self.zin)
        except ValueError as e:
            raise EditError(str(e))
        names = self.zin.namelist()
        self.sec_names = sorted((n for n in names if _SEC.search(n)), key=lambda n: int(_SEC.search(n).group(1)))
        if not self.sec_names or "Contents/header.xml" not in names:
            raise EditError("한글 본문(section)·header 가 없는 파일이다.")
        self.secs = {n: self.zin.read(n).decode("utf-8") for n in self.sec_names}
        self._orig = dict(self.secs)
        self.hdr = _Hdr(self.zin.read("Contents/header.xml").decode("utf-8"))
        self._hdr0 = self.hdr.xml
        self.hpf_name = next((n for n in names if n.endswith("content.hpf")), None)
        self.hpf = self.zin.read(self.hpf_name).decode("utf-8") if self.hpf_name else None
        self._hpf0 = self.hpf
        self.added: dict[str, bytes] = {}
        self.ids = wf._Ids([self.hdr.xml, *self.secs.values()])

    def changed(self) -> bool:
        return self.secs != self._orig or self.hdr.xml != self._hdr0 or self.hpf != self._hpf0 or bool(self.added)

    def pack(self) -> bytes:
        repl = {n: x.encode("utf-8") for n, x in self.secs.items() if x != self._orig[n]}
        if self.hdr.xml != self._hdr0:
            repl["Contents/header.xml"] = self.hdr.xml.encode("utf-8")
        if self.hpf_name and self.hpf != self._hpf0:
            repl[self.hpf_name] = self.hpf.encode("utf-8")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for info in self.zin.infolist():
                body = repl.get(info.filename)
                z.writestr(info, body if body is not None else self.zin.read(info.filename),
                           compress_type=zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED)
            for name, body in self.added.items():
                z.writestr(name, body, compress_type=zipfile.ZIP_STORED)
        out = buf.getvalue()
        if len(out) > MAX_FILE:
            raise EditError("고친 결과가 너무 커서 저장하지 않는다(상한 초과).")
        return out

    def refs(self) -> tuple[str, str]:
        return hf._default_refs(self.hdr.xml)


# ── 문단 찾기 ──
_P_TOK = re.compile(r"<hp:p\b[^>]*>|</hp:p>")
_FIELD = re.compile(r'<hp:fieldBegin\b[^>]*\bid="(\d+)"[^>]*type="CLICK_HERE"[^>]*\bname="([^"]*)"')


def _paras(xml: str) -> list[tuple[int, int, int]]:
    """문단 (시작, 끝, 깊이) — 깊이 0 이 본문 문단, 표 칸·머리말 안 문단은 1 이상."""
    out, stack = [], []
    for t in _P_TOK.finditer(xml):
        if t.group(0) == "</hp:p>":
            if stack:
                s = stack.pop()
                out.append((s, t.end(), len(stack)))
        elif not t.group(0).endswith("/>"):
            stack.append(t.start())
    return sorted(out)


def _container(xml: str, pos: int) -> tuple[int, int]:
    """pos 를 품은 가장 안쪽 subList(표 칸·머리말·각주) 범위 — 없으면 문서 전체."""
    best = (0, len(xml))
    starts = []
    for t in re.finditer(r"<hp:subList\b[^>]*>|</hp:subList>", xml):
        if t.group(0).startswith("</"):
            if starts:
                s = starts.pop()
                if s <= pos < t.end() and (t.end() - s) < (best[1] - best[0]):
                    best = (s, t.end())
        else:
            starts.append(t.start())
    return best


def _siblings(xml: str, at: int) -> tuple[tuple[int, int, int], list[tuple[int, int, int]]]:
    """at 을 품은 가장 안쪽 문단과, 같은 칸(본문) 같은 깊이에서 그 뒤에 오는 문단들."""
    ps = _paras(xml)
    mine = [p for p in ps if p[0] <= at < p[1]]
    if not mine:
        raise EditError("그 자리의 문단을 찾지 못했다.")
    me = max(mine, key=lambda p: p[2])
    box = _container(xml, at)
    return me, [p for p in ps if p[2] == me[2] and p[0] >= me[1] and box[0] <= p[0] < box[1]]


_GUIDE_BOX = re.compile(r"^\s*【\s*(작성\s*방법|작성\s*요령|작성\s*가이드|유의\s*사항|작성\s*예시)")


def _is_guide_para(seg: str) -> bool:
    """작성방법 상자(한 칸 표)를 든 문단 — 절 제목과 절 필드 사이에 놓인다(제목 → 안내 → 쓸 자리)."""
    return "<hp:tbl" in seg and bool(_GUIDE_BOX.match(_text(seg)))


def _text(frag: str) -> str:
    frag = re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", frag, flags=re.S)
    t = re.sub(r"<[^>]+>", "", frag)
    return re.sub(r"\s+", " ", t.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')).strip()


def _unq(name: str) -> str:
    return name.replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def field_names(doc: _Doc) -> list[str]:
    return [_unq(m.group(2)) for n in doc.sec_names for m in _FIELD.finditer(doc.secs[n])]


def _resolve_field(doc: _Doc, name: str) -> str:
    """에이전트가 쓴 필드 이름 → 실제 이름(접두사·띄어쓰기 달라도 하나로 맞으면)."""
    names = field_names(doc)
    if name in names:
        return name
    key = lambda n: re.sub(r"\s+", "", re.sub(r"^(칸|절|표\d+):", "", n))
    hits = [n for n in names if key(n) == key(name)]
    if len(hits) == 1:
        return hits[0]
    raise EditError(f"필드 「{name}」 이 없다" + (f"(비슷한 것이 여럿: {', '.join(hits[:5])})" if hits else "") + " — hwp_fields 의 이름을 쓴다.")


def _find_field(doc: _Doc, name: str) -> tuple[str, int]:
    name = _resolve_field(doc, name)
    for n in doc.sec_names:
        for m in _FIELD.finditer(doc.secs[n]):
            if _unq(m.group(2)) == name:
                return n, m.start()
    raise EditError(f"필드 「{name}」 이 없다.")


def _find_text(doc: _Doc, text: str) -> tuple[str, int, int]:
    """글이 든 가장 안쪽 문단 (구역, 시작, 끝) — 본문 문단을 먼저, 없으면 표 칸 안."""
    want = re.sub(r"\s+", " ", str(text or "")).strip()
    if not want:
        raise EditError("찾을 글(after_text·text)이 비었다.")
    best = None
    for n in doc.sec_names:
        xml = doc.secs[n]
        for s, e, d in _paras(xml):
            seg = xml[s:e]
            if re.search(r"<hp:p\b", seg[1:]):                # 안쪽 문단이 있는 문단(표 문단)은 건너뛴다
                continue
            if want in _text(seg) and (best is None or d < best[3]):
                best = (n, s, e, d)
        if best and best[3] == 0:
            break
    if not best:
        raise EditError(f"「{want[:30]}」 이 든 문단을 찾지 못했다.")
    return best[0], best[1], best[2]


def _section_span(doc: _Doc, field: str) -> tuple[str, int, int]:
    """절 필드의 범위 — 필드 문단부터, 같은 칸의 뒤 문단 가운데 다음 절 제목(다음 절 필드 문단의 바로 앞) 전까지."""
    n, at = _find_field(doc, field)
    xml = doc.secs[n]
    me, sibs = _siblings(xml, at)
    end = me[1]
    for i, p in enumerate(sibs):
        seg = xml[p[0]:p[1]]
        # 다음 절 제목 — 다음 절 필드 문단 앞의 작성방법 상자들을 건너뛴 바로 앞 문단(제목 → 안내 → 쓸 자리)
        k = i + 1
        while k < len(sibs) and _is_guide_para(xml[sibs[k][0]:sibs[k][1]]):
            k += 1
        nxt = xml[sibs[k][0]:sibs[k][1]] if k < len(sibs) else ""
        if re.search(r'name="절:', seg) or re.search(r'name="절:', nxt):
            break
        end = p[1]
    return n, me[0], end


def _anchor(doc: _Doc, at: dict | None) -> tuple[str, int, int]:
    """넣을 자리 (구역, 위치, 쓸 폭 HWPUNIT). at = {field, where: start|end} | {after_text} | {} (문서 끝)."""
    at = at if isinstance(at, dict) else {}
    if at.get("field"):
        if str(at.get("where") or "start") == "end":
            n, _s, e = _section_span(doc, str(at["field"]))
        else:
            n, pos = _find_field(doc, str(at["field"]))
            me, _ = _siblings(doc.secs[n], pos)
            e = me[1]
        return n, e, _width_at(doc.secs[n], e - 1)
    if at.get("after_text"):
        n, _s, e = _find_text(doc, str(at["after_text"]))
        return n, e, _width_at(doc.secs[n], e - 1)
    n = doc.sec_names[-1]
    xml = doc.secs[n]
    return n, xml.rfind("</hs:sec>"), hf._text_width_hu(doc.secs[doc.sec_names[0]])


def _width_at(xml: str, at: int) -> int:
    """그 자리의 쓸 폭 — 표 칸 안이면 칸 폭, 아니면 본문 폭."""
    tc = xml.rfind("<hp:tc", 0, at)
    end = xml.rfind("</hp:tc>", 0, at)
    if tc != -1 and end < tc:
        m = re.search(r'<hp:cellSz width="(\d+)"', xml[tc:xml.find("</hp:tc>", at) + 8])
        if m:
            return max(int(m.group(1)) - 1200, 3000)
    first = re.search(r"<hp:pagePr\b.*?</hp:pagePr>", xml, re.S)
    return hf._text_width_hu(first.group(0)) if first else 42520


def _strip_ls(p: str) -> str:
    """한 문단(표를 품은 문단 포함)의 자기 줄 배치 캐시만 지운다 — 안쪽 문단의 것은 그대로."""
    inner_end = p.rfind("</hp:p>", 0, len(p) - len("</hp:p>"))
    tail_from = max(inner_end + 7 if inner_end != -1 else 0, p.rfind("</hp:tbl>") + 9 if "</hp:tbl>" in p else 0)
    tail = p[tail_from:]
    return p[:tail_from] + wf._LINESEG.sub("", tail)


# ── 표 ──
class _Table:
    """표 하나의 격자 모델 — 칸(행·열·합침)과 열 폭·행 높이. 바꾼 뒤 to_xml 로 XML 을 다시 만든다."""

    def __init__(self, xml: str):
        self.xml = xml
        first_tr = xml.find("<hp:tr")
        self.head = xml[:first_tr]
        self.tail = xml[xml.rfind("</hp:tr>") + len("</hp:tr>"):]
        self.cells: list[dict] = []
        for m in ws._TC.finditer(xml):
            x = m.group(0)
            a = re.search(r'<hp:cellAddr colAddr="(\d+)" rowAddr="(\d+)"', x)
            sp = re.search(r'<hp:cellSpan colSpan="(\d+)" rowSpan="(\d+)"', x)
            sz = re.search(r'<hp:cellSz width="(\d+)" height="(\d+)"', x)
            if not a:
                continue
            self.cells.append({"r": int(a.group(2)), "c": int(a.group(1)), "cs": int(sp.group(1)) if sp else 1,
                               "rs": int(sp.group(2)) if sp else 1, "w": int(sz.group(1)) if sz else 0,
                               "h": int(sz.group(2)) if sz else hs.MIN_ROW_HEIGHT, "xml": x})
        if not self.cells:
            raise EditError("칸이 없는 표다.")
        self.nr = max(c["r"] + c["rs"] for c in self.cells)
        self.nc = max(c["c"] + c["cs"] for c in self.cells)
        tw = re.search(r'<hp:sz width="(\d+)"', self.head)
        self.total_w = int(tw.group(1)) if tw else sum(c["w"] for c in self.cells if c["r"] == 0)
        self.col_w = self._solve([(c["c"], c["cs"], c["w"]) for c in self.cells], self.nc, self.total_w)
        self.row_h = self._solve([(c["r"], c["rs"], c["h"]) for c in self.cells], self.nr, None, hs.MIN_ROW_HEIGHT)

    @staticmethod
    def _solve(spans, n, total, floor=600):
        """칸 폭(합친 칸 포함)에서 열마다 폭을 푼다 — 한 칸짜리를 먼저, 합친 칸은 모르는 열이 하나 남을 때."""
        val: list[int | None] = [None] * n
        for i, k, w in spans:
            if k == 1 and val[i] is None:
                val[i] = w
        for _ in range(n):
            for i, k, w in spans:
                unknown = [j for j in range(i, i + k) if val[j] is None]
                if len(unknown) == 1:
                    val[unknown[0]] = max(w - sum(val[j] or 0 for j in range(i, i + k) if j != unknown[0]), floor)
        rest = [j for j in range(n) if val[j] is None]
        if rest:
            known = sum(v for v in val if v)
            share = max(((total or 0) - known) // len(rest), floor) if total else floor
            for j in rest:
                val[j] = share
        return [int(v) for v in val]

    def covering(self, r: int, c: int) -> dict | None:
        return next((x for x in self.cells if x["r"] <= r < x["r"] + x["rs"] and x["c"] <= c < x["c"] + x["cs"]), None)

    def _check_rc(self, r: int, c: int | None = None) -> None:
        if not 0 <= r < self.nr or (c is not None and not 0 <= c < self.nc):
            raise EditError(f"표는 {self.nr}행×{self.nc}열이다 — 행·열 번호는 1부터 그 안에서.")

    def new_cell(self, like: dict, text: str = "", ids=None) -> dict:
        """like 칸의 모양(테두리·문단·글자·세로 정렬)을 따른 빈 칸."""
        x = like["xml"]
        tc_open = re.match(r"<hp:tc\b[^>]*>", x).group(0)
        sub_open = re.search(r"<hp:subList\b[^>]*>", x).group(0)
        p_open = re.search(r"<hp:p\b[^>]*>", x)
        pp = re.search(r'paraPrIDRef="(\d+)"', p_open.group(0)).group(1) if p_open else "0"
        cp = re.search(r'charPrIDRef="(\d+)"', x)
        cp = cp.group(1) if cp else "0"
        margin = re.search(r"<hp:cellMargin\b[^>]*/>", x)
        body = "".join(hf.paragraph_xml(ln, pp, cp).replace('<hp:p id="0"', f'<hp:p id="{ids.new() if ids else "0"}"', 1)
                       for ln in (hf.split_lines(text) if text else [""]))
        tc_open = re.sub(r'\bname="[^"]*"', 'name=""', tc_open, count=1)
        xml = (f"{tc_open}{sub_open}{body}</hp:subList>"
               f'<hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
               f'<hp:cellSz width="0" height="0"/>{margin.group(0) if margin else ""}</hp:tc>')
        return {"r": 0, "c": 0, "cs": 1, "rs": 1, "w": 0, "h": 0, "xml": xml}

    # 행·열
    def insert_rows(self, r: int, count: int, below: bool, ids) -> None:
        if self.nr + count > MAX_ROWS or (self.nr + count) * self.nc > MAX_CELLS:
            raise EditError(f"표가 너무 커진다(행 {MAX_ROWS}·칸 {MAX_CELLS} 상한) — 표를 나누어 넣는다.")
        self._check_rc(r)
        # 머리줄(첫 행) 아래에 넣으면 모양은 본문 행(둘째 행)을 따른다 — 새 행이 머리줄 음영·굵게를 물려받지 않게
        src = 1 if r == 0 and below and self.nr > 1 else r
        ref_row = [(c, self.covering(r, c)) for c in range(self.nc)]
        look = {c: self.covering(src, c) for c in range(self.nc)}
        new = r + 1 if below else r
        for x in self.cells:
            if x["r"] >= new:
                x["r"] += count
            elif x["r"] < new < x["r"] + x["rs"]:
                x["rs"] += count                             # 위아래로 합친 칸이 새 행을 지나가면 그 칸을 늘린다
        for k in range(count):
            c = 0
            while c < self.nc:
                ref = dict(ref_row)[c]
                if ref is None:
                    c += 1
                    continue
                if ref["r"] <= new + k < ref["r"] + ref["rs"]:      # 위아래로 합친 칸이 이미 새 행을 덮는다
                    c = ref["c"] + ref["cs"]
                    continue
                cell = self.new_cell(look.get(c) or ref, ids=ids)
                cell.update(r=new + k, c=c, cs=max(1, min(ref["cs"] - (c - ref["c"]), self.nc - c)))
                self.cells.append(cell)
                c += cell["cs"]
        self.row_h[new:new] = [self.row_h[r]] * count
        self.nr += count

    def delete_rows(self, r: int, count: int) -> None:
        self._check_rc(r)
        count = min(count, self.nr - r)
        if count >= self.nr:
            raise EditError("표의 모든 행을 지울 수는 없다 — 표를 지우려면 기안기에서 지운다.")
        for _ in range(count):
            keep = []
            for x in self.cells:
                if x["r"] == r:
                    if x["rs"] > 1:
                        x["rs"] -= 1                          # 합친 칸은 남은 행으로 줄인다(글은 그대로)
                        keep.append(x)
                elif x["r"] < r < x["r"] + x["rs"]:
                    x["rs"] -= 1
                    keep.append(x)
                else:
                    if x["r"] > r:
                        x["r"] -= 1
                    keep.append(x)
            self.cells = keep
            self.row_h.pop(r)
            self.nr -= 1

    def insert_cols(self, c: int, count: int, right: bool, ids) -> None:
        if self.nc + count > MAX_COLS or self.nr * (self.nc + count) > MAX_CELLS:
            raise EditError(f"표가 너무 넓어진다(열 {MAX_COLS}·칸 {MAX_CELLS} 상한).")
        self._check_rc(0, c)
        ref_col = {r: self.covering(r, c) for r in range(self.nr)}
        new = c + 1 if right else c
        for x in self.cells:
            if x["c"] >= new:
                x["c"] += count
            elif x["c"] < new < x["c"] + x["cs"]:
                x["cs"] += count
        for k in range(count):
            r = 0
            while r < self.nr:
                ref = ref_col[r]
                if ref is None:
                    r += 1
                    continue
                if ref["c"] <= new + k < ref["c"] + ref["cs"]:
                    r = ref["r"] + ref["rs"]
                    continue
                cell = self.new_cell(ref, ids=ids)
                cell.update(r=r, c=new + k, rs=max(1, min(ref["rs"] - (r - ref["r"]), self.nr - r)))
                self.cells.append(cell)
                r += cell["rs"]
        self.col_w[new:new] = [self.col_w[c]] * count
        self.nc += count
        self._fit_width()

    def delete_cols(self, c: int, count: int) -> None:
        self._check_rc(0, c)
        count = min(count, self.nc - c)
        if count >= self.nc:
            raise EditError("표의 모든 열을 지울 수는 없다.")
        for _ in range(count):
            keep = []
            for x in self.cells:
                if x["c"] == c:
                    if x["cs"] > 1:
                        x["cs"] -= 1
                        keep.append(x)
                elif x["c"] < c < x["c"] + x["cs"]:
                    x["cs"] -= 1
                    keep.append(x)
                else:
                    if x["c"] > c:
                        x["c"] -= 1
                    keep.append(x)
            self.cells = keep
            self.col_w.pop(c)
            self.nc -= 1
        self._fit_width()

    def _fit_width(self) -> None:
        """열을 넣거나 지워도 표 전체 폭은 그대로(본문 폭을 넘지 않게) — 열 폭을 비율대로 맞춘다."""
        s = sum(self.col_w) or 1
        self.col_w = [max(int(self.total_w * w / s), 600) for w in self.col_w]

    def set_widths(self, ratios: list[float]) -> None:
        try:
            ratios = [float(x) for x in ratios] if isinstance(ratios, (list, tuple)) else []
        except (TypeError, ValueError):
            ratios = []
        if len(ratios) != self.nc or any(x <= 0 or x != x or x == float("inf") for x in ratios):
            raise EditError(f"열 폭 비율은 열 수({self.nc})만큼의 양수다.")
        s = sum(float(x) for x in ratios)
        self.col_w = [max(int(self.total_w * float(x) / s), 600) for x in ratios]

    # 칸
    def set_text(self, r: int, c: int, text: str, ids) -> None:
        self._check_rc(r, c)
        cell = self.covering(r, c)
        x = cell["xml"]
        fb = re.search(r'<hp:fieldBegin\b[^>]*\bid="(\d+)"', x)
        if fb:                                               # 칸에 누름틀이 있으면 그 안에 — 필드 이름은 그대로 둔다
            end = re.search(rf'<hp:ctrl><hp:fieldEnd\b[^>]*beginIDRef="{fb.group(1)}"[^>]*/></hp:ctrl>', x)
            open_end = x.find("</hp:ctrl>", fb.end()) + len("</hp:ctrl>")
            if end and open_end <= end.start():
                vx = "<hp:t>" + "<hp:lineBreak/>".join(escape(t) for t in str(text).replace("\r", "").split("\n")) + "</hp:t>"
                x = x[:open_end] + vx + x[end.start():]
                cell["xml"] = wf.strip_lineseg_in(x, lambda p: "fieldBegin" in p)
                return
        sub = re.search(r"(<hp:subList\b[^>]*>)(.*)(</hp:subList>)", x, re.S)
        p_open = re.search(r"<hp:p\b[^>]*>", sub.group(2))
        pp = re.search(r'paraPrIDRef="(\d+)"', p_open.group(0)).group(1) if p_open else "0"
        cp = re.search(r'charPrIDRef="(\d+)"', sub.group(2))
        cp = cp.group(1) if cp else "0"
        body = "".join(hf.paragraph_xml(ln, pp, cp).replace('<hp:p id="0"', f'<hp:p id="{ids.new()}"', 1)
                       for ln in (hf.split_lines(str(text)) or [""]))
        cell["xml"] = x[:sub.start(2)] + body + x[sub.end(2):]

    def merge(self, r1: int, c1: int, r2: int, c2: int) -> None:
        r1, r2 = sorted((r1, r2))
        c1, c2 = sorted((c1, c2))
        self._check_rc(r1, c1)
        self._check_rc(r2, c2)
        inside = []
        for x in self.cells:
            xr2, xc2 = x["r"] + x["rs"] - 1, x["c"] + x["cs"] - 1
            overlap = not (xr2 < r1 or x["r"] > r2 or xc2 < c1 or x["c"] > c2)
            if not overlap:
                continue
            if x["r"] < r1 or xr2 > r2 or x["c"] < c1 or xc2 > c2:
                raise EditError("합칠 범위가 이미 합친 칸에 걸친다 — 범위를 그 칸까지 넓히거나 먼저 나눈다.")
            inside.append(x)
        anchor = next((x for x in inside if x["r"] == r1 and x["c"] == c1), None)
        if anchor is None or len(inside) < 2:
            raise EditError("합칠 칸이 둘 이상이어야 한다.")
        # 글이 있는 다른 칸의 문단을 첫 칸 뒤에 잇는다(한글의 칸 합치기와 같다)
        extra = []
        for x in sorted(inside, key=lambda x: (x["r"], x["c"])):
            if x is anchor:
                continue
            sub = re.search(r"<hp:subList\b[^>]*>(.*)</hp:subList>", x["xml"], re.S)
            if sub and _text(sub.group(1)):
                extra.append(sub.group(1))
        if extra:
            ax = anchor["xml"]
            close = ax.rfind("</hp:subList>")
            body = ax[:close]
            if not _text(re.search(r"<hp:subList\b[^>]*>(.*)", body, re.S).group(1)):
                sub_open = re.search(r"<hp:subList\b[^>]*>", body)
                body = body[:sub_open.end()]
            anchor["xml"] = body + "".join(extra) + ax[close:]
        self.cells = [x for x in self.cells if x is anchor or x not in inside]
        anchor["rs"], anchor["cs"] = r2 - r1 + 1, c2 - c1 + 1

    def split(self, r: int, c: int, rows: int | None, cols: int | None, ids) -> None:
        self._check_rc(r, c)
        t = self.covering(r, c)
        R = _int(rows, "rows", 1, 50) if rows not in (None, "", 0) else (1 if cols else t["rs"])
        C = _int(cols, "cols", 1, 30) if cols not in (None, "", 0) else (1 if rows else t["cs"])
        if self.nr + R > MAX_ROWS + t["rs"] or self.nc + C > MAX_COLS + t["cs"] or (self.nr + R) * (self.nc + C) > MAX_CELLS:
            raise EditError("칸을 그만큼 나누면 표가 상한을 넘는다.")
        if R < 1 or C < 1 or R * C == 1 and t["rs"] * t["cs"] == 1:
            raise EditError("나눌 행·열 수(rows·cols)를 준다 — 합친 칸을 풀 때는 둘 다 비운다.")
        if C > t["cs"]:                                      # 열이 모자라면 격자에 열을 더한다(다른 행의 같은 자리 칸은 넓힌다)
            n = C - t["cs"]
            last = t["c"] + t["cs"] - 1
            width = sum(self.col_w[t["c"]:last + 1])
            for x in self.cells:
                if x is t:
                    continue
                if x["c"] <= last < x["c"] + x["cs"]:
                    x["cs"] += n
                elif x["c"] > last:
                    x["c"] += n
            t["cs"] = C
            self.col_w[t["c"]:last + 1] = [width // C] * C
            self.nc += n
        if R > t["rs"]:
            n = R - t["rs"]
            last = t["r"] + t["rs"] - 1
            height = sum(self.row_h[t["r"]:last + 1])
            for x in self.cells:
                if x is t:
                    continue
                if x["r"] <= last < x["r"] + x["rs"]:
                    x["rs"] += n
                elif x["r"] > last:
                    x["r"] += n
            t["rs"] = R
            self.row_h[t["r"]:last + 1] = [max(height // R, hs.MIN_ROW_HEIGHT)] * R
            self.nr += n
        if t["rs"] % R or t["cs"] % C:
            raise EditError(f"이 칸({t['rs']}행×{t['cs']}열 합침)은 {R}×{C} 로 고르게 나눌 수 없다.")
        pr, pc = t["rs"] // R, t["cs"] // C
        top, left = t["r"], t["c"]
        t["rs"], t["cs"] = pr, pc
        for i in range(R):
            for j in range(C):
                if i == 0 and j == 0:
                    continue
                cell = self.new_cell(t, ids=ids)
                cell.update(r=top + i * pr, c=left + j * pc, rs=pr, cs=pc)
                self.cells.append(cell)

    def cells_in(self, rng: dict | None) -> list[dict]:
        """범위 {rows: [첫, 끝], cols: [첫, 끝]}(0부터) 에 든 칸 — 없으면 표 전체."""
        if not rng:
            return list(self.cells)
        r1, r2 = rng.get("rows") or (0, self.nr - 1)
        c1, c2 = rng.get("cols") or (0, self.nc - 1)
        return [x for x in self.cells if r1 <= x["r"] <= r2 and c1 <= x["c"] <= c2]

    def to_xml(self) -> str:
        self.cells.sort(key=lambda x: (x["r"], x["c"]))
        # 덮개 검사 — 모든 자리를 꼭 한 칸이 덮어야 한다(한글이 깨진 표를 열지 못한다)
        cover = [[0] * self.nc for _ in range(self.nr)]
        for x in self.cells:
            for i in range(x["r"], x["r"] + x["rs"]):
                for j in range(x["c"], x["c"] + x["cs"]):
                    if i >= self.nr or j >= self.nc:
                        raise EditError("표 격자가 어긋났다(칸이 표 밖으로 나감).")
                    cover[i][j] += 1
        if any(v != 1 for row in cover for v in row):
            raise EditError("표 격자가 어긋났다(빈 자리나 겹친 칸).")
        rows = []
        for r in range(self.nr):
            tcs = []
            for x in (x for x in self.cells if x["r"] == r):
                w = sum(self.col_w[x["c"]:x["c"] + x["cs"]])
                h = sum(self.row_h[x["r"]:x["r"] + x["rs"]])
                y = re.sub(r'<hp:cellAddr colAddr="\d+" rowAddr="\d+"/>', f'<hp:cellAddr colAddr="{x["c"]}" rowAddr="{x["r"]}"/>', x["xml"], count=1)
                y = re.sub(r'<hp:cellSpan colSpan="\d+" rowSpan="\d+"/>', f'<hp:cellSpan colSpan="{x["cs"]}" rowSpan="{x["rs"]}"/>', y, count=1)
                y = re.sub(r'<hp:cellSz width="\d+" height="\d+"/>', f'<hp:cellSz width="{w}" height="{h}"/>', y, count=1)
                tcs.append(y)
            rows.append("<hp:tr>" + "".join(tcs) + "</hp:tr>")
        head = re.sub(r'\browCnt="\d+"', f'rowCnt="{self.nr}"', self.head, count=1)
        head = re.sub(r'\bcolCnt="\d+"', f'colCnt="{self.nc}"', head, count=1)
        head = re.sub(r'(<hp:sz width=")\d+(" widthRelTo="\w+" height=")\d+', rf"\g<1>{sum(self.col_w)}\g<2>{sum(self.row_h)}", head, count=1)
        body = head + "".join(rows) + self.tail
        # 바뀐 표 안의 줄 배치 캐시는 지운다(칸 폭·글이 바뀌었다) — 표 밖 문단은 그대로
        return wf._LINESEG.sub("", body)


def list_tables(doc: _Doc) -> list[dict]:
    """표 목록 — 번호는 hwp_tables·hwp_style 과 같은 차례(안쪽 표, 문서 차례). 필드 이름도 함께."""
    out = []
    for n in doc.sec_names:
        for m in ws._TBL.finditer(doc.secs[n]):
            cells = ws._cells(m.group(0))
            if not cells:
                continue
            first = [c["text"] for c in sorted(cells, key=lambda c: (c["r"], c["c"])) if c["r"] == 0]
            out.append({"no": len(out) + 1, "sec": n, "s": m.start(), "e": m.end(),
                        "rows": max(c["r"] + c["rs"] for c in cells), "cols": max(c["c"] + c["cs"] for c in cells),
                        "head": " | ".join(first)[:80], "fields": [_unq(f.group(2)) for f in _FIELD.finditer(m.group(0))]})
    return out


def _pick_table(doc: _Doc, table) -> dict:
    tl = list_tables(doc)
    if not tl:
        raise EditError("문서에 표가 없다.")
    if isinstance(table, dict):
        if table.get("no") is not None:
            table = table["no"]
        elif table.get("field"):
            name = _resolve_field(doc, str(table["field"]))
            hit = [t for t in tl if name in t["fields"]]
            if not hit:
                raise EditError(f"필드 「{name}」 이 든 표가 없다.")
            return hit[0]
        elif table.get("head"):
            want = re.sub(r"\s+", "", str(table["head"]))
            hit = [t for t in tl if want in re.sub(r"\s+", "", t["head"])]
            if len(hit) != 1:
                raise EditError(f"머리줄에 「{table['head']}」 이 든 표가 {len(hit)}개다 — 번호(no)로 고른다.")
            return hit[0]
    m = re.search(r"\d+", str(table if table is not None else ""))
    if not m:
        raise EditError("table 은 표 번호(hwp_tables 의 번호)나 {head: 머리줄 글} · {field: 필드 이름} 이다.")
    no = int(m.group(0))
    if not 1 <= no <= len(tl):
        raise EditError(f"표 번호는 1~{len(tl)} 이다.")
    return tl[no - 1]


def _replace_table(doc: _Doc, t: dict, new_tbl: str) -> None:
    xml = doc.secs[t["sec"]]
    xml = xml[:t["s"]] + new_tbl + xml[t["e"]:]
    # 표를 품은 문단(바깥 문단)의 자기 줄 배치는 지운다 — 표 높이가 바뀌었다
    ps = [p for p in _paras(xml) if p[0] < t["s"] < p[1]]
    if ps:
        p = max(ps, key=lambda p: p[2])
        xml = xml[:p[0]] + _strip_ls(xml[p[0]:p[1]]) + xml[p[1]:]
    doc.secs[t["sec"]] = xml


def _idx(v, name: str) -> int:
    try:
        i = int(v)
    except (TypeError, ValueError):
        raise EditError(f"{name} 는 1부터 세는 번호다.")
    if i < 1:
        raise EditError(f"{name} 는 1부터 센다.")
    return i - 1


def _color(c) -> str:
    v = ws._color(str(c or ""))
    return v


# ── 연산(바이트 → 바이트, 요약) ──
@_guarded
def page_setup(data: bytes, a: dict) -> tuple[bytes, str]:
    doc = _Doc(data)
    paper = str(a.get("paper") or "").strip()
    orient = str(a.get("orientation") or "").strip()
    preset = str(a.get("margin") or "").strip()
    mm = dict(MARGIN_PRESETS.get(preset, {})) if preset else {}
    if preset and not mm:
        raise EditError(f"여백 프리셋은 {', '.join(MARGIN_PRESETS)} 가운데 하나다.")
    if a.get("margins_mm") not in (None, "") and not isinstance(a.get("margins_mm"), dict):
        raise EditError("margins_mm 는 {top, bottom, left, right, header, footer, gutter: mm} 꼴이다.")
    for k, v in (a.get("margins_mm") or {}).items():
        if k in _MARGIN_KEYS and v not in (None, ""):
            try:
                mm[k] = float(v)
            except (TypeError, ValueError):
                raise EditError(f"여백 {k} 는 mm 숫자다.")
    if any(v < 0 or v > 100 for v in mm.values()):
        raise EditError("여백은 0~100mm 다.")
    size = None
    if paper:
        key = next((k for k in PAPERS if k.lower() == paper.lower().replace(" ", "")), None)
        if not key:
            raise EditError(f"용지는 {', '.join(PAPERS)} 가운데 하나다.")
        size = PAPERS[key]
    land = None
    if orient:
        land = orient in ("가로", "landscape", "LANDSCAPE", "NARROWLY")
        if not land and orient not in ("세로", "portrait", "PORTRAIT", "WIDELY"):
            raise EditError("orientation 은 세로|가로 다.")
    gtype = _GUTTER_TYPES.get(str(a.get("gutter_type") or ""), "")
    if not (size or land is not None or mm or gtype):
        raise EditError("바꿀 것(paper·orientation·margin·margins_mm·gutter_type)을 하나 이상 준다.")
    done = 0
    old_tw = {n: _text_width(doc.secs[n]) for n in doc.sec_names}
    for n in doc.sec_names:
        def sub(m: re.Match) -> str:
            p = m.group(0)
            if size:
                p = re.sub(r'\bwidth="\d+"', f'width="{hu(size[0])}"', p, count=1)
                p = re.sub(r'\bheight="\d+"', f'height="{hu(size[1])}"', p, count=1)
            if land is not None:                        # 한글은 너비·높이를 세로 기준으로 두고 방향만 바꾼다(기안기 저장본으로 확인)
                p = re.sub(r'\blandscape="\w+"', f'landscape="{"NARROWLY" if land else "WIDELY"}"', p, count=1)
            if gtype:
                p = re.sub(r'\bgutterType="\w+"', f'gutterType="{gtype}"', p, count=1)
            for k, v in mm.items():
                p = re.sub(rf'(<hp:margin\b[^>]*\b{k}=")\d+', rf"\g<1>{hu(v)}", p, count=1)
            return p
        new, k = re.subn(r"<hp:pagePr\b.*?</hp:pagePr>", sub, doc.secs[n], flags=re.S)
        doc.secs[n] = new
        done += k
    if not done:
        raise EditError("페이지 설정(pagePr)이 없는 문서다.")
    refit = 0
    for n in doc.sec_names:                              # 본문 폭에 맞춰 둔 표는 바뀐 본문 폭을 따라간다(가로로 바꾸면 표도 넓게)
        new_tw = _text_width(doc.secs[n])
        if old_tw.get(n) and new_tw and abs(new_tw - old_tw[n]) > 200:
            doc.secs[n], k = _refit_tables(doc.secs[n], old_tw[n], new_tw)
            refit += k
    pp = re.search(r"<hp:pagePr\b.*?</hp:pagePr>", doc.secs[doc.sec_names[0]], re.S).group(0)
    w, h = (int(x) for x in re.search(r'width="(\d+)" height="(\d+)"', pp).groups())
    mg = {k: mm_of(int(v)) for k, v in re.findall(r'\b(top|bottom|left|right|header|footer|gutter)="(\d+)"', pp)}
    is_land = 'landscape="NARROWLY"' in pp
    return doc.pack(), (f"페이지 설정: {mm_of(w)}×{mm_of(h)}mm {'가로' if is_land else '세로'}, 여백 위 {mg.get('top')}·아래 {mg.get('bottom')}·"
                        f"왼쪽 {mg.get('left')}·오른쪽 {mg.get('right')}·머리말 {mg.get('header')}·꼬리말 {mg.get('footer')}·제본 {mg.get('gutter')}mm."
                        + (f" 본문 폭에 맞춘 표 {refit}개도 새 폭으로 맞췄다." if refit else ""))


@_guarded
def _text_width(sec: str) -> int:
    """구역의 본문 폭(HWPUNIT) — 방향을 따져 쪽 폭에서 왼쪽·오른쪽·제본 여백을 뺀 값. 모르면 0."""
    m = re.search(r"<hp:pagePr\b.*?</hp:pagePr>", sec, re.S)
    if not m:
        return 0
    pp = m.group(0)
    try:
        w, h = (int(x) for x in re.search(r'width="(\d+)" height="(\d+)"', pp).groups())
        mg = {k: int(v) for k, v in re.findall(r'\b(left|right|gutter)="(\d+)"', pp)}
    except (AttributeError, ValueError):
        return 0
    page_w = max(w, h) if 'landscape="NARROWLY"' in pp else min(w, h)
    return max(page_w - mg.get("left", 0) - mg.get("right", 0) - mg.get("gutter", 0), 0)


def _refit_tables(sec: str, old_w: int, new_w: int) -> tuple[str, int]:
    """본문 폭(±10%)에 맞춰 둔 맨 바깥 표만 새 폭 비율로 늘이거나 줄인다 — 열 비율은 그대로(칸 폭·표 폭을 같은 비율로)."""
    if old_w <= 0 or new_w <= 0:
        return sec, 0
    ratio = new_w / old_w
    out, i, n = [], 0, 0
    depth, start = 0, -1
    for m in re.finditer(r"<hp:tbl\b|</hp:tbl>", sec):
        if m.group(0).startswith("<hp:tbl"):
            if depth == 0:
                start = m.start()
            depth += 1
        else:
            depth -= 1
            if depth == 0 and start >= 0:
                seg = sec[start:m.end()]
                sz = re.search(r'<hp:sz width="(\d+)"', seg)
                if sz and abs(int(sz.group(1)) - old_w) <= old_w * 0.1:
                    seg = re.sub(r'(<hp:sz width=")(\d+)', lambda x: f'{x.group(1)}{int(round(int(x.group(2)) * ratio))}', seg)
                    seg = re.sub(r'(<hp:cellSz width=")(\d+)', lambda x: f'{x.group(1)}{int(round(int(x.group(2)) * ratio))}', seg)
                    out.append(sec[i:start]); out.append(seg); i = m.end(); n += 1
                start = -1
    out.append(sec[i:])
    return "".join(out), n


def current_page(data: bytes) -> dict:
    """첫 구역의 페이지 설정(mm) — 편집 화면의 「페이지 설정」 창이 처음 값으로 쓴다."""
    doc = _Doc(data)
    pp = re.search(r"<hp:pagePr\b.*?</hp:pagePr>", doc.secs[doc.sec_names[0]], re.S)
    if not pp:
        return {}
    w, h = (int(x) for x in re.search(r'width="(\d+)" height="(\d+)"', pp.group(0)).groups())
    paper = next((k for k, (pw, ph) in PAPERS.items() if abs(hu(pw) - w) < 60 and abs(hu(ph) - h) < 60), "")
    return {"paper": paper, "orientation": "가로" if 'landscape="NARROWLY"' in pp.group(0) else "세로",
            "margins_mm": {k: mm_of(int(v)) for k, v in re.findall(r'\b(top|bottom|left|right|header|footer|gutter)="(\d+)"', pp.group(0))}}


@_guarded
def table_insert(data: bytes, a: dict) -> tuple[bytes, str]:
    doc = _Doc(data)
    values = a.get("values") if isinstance(a.get("values"), list) else []
    values = [[str(c) if c is not None else "" for c in (r if isinstance(r, list) else [r])] for r in values]
    try:
        nr = int(a.get("rows") or len(values) or 0)
        nc = int(a.get("cols") or max((len(r) for r in values), default=0))
    except (TypeError, ValueError):
        raise EditError("rows·cols 는 숫자다.")
    head = a.get("head")
    if isinstance(head, list) and head:                          # 머리줄 글을 따로 주면 첫 행으로
        values = [[str(x) for x in head]] + values
        nr = max(nr, len(values))
        nc = max(nc, len(head))
    if not (1 <= nr <= 200 and 1 <= nc <= 30):
        raise EditError("표는 1~200행, 1~30열로 만든다(rows·cols 또는 values 를 준다).")
    grid = [[(values[r][c] if r < len(values) and c < len(values[r]) else "") for c in range(nc)] for r in range(nr)]
    widths = a.get("widths")
    if widths is not None:
        try:
            widths = [float(x) for x in widths] if isinstance(widths, list) and len(widths) == nc else None
        except (TypeError, ValueError):
            widths = None
        if not widths or any(x <= 0 or x != x or x == float("inf") for x in widths):
            raise EditError(f"widths 는 열 수({nc})만큼의 양수 비율이다.")
    n, pos, width = _anchor(doc, a.get("at"))
    pp, cp = doc.refs()
    bf = next(iter(re.findall(r'<hh:borderFill\b[^>]*\bid="(\d+)"', doc.hdr.xml)), "1")
    m = hs.CELL_MARGIN
    margin = f'<hp:cellMargin left="{m["left"]}" right="{m["right"]}" top="{m["top"]}" bottom="{m["bottom"]}"/>'
    pid = doc.ids.new()
    if not widths:                                               # 열 폭은 칸 글 길이로(하한·상한 — house_style.fit_widths)
        widths = [float(w) for w in hs.fit_widths([[r[c] for r in grid] for c in range(nc)], width)]
    px = hf.table_xml(grid, pp, cp, width, bf, margin, int(doc.ids.new()), widths, None)
    px = px.replace('<hp:p id="0"', f'<hp:p id="{pid}"', 1)
    px = re.sub(r'<hp:p id="0"', lambda _m: f'<hp:p id="{doc.ids.new()}"', px)
    # 학교 표 기본 모양(webhwp_style.HOUSE) — 머리줄은 첫 행이 모두 글이면 저절로. head=false 면 머리줄 모양을 빼고
    # 머리줄이 있는 자료 표는 본문 칸에 라벨 음영을 두지 않는다(오른쪽이 빈 값 칸을 라벨로 잘못 보지 않게)
    opts = ({"head_fill": "none", "head_bold": False, "head_align": ""} if head is False
            else {"label_fill": "none"} if all(grid[0]) and nr > 1 else None)
    tbl = ws._TBL.search(px)
    px = px[:tbl.start()] + ws.style_table(tbl.group(0), doc.hdr, opts) + px[tbl.end():]
    if head is not False and all(grid[0]):
        px = px.replace('repeatHeader="1"', 'repeatHeader="1"', 1)
    xml = doc.secs[n]
    doc.secs[n] = xml[:pos] + px + xml[pos:]
    no = next((t["no"] for t in list_tables(doc) if t["sec"] == n and t["s"] >= pos), "?")
    return doc.pack(), f"표{no} 을 만들었다({nr}행×{nc}열{', 머리줄 있음' if head is not False and all(grid[0]) else ''})."


TABLE_OPS = ("add_rows", "delete_rows", "add_cols", "delete_cols", "set_cells", "merge", "split", "widths", "head",
             "align", "shade", "valign")


def _range(a: dict, tb: _Table) -> dict | None:
    """범위 인자(cells: [첫 행, 첫 열, 끝 행, 끝 열] | row | col, 1부터) → 0부터 범위. 없으면 None(표 전체)."""
    if a.get("cells"):
        v = a["cells"]
        if not isinstance(v, list) or len(v) not in (2, 4):
            raise EditError("cells 는 [행, 열] 또는 [첫 행, 첫 열, 끝 행, 끝 열](1부터)이다.")
        r1, c1 = _idx(v[0], "행"), _idx(v[1], "열")
        r2, c2 = (_idx(v[2], "행"), _idx(v[3], "열")) if len(v) == 4 else (r1, c1)
        return {"rows": sorted((r1, r2)), "cols": sorted((c1, c2))}
    if a.get("row") is not None:
        r = _idx(a["row"], "row")
        return {"rows": [r, r], "cols": [0, tb.nc - 1]}
    if a.get("col") is not None:
        c = _idx(a["col"], "col")
        return {"rows": [0, tb.nr - 1], "cols": [c, c]}
    return None


@_guarded
def table_edit(data: bytes, a: dict) -> tuple[bytes, str]:
    doc = _Doc(data)
    op = str(a.get("op") or "").strip()
    if op not in TABLE_OPS:
        raise EditError(f"op 는 {', '.join(TABLE_OPS)} 가운데 하나다.")
    t = _pick_table(doc, a.get("table"))
    xml = doc.secs[t["sec"]][t["s"]:t["e"]]
    tb = _Table(xml)
    count = _int(a.get("count"), "count", 1, 100, default=1)
    msg = ""
    if op == "add_rows":
        r = _idx(a.get("row") or tb.nr, "row")
        below = str(a.get("where") or "below") != "above"
        tb.insert_rows(r, count, below, doc.ids)
        msg = f"{r + 1}행 {'아래' if below else '위'}에 {count}행을 넣었다"
    elif op == "delete_rows":
        r = _idx(a.get("row"), "row")
        tb.delete_rows(r, count)
        msg = f"{r + 1}행부터 {count}행을 지웠다"
    elif op == "add_cols":
        c = _idx(a.get("col") or tb.nc, "col")
        right = str(a.get("where") or "right") != "left"
        tb.insert_cols(c, count, right, doc.ids)
        msg = f"{c + 1}열 {'오른쪽' if right else '왼쪽'}에 {count}열을 넣었다"
    elif op == "delete_cols":
        c = _idx(a.get("col"), "col")
        tb.delete_cols(c, count)
        msg = f"{c + 1}열부터 {count}열을 지웠다"
    elif op == "set_cells":
        values = a.get("values")
        if not isinstance(values, list) or not values:
            raise EditError("values 는 2차원 배열이다(start 칸부터 채운다).")
        start = a.get("start") or [1, 1]
        if not isinstance(start, (list, tuple)) or len(start) != 2:
            raise EditError("start 는 [행, 열](1부터)이다.")
        r0, c0 = _idx(start[0], "start 행"), _idx(start[1], "start 열")
        n = 0
        for i, row in enumerate(values):
            for j, v in enumerate(row if isinstance(row, list) else [row]):
                if v is None:
                    continue
                if r0 + i >= tb.nr or c0 + j >= tb.nc:
                    raise EditError(f"값이 표({tb.nr}행×{tb.nc}열) 밖으로 나간다 — 먼저 add_rows·add_cols 로 늘린다.")
                tb.set_text(r0 + i, c0 + j, str(v), doc.ids)
                n += 1
        msg = f"칸 {n}개에 값을 넣었다"
    elif op in ("merge", "split"):
        rng = _range(a, tb)
        if not rng:
            raise EditError("cells 로 칸을 짚는다 — merge 는 [첫 행, 첫 열, 끝 행, 끝 열], split 은 [행, 열].")
        if op == "merge":
            tb.merge(rng["rows"][0], rng["cols"][0], rng["rows"][1], rng["cols"][1])
            msg = f"{rng['rows'][0] + 1}행 {rng['cols'][0] + 1}열 ~ {rng['rows'][1] + 1}행 {rng['cols'][1] + 1}열을 합쳤다"
        else:
            tb.split(rng["rows"][0], rng["cols"][0], a.get("rows"), a.get("cols"), doc.ids)
            msg = f"{rng['rows'][0] + 1}행 {rng['cols'][0] + 1}열을 나눴다"
    elif op == "widths":
        tb.set_widths(a.get("widths") or [])
        msg = "열 폭을 " + ":".join(str(x) for x in a.get("widths")) + " 로 맞췄다"
    new_tbl = tb.to_xml()
    if op in ("head", "align", "shade", "valign"):
        new_tbl = _restyle(tb, new_tbl, op, a, doc)
        msg = {"head": f"머리줄을 {a.get('rows', 1)}행으로 했다", "align": "정렬을 바꿨다", "shade": "음영을 바꿨다",
               "valign": "세로 정렬을 바꿨다"}[op]
    _replace_table(doc, t, new_tbl)
    after = _Table(new_tbl)
    return doc.pack(), f"표{t['no']}: {msg} — 지금 {after.nr}행×{after.nc}열."


def _restyle(tb: _Table, tbl: str, op: str, a: dict, doc: _Doc) -> str:
    """칸 범위의 모양 — 머리줄(음영·굵게·가운데·제목 줄 반복), 정렬, 음영, 세로 정렬."""
    tb2 = _Table(tbl)
    hdr = doc.hdr
    if op == "head":
        n = _int(a.get("rows"), "rows(머리줄 행 수)", 0, max(tb2.nr - 1, 0), default=1)
        fill = _color(a.get("fill") or hs.HOUSE["head_fill"])
        targets = [(x, x["r"] < n) for x in tb2.cells if x["r"] < max(n, 1)]
    else:
        targets = [(x, True) for x in tb2.cells_in(_range(a, tb2))]
    for x, on in targets:
        y = x["xml"]
        if op == "head":
            bf = re.search(r'borderFillIDRef="(\d+)"', y).group(1)
            y = re.sub(r'(<hp:tc\b[^>]*\bborderFillIDRef=")\d+', rf"\g<1>{hdr.fill_clone(bf, fill if on else 'none')}", y, count=1)
            y = re.sub(r'(<hp:run\b[^>]*\bcharPrIDRef=")(\d+)(")', lambda m: f"{m.group(1)}{hdr.char(m.group(2), bold=on)}{m.group(3)}", y)
            if on:
                y = re.sub(r'(<hp:p\b[^>]*\bparaPrIDRef=")(\d+)(")', lambda m: f"{m.group(1)}{hdr.para(m.group(2), 'CENTER')}{m.group(3)}", y)
        elif op == "align":
            al = ALIGNS.get(str(a.get("align") or ""), "")
            if not al:
                raise EditError("align 은 가운데|왼쪽|오른쪽|양쪽 이다.")
            y = re.sub(r'(<hp:p\b[^>]*\bparaPrIDRef=")(\d+)(")', lambda m: f"{m.group(1)}{hdr.para(m.group(2), al)}{m.group(3)}", y)
        elif op == "valign":
            va = VALIGNS.get(str(a.get("valign") or a.get("align") or ""), "")
            if not va:
                raise EditError("valign 은 위|가운데|아래 다.")
            y = re.sub(r'(<hp:subList\b[^>]*\bvertAlign=")\w+', rf"\g<1>{va}", y, count=1)
        elif op == "shade":
            bf = re.search(r'borderFillIDRef="(\d+)"', y).group(1)
            y = re.sub(r'(<hp:tc\b[^>]*\bborderFillIDRef=")\d+', rf"\g<1>{hdr.fill_clone(bf, _color(a.get('fill') or a.get('color') or '회색'))}", y, count=1)
        x["xml"] = y
    out = tb2.to_xml()
    if op == "head":
        out = re.sub(r'\brepeatHeader="\d"', f'repeatHeader="{1 if n else 0}"', out, count=1)
    return out


@_guarded
def table_view(data: bytes, a: dict) -> str:
    """표 하나의 칸 글 — 행마다 「r행: 칸 | 칸」, 합친 칸은 [합침 r×c] 로. 고칠 행·열 번호를 정하는 데 쓴다."""
    doc = _Doc(data)
    t = _pick_table(doc, a.get("table"))
    tb = _Table(doc.secs[t["sec"]][t["s"]:t["e"]])
    lines = [f"표{t['no']} — {tb.nr}행×{tb.nc}열" + (f", 필드: {', '.join(t['fields'][:8])}" if t["fields"] else "")]
    for r in range(min(tb.nr, 60)):
        parts = []
        c = 0
        while c < tb.nc:
            x = tb.covering(r, c)
            if x is None:
                parts.append("?")
                c += 1
                continue
            if x["r"] != r:
                parts.append("↑")
            else:
                tag = f"[합침 {x['rs']}×{x['cs']}]" if x["rs"] * x["cs"] > 1 else ""
                parts.append((_text(re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", x["xml"], flags=re.S)) or "(빈 칸)")[:30] + tag)
            c = x["c"] + x["cs"]
        lines.append(f"{r + 1}행: " + " | ".join(parts))
    return "\n".join(lines)


def _first_run_insert(xml: str, ctrl: str, drop: str) -> str:
    """구역 첫 문단(secPr 가 든 문단)에 조종 개체(쪽 번호·머리말)를 넣는다. drop 꼴의 옛 것은 지운다."""
    xml = re.sub(drop, "", xml, flags=re.S)
    m = re.search(r"</hp:secPr>(?:\s*<hp:ctrl>\s*<hp:colPr\b[^>]*/>\s*</hp:ctrl>)?", xml)
    if not m:
        raise EditError("구역 정의(secPr)를 찾지 못했다.")
    return xml[:m.end()] + ctrl + xml[m.end():]


@_guarded
def page_number(data: bytes, a: dict) -> tuple[bytes, str]:
    doc = _Doc(data)
    pos_in = str(a.get("position") or "아래 가운데")
    pos = PAGE_NUM_POS.get(pos_in) or (pos_in if pos_in in PAGE_NUM_POS.values() else "")
    off = pos_in in ("없음", "none", "NONE")
    if not pos and not off:
        raise EditError(f"position 은 {', '.join(PAGE_NUM_POS)} 또는 없음 이다.")
    fmt_in = str(a.get("format") or "1")
    fmt = PAGE_NUM_FMT.get(fmt_in) or (fmt_in if fmt_in in PAGE_NUM_FMT.values() else "")
    if not fmt:
        raise EditError(f"format 은 {', '.join(PAGE_NUM_FMT)} 가운데 하나다.")
    side = "-" if a.get("dash") in (True, "true", "1", 1, "-") else ""
    n = doc.sec_names[0]
    ctrl = "" if off else f'<hp:ctrl><hp:pageNum pos="{pos}" formatType="{fmt}" sideChar="{side}"/></hp:ctrl>'
    xml = _first_run_insert(doc.secs[n], ctrl, r"<hp:ctrl><hp:pageNum\b[^>]*/></hp:ctrl>")
    for other in doc.sec_names[1:]:
        doc.secs[other] = re.sub(r"<hp:ctrl><hp:pageNum\b[^>]*/></hp:ctrl>", "", doc.secs[other])
    if a.get("start") not in (None, ""):
        try:
            start = int(a["start"])
        except (TypeError, ValueError):
            raise EditError("start 는 시작 쪽 번호(숫자)다.")
        xml = re.sub(r'(<hp:startNum\b[^>]*\bpage=")\d+', rf"\g<1>{max(0, start)}", xml, count=1)
    doc.secs[n] = xml
    return doc.pack(), ("쪽 번호를 뺐다." if off else f"쪽 번호: {pos_in} · 꼴 {fmt_in}{' · 줄표' if side else ''}"
                        + (f" · {a['start']}쪽부터" if a.get("start") not in (None, "") else "") + ".")


@_guarded
def header_footer(data: bytes, a: dict) -> tuple[bytes, str]:
    doc = _Doc(data)
    n = doc.sec_names[0]
    xml = doc.secs[n]
    pp, cp = doc.refs()
    out = []
    width = _width_at(xml, 0)
    al = ALIGNS.get(str(a.get("align") or ""), "")
    pp2 = doc.hdr.para_ex(pp, align=al) if al else pp
    for kind, valign, mkey in (("header", "TOP", "header"), ("footer", "BOTTOM", "footer")):
        if kind not in a or a.get(kind) is None:
            continue
        text = str(a.get(kind) or "")
        drop = rf'<hp:ctrl><hp:{kind}\b[^>]*applyPageType="BOTH"[^>]*>.*?</hp:{kind}></hp:ctrl>'
        if not text.strip():
            xml = re.sub(drop, "", xml, flags=re.S)
            out.append(f"{'머리말' if kind == 'header' else '꼬리말'}을 뺐다")
            continue
        h = re.search(rf'<hp:margin\b[^>]*\b{mkey}="(\d+)"', xml)
        paras = "".join(hf.paragraph_xml(ln, pp2, cp).replace('<hp:p id="0"', f'<hp:p id="{doc.ids.new()}"', 1)
                        for ln in hf.split_lines(text)[:5])
        ctrl = (f'<hp:ctrl><hp:{kind} id="{doc.ids.new()}" applyPageType="BOTH"><hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
                f'vertAlign="{valign}" linkListIDRef="0" linkListNextIDRef="0" textWidth="{width}" textHeight="{h.group(1) if h else 4252}" '
                f'hasTextRef="0" hasNumRef="0">{paras}</hp:subList></hp:{kind}></hp:ctrl>')
        xml = _first_run_insert(xml, ctrl, drop)
        out.append(f"{'머리말' if kind == 'header' else '꼬리말'} 「{text[:30]}」")
    if not out:
        raise EditError("header·footer 가운데 하나를 준다(빈 글이면 그것을 뺀다).")
    doc.secs[n] = xml
    return doc.pack(), ", ".join(out) + "."


def _target_para(doc: _Doc, a: dict) -> tuple[str, int, int]:
    """짚은 문단 — field(절이면 그 제목 문단, 칸이면 필드 문단) 또는 text(그 글이 든 문단)."""
    if a.get("field"):
        n, pos = _find_field(doc, str(a["field"]))
        xml = doc.secs[n]
        me, _ = _siblings(xml, pos)
        name = _resolve_field(doc, str(a["field"]))
        if name.startswith("절:"):                         # 절 필드는 제목 아래 문단 — 제목 문단을 짚는다(사이의 작성방법 상자는 건너뛴다)
            ps = [p for p in _paras(xml) if p[2] == me[2] and p[1] <= me[0] and not _is_guide_para(xml[p[0]:p[1]])]
            if ps:
                return n, ps[-1][0], ps[-1][1]
        return n, me[0], me[1]
    if a.get("text"):
        return _find_text(doc, str(a["text"]))
    raise EditError("field 나 text 로 문단을 짚는다.")


@_guarded
def page_break(data: bytes, a: dict) -> tuple[bytes, str]:
    doc = _Doc(data)
    n, s, e = _target_para(doc, a)
    on = a.get("on", True) not in (False, "false", "0", 0)
    xml = doc.secs[n]
    p = xml[s:e]
    p2 = re.sub(r'(<hp:p\b[^>]*\bpageBreak=")\d', rf"\g<1>{1 if on else 0}", p, count=1)
    doc.secs[n] = xml[:s] + _strip_ls(p2) + xml[e:]
    return doc.pack(), f"「{_text(p)[:30]}」 앞에서 쪽을 {'나눴다' if on else '나누지 않게 했다'}."


def _scope_spans(doc: _Doc, a: dict) -> list[tuple[str, int, int]]:
    scope = a.get("scope") or "all"
    if scope == "all":
        return [(n, 0, len(doc.secs[n])) for n in doc.sec_names]
    if isinstance(scope, dict) and scope.get("field") or isinstance(scope, str) and scope.startswith(("절:", "칸:")):
        name = scope["field"] if isinstance(scope, dict) else scope
        if _resolve_field(doc, name).startswith("절:"):
            return [_section_span(doc, name)]
        n, pos = _find_field(doc, name)
        me, _ = _siblings(doc.secs[n], pos)
        return [(n, me[0], me[1])]
    if isinstance(scope, dict) and scope.get("text"):
        return [_find_text(doc, scope["text"])]
    raise EditError("scope 는 all | {field: 절·칸 필드 이름} | {text: 문단 글} 이다.")


def _in_table(xml: str, pos: int) -> bool:
    return xml.rfind("<hp:tbl", 0, pos) > xml.rfind("</hp:tbl>", 0, pos)


@_guarded
def text_style(data: bytes, a: dict) -> tuple[bytes, str]:
    """글꼴·글자 크기·굵게(글자 모양)와 줄 간격·정렬·들여쓰기(문단 모양)를 범위에 한꺼번에."""
    doc = _Doc(data)
    face = str(a.get("font") or "").strip()[:40]
    pt = a.get("pt")
    bold = a.get("bold") if isinstance(a.get("bold"), bool) else None
    line = a.get("line_spacing")
    al = ALIGNS.get(str(a.get("align") or ""), "")
    left = a.get("left_mm")
    indent = a.get("indent_mm")
    try:
        pt = float(pt) if pt not in (None, "") else None
        line = int(float(line)) if line not in (None, "") else None
        left = hu(float(left)) if left not in (None, "") else None
        indent = hu(float(indent)) if indent not in (None, "") else None
    except (TypeError, ValueError):
        raise EditError("pt·line_spacing·left_mm·indent_mm 는 숫자다.")
    if pt is not None and not 4 <= pt <= 72:
        raise EditError("글자 크기는 4~72pt 다.")
    if line is not None and not 50 <= line <= 500:
        raise EditError("줄 간격은 50~500% 다.")
    if a.get("align") and not al:
        raise EditError("align 은 가운데|왼쪽|오른쪽|양쪽|배분 이다.")
    if not (face or pt or bold is not None or line or al or left is not None or indent is not None):
        raise EditError("바꿀 것(font·pt·bold·line_spacing·align·left_mm·indent_mm)을 하나 이상 준다.")
    tables = a.get("tables") is True
    changed = seen = 0
    for n, s, e in _scope_spans(doc, a):
        xml = doc.secs[n]
        out, pos = [], 0
        for ps, pe, _d in _paras(xml):
            if ps < s or pe > e or re.search(r"<hp:p\b", xml[ps + 1:pe]):
                continue                                      # 범위 밖, 표를 품은 문단(안쪽 문단이 따로 잡힌다)
            if ps < pos:
                continue
            if not tables and _in_table(xml, ps):
                continue
            if "<hp:secPr" in xml[ps:pe] or re.search(r"<hp:(header|footer|footNote)\b", xml[max(0, ps - 2000):ps]) and \
                    xml.rfind("<hp:subList", 0, ps) > xml.rfind("</hp:subList>", 0, ps) and not _in_table(xml, ps):
                continue                                      # 구역 첫 문단·머리말 안은 그대로
            p = xml[ps:pe]
            q = p
            seen += 1
            if face or pt or bold is not None:
                q = re.sub(r'(<hp:run\b[^>]*\bcharPrIDRef=")(\d+)(")',
                           lambda m: f"{m.group(1)}{doc.hdr.char_ex(m.group(2), pt=pt, face=face, bold=bold)}{m.group(3)}", q)
            if line or al or left is not None or indent is not None:
                q = re.sub(r'(<hp:p\b[^>]*\bparaPrIDRef=")(\d+)(")',
                           lambda m: f"{m.group(1)}{doc.hdr.para_ex(m.group(2), align=al, line=line, left=left, indent=indent)}{m.group(3)}",
                           q, count=1)
            if q != p:
                out.append(xml[pos:ps])
                out.append(wf._LINESEG.sub("", q))
                pos = pe
                changed += 1
        out.append(xml[pos:])
        doc.secs[n] = "".join(out)
    if not changed:
        if seen:                                              # 같은 연산을 두 번 — 이미 그 모양이면 바꾸지 않고 그대로(멱등)
            return data, f"문단 {seen}개가 이미 그 모양이다(바꾼 것 없음)."
        raise EditError("범위 안에 바꿀 문단이 없다.")
    what = [x for x in (f"글꼴 {face}" if face else "", f"{pt:g}pt" if pt else "", "굵게" if bold else ("굵게 끔" if bold is False else ""),
                        f"줄 간격 {line}%" if line else "", f"정렬 {a.get('align')}" if al else "",
                        f"왼쪽 여백 {a.get('left_mm')}mm" if left is not None else "", f"첫 줄 {a.get('indent_mm')}mm" if indent is not None else "") if x]
    return doc.pack(), f"문단 {changed}개에 {', '.join(what)} 을 입혔다."


@_guarded
def footnote(data: bytes, a: dict) -> tuple[bytes, str]:
    doc = _Doc(data)
    text = str(a.get("text") or "").strip()
    if not text:
        raise EditError("text(각주 글)가 비었다.")
    pp, cp = doc.refs()
    if a.get("field"):
        n, pos = _find_field(doc, str(a["field"]))
        name = _resolve_field(doc, str(a["field"]))
        xml = doc.secs[n]
        fb = _FIELD.search(xml, pos)
        end = re.search(rf'<hp:ctrl><hp:fieldEnd\b[^>]*beginIDRef="{fb.group(1)}"[^>]*/></hp:ctrl>', xml[pos:])
        at = pos + end.end() if end else None
        label = name
    else:
        want = re.sub(r"\s+", " ", str(a.get("after_text") or "")).strip()
        n, s, e = _find_text(doc, want)
        xml = doc.secs[n]
        at = None
        for t in re.finditer(r"<hp:t>([^<]*)</hp:t>", xml[s:e]):     # 그 글이 끝나는 run 글 바로 뒤
            if want[-6:].strip() and want[-6:].strip() in _unq(t.group(1)):
                at = s + t.end()
        if at is None:
            close = xml.rfind("</hp:run>", s, e)
            at = close if close != -1 else None
        label = want[:30]
    if at is None:
        raise EditError("각주를 달 자리를 찾지 못했다.")
    num = len(re.findall(r"<hp:footNote\b", "".join(doc.secs.values()))) + 1
    body = (f'<hp:p id="{doc.ids.new()}" paraPrIDRef="{pp}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="{cp}">'
            f'<hp:ctrl><hp:autoNum num="{num}" numType="FOOTNOTE"><hp:autoNumFormat type="DIGIT" userChar="" prefixChar="" suffixChar=")" supscript="0"/>'
            f'</hp:autoNum></hp:ctrl><hp:t> {escape(text[:500])}</hp:t></hp:run></hp:p>')
    ctrl = (f'<hp:ctrl><hp:footNote number="{num}" suffixChar="41" instId="{doc.ids.new()}"><hp:subList id="" textDirection="HORIZONTAL" '
            f'lineWrap="BREAK" vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" '
            f'hasNumRef="0">{body}</hp:subList></hp:footNote></hp:ctrl>')
    xml = xml[:at] + ctrl + xml[at:]
    ps = [p for p in _paras(xml) if p[0] < at < p[1]]
    if ps:
        p = min(ps, key=lambda p: p[1] - p[0])
        xml = xml[:p[0]] + _strip_ls(xml[p[0]:p[1]]) + xml[p[1]:]
    doc.secs[n] = xml
    return doc.pack(), f"「{label}」 뒤에 각주 {num}) 을 달았다."


@_guarded
def picture(data: bytes, img: bytes, a: dict) -> tuple[bytes, str]:
    doc = _Doc(data)
    kind = hf.image_kind(img or b"")
    if not kind:
        raise EditError("그림 파일(png·jpg·gif·bmp)이 아니다.")
    if doc.hpf is None:
        raise EditError("매니페스트(content.hpf)가 없는 파일이라 그림을 넣을 수 없다.")
    ext, media = kind
    used = set(hf._MANIFEST_ID.findall(doc.hpf)) | {Path(x).stem for x in doc.zin.namelist() if x.startswith("BinData/")}
    k = 1
    while f"image{k}" in used:
        k += 1
    bin_id = f"image{k}"
    doc.added[f"BinData/{bin_id}.{ext}"] = img
    doc.hpf = doc.hpf.replace("</opf:manifest>", f'<opf:item id="{bin_id}" href="BinData/{bin_id}.{ext}" media-type="{media}" isEmbeded="1"/></opf:manifest>', 1)
    n, pos, width = _anchor(doc, a.get("at"))
    px = hf._image_px(img)
    org_w, org_h = (px[0] * 75, px[1] * 75) if px else (width, width // 2)
    try:
        w = hu(float(a["width_mm"])) if a.get("width_mm") not in (None, "") else min(org_w, width)
    except (TypeError, ValueError):
        raise EditError("width_mm 는 숫자다.")
    w = max(min(w, width), hu(10))
    h = int(w * org_h / org_w)
    pp, cp = doc.refs()
    al = ALIGNS.get(str(a.get("align") or "가운데"), "CENTER")
    pxml = hf.picture_xml(bin_id, int(doc.ids.new()), w, h, org_w, org_h, doc.hdr.para(pp, al), cp)
    pxml = pxml.replace('<hp:p id="0"', f'<hp:p id="{doc.ids.new()}"', 1)
    xml = doc.secs[n]
    doc.secs[n] = xml[:pos] + pxml + xml[pos:]
    return doc.pack(), f"그림을 넣었다(폭 {mm_of(w)}mm)."


@_guarded
def replace_text(data: bytes, a: dict) -> tuple[bytes, str]:
    """문서 전체(또는 범위)에서 낱말 바꾸기 — 글 조각(<hp:t>) 안의 글만 바꾼다. 필드 이름·안내 글(parameters)은 건드리지 않는다.
    글이 글자 모양 경계로 쪼개져 있으면(한 낱말에 두 모양) 그 자리는 찾지 못한다 — 찾은 곳 수를 돌려준다."""
    doc = _Doc(data)
    find = str(a.get("find") or "")
    if not find.strip():
        raise EditError("find(찾을 글)가 비었다.")
    if len(find) > 200:
        raise EditError("찾을 글은 200자까지다.")
    rep = str(a.get("replace") if a.get("replace") is not None else "")[:2000]
    want = escape(find)
    total = 0
    for n, s, e in _scope_spans(doc, a):
        xml = doc.secs[n]
        out, pos = [], 0
        for ps, pe, _d in _paras(xml):
            if ps < s or pe > e or ps < pos or re.search(r"<hp:p\b", xml[ps + 1:pe]):
                continue
            p = xml[ps:pe]
            k = [0]

            def sub(m: re.Match) -> str:
                body = m.group(2)
                c = body.count(want)
                if not c:
                    return m.group(0)
                k[0] += c
                return m.group(1) + body.replace(want, escape(rep)) + m.group(3)
            q = re.sub(r"(<hp:t>)([^<]*)(</hp:t>)", sub, p)
            if k[0]:
                out.append(xml[pos:ps])
                out.append(wf._LINESEG.sub("", q))
                pos = pe
                total += k[0]
        out.append(xml[pos:])
        doc.secs[n] = "".join(out)
    if not total:
        raise EditError(f"「{find[:30]}」 을 찾지 못했다(0곳).")
    return doc.pack(), f"「{find[:30]}」 → 「{rep[:30]}」 {total}곳을 바꿨다."


def count_text(data: bytes, find: str) -> int:
    doc = _Doc(data)
    want = escape(find)
    return sum(m.group(1).count(want) for n in doc.sec_names for m in re.finditer(r"<hp:t>([^<]*)</hp:t>", doc.secs[n])) if find else 0


# ── 저장(판 남기기 · 점검 · 되돌리기) ──
UNDO_KEEP = 10


def _snapshot(db, base: Path, fid: int) -> str:
    """고치기 전 파일을 판(.판/)으로 남긴다 — webhwp._versions 이름 꼴 그대로(문서함 「앞 판」 에 같이 나온다)."""
    f = webhwp.get(db, fid)
    p = Path(f["path"])
    vdir = p.parent / ".판"
    vdir.mkdir(exist_ok=True)
    olds = webhwp._versions(vdir, p)
    if olds and olds[-1].read_bytes() == p.read_bytes():
        return str(olds[-1])
    dest = vdir / f"{p.stem} {datetime.now():%Y%m%d-%H%M%S}{p.suffix}"
    k = 1
    while dest.exists():
        k += 1
        dest = vdir / f"{p.stem} {datetime.now():%Y%m%d-%H%M%S}-{k}{p.suffix}"
    shutil.copy2(p, dest)
    return str(dest)


def check_bytes(data: bytes) -> list[str]:
    """고친 결과의 자가 점검 — hwpx_fill.check(ZIP·웰폼드·참조 id·itemCnt·표 rowCnt·덮개 합)에 더해
    누름틀 쌍(여는 것마다 닫는 것 하나, id 겹침 없음)과 표 격자(칸 주소·합침으로 모든 자리를 꼭 한 칸이 덮는지)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.hwpx"
        p.write_bytes(data)
        probs = hf.check(p)
    if probs:
        return probs
    return structure_problems(data)


def structure_problems(data: bytes) -> list[str]:
    """누름틀 쌍과 표 격자(칸 주소 기준) 점검 — 문제 목록(비면 통과)."""
    import xml.etree.ElementTree as ET
    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    probs: list[str] = []
    z = zipfile.ZipFile(io.BytesIO(data))
    for n in z.namelist():
        if not _SEC.search(n):
            continue
        x = z.read(n).decode("utf-8")
        begins = re.findall(r'<hp:fieldBegin\b[^>]*\bid="(\d+)"', x)
        ends = re.findall(r'<hp:fieldEnd\b[^>]*\bbeginIDRef="(\d+)"', x)
        if len(set(begins)) != len(begins):
            probs.append(f"{n}: 누름틀 id 가 겹친다")
        if sorted(begins) != sorted(ends):
            probs.append(f"{n}: 누름틀 여닫이가 맞지 않는다(여는 것 {len(begins)}, 닫는 것 {len(ends)})")
        try:
            root = ET.fromstring(x.encode("utf-8"))
        except ET.ParseError as e:
            probs.append(f"{n}: 웰폼드 아님 {e}")
            continue
        for tbl in root.iter(hp + "tbl"):
            rc, cc = int(tbl.get("rowCnt") or 0), int(tbl.get("colCnt") or 0)
            cover = [[0] * cc for _ in range(rc)]
            bad = False
            for tr in (e for e in tbl if e.tag == hp + "tr"):
                for tc in (e for e in tr if e.tag == hp + "tc"):
                    a, sp = tc.find(hp + "cellAddr"), tc.find(hp + "cellSpan")
                    if a is None:
                        bad = True
                        continue
                    r0, c0 = int(a.get("rowAddr") or 0), int(a.get("colAddr") or 0)
                    rs = int(sp.get("rowSpan") or 1) if sp is not None else 1
                    cs = int(sp.get("colSpan") or 1) if sp is not None else 1
                    if rs < 1 or cs < 1 or r0 + rs > rc or c0 + cs > cc:
                        bad = True
                        continue
                    for i in range(r0, r0 + rs):
                        for j in range(c0, c0 + cs):
                            cover[i][j] += 1
            if bad or any(v != 1 for row in cover for v in row):
                probs.append(f"{n}: 표 {tbl.get('id')} 격자가 어긋났다(빈 자리·겹친 칸·표 밖 칸)")
    return probs


def apply(db, base: Path, fid: int, fn, *, user: str = "AURA") -> str:
    """연산 하나를 파일에 — 고치기 전 판을 남기고, 점검을 통과할 때만 저장한다. 실패면 까닭 글(파일 그대로)."""
    f = webhwp.get(db, fid)
    if not f or not Path(f["path"]).exists():
        return "작업본 파일이 없다."
    if webhwp.locked(db, fid):
        return "공통 양식은 고칠 수 없다 — 복사해 쓴다."
    data = Path(f["path"]).read_bytes()
    try:
        new, summary = fn(data)
    except EditError as e:
        return "바꾸지 않았다 — " + str(e)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as e:     # 도구 밖 연산(쓰기·서식)도 파일은 그대로
        return f"바꾸지 않았다 — 인자를 읽지 못했다({type(e).__name__}: {str(e)[:80]})"
    if new == data:
        return "바뀐 것이 없다."
    probs = check_bytes(new)
    if probs:
        return "바꾸지 않았다 — 고친 파일이 점검을 통과하지 못했다: " + "; ".join(probs[:3])
    snap = _snapshot(db, base, fid)
    webhwp.save(db, base, fid, new, user=user)
    m = webhwp.meta(db, fid)
    undo = [x for x in (m.get("aura_undo") or []) if Path(x).exists()]
    m["aura_undo"] = (undo + [snap])[-UNDO_KEEP:]
    db.set_setting(f"webhwp_meta:{int(fid)}", json.dumps(m, ensure_ascii=False))
    return summary


def remember(db, base: Path, fid: int) -> None:
    """열린 기안기에 명령을 넘기기 전 — 지금 파일을 판으로 남기고 되돌리기 목록에 올린다(hwp_undo 가 이 판으로 돌아간다)."""
    if not webhwp.get(db, fid):
        return
    snap = _snapshot(db, base, fid)
    m = webhwp.meta(db, fid)
    undo_ = [x for x in (m.get("aura_undo") or []) if Path(x).exists() and x != snap]
    m["aura_undo"] = (undo_ + [snap])[-UNDO_KEEP:]
    db.set_setting(f"webhwp_meta:{int(fid)}", json.dumps(m, ensure_ascii=False))


def undo(db, base: Path, fid: int, *, user: str = "AURA") -> str:
    """AURA 가 마지막으로 고치기 전 판으로 되돌린다(지금 것도 판으로 남는다)."""
    m = webhwp.meta(db, fid)
    stack = [x for x in (m.get("aura_undo") or []) if Path(x).exists()]
    if not stack:
        return "되돌릴 AURA 의 고침이 없다(문서함의 「앞 판」 에서 고른다)."
    prev = Path(stack.pop())
    _snapshot(db, base, fid)
    webhwp.save(db, base, fid, prev.read_bytes(), user=user)
    m = webhwp.meta(db, fid)
    m["aura_undo"] = stack
    db.set_setting(f"webhwp_meta:{int(fid)}", json.dumps(m, ensure_ascii=False))
    return f"AURA 가 고치기 전({prev.stem.rsplit(' ', 1)[-1]})으로 되돌렸다."


# ── 열린 기안기에 명령(B) ──
# 허용 목록 — 기안기(13.82)에서 동작을 하나씩 돌려 결과 XML 로 확인한 것만(10-22). 단계는 편집 화면이 그대로 실행한다:
#   ["run", 이름] = ctrl.Run, ["exec", 이름, {항목: 값}] = CreateAction → CreateSet → GetDefault → SetItem → Execute,
#   ["text", 글] = InsertText, ["field", 이름, 고르기] = MoveToField.
# 파일 열기·저장·다른 이름·인쇄·끼워 넣기 파일·하이퍼링크·대화 상자(PopupDialog)는 없다. 되돌리기(Undo)는 API 로 듣지 않아 뺐다 —
# 되돌릴 때는 명령 전에 남긴 판을 쓴다(hwp_undo).
LIVE_ACTIONS: dict[str, dict] = {
    "page_setup": {"desc": "용지·방향·여백(mm)", "params": ["paper", "orientation", "margin", "margins_mm"]},
    "page_number": {"desc": "쪽 번호 위치·꼴", "params": ["position", "format", "dash"]},
    "header": {"desc": "머리말 글", "params": ["text"]},
    "footer": {"desc": "꼬리말 글", "params": ["text"]},
    "footnote": {"desc": "커서 자리(field 끝)에 각주", "params": ["field", "text"]},
    "insert_text": {"desc": "커서 자리(field)에 글 넣기", "params": ["field", "text"]},
    "page_break": {"desc": "커서 자리(field)에서 쪽 나누기", "params": ["field"]},
    "bold": {"desc": "필드 글 굵게(다시 하면 끔)", "params": ["field"]},
    "italic": {"desc": "필드 글 기울임", "params": ["field"]},
    "underline": {"desc": "필드 글 밑줄", "params": ["field"]},
    "text_color": {"desc": "필드 글 색(빨강·파랑·검정)", "params": ["field", "color"]},
    "char_shape": {"desc": "필드 글 글꼴·크기(pt)", "params": ["field", "font", "pt"]},
    "paragraph": {"desc": "필드 문단 정렬·줄 간격·왼쪽 여백(mm)", "params": ["field", "align", "line_spacing", "left_mm"]},
    "indent": {"desc": "필드 문단 들여쓰기 한 단계", "params": ["field"]},
    "table_create": {"desc": "필드 자리 뒤에 행×열 표", "params": ["field", "rows", "cols"]},
    "table_row": {"desc": "필드가 든 칸 위·아래에 행 넣기 / 그 행 지우기", "params": ["field", "where"]},
    "table_col": {"desc": "필드가 든 칸 왼쪽·오른쪽에 열 넣기 / 그 열 지우기", "params": ["field", "where"]},
    "table_append_row": {"desc": "필드가 든 표 끝에 행 더하기", "params": ["field"]},
    "table_merge": {"desc": "필드가 든 칸부터 오른쪽 right 칸·아래 down 칸까지 합치기", "params": ["field", "right", "down"]},
    "table_split": {"desc": "필드가 든 칸을 rows×cols 로 나누기", "params": ["field", "rows", "cols"]},
    "table_distribute": {"desc": "필드가 든 표의 칸 폭·높이 고르게", "params": ["field", "what"]},
    "cell_fill": {"desc": "필드가 든 칸 음영 색", "params": ["field", "color"]},
}
_PRIMS = {"run": {"CharShapeBold", "CharShapeItalic", "CharShapeUnderline", "CharShapeTextColorRed", "CharShapeTextColorBlue",
                  "CharShapeTextColorBlack", "ParagraphShapeIncreaseLeftMargin", "BreakPage", "TableInsertUpperRow", "TableInsertLowerRow",
                  "TableInsertLeftColumn", "TableInsertRightColumn", "TableDeleteRow", "TableDeleteColumn", "TableAppendRow",
                  "TableCellBlock", "TableCellBlockExtend", "TableRightCell", "TableLowerCell", "TableMergeCell",
                  "TableDistributeCellWidth", "TableDistributeCellHeight", "MoveLineEnd", "MoveLineBegin", "MoveSelLineBegin",
                  "MoveSelLineEnd", "InsertFootnote", "CloseEx", "Cancel"},
          "exec": {"PageSetup", "PageNumPos", "HeaderFooter", "InsertText", "CharShape", "ParagraphShape", "TableCreate",
                   "TableSplitCell", "CellBorderFill"}}
_PN_DRAW = {"TOP_LEFT": 1, "TOP_CENTER": 2, "TOP_RIGHT": 3, "BOTTOM_LEFT": 4, "BOTTOM_CENTER": 5, "BOTTOM_RIGHT": 6,
            "OUTSIDE_TOP": 7, "OUTSIDE_BOTTOM": 8, "INSIDE_TOP": 9, "INSIDE_BOTTOM": 10}
_PN_FMT = {"DIGIT": 0, "CIRCLED_DIGIT": 1, "ROMAN_CAPITAL": 2, "ROMAN_SMALL": 3, "LATIN_CAPITAL": 4, "LATIN_SMALL": 5}
_ALIGN_TYPE = {"JUSTIFY": 0, "LEFT": 1, "RIGHT": 2, "CENTER": 3, "DISTRIBUTE": 4}


def _bgr(color: str) -> int:
    """#RRGGBB → 기안기 색 값(0xBBGGRR)."""
    c = _color(color)
    if not re.fullmatch(r"#[0-9A-F]{6}", c):
        c = "#E5E5E5"
    r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
    return (b << 16) | (g << 8) | r


def live_steps(action: str, p: dict) -> list[list]:
    """허용 동작 + 인자 → 편집 화면이 실행할 단계. 모르는 동작·인자는 EditError."""
    if action not in LIVE_ACTIONS:
        raise EditError(f"기안기에서 쓸 수 있는 동작이 아니다 — {', '.join(LIVE_ACTIONS)}.")
    p = {k: v for k, v in (p or {}).items() if k in LIVE_ACTIONS[action]["params"]}
    field = str(p.get("field") or "")
    at = [["field", field, False]] if field else []
    sel = [["field", field, True]] if field else []
    num = lambda k, d, lo, hi: max(lo, min(hi, int(float(p.get(k) if p.get(k) not in (None, "") else d))))
    try:
        if action == "page_setup":
            items = {}
            if p.get("paper"):
                key = next((k for k in PAPERS if k.lower() == str(p["paper"]).lower()), None)
                if not key:
                    raise EditError(f"용지는 {', '.join(PAPERS)} 가운데 하나다.")
                items["PageDef.PaperWidth"], items["PageDef.PaperHeight"] = hu(PAPERS[key][0]), hu(PAPERS[key][1])
            if p.get("orientation"):
                items["PageDef.Landscape"] = 1 if str(p["orientation"]) in ("가로", "landscape") else 0
            mm = dict(MARGIN_PRESETS.get(str(p.get("margin") or ""), {}))
            mm.update({k: float(v) for k, v in (p.get("margins_mm") or {}).items() if k in _MARGIN_KEYS and v not in (None, "")})
            names = {"top": "TopMargin", "bottom": "BottomMargin", "left": "LeftMargin", "right": "RightMargin",
                     "header": "HeaderLen", "footer": "FooterLen", "gutter": "GutterLen"}
            for k, v in mm.items():
                if not 0 <= v <= 100:
                    raise EditError("여백은 0~100mm 다.")
                items[f"PageDef.{names[k]}"] = hu(v)
            if not items:
                raise EditError("바꿀 것(paper·orientation·margin·margins_mm)을 준다.")
            return [["exec", "PageSetup", items]]
        if action == "page_number":
            pos = PAGE_NUM_POS.get(str(p.get("position") or "아래 가운데"))
            fmt = PAGE_NUM_FMT.get(str(p.get("format") or "1"))
            if not pos or fmt not in _PN_FMT:
                raise EditError("position·format 이 허용 값이 아니다.")
            items = {"DrawPos": _PN_DRAW[pos], "NumberFormat": _PN_FMT[fmt]}
            if p.get("dash"):
                items["SideChar"] = 0x2D
            return [["exec", "PageNumPos", items]]
        if action in ("header", "footer"):
            text = str(p.get("text") or "").strip()[:200]
            if not text:
                raise EditError("text 가 비었다.")
            return [["exec", "HeaderFooter", {"HeaderFooterCtrlType": 0 if action == "header" else 1, "Type": 0}],
                    ["text", text], ["run", "CloseEx"]]
        if action == "footnote":
            text = str(p.get("text") or "").strip()[:500]
            if not text or not field:
                raise EditError("field 와 text 를 준다.")
            return [["field", field, False], ["run", "MoveLineEnd"], ["run", "InsertFootnote"], ["text", text], ["run", "CloseEx"]]
        if action == "insert_text":
            text = str(p.get("text") or "")[:2000]
            if not text or not field:
                raise EditError("field 와 text 를 준다.")
            return at + [["text", text]]
        if action == "page_break":
            return at + [["run", "BreakPage"]]
        if action in ("bold", "italic", "underline"):
            return sel + [["run", {"bold": "CharShapeBold", "italic": "CharShapeItalic", "underline": "CharShapeUnderline"}[action]], ["run", "Cancel"]]
        if action == "text_color":
            name = {"빨강": "Red", "파랑": "Blue", "검정": "Black"}.get(str(p.get("color") or ""), "")
            if not name:
                raise EditError("color 는 빨강|파랑|검정 이다.")
            return sel + [["run", f"CharShapeTextColor{name}"], ["run", "Cancel"]]
        if action == "char_shape":
            items = {}
            if p.get("pt") not in (None, ""):
                items["Height"] = int(round(max(4.0, min(72.0, float(p["pt"]))) * 100))
            if p.get("font"):
                items["FaceNameHangul"], items["FontTypeHangul"] = str(p["font"])[:40], 1
            if not items:
                raise EditError("font·pt 가운데 하나를 준다.")
            return sel + [["exec", "CharShape", items], ["run", "Cancel"]]
        if action == "paragraph":
            items = {}
            if p.get("align"):
                al = ALIGNS.get(str(p["align"]))
                if al not in _ALIGN_TYPE:
                    raise EditError("align 은 가운데|왼쪽|오른쪽|양쪽|배분 이다.")
                items["AlignType"] = _ALIGN_TYPE[al]
            if p.get("line_spacing") not in (None, ""):
                items["LineSpacing"] = num("line_spacing", 160, 50, 500)
            if p.get("left_mm") not in (None, ""):
                items["LeftMargin"] = hu(max(0.0, min(100.0, float(p["left_mm"]))))
            if not items:
                raise EditError("align·line_spacing·left_mm 가운데 하나를 준다.")
            return at + [["exec", "ParagraphShape", items]]
        if action == "indent":
            return at + [["run", "ParagraphShapeIncreaseLeftMargin"]]
        if not field:
            raise EditError("표 동작은 field(그 표 칸의 필드)를 준다.")
        if action == "table_create":
            return at + [["run", "MoveLineEnd"], ["exec", "TableCreate", {"Rows": num("rows", 2, 1, 100), "Cols": num("cols", 2, 1, 30)}]]
        if action == "table_row":
            w = str(p.get("where") or "아래")
            run = {"위": "TableInsertUpperRow", "아래": "TableInsertLowerRow", "지우기": "TableDeleteRow"}.get(w)
            if not run:
                raise EditError("where 는 위|아래|지우기 다.")
            return at + [["run", run]]
        if action == "table_col":
            w = str(p.get("where") or "오른쪽")
            run = {"왼쪽": "TableInsertLeftColumn", "오른쪽": "TableInsertRightColumn", "지우기": "TableDeleteColumn"}.get(w)
            if not run:
                raise EditError("where 는 왼쪽|오른쪽|지우기 다.")
            return at + [["run", run]]
        if action == "table_append_row":
            return at + [["run", "TableAppendRow"]]
        if action == "table_merge":
            right, down = num("right", 0, 0, 30), num("down", 0, 0, 100)
            if not right and not down:
                raise EditError("right·down 가운데 하나는 1 이상.")
            return at + [["run", "TableCellBlock"], ["run", "TableCellBlockExtend"]] + [["run", "TableRightCell"]] * right \
                + [["run", "TableLowerCell"]] * down + [["run", "TableMergeCell"], ["run", "Cancel"]]
        if action == "table_split":
            return at + [["exec", "TableSplitCell", {"Rows": num("rows", 1, 1, 20), "Cols": num("cols", 2, 1, 20)}]]
        if action == "table_distribute":
            w = str(p.get("what") or "폭")
            return at + [["run", "TableDistributeCellHeight" if w == "높이" else "TableDistributeCellWidth"]]
        if action == "cell_fill":
            return at + [["exec", "CellBorderFill", {"FillAttr.Type": 1, "FillAttr.WinBrushFaceColor": _bgr(str(p.get("color") or "회색")),
                                                     "FillAttr.WinBrushHatchColor": 0, "FillAttr.WinBrushFaceStyle": -1}]]
    except (TypeError, ValueError):
        raise EditError("숫자 인자가 숫자가 아니다.")
    raise EditError("알 수 없는 동작.")


def steps_allowed(steps: list, runner: int = 1) -> bool:
    """단계가 모두 허용 원소인가 — 편집 화면에 넘기기 전 마지막 관문(명령 줄에 직접 쓴 것도 여기서 막는다).
    runner 1 = 편집 화면(hwp_edit.html)에 든 예전 실행기(run·exec·text·field 와 _PRIMS 만), 2 = static/webhwp-agent-runner.js(webhwp_native 허용 목록)."""
    if runner >= 2:
        return _nat.steps_ok(steps)
    for st in steps or []:
        if not isinstance(st, list) or not st:
            return False
        k = st[0]
        if k in ("run", "exec") and (len(st) < 2 or st[1] not in _PRIMS[k]):
            return False
        if k == "exec" and (len(st) < 3 or not isinstance(st[2], dict)):
            return False
        if k not in ("run", "exec", "text", "field"):
            return False
    return True


CMD_TTL = 600            # 명령은 10분 안에 열린 기안기가 가져가지 않으면 버린다
SEEN_TTL = 8             # 편집 화면이 이 초 안에 명령 줄을 물었으면 「열려 있음」
MAX_IMAGE = 8 * 1024 * 1024   # 기안기 명령으로 넣는 그림 하나(기안기 도움말: 큰 그림은 브라우저에서 늦거나 실패)
MAX_DATA = 20000         # 편집 화면이 돌려주는 읽기 결과(글자 수)
ASSET_DIR = ".aura_cmd"  # 명령에 붙은 그림을 잠시 두는 곳(데이터 폴더 아래) — 실행기가 /hwp/{id}/cmd/{cid}/asset 으로 받아 간다


_Q_LOCKS: dict[int, "threading.RLock"] = {}
_Q_GUARD = __import__("threading").Lock()


def _qlock(fid: int):
    """명령 줄(webhwp_cmd:<id>) 읽고 고쳐 쓰기를 한 번에 하나씩 — 에이전트가 넣는 동안 편집 화면이 같은 줄을 읽고 다시 쓰면
    방금 넣은 명령이 덮여 사라졌다(병렬 시험에서 드러남). 운영 앱은 한 프로세스라 프로세스 안 잠금으로 막는다."""
    import threading
    with _Q_GUARD:
        return _Q_LOCKS.setdefault(int(fid), threading.RLock())


def _queued(fn):
    import functools

    @functools.wraps(fn)
    def run(db, fid, *a, **k):
        with _qlock(fid):
            return fn(db, fid, *a, **k)
    return run


def _cmds(db, fid: int) -> dict:
    try:
        v = json.loads(db.get_setting(f"webhwp_cmd:{int(fid)}", "{}") or "{}")
    except ValueError:
        v = {}
    v.setdefault("seq", 0)
    v.setdefault("items", [])
    return v


def _put_cmds(db, fid: int, v: dict) -> None:
    now = time.time()
    v["items"] = [c for c in v["items"] if now - float(c.get("at") or 0) < CMD_TTL * 3][-50:]
    db.set_setting(f"webhwp_cmd:{int(fid)}", json.dumps(v, ensure_ascii=False))


def _asset_path(base: Path, fid: int, cid: int) -> Path:
    return Path(base) / ASSET_DIR / f"{int(fid)}-{int(cid)}.bin"


def _sweep_assets(base: Path) -> None:
    d = Path(base) / ASSET_DIR
    if not d.is_dir():
        return
    now = time.time()
    for p in d.glob("*.bin"):
        try:
            if now - p.stat().st_mtime > CMD_TTL * 3:
                p.unlink()
        except OSError:
            pass


@_queued
def push(db, fid: int, action: str, params: dict, by: str = "AURA", *, steps: list | None = None, label: str = "",
         runner: int = 1, owner: str = "", asset: bytes | None = None, base: Path | None = None) -> dict:
    """명령 줄에 넣는다 — {id, action, params, steps, by, at, status:new}. 허용 목록 밖이면 EditError.
    steps 를 주면(기안기 기본 동작 — webhwp_native) 그대로, 없으면 LIVE_ACTIONS 로 만든다. owner 는 그 사람의 편집 화면이 열려 있으면
    그 화면이 먼저 가져가게(여럿이 같이 열었을 때 남의 화면에서 AURA 명령이 돌지 않게). asset 은 그림 바이트(실행기가 받아 간다)."""
    if steps is None:
        steps = live_steps(action, params)
        label = label or LIVE_ACTIONS[action]["desc"]
    if not steps_allowed(steps, runner):
        raise EditError("허용 목록 밖의 단계가 있다.")
    v = _cmds(db, fid)
    v["seq"] += 1
    c = {"id": v["seq"], "action": action, "label": label or action, "params": params or {}, "steps": steps,
         "by": by, "at": time.time(), "status": "new", "runner": int(runner)}
    if owner:
        c["for"] = owner
    if asset is not None:
        if base is None or len(asset) > MAX_IMAGE:
            raise EditError("그림이 너무 크다(8MB 까지).")
        _sweep_assets(base)
        p = _asset_path(base, fid, c["id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(asset)
        c["asset"] = True
    v["items"].append(c)
    _put_cmds(db, fid, v)
    return c


def _seen_runners(db, fid: int) -> dict:
    try:
        v = json.loads(db.get_setting(f"webhwp_cmd_r2:{int(fid)}", "{}") or "{}")
    except ValueError:
        v = {}
    now = time.time()
    return {u: float(t) for u, t in v.items() if now - float(t or 0) < SEEN_TTL}


@_queued
def take(db, fid: int, user: str, runner: int = 1) -> list[dict]:
    """편집 화면이 가져갈 새 명령 — 가져간 것은 running 으로(두 창이 같은 명령을 두 번 하지 않게). 묻는 시각도 남긴다.
    새 실행기(runner 2)가 하나라도 살아 있으면 예전 실행기에는 주지 않는다(한 명령을 한 실행기만). 명령에 owner(for)가 있고
    그 사람의 새 실행기가 살아 있으면 그 사람 화면만 가져간다."""
    db.set_setting(f"webhwp_cmd_seen:{int(fid)}", json.dumps({"at": time.time(), "user": user}))
    alive = _seen_runners(db, fid)
    if runner >= 2:
        alive[user] = time.time()
        db.set_setting(f"webhwp_cmd_r2:{int(fid)}", json.dumps(alive))
    elif alive:
        return []
    v = _cmds(db, fid)
    now = time.time()
    out = []
    for c in v["items"]:
        if c["status"] == "new" and now - float(c["at"]) > CMD_TTL:
            c["status"], c["result"] = "error", "기한(10분)이 지나 버렸다"
            continue
        need = int(c.get("runner") or 1)               # 명령을 만든 갈래의 관문으로 다시 본다(예전 명령은 예전 관문)
        if c["status"] != "new" or need > runner or not steps_allowed(c.get("steps"), need):
            continue
        if c.get("for") and c["for"] != user and c["for"] in alive:
            continue
        c["status"], c["taken_by"], c["taken"] = "running", user, now
        item = {k: c[k] for k in ("id", "action", "label", "steps", "by") if k in c}
        if c.get("asset"):
            item["asset"] = True
        if _nat.read_only(c.get("steps")):
            item["readonly"] = True                # 읽기만 — 편집 화면은 구조 잠금·저장 없이
        out.append(item)
    if out or v["items"]:
        _put_cmds(db, fid, v)
    return out


@_queued
def report(db, fid: int, cid: int, ok: bool, message: str, data=None) -> bool:
    v = _cmds(db, fid)
    for c in v["items"]:
        if int(c["id"]) == int(cid) and c["status"] == "running":
            c["status"], c["result"], c["done"] = ("done" if ok else "error"), str(message or "")[:300], time.time()
            if data is not None:
                try:
                    raw = json.dumps(data, ensure_ascii=False)
                except (TypeError, ValueError):
                    raw = "null"
                c["data"] = json.loads(raw) if len(raw) <= MAX_DATA else {"_cut": raw[:MAX_DATA]}
            _put_cmds(db, fid, v)
            return True
    return False


@_queued
def drop(db, fid: int, cid: int, message: str) -> None:
    """가져가지 않은 명령을 버린다(읽기 명령인데 기안기가 닫혀 있을 때 등)."""
    v = _cmds(db, fid)
    for c in v["items"]:
        if int(c["id"]) == int(cid) and c["status"] in ("new", "running"):
            c["status"], c["result"] = "error", str(message)[:300]
            _put_cmds(db, fid, v)
            return


def asset(db, base: Path, fid: int, cid: int) -> Path | None:
    """실행 중인 명령의 그림 — 그 명령이 그림을 달고 있고 아직 끝나지 않았을 때만."""
    c = status(db, fid, cid)
    if not c or not c.get("asset") or c.get("status") not in ("new", "running"):
        return None
    p = _asset_path(base, fid, cid)
    return p if p.is_file() else None


def status(db, fid: int, cid: int) -> dict:
    return next((c for c in _cmds(db, fid)["items"] if int(c["id"]) == int(cid)), {})


def editor_open(db, fid: int, runner: int = 1) -> bool:
    if runner >= 2:
        return bool(_seen_runners(db, fid))
    try:
        s = json.loads(db.get_setting(f"webhwp_cmd_seen:{int(fid)}", "{}") or "{}")
    except ValueError:
        return False
    return time.time() - float(s.get("at") or 0) < SEEN_TTL


def lease_block(db, fid: int, field: str, owner: str) -> str:
    """공동 편집 임대(webhwp_coedit)를 지킨다 — 그 칸(필드가 없으면 문서 구조 잠금)을 다른 사람이 쥐고 있으면 그 사람, 아니면 ''.
    AURA 와 이 대화의 담당자가 쥔 것은 막지 않는다. 공동 편집 모듈이 없으면 막지 않는다."""
    try:
        from zzaimy.app import webhwp_coedit as ce
    except ImportError:
        return ""
    try:
        who = ce.holder(db, fid, field if field else ce.STRUCT)
    except Exception:                               # 표가 아직 없는 등 — 임대를 읽지 못하면 막지 않는다(파일 쪽 관문이 따로 있다)
        return ""
    return "" if who in ("", getattr(ce, "AGENT", "AURA"), owner) else who


_STYLE = re.compile(r'<hh:style\b([^>]*)/?>')


def styles(data: bytes) -> list[dict]:
    """작업본 header 의 스타일 목록(header 차례 그대로) — 기안기의 스타일 단축(StyleShortcut1~10)이 이 차례를 따른다."""
    doc = _Doc(data)
    out = []
    for m in _STYLE.finditer(doc.hdr.xml):
        at = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        out.append({"id": at.get("id", ""), "name": at.get("name", ""), "eng": at.get("engName", ""),
                    "type": "글자" if at.get("type") == "CHAR" else "문단"})
    return out


def styles_text(data: bytes) -> str:
    st = styles(data)
    if not st:
        return "스타일 목록이 없다."
    return "\n".join(f"{i}. {s['name']}({s['type']})" + (f" — hwp_action StyleShortcut{i}" if i <= 10 else "")
                      for i, s in enumerate(st, 1))


def _ps_native(a: dict):
    """hwp_page_setup 인자 → PageSetup 기안기 동작(편집 화면이 열려 있을 때)."""
    p: dict = {}
    if a.get("paper"):
        p["paper"] = a["paper"]
    if a.get("orientation"):
        p["Landscape"] = str(a["orientation"]) in ("가로", "landscape")
    names = {"top": "TopMargin", "bottom": "BottomMargin", "left": "LeftMargin", "right": "RightMargin",
             "header": "HeaderLen", "footer": "FooterLen", "gutter": "GutterLen"}
    mm = dict(MARGIN_PRESETS.get(str(a.get("margin") or ""), {}))
    if a.get("margin") and not mm:
        raise EditError("margin 은 보통|좁게|넓게 다.")
    mm.update({k: v for k, v in (a.get("margins_mm") or {}).items() if k in names and v not in (None, "")})
    for k, v in mm.items():
        p[names[k]] = v
    if a.get("gutter_type"):
        g = {"왼쪽": "왼쪽", "맞쪽": "맞쪽", "위쪽": "위쪽", "LEFT_ONLY": "왼쪽", "LEFT_RIGHT": "맞쪽", "TOP_BOTTOM": "위쪽"}.get(str(a["gutter_type"]))
        if not g:
            raise EditError("gutter_type 은 왼쪽|맞쪽|위쪽 이다.")
        p["GutterType"] = g
    if not p:
        raise EditError("바꿀 것이 없다.")
    return "PageSetup", p, None


def _pn_native(a: dict):
    """hwp_page_number 인자 → PageNumPos(시작 번호를 주면 파일 도구로 — NewNumber 는 쪽 자리가 필요해 따로)."""
    if a.get("start") not in (None, ""):
        raise EditError("시작 번호는 파일 도구로.")
    pos = str(a.get("position") or "아래 가운데")
    fmt = str(a.get("format") or "1")
    p = {"DrawPos": pos, "NumberFormat": fmt}
    if a.get("dash"):
        p["SideChar"] = True
    return "PageNumPos", p, None


def _hf_native(a: dict):
    """hwp_header_footer 인자 → HeaderFooter(머리말·꼬리말 가운데 하나, 글이 있을 때만 — 빼기·정렬은 파일 도구로)."""
    keys = [k for k in ("header", "footer") if a.get(k) not in (None, "")]
    if len(keys) != 1 or a.get("align"):
        raise EditError("하나만, 정렬 없이.")
    k = keys[0]
    return "HeaderFooter", {"HeaderFooterCtrlType": "머리말" if k == "header" else "꼬리말", "Type": "양쪽", "text": str(a[k])}, None


# 기안기가 열려 있으면 파일 대신 기안기 동작으로 하는 파일 도구 — 기안기 창이 하는 일과 똑같이(편집 용지·쪽 번호·머리말)
_NATIVE_FIRST = {"hwp_page_setup": _ps_native, "hwp_page_number": _pn_native, "hwp_header_footer": _hf_native}


# ── 에이전트 도구 ──
# 이름·설명·인자 꼴을 한 곳에 — main.py 는 이 목록을 그대로 doc_agent.Tool 로 잇는다(tests/test_agent_docs 가 문서 표와 맞춰 본다)
_AT = "at: {field: 절 필드 이름, where: start(절 맨 앞)|end(절 끝)} 또는 {after_text: 이 글이 든 문단 뒤} 또는 비우면 문서 끝"
TOOLS: list[dict] = [
    {"name": "hwp_page_setup", "writes": True, "label": "페이지 설정",
     "desc": "작업본 페이지 설정(파일을 바로 고침). args {paper: A4|A3|A5|B4|B5|Letter|Legal, orientation: 세로|가로, "
             "margin: 보통|좁게|넓게, margins_mm: {top, bottom, left, right, header, footer, gutter}(주면 프리셋 위에 덮음), gutter_type: 왼쪽|맞쪽|위쪽}",
     "args": {"paper": "str", "orientation": "str", "margin": "str", "margins_mm": "dict", "gutter_type": "str"}},
    {"name": "hwp_table_insert", "writes": True, "label": "표 만들기",
     "desc": "새 표 넣기(학교 표 기본 모양). args {" + _AT + ", rows, cols, values: [[행1 칸들], …], head: true|false|[머리줄 글], "
             "widths: [열 폭 비율]} — values 가 있으면 rows·cols 는 생략 가능, 첫 행이 모두 글이면 머리줄",
     "args": {"at": "dict", "rows": "int", "cols": "int", "values": "list", "head": "any", "widths": "list"}},
    {"name": "hwp_table_view", "writes": False, "label": "표 보기",
     "desc": "표 하나의 칸 글과 합친 칸을 행마다 보기 — 고칠 행·열 번호를 정할 때. args {table: hwp_tables 번호 | {head: 머리줄 글} | {field: 필드 이름}}",
     "args": {"table": "any"}},
    {"name": "hwp_table_edit", "writes": True, "label": "표 고치기",
     "desc": "표 고치기(행·열 번호는 1부터). args {table, op, …}: add_rows {row, where: above|below, count} · delete_rows {row, count} · "
             "add_cols {col, where: left|right, count} · delete_cols {col, count} · set_cells {start: [행, 열], values: [[…]]} · "
             "merge {cells: [첫 행, 첫 열, 끝 행, 끝 열]} · split {cells: [행, 열], rows, cols}(둘 다 비우면 합친 칸 풀기) · "
             "widths {widths: [비율…]} · head {rows: 머리줄 행 수, fill} · align {align: 가운데|왼쪽|오른쪽|양쪽, cells|row|col} · "
             "valign {valign: 위|가운데|아래, cells|row|col} · shade {fill: 색(#RRGGBB·회색·연파랑·노랑·없음), cells|row|col}",
     "args": {"table": "any", "op": "str"}},
    {"name": "hwp_page_number", "writes": True, "label": "쪽 번호",
     "desc": "쪽 번호 넣기·빼기. args {position: 아래 가운데|아래 오른쪽|위 오른쪽|바깥 아래|…|없음, format: 1|①|I|i|A|a|가, dash: true(- 1 -), start: 시작 번호}",
     "args": {"position": "str", "format": "str", "dash": "bool", "start": "int"}},
    {"name": "hwp_header_footer", "writes": True, "label": "머리말·꼬리말",
     "desc": "머리말·꼬리말 글(빈 글이면 뺌). args {header: 글, footer: 글, align: 가운데|왼쪽|오른쪽}",
     "args": {"header": "str", "footer": "str", "align": "str"}},
    {"name": "hwp_page_break", "writes": True, "label": "쪽 나누기",
     "desc": "그 문단 앞에서 새 쪽으로(절 필드면 그 절 제목 앞). args {field | text: 문단 글, on: true|false}",
     "args": {"field": "str", "text": "str", "on": "bool"}},
    {"name": "hwp_text_style", "writes": True, "label": "글자·문단 모양",
     "desc": "글꼴·글자 크기·굵게·줄 간격·정렬·들여쓰기를 범위에 한꺼번에. args {scope: all | {field: 절·칸 필드} | {text: 문단 글}, "
             "font: 글꼴 이름, pt, bold: true|false, line_spacing: %, align: 가운데|왼쪽|오른쪽|양쪽, left_mm, indent_mm(내어쓰기는 음수), "
             "tables: true(표 칸 글도)}",
     "args": {"scope": "any", "font": "str", "pt": "float", "bold": "bool", "line_spacing": "int", "align": "str",
              "left_mm": "float", "indent_mm": "float", "tables": "bool"}},
    {"name": "hwp_footnote", "writes": True, "label": "각주",
     "desc": "각주 달기 — 근거·출처를 밝힐 때. args {field: 필드(그 값 끝에) | after_text: 이 글 뒤에, text: 각주 글}",
     "args": {"field": "str", "after_text": "str", "text": "str"}},
    {"name": "hwp_picture", "writes": True, "label": "그림 넣기",
     "desc": "그림 넣기. args {image: doc:<문서 id>(이 대화·프로젝트 문서의 그림 파일) | file:<파일 id>(이 대화에 올린 그림), width_mm, "
             "align: 가운데|왼쪽|오른쪽, " + _AT + "}",
     "args": {"image": "str", "width_mm": "float", "align": "str", "at": "dict"}},
    {"name": "hwp_replace", "writes": True, "label": "찾아 바꾸기",
     "desc": "문서에서 낱말을 찾아 바꾸기(몇 곳 바뀌었는지 돌려줌, 바꾸기 전 판을 남김). replace 를 빼면 찾은 곳 수만 센다. "
             "args {find, replace, scope: all | {field: 절·칸 필드} | {text: 문단 글}}",
     "args": {"find": "str", "replace": "str", "scope": "any"}},
    {"name": "hwp_undo", "writes": True, "label": "되돌리기",
     "desc": "AURA 가 마지막으로 고치기 전 판으로 작업본을 되돌리기(담당자가 되돌려 달라고 할 때). args {}", "args": {}},
    {"name": "hwp_live", "writes": True, "label": "기안기 명령",
     "desc": "열려 있는 기안기 화면에서 바로 하기(화면에 고친 것이 남아 있어 파일 고치기보다 나을 때). args {action, params}. action: "
             + "; ".join(f"{k}({v['desc']}: {', '.join(v['params'])})" for k, v in LIVE_ACTIONS.items())
             + ". field 는 hwp_fields 의 이름. 기안기가 닫혀 있으면 명령을 두었다가 열면 이어서 한다 — 바로 해야 하면 위 파일 도구를 쓴다",
     "args": {"action": "str", "params": "dict"}},
    {"name": "hwp_action", "writes": True, "label": "기안기 기능",
     "desc": "열린 기안기의 기본 기능을 그대로 부르기(한컴 동작 이름·매개변수 항목 그대로, 단위만 mm·pt·#RRGGBB/색 이름). "
             "args {action, params: {항목: 값}, at}. at(자리 — 사람의 커서 자리에는 쓰지 않는다): {field: 필드, select: true(필드 글 블록)} | "
             "{find: 찾을 글, nth: n번째}(그 글을 블록으로) | {cell: 표 칸 필드, right: n, down: n}(칸 블록) | {move: 문서 처음|문서 끝}. "
             "글자 모양은 블록(field+select 또는 find), 표 동작은 표 칸 자리, 쪽 동작(PageSetup·PageNumPos·HeaderFooter)·AllReplace 는 at 없이 된다. "
             "그림은 InsertPicture {image: doc:<문서 id>|file:<파일 id>, width_mm}. 기안기가 닫혀 있으면 명령을 두었다가 열면 한다. action: "
             + _nat.describe(),
     "args": {"action": "str", "params": "dict", "at": "dict"}},
    {"name": "hwp_inspect", "writes": False, "label": "기안기 보기",
     "desc": "열린 기안기에서 지금 자리의 정보 읽기 — 커서 위치·필드·블록 글·글자 모양(글꼴·크기·굵게·색)·문단 모양(정렬·줄 간격·여백)·"
             "개요 번호·쪽 수·표 안인지·필드 목록·문서 글. args {at(없으면 담당자의 지금 커서 — 옮기지 않음), what: [pos, field, selection, char, "
             "para, heading, pages, table, fields, text]}",
     "args": {"at": "dict", "what": "list"}},
    {"name": "hwp_styles", "writes": False, "label": "스타일 목록",
     "desc": "작업본의 스타일 목록(차례·이름·문단/글자) — hwp_action StyleShortcut1~10 이 목록의 1~10번째를 적용한다. args {}",
     "args": {}},
]
LABELS = {t["name"]: t["label"] for t in TOOLS}


class EditTools:
    """HwpTools(이 대화의 작업본) 위에서 도는 편집 도구 실행부."""

    LIVE_WAIT = 12.0

    def __init__(self, hw):
        self.hw = hw

    def _fid(self) -> int | None:
        return self.hw.fid()

    def run(self, name: str, a: dict) -> str:
        a = a if isinstance(a, dict) else {}
        fid = self._fid()
        if not fid:
            return "이 대화에 한글 작업본이 없다 — hwp_forms → hwp_start."
        db, base = self.hw.db, self.hw.base
        ops = {"hwp_page_setup": page_setup, "hwp_table_insert": table_insert, "hwp_table_edit": table_edit,
               "hwp_page_number": page_number, "hwp_header_footer": header_footer, "hwp_page_break": page_break,
               "hwp_text_style": text_style, "hwp_footnote": footnote}
        if name == "hwp_table_view":
            try:
                return table_view(Path(webhwp.get(db, fid)["path"]).read_bytes(), a)
            except EditError as e:
                return str(e)
        if name == "hwp_replace" and a.get("replace") is None:
            try:
                k = count_text(Path(webhwp.get(db, fid)["path"]).read_bytes(), str(a.get("find") or ""))
            except EditError as e:
                return str(e)
            return f"「{str(a.get('find') or '')[:30]}」 {k}곳(글 조각 안에서 센 수)."
        ops["hwp_replace"] = replace_text
        if name == "hwp_undo":
            out = undo(db, base, fid)
            self.hw.writes += 1
            return out
        if name == "hwp_live":
            return self.live(fid, a)
        if name == "hwp_action":
            return self.action(fid, a)
        if name == "hwp_inspect":
            return self.inspect(fid, a)
        if name == "hwp_styles":
            try:
                return styles_text(Path(webhwp.get(db, fid)["path"]).read_bytes())
            except (EditError, OSError) as e:
                return str(e)
        if name in _NATIVE_FIRST and editor_open(db, fid, 2):     # 기안기가 열려 있으면 파일 대신 기안기 동작으로(화면이 다시 불러오지 않게)
            try:
                return self.native(fid, *_NATIVE_FIRST[name](a))
            except (EditError, _nat.NativeError):
                pass                                                # 기안기 동작으로 못 옮기는 인자면 파일 도구로
        if name == "hwp_picture":
            try:
                img = self._image(str(a.get("image") or ""))
            except EditError as e:
                return "넣지 않았다 — " + str(e)
            fn = lambda d: picture(d, img, a)
        elif name in ops:
            fn = lambda d: ops[name](d, a)
        else:
            return "모르는 도구."
        self.hw._mark(fid, LABELS.get(name, name))
        out = apply(db, base, fid, fn)
        if not out.startswith(("바꾸지 않았다", "바뀐 것이 없다", "작업본 파일", "공통 양식")):
            self.hw.writes += 1
        return out

    def _image(self, ref: str) -> bytes:
        db = self.hw.db
        m = re.fullmatch(r"(doc|file):(\d+)", ref.strip())
        if not m:
            raise EditError("image 는 doc:<문서 id> 또는 file:<파일 id> 다.")
        if m.group(1) == "file":
            row = next((r for r in db.list_files(session_id=self.hw.sid, limit=500) if int(r["id"]) == int(m.group(2))), None)
            if not row or not Path(row["path"]).exists():
                raise EditError("이 대화에 올린 파일이 아니다.")
            return Path(row["path"]).read_bytes()
        d = next((x for x in self.hw.project_docs() or [] if int(x["id"]) == int(m.group(2))), None)
        if not d:
            raise EditError("이 대화에서 볼 수 있는 문서가 아니다.")
        from zzaimy.app.webhwp_routes import _original_bytes

        class _St:
            pass
        _St.db = db
        return _original_bytes(_St, d)

    def live(self, fid: int, a: dict) -> str:
        db = self.hw.db
        try:
            c = push(db, fid, str(a.get("action") or ""), a.get("params") if isinstance(a.get("params"), dict) else {})
        except EditError as e:
            return "보내지 않았다 — " + str(e)
        self.hw._mark(fid, f"기안기 · {c['label']}")
        if not editor_open(db, fid):
            return (f"기안기를 열면 이어서 합니다 — 「{c['label']}」 명령을 줄에 두었다(10분 안에 열면 실행). "
                    "지금 바로 파일에 반영해야 하면 hwp_page_setup·hwp_table_edit 같은 파일 도구를 쓴다.")
        end = time.time() + self.LIVE_WAIT
        while time.time() < end:
            st = status(db, fid, c["id"])
            if st.get("status") in ("done", "error"):
                self.hw.writes += 1
                return (f"기안기에서 「{c['label']}」 을 했다" if st["status"] == "done" else f"기안기에서 「{c['label']}」 이 안 됐다") + \
                    (f" — {st.get('result')}" if st.get("result") else "") + "."
            time.sleep(0.5)
        return f"기안기에 「{c['label']}」 명령을 보냈다 — 아직 결과가 오지 않았다(화면이 바쁘면 곧 실행된다)."

    # ── 기안기 기본 기능(webhwp_native) ──
    def _wait(self, fid: int, c: dict) -> dict:
        db = self.hw.db
        end = time.time() + self.LIVE_WAIT
        while time.time() < end:
            st = status(db, fid, c["id"])
            if st.get("status") in ("done", "error"):
                return st
            time.sleep(0.4)
        return {}

    def _send(self, fid: int, action: str, label: str, steps: list, field: str, params: dict, *, writes: bool = True,
              asset: bytes | None = None) -> tuple[dict | None, str]:
        db = self.hw.db
        who = lease_block(db, fid, field, self.hw.owner) if writes else ""
        if who:
            where = f"「{field}」 칸" if field else "문서 구조(칸 밖 본문·쪽 설정)"
            return None, f"하지 않았다 — {who} 님이 {where}을 편집 중이다. 그 사람이 마치면 다시 하거나 담당자에게 묻는다."
        try:
            c = push(db, fid, action, params, steps=steps, label=label, runner=2, owner=self.hw.owner, asset=asset, base=self.hw.base)
        except EditError as e:
            return None, "보내지 않았다 — " + str(e)
        if writes:
            self.hw._mark(fid, field or f"기안기 · {label}")   # 칸이 있으면 칸 이름 — 머리줄이 「「기안기 · 글 넣기」 을 쓰는 중」 이었다(10-26)
        if not editor_open(db, fid, 2):
            return c, (f"기안기를 열면 이어서 합니다 — 「{label}」 명령을 줄에 두었다(10분 안에 열면 실행). "
                       "바로 파일에 반영해야 하면 hwp_page_setup·hwp_table_edit 같은 파일 도구를 쓴다.")
        return c, ""

    def native(self, fid: int, action: str, params: dict, at=None, picture=None, asset=None) -> str:
        steps, field = _nat.steps_for(action, params, at, picture)
        label = _nat.NATIVE[action]["label"]
        c, msg = self._send(fid, action, label, steps, field, {"params": params, "at": at}, asset=asset)
        if c is None or msg:
            return msg
        st = self._wait(fid, c)
        if not st:
            return f"기안기에 「{label}」 명령을 보냈다 — 아직 결과가 오지 않았다(화면이 바쁘면 곧 실행된다)."
        self.hw.writes += 1
        return (f"기안기에서 「{label}」 을 했다" if st["status"] == "done" else f"기안기에서 「{label}」 이 안 됐다") + \
            (f" — {st.get('result')}" if st.get("result") else "") + "."

    def action(self, fid: int, a: dict) -> str:
        name = str(a.get("action") or "")
        params = a.get("params") if isinstance(a.get("params"), dict) else ({} if a.get("params") in (None, "") else a.get("params"))
        at = a.get("at")
        try:
            if name == "InsertPicture":
                if not isinstance(params, dict):
                    raise _nat.NativeError("params 는 {image, width_mm} 꼴이다.")
                img = self._image(str(params.get("image") or ""))
                if len(img) > MAX_IMAGE:
                    raise EditError("그림이 너무 크다(8MB 까지).")
                if not hf.image_kind(img):
                    raise EditError("그림 파일(png·jpg·gif·bmp)이 아니다.")
                w = _nat.convert("width_mm", ("int", 5, 400), int(round(_nat._num(params.get("width_mm") or 80, "width_mm"))))
                px = hf._image_px(img)
                h = params.get("height_mm")
                h = _nat._num(h, "height_mm") if h not in (None, "") else (w * px[1] / px[0] if px and px[0] else w * 0.75)
                if not 1 <= h <= 1000:
                    raise _nat.NativeError("height_mm 는 1~1000mm 다.")
                return self.native(fid, name, {}, at, (w, h), asset=img)
            return self.native(fid, name, params, at)
        except (EditError, _nat.NativeError) as e:
            return "보내지 않았다 — " + str(e)

    def inspect(self, fid: int, a: dict) -> str:
        try:
            steps, field = _nat.read_steps(a.get("at"), a.get("what") if isinstance(a.get("what"), list) else None)
        except _nat.NativeError as e:
            return "읽지 않았다 — " + str(e)
        c, msg = self._send(fid, "read", "기안기 보기", steps, field, {"at": a.get("at")}, writes=False)
        if c is None:
            return msg
        if msg:                                         # 닫혀 있으면 읽기 명령은 두지 않는다
            drop(self.hw.db, fid, c["id"], "기안기가 닫혀 있어 읽지 않음")
            return "기안기가 열려 있지 않아 읽지 못했다 — 파일 기준 정보는 hwp_fields·hwp_tables·hwp_styles 로 본다."
        st = self._wait(fid, c)
        if not st:
            return "기안기에 읽기를 보냈다 — 아직 답이 없다."
        if st["status"] != "done":
            return "읽지 못했다 — " + str(st.get("result") or "")
        return "기안기에서 읽은 것: " + json.dumps(st.get("data") or {}, ensure_ascii=False)[:4000]


def doc_tools(hw, Tool) -> list:
    """main.py 의 문서 에이전트 도구 목록에 붙일 Tool 들 — Tool 은 doc_agent.Tool."""
    et = EditTools(hw)
    return [Tool(t["name"], t["desc"], (lambda n: lambda a: et.run(n, a))(t["name"]), writes=t["writes"]) for t in TOOLS]
