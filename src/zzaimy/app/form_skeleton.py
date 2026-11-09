"""큰 한글 양식의 구조 사본 — 기안기 변환 서버가 받지 못하는 큰 원본(그림이 많은 지난 결과보고서 등)을, 반입 때 읽어 둔 조각
(doc_chunks: 문단·표 칸)에서 목차와 표 머리만 옮겨 빈 양식으로 다시 만든다.

사용자가 준 실제 양식(작년도 보고서)의 짜임을 그대로 따르는 것이 핵심이다(11-10 시나리오 A — 370MB hwp 가 변환 한도 약 56MB 에 걸려
에이전트가 목차만 본뜬 새 양식으로 물러났다). 그림·서식은 빠지지만 절 차례·단계·표 열 머리·항목 라벨은 원본 그대로다.
값(날짜·수치·이름)은 옮기지 않는다 — 지난 해 값이 새 문서에 남지 않게(form_blank 와 같은 원칙).

절 단계는 원본 표기에서 읽는다: 장 상자(1행짜리 번호 표 「1 | | 행사개요」)=1단계, □·(1)·Ⅰ. 줄=2단계.
◦ 줄과 「◦ (기 간) …」 같은 항목 줄은 절이 아니라 그 절에 쓸 항목이라 작성방법 안내로 옮긴다.
"""
from __future__ import annotations

import json
import re

from zzaimy.app import webhwp_form_create as fc

_L2 = re.compile(r"^\s*(?:□|■|\(\s*\d{1,2}\s*\)|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\s*[.．]?)\s*(?P<t>.+)$")
_ITEM_LABEL = re.compile(r"^\s*[◦○∘·•\-]?\s*\(\s*(?P<l>[가-힣A-Za-z ]{1,12})\s*\)")
_CHAPTER_NO = re.compile(r"^\s*\d{1,2}(?:\s*[-.]\s*\d{1,2})?\s*$")
_VALUEISH = re.compile(r"\d{4}\s*[.년]|\d{1,3}(?:,\d{3})+|\d+\s*(?:명|원|개|점|건|%)")


def _clean_title(t: str) -> str:
    t = fc.strip_number(re.sub(r"\s+", " ", t or "").strip())   # 「3D 프린팅존」 의 3 은 번호가 아니다(strip_number 가 가린다)
    t = re.sub(r"[「」『』\"']", "", t).strip(" ·:-")
    return t[: fc.SECTION_TITLE_MAX]


def _table(content: str) -> dict | None:
    try:
        t = json.loads(content)
    except (TypeError, ValueError):
        return None
    return t if isinstance(t, dict) and isinstance(t.get("cells"), list) else None


def _row(t: dict, r: int) -> list[str]:
    cells = sorted((c for c in t["cells"] if isinstance(c, list) and len(c) >= 6 and c[0] == r), key=lambda c: c[1])
    return [re.sub(r"\s+", " ", str(c[5] or "")).strip() for c in cells]


def _chapter(t: dict) -> str | None:
    """1행짜리 번호 상자(「1 | | 행사개요」) — 장 제목."""
    if int(t.get("n_rows") or 0) != 1 or not 2 <= int(t.get("n_cols") or 0) <= 4:
        return None
    row = [x for x in _row(t, 0)]
    if not row or not _CHAPTER_NO.match(row[0] or ""):
        return None
    title = next((x for x in reversed(row) if x and not _CHAPTER_NO.match(x)), "")
    return _clean_title(title) or None


def _columns(t: dict) -> list[str]:
    """목록표 열 머리 — 2행 이상, 첫 행이 짧은 라벨로만 된 표. 그림 표·값이 든 머리는 뺀다."""
    if int(t.get("n_rows") or 0) < 2:
        return []
    head = _row(t, 0)
    if any("![" in h or "image" in h.lower() for h in head):
        return []
    if any(_VALUEISH.search(h) or re.fullmatch(r"[\d\s.,\-~]+", h) for h in head if h):
        return []                                            # 첫 행에 날짜·수치·번호만 든 칸 — 머리가 아니라 값 행이다
    cols, seen = [], set()
    for h in head:
        h = re.sub(r"[\s\n]+", " ", h).strip()[: fc.COL_MAX]
        if not h or _VALUEISH.search(h) or h in seen:
            continue
        seen.add(h)
        cols.append(h)
    return cols if fc.COLS_MIN <= len(cols) <= fc.COLS_MAX else []


def design_from_chunks(chunks: list[dict], title: str) -> dict:
    """반입 조각(차례대로) → 양식 설계(webhwp_form_create.validate 가 받는 꼴). 값은 옮기지 않는다."""
    secs: list[dict] = []
    seen: set[str] = set()
    items: dict[int, list[str]] = {}
    extra_tables = 0
    dropped = [0]
    cur = -1

    def add(level: int, t: str) -> None:
        nonlocal cur
        key = re.sub(r"\s+", "", t)
        if len(t) < 2 or key in seen:
            return
        if len(secs) >= fc.MAX_SECTIONS:
            dropped[0] += 1
            return
        seen.add(key)
        secs.append({"level": level, "title": t, "table": [], "guide": ""})
        cur = len(secs) - 1

    for c in chunks:
        kind = c.get("kind") or "text"
        content = str(c.get("content") or "")
        if kind == "table":
            t = _table(content)
            if not t:
                continue
            ch = _chapter(t)
            if ch:
                add(1, ch)
                continue
            cols = _columns(t)
            if cols and cur >= 0:
                if not secs[cur]["table"]:
                    secs[cur]["table"] = cols
                else:
                    extra_tables += 1
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _L2.match(line)
            if m and len(m.group("t")) <= 40 and not _VALUEISH.search(m.group("t")):
                add(2, _clean_title(m.group("t")))
                continue
            lab = _ITEM_LABEL.match(line)
            if lab and cur >= 0:
                l = re.sub(r"\s+", "", lab.group("l"))
                if l and l not in items.setdefault(cur, []):
                    items[cur].append(l)
    # 단계 바로잡기 — 첫 절은 1단계, 건너뛰지 않게(장 상자 없이 □ 만 있는 양식은 □ 가 1단계)
    prev = 0
    for s in secs:
        s["level"] = max(1, min(s["level"], prev + 1))
        prev = s["level"]
    for i, s in enumerate(secs):
        labels = items.get(i) or []
        g = "작년 양식의 이 절 짜임대로 쓴다."
        if labels:
            g = "항목: " + "·".join(labels[:8]) + " — 작년 양식처럼 항목마다 올해 자료의 값을 쓰고, 없으면 「확인 필요」."
        s["guide"] = g[: fc.GUIDE_MAX]
    from zzaimy.app import webhwp_fields as wf
    summary = [l for l in (items.get(0) or []) if wf._real_label(l)][: fc.SUMMARY_MAX]
    return {"title": re.sub(r"\s+", " ", title or "").strip()[: fc.TITLE_MAX] or "결과보고서", "cover": True, "subtitle": "",
            "summary": summary, "sections": secs, "why": "올린 양식 원본의 목차·표 구성을 옮긴 구조 사본", "_extra_tables": extra_tables,
            "_dropped": dropped[0]}


def _repair(design: dict) -> dict:
    """관문에 걸리는 절을 덜어 낸다 — 제목 꼴이 안내문 같은 절, 너무 긴 표 머리 등. 짜임(차례·단계)은 그대로."""
    from zzaimy.app import webhwp_fields as wf
    secs = []
    for s in design["sections"]:
        heading = f"{fc.number_of(s['level'], 1)} {s['title']}"
        if not wf._is_heading(heading):
            continue
        secs.append(s)
    prev = 0
    for s in secs:
        s["level"] = max(1, min(s["level"], prev + 1))
        prev = s["level"]
    return {**design, "sections": secs}


def skeleton_hwpx(db, doc: dict, title: str, facts: dict | None = None) -> tuple[bytes, list[dict], dict]:
    """반입 문서(doc) 조각 → (빈 양식 hwpx, 필드 목록, 정보{sections, tables, extra_tables}). 만들 수 없으면 ValueError."""
    from zzaimy.app import webhwp_fields
    chunks = db.list_doc_chunks(int(doc["id"]))
    if not chunks:
        raise ValueError("이 문서는 읽어 둔 조각이 없어 구조를 옮길 수 없다")
    design = design_from_chunks(chunks, title)
    extra = design.pop("_extra_tables", 0)
    dropped = design.pop("_dropped", 0)
    clean, probs = fc.validate(design)
    if probs:
        clean, probs = fc.validate(_repair(design))
    if probs or not clean.get("sections"):
        raise ValueError("구조 사본이 양식 관문을 지나지 못했다: " + "; ".join(probs[:3]))
    data = fc.build(clean, facts)
    data, fields = webhwp_fields.fieldize(data)
    sh = fc.shape(clean)
    return data, fields, {"sections": sh["sections"], "tables": sh["tables"], "extra_tables": extra, "dropped_sections": dropped,
                          "outline": [s["heading"] for s in clean["sections"]]}
