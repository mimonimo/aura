"""항목 단계 글자 맞추기 — 절 제목 > 1) > 가) > (1) 처럼 위 단계부터 아래 단계로 글자가 같거나 작아지게, 같은 단계는 문서 어디서나 같게.

절을 쓰는 길(칸 쓰기·절 쓰기·표 도구)마다 견본 문단의 글자를 물려받아, 소항목 「1) 정책 방향」(11pt)이 그 아래 본문 「가) …」(13pt)보다
작고 같은 「나)」가 11pt·13pt 로 섞였다(11-17 포스터 예시). 저장 때 문서 전체를 한 번 훑어 단계별 글자 크기를 정한다.

- AURA 가 만든 양식(「양식 틀」 스타일이 있는 문서)에만 — 학교 원본 서식은 서식 작성자의 뜻이라 손대지 않는다.
- 바탕 크기 B 는 긴 본문 문단에서 가장 흔한 크기. 1단계 항목(첫 부호) B+1pt 굵게, 2단계 B, 3단계 아래 B-1pt(10pt 아래로는 내리지 않음).
  부호 없는 문단은 바로 앞 항목 단계의 크기(항목 앞 문단은 B). 절 제목(「1. 사업 개요」)은 1단계보다 1pt 이상 크게.
- 글꼴·색·기울임은 그대로 두고 크기·굵기만 바꾼 글자 모양을 header 에 한 번씩 등록한다. 표·그림 문단, 양식 틀 글은 건드리지 않는다.
"""
from __future__ import annotations

import re
from collections import Counter

from zzaimy.app import webhwp_fields as wf
from zzaimy.app import webhwp_write as ww

LONG = 40                      # 바탕 크기를 셀 때 쓰는 「긴 문단」 글자 수
STEP = 100                     # 한 단계 차이(HWPUNIT, 1pt)
FLOOR = 1000                   # 10pt 아래로는 내리지 않는다
_TITLE = re.compile(r"^\s*(\d{1,2}|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])\.\s*\S")
_T = re.compile(r"<hp:t\b[^>]*>([^<]*)</hp:t>")
_CPREF = re.compile(r'(<hp:run\b[^>]*\bcharPrIDRef=")(\d+)(")')


class _Chars:
    def __init__(self, header: str):
        self.header = header
        self.cache: dict[tuple[str, int, bool], str] = {}

    def body(self, cid: str) -> str | None:
        m = re.search(rf'<hh:charPr\b[^>]*\bid="{cid}"[^>]*>.*?</hh:charPr>', self.header, re.S)
        return m.group(0) if m else None

    def height(self, cid: str) -> int:
        m = re.search(rf'<hh:charPr\b[^>]*\bid="{cid}"[^>]*\bheight="(\d+)"', self.header)
        return int(m.group(1)) if m else 0

    def bold(self, cid: str) -> bool:
        b = self.body(cid) or ""
        return "<hh:bold" in b

    def variant(self, cid: str, height: int, bold: bool) -> str:
        if self.height(cid) == height and self.bold(cid) == bold:
            return cid
        key = (cid, height, bold)
        if key in self.cache:
            return self.cache[key]
        b = self.body(cid)
        if not b:
            return cid
        ids = [int(x) for x in re.findall(r'<hh:charPr\b[^>]*\bid="(\d+)"', self.header)]
        new_id = str(max(ids) + 1)
        nb = re.sub(r'(<hh:charPr\b[^>]*\bid=")\d+(")', rf"\g<1>{new_id}\2", b, count=1)
        nb = re.sub(r'(<hh:charPr\b[^>]*\bheight=")\d+(")', rf"\g<1>{height}\2", nb, count=1)
        if bold and "<hh:bold" not in nb:
            nb = nb.replace("</hh:charPr>", "<hh:bold/></hh:charPr>")
        if not bold:
            nb = re.sub(r"<hh:bold\s*/>", "", nb)
        close = self.header.find("</hh:charProperties>")
        self.header = self.header[:close] + nb + self.header[close:]
        cnt = re.search(r'(<hh:charProperties\b[^>]*\bitemCnt=")(\d+)(")', self.header)
        if cnt:
            self.header = self.header[:cnt.start(2)] + str(int(cnt.group(2)) + 1) + self.header[cnt.end(2):]
        self.cache[key] = new_id
        return new_id


def _paras(xml: str):
    """절 필드 뒤의 겉 문단(표·그림·양식 틀 글 빼고) — (시작, 끝, 글, 문단 XML)."""
    first = next((m.start() for m in ww._BEGIN.finditer(xml) if ww._unq(m.group(2)).startswith("절:")), None)
    if first is None:
        return
    spans = list(ww._top_paras(xml, len(xml)))
    before = [x for x in spans if x[1] <= first]
    if before:                                                   # 첫 절 제목 문단은 첫 절 필드 바로 앞에 있다
        first = before[-1][0]
    for a, b in spans:
        if a < first:
            continue
        p = xml[a:b]
        if "<hp:tbl" in p or "<hp:pic" in p or "<hp:rect" in p or "<hp:container" in p:
            continue
        t = "".join(_T.findall(re.sub(r"<hp:parameters\b.*?</hp:parameters>", "", p, flags=re.S))).strip()
        if t:
            yield a, b, t, p


def apply(data: bytes) -> bytes:
    """단계별 글자 크기를 맞춘 바이트. 바꿀 것이 없거나 AURA 양식이 아니면 같은 바이트."""
    zin, secs = ww._open(data)
    header = zin.read("Contents/header.xml").decode("utf-8")
    sid = wf.form_style_id(header)
    if sid is None:
        return data
    ch = _Chars(header)
    plan = []                  # (구역, 시작, 끝, 문단, 단계) — 단계 -1 은 절 제목, 0 은 항목 앞 본문
    sizes = Counter()
    for n in ww._sec_order(secs):
        xml = secs[n]
        stack: list[int] = []
        depth = 0
        for a, b, t, p in _paras(xml):
            if re.search(rf'<hp:p\b[^>]*\bstyleIDRef="{sid}"', p[:400]):
                continue                                         # 양식 틀 글(안내·견본)
            if re.search(r'<hp:fieldBegin\b[^>]*\bname="절:', p) is None and _TITLE.match(t) and len(t) <= 30 and ww.marker_level(t) == 0:
                plan.append((n, a, b, p, -1))
                stack, depth = [], 0
                continue
            lv = ww.marker_level(t)
            if lv is not None and lv > 0 and ww._small_number(t):
                depth = ww._push_depth(stack, lv)
            elif lv == 0 and ww._small_number(t) and len(t) > 30:
                depth = ww._push_depth(stack, lv)
            plan.append((n, a, b, p, depth))
            if len(t) >= LONG:
                c = _CPREF.search(p)
                if c:
                    sizes[ch.height(c.group(2))] += 1
    if not sizes:
        return data
    base = sizes.most_common(1)[0][0]
    if base <= 0:
        return data

    def target(depth: int) -> tuple[int, bool]:
        if depth == -1:
            return base + 2 * STEP, True
        if depth == 1:
            return base + STEP, True
        if depth <= 2:
            return base, False
        return max(FLOOR, base - STEP), False

    new = {}
    for n in ww._sec_order(secs):
        xml = secs[n]
        edits = [(a, b, p, d) for (m, a, b, p, d) in plan if m == n]
        for a, b, p, d in reversed(edits):
            h, bold = target(d)
            if d == -1:
                c = _CPREF.search(p)
                if c and ch.height(c.group(2)) >= h:
                    continue                                     # 이미 충분히 큰 절 제목은 둔다
            q = _CPREF.sub(lambda m: m.group(1) + ch.variant(m.group(2), h, bold or (d == -1 and ch.bold(m.group(2)))) + m.group(3), p)
            if q != p:
                xml = xml[:a] + q + xml[b:]
        if xml != secs[n]:
            new[n] = xml
    if not new:
        return data
    new["Contents/header.xml"] = ch.header
    return ww._pack(zin, new)
