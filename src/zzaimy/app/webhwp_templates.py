"""웹 한글 기안기용 공통 양식 — 구글 문서 공통 양식(ingest.gdocs_templates.SPECS)과 같은 절·표를 한글(hwpx)로 만든다.

왜. 공통 양식으로 쓴 작업본은 한글 원본 서식이 없어 지금까지 Word 로만 내보냈다. 같은 정의에서 hwpx 양식을 만들면
절 제목이 그대로 맞으므로 「한글로 내보내기」 가 서식 보존 채우기(hwpx_fill)로 작업본 내용을 이 양식에 채울 수 있고,
기안기에서 바로 열어 고칠 수 있다. 양식의 모양·구조는 구글 쪽 정의 하나만 고치면 두 편집기에 같이 따라온다(입력 하나 → 결과 둘).

- 절 제목(H) → 제목 문단, 작성 지침(G) → 【작성방법】 상자(한 칸 표 — 제출본 만들 때 remove_boxes 로 지운다),
  빈 문단(P) → 빈 문단, 표(T·TG·FORM·KV) → 같은 칸 수의 표(머리행·라벨 글자, 칸 합치기).
- 만든 파일은 한글 문서함 「공통/공통 양식」 폴더에 둔다(ensure_all). 정의가 바뀌면(지문이 달라지면) 다시 만든다.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from zzaimy.export import house_style as hs
from zzaimy.ingest.gdocs_templates import BUDGET_RULE, NUM_RULE, G, H, _with_sources

FOLDER = "공통 양식"


def _title(spec: dict) -> str:
    return spec["title"].replace("(구글 독스)", "").replace("공통 양식", "").strip() + " 공통 양식"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


BUILD = 13          # 만드는 방식(build)이 바뀌면 올린다 — 정의가 같아도 운영의 양식이 새 방식으로 다시 만들어진다
TEXT_W = hs.text_width()      # A4 본문 폭(HWPUNIT) — 여백은 house_style.PAGE 한 곳(좌우 20mm → 170mm)
TOC_LEADER = " " + "·" * 24      # 목차 줄 끝 점선 — 절 필드 박기(webhwp_fields)가 목차 줄을 제목으로 보지 않는 표시이기도 하다


def fingerprint(spec: dict) -> str:
    return hashlib.sha256((str(BUILD) + json.dumps(spec, ensure_ascii=False, sort_keys=True, default=str)).encode()).hexdigest()[:16]


# ── 표 갈래 빌더 ── 27B(또는 양식 정의)가 갈래와 내용만 정하면 모양(머리줄·라벨·합계·음영·열 폭·정렬)은 house_style 이 짠다
def summary_table(pairs, cols: int = 2) -> dict:
    """요약 표 블록 — 라벨·값 2열 또는 4열. pairs = [[라벨, 값]…] 또는 [라벨…]."""
    return {"table": {"kind": "summary", "pairs": [list(p) if isinstance(p, (list, tuple)) else [p, ""] for p in pairs], "cols": cols}}


def schedule_table(tasks, months=None, marks: dict | None = None) -> dict:
    """일정표 블록(간트 꼴) — months 기본은 1~12월, marks = {과제: [달…]} 이면 그 달 칸 음영."""
    return {"table": {"kind": "schedule", "tasks": list(tasks), "months": list(months or [f"{m}월" for m in range(1, 13)]),
                      "marks": dict(marks or {})}}


def budget_table(items, unit: str = "천원", total: str | None = None) -> dict:
    """예산표 블록 — items = [[비목, 금액, 비고]…] 또는 [비목…]. 끝에 합계 줄(total 을 주면 그 값, 계산하지 않음)."""
    return {"table": {"kind": "budget", "items": [list(i) if isinstance(i, (list, tuple)) else [i] for i in items], "unit": unit,
                      "total": total}}


def table_grid(t: dict) -> dict:
    """표 블록 → 격자 {rows, head_rows, weights, merges, marks, labels, min_w, tall}. 갈래(kind)가 있으면 house_style 의 갈래 빌더,
    없으면 정의의 columns·rows(·head·merges·form)를 그대로."""
    kind = t.get("kind") or ""
    if kind == "summary":
        g = hs.summary_grid(t.get("pairs") or t.get("labels") or [], int(t.get("cols") or 2))
        step = 2 if int(t.get("cols") or 2) == 2 else 4
        g["labels"] = [(r, c) for r in range(len(g["rows"])) for c in range(0, step, 2) if g["rows"][r][c]]
        return g
    if kind == "schedule":
        g = hs.schedule_grid(t.get("tasks") or [], t.get("months") or [], t.get("marks") or {})
        g["labels"] = [(r, 0) for r in range(1, len(g["rows"]))]          # 과제 열은 라벨
        return g
    if kind == "budget":
        g = hs.budget_grid(t.get("items") or [], t.get("unit", "천원"), t.get("total"))
        g["labels"] = []
        return g
    head = t.get("head") or ([] if t.get("form") or t.get("columns") == ["항목", "내용"] else [t["columns"]])
    rows = [[str(c) for c in r] for r in [*head, *t.get("rows", [])]]
    n = max((len(r) for r in rows), default=0)
    rows = [r + [""] * (n - len(r)) for r in rows]
    labels = [tuple(x) for x in t.get("labels") or []]
    tall = {int(k): int(float(v) * 100) for k, v in (t.get("tall") or {}).items()}      # 정의의 tall 은 pt — HWPUNIT 로
    weights = t.get("widths") if t.get("widths") and len(t["widths"]) == n else None
    gantt = bool(t.get("gantt"))
    return {"rows": rows, "head_rows": len(head), "weights": weights, "merges": [tuple(m) for m in t.get("merges") or []],
            "marks": [], "labels": labels, "min_w": int(hs.TABLE["month_min_mm"] * 283.465) if gantt else None, "tall": tall,
            "gantt": gantt}


def head_names(t: dict) -> tuple[int, list[str]]:
    """목록표의 머리줄 수와 열 이름 — 머리줄이 여럿이면 위에서부터 그 열을 덮는 머리 글을 「/」로 잇는다(「참여 인원 (명)/계획」)."""
    head = t.get("head") or [t["columns"]]
    n = max(len(r) for r in head)
    cover: dict[tuple[int, int], str] = {}
    for (r, c, nr, nc) in [tuple(m) for m in t.get("merges") or [] if len(m) == 4]:
        for rr in range(r, min(r + nr, len(head))):
            for cc in range(c, c + nc):
                cover[(rr, cc)] = str(head[r][c]) if c < len(head[r]) else ""
    out = []
    for c in range(n):
        parts = []
        for r in range(len(head)):
            x = str(cover.get((r, c), head[r][c] if c < len(head[r]) else "")).strip()
            if x and x not in parts:
                parts.append(x)
        out.append("/".join(parts))
    return len(head), out


def width_columns(rows: list[list[str]], merges) -> tuple[list[list[str]], list[list[int]]]:
    """열 폭 셈의 재료 — 열마다 칸 글과 그 칸이 덮는 열 수. 합친 칸 글은 왼쪽 위 칸에만 있고(덮인 칸은 빈 글),
    덮은 열 수를 함께 넘겨 fit_widths 가 열마다 나눠 센다."""
    n = max((len(r) for r in rows), default=0)
    span = {(int(m[0]), int(m[1])): max(1, int(m[3])) for m in merges or [] if len(m) == 4}
    cols = [[r[c] if c < len(r) else "" for r in rows] for c in range(n)]
    spans = [[span.get((ri, c), 1) for ri in range(len(rows))] for c in range(n)]
    return cols, spans


def _table(doc, t: dict) -> dict | None:
    """표 하나를 만들고 그 표의 서식 값(webhwp_style.apply 의 per_table)을 돌려준다."""
    g = table_grid(t)
    rows = g["rows"]
    if not rows:
        return None
    n = max(len(r) for r in rows)
    tbl = doc.add_table(len(rows), n, width=TEXT_W)
    for ri, row in enumerate(rows):
        for ci in range(n):
            text = row[ci] if ci < len(row) else ""
            if text:
                try:
                    tbl.set_cell_text(ri, ci, str(text), logical=True)
                except TypeError:
                    tbl.set_cell_text(ri, ci, str(text))
    for (r, c, nr, nc) in g.get("merges") or []:             # (행, 열, 행 수, 열 수) — 머리행 포함 격자 좌표
        if nr <= 1 and nc <= 1:
            continue
        try:
            tbl.merge_cells(r, c, r + nr - 1, c + nc - 1)
        except Exception:
            pass
    # 열 폭 — 정의 비율(weights)이 바람, 칸 글은 하한(머리 낱말이 한 줄에 드는 폭)으로만. 합은 본문 폭 그대로.
    # 누름틀 안내 글(「<머리> 입력」)은 세지 않는다 — 안내 글은 비어 있을 때만 보이는 글이라, 세면 하한 합이 본문 폭을 넘어
    # 비율이 버려졌다(10-25 점검: 「사유」(비율 2)가 가장 좁았다). 합친 칸 글은 덮은 열 수로 나눠 센다
    cols, spans = width_columns(rows, g.get("merges"))
    widths = hs.fit_widths(cols, TEXT_W, g.get("weights"), pt=hs.TYPE["table"]["pt"], min_w=g.get("min_w"), spans=spans)
    try:
        tbl.set_column_widths(widths)
    except Exception:
        pass
    marks = list(g.get("marks") or [])
    if g.get("gantt"):                                        # 정의의 간트 표 — 달 머리 칸만 연남색(빈 일정표에서도 달 칸이 보이게)
        marks += [(r, c) for r in range(max(1, g["head_rows"])) for c, h in enumerate(rows[r])
                  if re.fullmatch(r"\d{1,2}(월)?", h.strip())]      # 달 번호는 둘째 머리행에 있을 수 있다
    opts = {"row_height": hs.MIN_ROW_HEIGHT, "marks": marks, "labels": g.get("labels") or [], "tall": g.get("tall") or {},
            "line": hs.TYPE["table"]["line"]}
    if t.get("kind") in ("summary", "schedule", "budget"):     # 갈래 표는 머리줄·라벨을 빌더가 안다 — 짐작을 끈다
        opts.update(head_rows=g["head_rows"], labels_only=True)
    return opts


def _cover(doc, cover, plan: list) -> bool:
    """표지 — 글 목록(옛 꼴: 첫 줄 제목, 나머지 부제)은 문서 머리에, 사전 {title, subtitle, date, org, page} 은 한 쪽 표지로
    (제목을 쪽 위 약 1/4 에, 날짜·기관은 아래쪽에). 돌려주는 것: 다음 문단에서 쪽을 나눌지."""
    if isinstance(cover, dict):
        title = str(cover.get("title") or "").strip()
        if not title:
            return False
        page = cover.get("page", True) is not False
        for _ in range(7 if page else 0):
            doc.add_paragraph("", style="바탕글")
        plan.append((title, "cover_title"))
        doc.add_paragraph(title, style="바탕글")
        if cover.get("subtitle"):
            doc.add_paragraph("", style="바탕글")
            plan.append((str(cover["subtitle"]), "cover_sub"))
            doc.add_paragraph(str(cover["subtitle"]), style="바탕글")
        for _ in range(15 if page else 1):
            doc.add_paragraph("", style="바탕글")
        if cover.get("date"):
            plan.append((str(cover["date"]), "cover_date"))
            doc.add_paragraph(str(cover["date"]), style="바탕글")
            doc.add_paragraph("", style="바탕글")
        if cover.get("org"):
            plan.append((str(cover["org"]), "cover_org"))
            doc.add_paragraph(str(cover["org"]), style="바탕글")
        return page
    for i, line in enumerate(cover or []):
        plan.append((line, "cover_title" if i == 0 else "cover_sub"))
        doc.add_paragraph(line, style="바탕글")
    return False


BLOCK_KEYS = ("h", "toc", "page_break", "guide", "p", "table", "sign")       # build 가 아는 블록 — 모르는 블록은 말없이 버리지 않고 막는다


def check_blocks(blocks: list) -> None:
    for i, b in enumerate(blocks):
        keys = set(b) if isinstance(b, dict) else set()
        if not keys & set(BLOCK_KEYS) or keys - set(BLOCK_KEYS) - {"text"}:
            raise ValueError(f"양식 정의 {i + 1}번째 블록을 모른다: {sorted(keys) or b!r} — 아는 블록은 {', '.join(BLOCK_KEYS)}")


def _grid_only(body: list) -> bool:
    """본문 자리 없이 라벨·값 격자(서식 격자·항목 표)만 있는 절 — 쓸 자리는 칸 필드다(본문 표로 바꿀 자리가 아니다)."""
    tables = [b["table"] for b in body if "table" in b]
    return (bool(tables) and not any("p" in b for b in body)
            and all(t.get("form") or t.get("columns") == ["항목", "내용"] or t.get("kind") == "summary" for t in tables))


def slots_of(blocks: list) -> list[tuple[str, bool]]:
    """제목 차례와 쓸 자리 여부 — 아래 절이 없는 제목이 잎 절(쓸 자리). 번호가 없어도(「사업 요약」), 로마 숫자 장이어도, 붙임·첨부여도 같다.
    라벨·값 격자만 있는 잎 절(「가. 과제 개요」 처럼 표만 채우는 절)은 절 필드 없이 칸 필드로만 쓴다."""
    heads = [i for i, b in enumerate(blocks) if "h" in b]
    out = []
    for n, i in enumerate(heads):
        nxt = heads[n + 1] if n + 1 < len(heads) else None
        leaf = nxt is None or int(blocks[nxt]["h"]) <= int(blocks[i]["h"])
        out.append((str(blocks[i]["text"]), leaf and not _grid_only(blocks[i + 1:nxt if nxt is not None else len(blocks)])))
    return out


def build(spec: dict) -> bytes:
    """양식 정의 하나 → hwpx 바이트. 정의 꼴(공통 양식·양식 만들기가 같이 쓴다):
    cover: [제목, 부제…] 또는 {title, subtitle, date, org, page(한 쪽 표지, 기본 참)}
    blocks: {"h": 단계, "text"} 제목 · {"guide"} 작성방법 상자 · {"p"} 문단 · {"toc": true} 목차 자리 · {"page_break": true} 쪽 나눔 ·
            {"sign": [제출 문구, 날짜, 기관장…]} 제출 문구(가운데, 셋째 줄부터 오른쪽 굵게) ·
            {"table": {columns, rows, widths, head, merges, form, labels, tall, gantt}} 또는 갈래 표
            {"table": {"kind": "summary"|"schedule"|"budget", …}} (summary_table·schedule_table·budget_table 이 만든다).
    모르는 블록은 예외. 절 필드는 아는 잎 절에만(slots_of → webhwp_fields.fieldize(slots=…)).
    모양은 house_style 한 곳 — 글자 체계(TYPE)·표 디자인(TABLE·HOUSE)·색(PALETTE)."""
    from hwpx import HwpxDocument

    blocks = spec.get("blocks") or []
    check_blocks(blocks)
    doc = HwpxDocument.new()
    doc.sections[0].properties.set_page_margins(**hs.page_margins_hu())     # 표 폭(TEXT_W)과 같은 여백 — house_style.PAGE
    plan: list[tuple[str, str]] = []                   # 글 갈래별 모양은 다 만든 뒤 webhwp_style.dress 가 입힌다
    chapter_mode = any("h" in b and int(b["h"]) == 1 and re.match(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.?\s", str(b["text"])) for b in blocks)
    page_next = _cover(doc, spec.get("cover"), plan)
    if not page_next:
        doc.add_paragraph("", style="바탕글")
    per_table: dict[int, dict] = {}
    keep: set[int] = set()                              # 결재란·서명란 — 비어 있어도 본문 표가 지우지 않는 표(webhwp_fields.FORM_KEEP_STYLE)
    people: dict[int, set[str] | None] = {}             # 사람 칸(「입력:」 필드) — 표 번호 → 라벨 묶음(None 이면 표 전체)
    heads: dict[int, tuple[int, list[str]]] = {}        # 목록표의 머리줄 수·열 이름 — 2단 머리줄 표에도 첫 본문 행 필드를
    n_tables = 0

    def para(text: str):
        nonlocal page_next
        p = doc.add_paragraph(text, style="바탕글")
        if page_next:
            p.element.set("pageBreak", "1")
            page_next = False
        return p
    for b in blocks:
        if "h" in b:
            if not page_next:
                doc.add_paragraph("", style="바탕글")
            lv = min(int(b["h"]), 3)
            plan.append((b["text"], hs.heading_type(b["text"], lv, chapter_mode)))
            para(b["text"])
        elif b.get("toc"):
            plan.append(("목 차", "toc_title"))
            para("목 차")
            for x in blocks:
                if "h" in x and (int(x["h"]) == 1 or chapter_mode and int(x["h"]) == 2):
                    line = ("    " if chapter_mode and int(x["h"]) == 2 else "") + str(x["text"]) + TOC_LEADER
                    plan.append((line, "toc"))
                    doc.add_paragraph(line, style="바탕글")
            page_next = True
        elif b.get("page_break"):
            page_next = True
        elif "guide" in b:
            if page_next:
                para("")
            box = doc.add_table(1, 1, width=TEXT_W)
            box.set_cell_text(0, 0, f"【작성방법】 {b['guide']}")
            n_tables += 1
            per_table[n_tables] = {"row_height": hs.MIN_ROW_HEIGHT}
        elif "p" in b:
            para(b["p"])
        elif "sign" in b:                                       # 제출 문구 — 문구·날짜는 가운데, 기관장(직인) 줄은 오른쪽 굵게(독스 판과 같은 차례)
            lines = [str(x) for x in b["sign"] if str(x).strip()]
            para("")
            doc.add_paragraph("", style="바탕글")
            for i, line in enumerate(lines):
                plan.append((line, "sign_org" if i >= 2 else "sign"))
                para(line)
                if i + 1 < len(lines):
                    doc.add_paragraph("", style="바탕글")
        elif "table" in b:
            if page_next:
                para("")
            opts = _table(doc, b["table"])
            if opts is not None:
                n_tables += 1
                per_table[n_tables] = opts
                if b["table"].get("keep"):
                    keep.add(n_tables)
                    people[n_tables] = None                     # 결재란·확인표 — 칸 모두 사람 칸
                elif b["table"].get("people"):
                    people[n_tables] = set(b["table"]["people"])
                t = b["table"]
                if not t.get("kind") and not t.get("form") and t.get("columns") != ["항목", "내용"]:
                    heads[n_tables] = head_names(t)
    # 쓸 자리(절 다음 빈 문단·라벨 칸·목록표)에 누름틀 — 기안기에서 사람·에이전트가 같은 이름으로 짚는다(webhwp_fields)
    # 표 모양은 학교 서식에서 캔 기본(house_style.HOUSE·TABLE), 글 모양은 TYPE(바탕글 = 본문)
    from zzaimy.app import webhwp_fields, webhwp_style
    data = webhwp_style.body_base(doc.to_bytes())
    data = webhwp_fields.fieldize(data, slots=slots_of(blocks), people=people, heads=heads)[0]
    data = webhwp_style.apply(data, per_table=per_table)
    data = webhwp_fields.mark_form_text(data, keep)    # 양식 표의 미리 적힌 글 — 본문 표가 오면 빈 양식 표를 지우는 판정에 쓴다
    data = webhwp_style.dress(data, plan)
    if spec.get("page_number", sum(1 for b in blocks if "h" in b) >= hs.PAGE["page_number_min_heads"]):
        # 여러 쪽 문서는 쪽 번호(아래 가운데) — 학교 표본 절반 넘게 있음
        from zzaimy.app import webhwp_edit_ops as eo
        pos = next(k for k, v in eo.PAGE_NUM_POS.items() if v == hs.PAGE["page_number"])
        data = eo.page_number(data, {"position": pos})[0]
    return data


# ── 기안기용 공통 공문 ── 행정업무규정 공문서 구성(두문·본문·결문)을 따른 학교 공문 서식. 쓸 자리는 build 가 누름틀로 박는다
# (표 라벨 칸 → 「칸:<라벨>」, 결재란 머리행 → 「표n:<직위>」, 번호 붙은 본문 항목 → 「절:<항목>」). 모르는 값(주소·전화)은 빈칸.
def _people(block, labels):
    """사람 칸 라벨 — 날짜·서명·결재·달성률처럼 담당자가 정하는 값. 「입력:<라벨>」 필드가 되어 에이전트는 물어 받은 값만 넣는다."""
    return {"table": {**block["table"], "people": list(labels)}}


def _kv(rows, people=()):
    b = {"table": {"columns": ["항목", "내용"], "rows": [list(r) for r in rows], "widths": [1, 4]}}
    return _people(b, people) if people else b


def _grid(rows, widths, people=()):
    b = {"table": {"columns": list(rows[0]), "form": True, "rows": [list(r) for r in rows], "widths": widths}}
    return _people(b, people) if people else b


def _list(head, n=1, widths=None):
    return {"table": {"columns": head, "rows": [[""] * len(head) for _ in range(n)], "widths": widths or [1] * len(head)}}


_SIGN = _list(["기안자", "검토자", "결재권자"], 1)                       # 결재란 — 직위 머리행 + 서명 칸(서명이 들어갈 높이)
_SIGN["table"]["tall"] = {1: 36}
_SIGN["table"]["keep"] = True          # 결재란은 비어 있어도 본문 표가 지우지 않는다
_FOOT = _grid([["시행(번호·일자)", "", "접수(번호·일자)", ""], ["우편번호", "", "주소", ""], ["전화", "", "전송", ""],
               ["전자우편", "", "공개구분", ""]], [1, 2, 1, 2], people=["시행(번호·일자)", "접수(번호·일자)"])   # 문서 번호는 전자결재가 매긴다

# 절마다 【작성방법】 상자 — 사업 문서(SPECS)와 같은 헬퍼(H·G)·같은 수치 규칙(NUM_RULE·BUDGET_RULE)·같은 근거 붙이기(_with_sources).
# check_spec 이 15종 모두에 돈다(맨 아래 절마다 작성방법이나 표).
_RELATED = G("결재·발송의 근거를 「가. 관련 규정·계획·공문(문서 번호, 일자, 제목)」 꼴로 적는다. 문서 번호·일자는 근거 문서에 있는 그대로 "
             "옮기고, 근거가 없으면 이 항목을 지운다.")

OFFICIAL = {
    "draft": {"id": "draft", "title": "기안문(내부결재) 공통 양식", "cover": ["영남이공대학교"], "blocks": [
        _kv([["수신", "내부결재"], ["(경유)", ""], ["제목", ""]]),
        {"p": ""},
        H(1, "1. 관련"), _RELATED,
        H(1, "2. 추진 내용"),
        G("무엇을 왜 언제 누가 하는지를 가. 목적 → 나. 일시·장소 → 다. 대상·내용 → 라. 소요 예산 차례로 개조식으로 적는다. "
          + NUM_RULE + ". " + BUDGET_RULE + "."),
        {"p": "붙임  1. (붙임 문서 이름) 1부.  끝."},
        {"p": ""}, _SIGN, {"p": ""}, _FOOT]},
    "outgoing": {"id": "outgoing", "title": "시행문(대외 발송) 공통 양식", "cover": ["영남이공대학교"], "blocks": [
        _kv([["수신", ""], ["(경유)", ""], ["제목", ""]]),
        {"p": ""},
        H(1, "1. 관련"), _RELATED,
        H(1, "2. 요청 사항"),
        G("받는 기관이 할 일을 가. 요청 내용 → 나. 회신 기한·방법 → 다. 담당 부서 차례로 적는다. " + NUM_RULE + "."),
        {"p": "붙임  1. (붙임 문서 이름) 1부.  끝."},
        {"p": ""}, {"p": "영남이공대학교총장"}, {"p": ""}, _SIGN, {"p": ""}, _FOOT]},
    "cooperation": {"id": "cooperation", "title": "협조문(부서 간) 공통 양식", "cover": ["업무 협조 요청"], "blocks": [
        _kv([["수신", ""], ["발신", ""], ["제목", ""], ["회신 기한", ""]]),
        {"p": ""},
        H(1, "1. 협조 배경"),
        G("협조가 필요한 까닭과 관련 근거(계획·회의 결과·공문)를 두세 줄로 적는다."),
        H(1, "2. 협조 요청 사항"),
        G("요청마다 표 한 줄 — 구분·요청 내용·기한·담당(받는 부서). 기한은 날짜로 적는다."),
        _list(["구분", "요청 내용", "기한", "담당"], 3, [1, 3, 1, 1]),
        H(1, "3. 문의"),
        G("문의할 담당 부서·담당자·내선 번호를 한 줄로 적는다. 모르는 연락처는 빈칸으로 둔다."),
        {"p": "끝."}]},
    "brief": {"id": "brief", "title": "1쪽 요약 보고서 공통 양식", "cover": ["(보고서 제목)"], "blocks": [
        _kv([["보고 일자", ""], ["보고 부서", ""], ["보고자", ""]], people=["보고 일자", "보고자"]),
        H(1, "1. 개요"), G("보고 목적과 결론을 두 줄 안에 먼저 적는다."),
        H(1, "2. 현황"), G("지금 상황을 수치와 함께 개조식으로 적는다. " + NUM_RULE + "."),
        H(1, "3. 문제점 및 개선 방안"), G("문제점마다 원인과 개선 방안을 짝지어 적는다. 근거 문서에 없는 판단은 「검토 필요」로 단다."),
        H(1, "4. 향후 계획"), G("추진 과제마다 표 한 줄 — 과제·일정·담당. 일정은 날짜나 달로 적는다."),
        _list(["추진 과제", "일정", "담당"], 3, [3, 1, 1])]},
}


# ── 국고 사업 운영 문서 ── 사업을 하는 동안 늘 만드는 문서(예산 변경·장비 구입·성과지표 증빙·자체성과분석·협약 신청·출장 결과).
# 금액은 천원 단위, 사업명·과제·지표 같은 머리 값은 라벨 칸(「칸:…」), 본문 항목은 「절:…」, 내역 표는 「표n:…」 필드가 된다
GRANT = {
    "budget_change": {"id": "budget_change", "title": "예산 변경 신청서 공통 양식", "cover": ["예산 변경 신청서"], "blocks": [
        _kv([["사업명", ""], ["세부 과제", ""], ["신청 부서", ""], ["담당자", ""], ["신청 일자", ""]], people=["신청 일자"]),
        H(1, "1. 변경 사유"),
        G("변경이 필요한 까닭(사업 여건 변화·집행 실적·계획 조정)을 근거 문서와 함께 적는다. 사업 관리 지침의 변경 승인 조항이 있으면 함께 적는다."),
        H(1, "2. 변경 내역"),
        G("비목·세목마다 표 한 줄. " + BUDGET_RULE + ". 증감도 셈하지 않고 담당자 입력·예산 문서 값을 옮긴다."),
        _list(["비목", "세목", "당초(천원)", "변경(천원)", "증감(천원)", "사유"], 3, [1, 1, 1, 1, 1, 2]),
        H(1, "3. 성과지표·사업 추진에 미치는 영향"),
        G("변경으로 달라지는 성과지표·과제 일정을 적고, 영향이 없으면 「영향 없음」과 까닭을 적는다. " + NUM_RULE + "."),
        {"p": ""}, _SIGN]},
    "equipment": {"id": "equipment", "title": "기자재 구입 계획서 공통 양식", "cover": ["기자재 구입 계획서"], "blocks": [
        _kv([["사업명", ""], ["세부 과제", ""], ["사용 부서", ""], ["담당자", ""]]),
        H(1, "1. 구입 목적"), G("어느 과제·교육과정에 왜 필요한지 적는다. 사업계획서의 과제 이름을 그대로 쓴다."),
        H(1, "2. 구입 내역"), G("품목마다 표 한 줄. 규격은 견적서·사양서 그대로 옮긴다. " + BUDGET_RULE + "."),
        _list(["품명", "규격", "수량", "단가(천원)", "금액(천원)", "활용 계획"], 3, [1.5, 2, 0.6, 1, 1, 2]),
        H(1, "3. 활용 및 관리 계획"), G("활용 교과·프로그램, 연간 활용 시간, 설치 장소, 관리 책임자, 자산 등록 방법을 적는다."),
        H(1, "4. 기대 효과"), G("구입 뒤 달라지는 교육·연구 성과를 성과지표와 이어 적는다. " + NUM_RULE + ".")]},
    "kpi_evidence": {"id": "kpi_evidence", "title": "성과지표 실적·증빙 공통 양식", "cover": ["성과지표 실적 및 증빙"], "blocks": [
        _kv([["성과지표", ""], ["지표 정의", ""], ["산출식", ""], ["목표값", ""], ["실적값", ""], ["달성률(%)", ""]], people=["달성률(%)"]),
        H(1, "1. 실적 산출 근거"),
        G("산출식의 값마다 어디서 가져왔는지 증빙 자료마다 표 한 줄로 적는다. 달성률은 셈하지 않고 실적 문서 값을 옮긴다. " + NUM_RULE + "."),
        _list(["연번", "증빙 자료", "내용", "수량", "비고"], 3, [0.6, 2, 3, 0.8, 1]),
        H(1, "2. 미달성 사유 및 개선 계획"), G("목표에 못 미친 지표만 원인과 개선 계획을 적는다. 모두 달성했으면 「해당 없음」.")]},
    "self_review": {"id": "self_review", "title": "운영 결과 자체성과분석 공통 양식", "cover": ["운영 결과 자체성과분석"], "blocks": [
        _kv([["프로그램명", ""], ["운영 기간", ""], ["참여 인원", ""], ["집행 예산(천원)", ""], ["운영 부서", ""]]),
        H(1, "1. 운영 개요"), G("프로그램 목적·운영 방식·주요 일정을 두세 줄로 적는다."),
        H(1, "2. 목표 대비 성과"), G("지표마다 표 한 줄. 목표·실적·달성률은 실적 문서 값 그대로 옮긴다. " + NUM_RULE + "."),
        _people(_list(["성과지표", "목표", "실적", "달성률(%)", "분석"], 3, [2, 1, 1, 1, 3]), ["달성률(%)"]),
        H(1, "3. 만족도 조사 결과"), G("응답 수·응답률·평균 점수와 주요 의견을 적는다. " + NUM_RULE + "."),
        H(1, "4. 개선 사항 및 환류 계획"), G("만족도·성과 분석에서 나온 개선 사항을 다음 운영에 어떻게 반영할지 적는다.")]},
    "partner_apply": {"id": "partner_apply", "title": "산학협력 협약 체결 신청서 공통 양식", "cover": ["산학협력 협약 체결 신청서"], "blocks": [
        _grid([["기관명", "", "대표자", ""], ["사업자등록번호", "", "업종", ""], ["주소", "", "전화", ""],
               ["담당자", "", "전자우편", ""]], [1.2, 2, 1, 2]),
        H(1, "1. 협약 목적"), G("협약으로 함께 하려는 일과 두 기관이 얻는 것을 두세 줄로 적는다."),
        H(1, "2. 협력 내용"), G("협력 분야마다 표 한 줄 — 분야·내용·기간. 기간은 협약 기간 안에서 적는다."),
        _list(["협력 분야", "내용", "기간"], 3, [1.5, 4, 1.5]),
        {"sign": ["위와 같이 산학협력 협약 체결을 신청합니다.", "20    .    .    ."]},
        _kv([["신청 기관", ""], ["대표자(서명)", ""]], people=["대표자(서명)"])]},
    "trip_report": {"id": "trip_report", "title": "출장·회의 결과 보고서 공통 양식", "cover": ["출장(회의) 결과 보고"], "blocks": [
        _kv([["관련 사업", ""], ["출장자", ""], ["기간", ""], ["장소", ""], ["목적", ""]], people=["출장자"]),
        H(1, "1. 주요 내용"), G("일정 차례로 만난 기관·사람과 논의·확인한 내용을 개조식으로 적는다."),
        H(1, "2. 결과 및 후속 조치"), G("결과와 후속 조치(할 일·담당·기한)를 적는다."),
        H(1, "3. 경비"), G("항목마다 표 한 줄. 금액은 영수증·여비 산정 값 그대로 적는다. " + BUDGET_RULE + "."),
        _list(["항목", "금액(원)", "비고"], 3, [2, 1, 2]),
        {"p": "붙임  1. 증빙 자료 1부.  끝."}]},
}
OFFICIAL = {k: _with_sources(v) for k, v in OFFICIAL.items()}
GRANT = {k: _with_sources(v) for k, v in GRANT.items()}


def ensure_all(db, base: Path, *, owner: str = "zzaimy") -> list[dict]:
    """공통 양식 hwpx 를 한글 문서함 「공통/공통 양식」 에 둔다 — 없거나 정의가 바뀐 것만 새로 만든다(앞 판은 .판/ 으로).
    사업 문서(구글 공통 양식과 같은 정의)는 「공통 양식」, 기안기용 공문은 「공통 양식/공문」."""
    from zzaimy.app import webhwp
    from zzaimy.ingest.gdocs_templates import SPECS

    out = []
    for sid, spec in [*SPECS.items(), *OFFICIAL.items(), *GRANT.items()]:
        folder = f"{FOLDER}/공문" if sid in OFFICIAL else (f"{FOLDER}/국고 사업" if sid in GRANT else FOLDER)
        fp = fingerprint(spec)
        key = f"webhwp_template:{sid}"
        try:
            cur = json.loads(db.get_setting(key, "{}") or "{}")
        except ValueError:
            cur = {}
        f = webhwp.get(db, int(cur["id"])) if cur.get("id") else None
        # 정의가 같고 파일 내용도 만든 그대로일 때만 둔다 — 누가 덮어썼으면(지문이 다르면) 정의대로 다시 만든다
        same = f and Path(f["path"]).exists() and cur.get("fp") == fp and cur.get("sha") == _sha(Path(f["path"]).read_bytes())
        if same:
            out.append({"id": f["id"], "spec": sid, "name": f["name"], "made": False})
            continue
        data = build(spec)
        if f and Path(f["path"]).exists():
            webhwp.save(db, base, f["id"], data, user=owner)
            fid = f["id"]
        else:
            fid = webhwp.add(db, base, webhwp.COMMON, f"{_title(spec)}.hwpx", data, owner=owner, source=f"양식:{sid}",
                             folder=folder)
        db.set_setting(key, json.dumps({"id": fid, "fp": fp, "sha": _sha(data)}))
        out.append({"id": fid, "spec": sid, "name": webhwp.get(db, fid)["name"], "made": True})
    return out


def path_for(db, spec_id: str) -> Path | None:
    """이 공통 양식의 hwpx 경로 — 한글 완성본을 만들 때 원본 서식으로 쓴다. 없으면 None."""
    from zzaimy.app import webhwp
    try:
        cur = json.loads(db.get_setting(f"webhwp_template:{spec_id}", "{}") or "{}")
    except ValueError:
        return None
    f = webhwp.get(db, int(cur["id"])) if cur.get("id") else None
    if not f or not Path(f["path"]).exists() or cur.get("sha") != _sha(Path(f["path"]).read_bytes()):
        return None                                   # 만든 그대로가 아니면 쓰지 않는다(호출부가 ensure_all 로 다시 만든 뒤 부른다)
    return Path(f["path"])
