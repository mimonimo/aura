"""서식 대조 — 결과 작업본(hwpx)과 정답(hwpx 또는 옛 .hwp)의 모양을 절마다 숫자로 견준다(11-15).

내용 비교(eval_set: 채움·수치·글 재현)와 따로, 「원본과 같은 모양인가」 를 잰다. 고친 것이 나아졌는지를 눈대중 대신 수치로 보려는 것이다.
- 본문: 절마다 글 양으로 무게를 둔 대표 글자 크기·글꼴·굵은 글 비율·검정 아닌 색, 문단 정렬
- 표: 수, 짝지은 표마다 행·열 수, 열 폭 비율 차(L1, 0~2), 칸 대표 글자 크기, 머리 행 음영 유무
두 형식을 같은 길로 읽는다 — hwpx 는 그대로, .hwp 는 hwp5_docx 가 hwpx 꼴로 옮긴 문단(pyhwp 구조 XML)을 쓴다.
절은 번호 머리(Ⅰ. · 1. · 1.1. · 4-4. · (1) 등)로 나눠 번호로 짝짓는다.
"""
from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

try:  # 외부 개체·XML 폭탄 방어 — 받은 문서를 읽는다
    from defusedxml.ElementTree import fromstring as _fromstring, parse as _parse
except Exception:  # pragma: no cover
    from xml.etree.ElementTree import fromstring as _fromstring, parse as _parse

from zzaimy.ingest.hwpx_docx import Styles, load_styles

_HEAD = re.compile(r"^\s*((?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.?)|(?:\d{1,2}(?:[.-]\d{1,2}){0,3}\.?))\s+\S")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _kids(el, name: str):
    return [c for c in el if _local(c.tag) == name]


@dataclass
class Table:
    rows: int
    cols: int
    col_ratio: list[float]
    cell_size: float
    head_fill: bool
    text: str


@dataclass
class Section:
    key: str
    title: str
    chars: Counter = field(default_factory=Counter)     # (크기, 글꼴, 굵게, 색) → 글자 수
    aligns: Counter = field(default_factory=Counter)
    tables: list[Table] = field(default_factory=list)
    n_chars: int = 0


def _run_text(run) -> str:
    return "".join((t.text or "") + "".join((x.tail or "") for x in t) for t in run.iter() if _local(t.tag) == "t")


def _para_text(p) -> str:
    return "".join(_run_text(r) for r in _kids(p, "run"))


def _table(tbl, st: Styles) -> Table:
    rows = int(tbl.get("rowCnt") or 0)
    cols = int(tbl.get("colCnt") or 0)
    widths: dict[int, int] = {}
    sizes: Counter = Counter()
    head_fill = False
    texts = []
    for tr in _kids(tbl, "tr"):
        for tc in _kids(tr, "tc"):
            addr = next(iter(_kids(tc, "cellAddr")), None)
            span = next(iter(_kids(tc, "cellSpan")), None)
            sz = next(iter(_kids(tc, "cellSz")), None)
            r = int(addr.get("rowAddr") or 0) if addr is not None else 0
            c = int(addr.get("colAddr") or 0) if addr is not None else 0
            cs = int(span.get("colSpan") or 1) if span is not None else 1
            if cs == 1 and sz is not None and c not in widths:
                widths[c] = int(sz.get("width") or 0)
            if r == 0:
                bf = st.borders.get(str(tc.get("borderFillIDRef") or ""))
                head_fill = head_fill or bool(bf and bf.fill and bf.fill.upper() not in ("FFFFFF", "NONE"))
            for p in tc.iter():
                if _local(p.tag) != "p":
                    continue
                for run in _kids(p, "run"):
                    t = _run_text(run)
                    if t.strip():
                        ch = st.chars.get(str(run.get("charPrIDRef") or ""))
                        sizes[round(ch.size_pt, 1) if ch else 10.0] += len(t)
                        texts.append(t)
    tot = sum(widths.get(c, 0) for c in range(cols)) or 1
    ratio = [round(widths.get(c, 0) / tot, 3) for c in range(cols)]
    return Table(rows, cols, ratio, sizes.most_common(1)[0][0] if sizes else 0.0, head_fill, " ".join(texts)[:120])


def sections(paras: list, st: Styles) -> list[Section]:
    """문단 차례 → 절 목록(번호 머리마다 새 절). 표 안 글은 표에만 센다."""
    out = [Section("앞", "")]
    chapter = "현황"                                   # 장(Ⅰ~Ⅳ) 앞은 현황 — 장마다 1. 2. 가 되풀이되므로 장을 앞에 붙여 짝짓는다
    for p in paras:
        tbls = [x for x in p.iter() if _local(x.tag) == "tbl"]
        top = [t for t in tbls if not any(t in list(o.iter())[1:] for o in tbls if o is not t)]
        text = _para_text(p).strip()
        box = None
        if len(top) == 1 and int(top[0].get("rowCnt") or 0) == 1 and int(top[0].get("colCnt") or 0) <= 2:
            # 제목 상자(1행 1~2칸 표) — 「1.2. 대학의 교육역량 … | [증빙 1] …」 처럼 절 제목을 상자에 둔 서식(04)
            first = next((c for c in top[0].iter() if _local(c.tag) == "tc"), None)
            box = "".join(_para_text(q) for q in first.iter() if _local(q.tag) == "p").strip() if first is not None else ""
        cand = box if box else text
        m = _HEAD.match(cand) if (box or not top) and (len(cand) < 80 or re.match(r"\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.", cand)) else None
        text = cand
        if m:
            num = m.group(1).rstrip(".")
            if re.fullmatch(r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+", num):
                chapter = num
                out.append(Section(num, text[:40]))
            else:
                out.append(Section(f"{chapter} {num}", text[:40]))
            continue                                    # 제목 글은 본문 모양 셈에서 뺀다
        sec = out[-1]
        for t in top:
            sec.tables.append(_table(t, st))
        if top:
            continue
        ps = st.paras.get(str(p.get("paraPrIDRef") or ""))
        for run in _kids(p, "run"):
            t = _run_text(run)
            if not t.strip():
                continue
            ch = st.chars.get(str(run.get("charPrIDRef") or ""))
            key = (round(ch.size_pt, 1), ch.font, ch.bold, ch.color.upper()) if ch else (10.0, "", False, "000000")
            sec.chars[key] += len(t)
            sec.n_chars += len(t)
            if ps:
                sec.aligns[ps.align] += len(t)
    merged: dict[str, Section] = {}
    for sec in out:                                     # 목차와 본문처럼 같은 번호가 두 번 — 하나로 합친다
        if sec.key in merged:
            m_ = merged[sec.key]
            m_.chars.update(sec.chars)
            m_.aligns.update(sec.aligns)
            m_.tables += sec.tables
            m_.n_chars += sec.n_chars
        else:
            merged[sec.key] = sec
    return list(merged.values())


def read_hwpx(path: Path) -> tuple[list, Styles]:
    z = zipfile.ZipFile(path)
    st = load_styles(_fromstring(z.read("Contents/header.xml")))
    paras = []
    for n in sorted(x for x in z.namelist() if re.match(r"Contents/section\d+\.xml$", x)):
        root = _fromstring(z.read(n))
        paras += [p for p in root if _local(p.tag) == "p"]
    return paras, st


def read_hwp(path: Path) -> tuple[list, Styles]:
    from zzaimy.ingest import hwp5_docx as h
    root = _parse(str(h.dump_xml(Path(path)))).getroot()
    st, _ = h._load_styles(root.find("DocInfo"))
    body = root.find("BodyText")
    paras = []
    for sec in (body.findall("SectionDef") + body.findall("Section")) if body is not None else []:
        for p_el in sec.findall("Paragraph") + [p for cs in sec.findall("ColumnSet") for p in cs.findall("Paragraph")]:
            paras.append(h._translate_paragraph(p_el))
    return paras, st


def read(path: Path) -> list[Section]:
    paras, st = (read_hwp if str(path).lower().endswith(".hwp") else read_hwpx)(Path(path))
    return sections(paras, st)


def _main_style(sec: Section):
    """글 양이 가장 많은 (크기, 글꼴) — 그 절의 본문 모양."""
    by: Counter = Counter()
    for (size, font, _b, _c), n in sec.chars.items():
        by[(size, font)] += n
    return by.most_common(1)[0][0] if by else None


def _bold_ratio(sec: Section) -> float:
    return round(sum(n for k, n in sec.chars.items() if k[2]) / sec.n_chars, 2) if sec.n_chars else 0.0


def _colors(sec: Section) -> set[str]:
    return {k[3] for k, n in sec.chars.items() if k[3] not in ("000000", "") and n >= 5}


def compare(answer: list[Section], result: list[Section]) -> dict:
    """절마다 견준 줄과 요약 수치."""
    res_by = {}
    for s in result:
        res_by.setdefault(s.key, s)
    rows = []
    for a in answer:
        if a.key == "앞" or (not a.n_chars and not a.tables):
            continue
        r = res_by.get(a.key)
        row = {"key": a.key, "title": a.title, "found": r is not None}
        if r is None:
            rows.append(row)
            continue
        am, rm = _main_style(a), _main_style(r)
        row.update({"size": (am[0] if am else None, rm[0] if rm else None), "font": (am[1] if am else "", rm[1] if rm else ""),
                    "bold": (_bold_ratio(a), _bold_ratio(r)), "colors": (sorted(_colors(a)), sorted(_colors(r))),
                    "tables": (len(a.tables), len(r.tables))})
        pairs = []
        for ta, tr_ in zip(a.tables, r.tables):
            l1 = round(sum(abs(x - y) for x, y in zip(ta.col_ratio, tr_.col_ratio)) + abs(len(ta.col_ratio) - len(tr_.col_ratio)) * 0.2, 2)
            pairs.append({"rows": (ta.rows, tr_.rows), "cols": (ta.cols, tr_.cols), "col_l1": l1 if ta.cols == tr_.cols else None,
                          "cell_size": (ta.cell_size, tr_.cell_size), "head_fill": (ta.head_fill, tr_.head_fill)})
        row["table_pairs"] = pairs
        rows.append(row)
    found = [r for r in rows if r["found"]]
    tp = [p for r in found for p in r.get("table_pairs", [])]
    summ = {
        "sections": len(rows), "found": len(found),
        "body_size_match": round(sum(1 for r in found if r["size"][0] == r["size"][1]) / max(len(found), 1), 2),
        "body_font_match": round(sum(1 for r in found if r["font"][0] == r["font"][1]) / max(len(found), 1), 2),
        "table_count_diff": sum(abs(r["tables"][0] - r["tables"][1]) for r in found),
        "table_shape_match": round(sum(1 for p in tp if p["cols"][0] == p["cols"][1] and p["rows"][0] == p["rows"][1]) / max(len(tp), 1), 2),
        "col_ratio_l1_mean": round(sum(p["col_l1"] for p in tp if p["col_l1"] is not None) / max(sum(1 for p in tp if p["col_l1"] is not None), 1), 3),
        "cell_size_match": round(sum(1 for p in tp if p["cell_size"][0] == p["cell_size"][1]) / max(len(tp), 1), 2),
        "head_fill_match": round(sum(1 for p in tp if p["head_fill"][0] == p["head_fill"][1]) / max(len(tp), 1), 2),
    }
    return {"summary": summ, "rows": rows}


def report_md(cmp: dict) -> str:
    s = cmp["summary"]
    out = ["# 서식 대조", "",
           f"절 {s['found']}/{s['sections']} 짝지음 · 본문 글자 크기 같음 {s['body_size_match']:.0%} · 본문 글꼴 같음 {s['body_font_match']:.0%} · "
           f"표 수 차 합 {s['table_count_diff']} · 표 행·열 같음 {s['table_shape_match']:.0%} · 열 폭 비율 차(평균 L1) {s['col_ratio_l1_mean']} · "
           f"칸 글자 크기 같음 {s['cell_size_match']:.0%} · 머리 행 음영 같음 {s['head_fill_match']:.0%}", "",
           "| 절 | 본문 크기(정답/결과) | 본문 글꼴 | 굵게 비율 | 표 수 | 표(행×열, 열폭 차, 칸 크기) |", "|---|---|---|---|---|---|"]
    for r in cmp["rows"]:
        if not r["found"]:
            out.append(f"| {r['key']} {r['title'][:20]} | 결과에 없음 | | | | |")
            continue
        flag = lambda a, b: f"{a}/{b}" + ("" if a == b else " ✗")
        tb = "; ".join(f"{p['rows'][0]}×{p['cols'][0]}/{p['rows'][1]}×{p['cols'][1]} {p['col_l1'] if p['col_l1'] is not None else '-'} "
                       f"{p['cell_size'][0]}/{p['cell_size'][1]}" for p in r.get("table_pairs", [])[:4])
        out.append(f"| {r['key']} {r['title'][:20]} | {flag(*r['size'])} | {flag(r['font'][0][:8], r['font'][1][:8])} | "
                   f"{r['bold'][0]}/{r['bold'][1]} | {flag(*r['tables'])} | {tb} |")
    return "\n".join(out) + "\n"
