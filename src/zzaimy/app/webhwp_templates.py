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

FOLDER = "공통 양식"


def _title(spec: dict) -> str:
    return spec["title"].replace("(구글 독스)", "").replace("공통 양식", "").strip() + " 공통 양식"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


BUILD = 12          # 만드는 방식(build)이 바뀌면 올린다 — 정의가 같아도 운영의 양식이 새 방식으로 다시 만들어진다
TEXT_W = 42520      # A4 본문 폭(HWPUNIT, 약 150mm — 새 문서 기본 여백)
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
    return {"rows": rows, "head_rows": len(head), "weights": weights, "merges": [tuple(m) for m in t.get("merges") or []],
            "marks": [], "labels": labels, "min_w": None, "tall": tall,
            "gantt": bool(t.get("gantt"))}


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
    # 열 폭 — 칸 글 길이로 나누고(하한: 머리 낱말이 한 줄에 드는 폭, 상한: 표 폭의 일부) 합은 본문 폭 그대로
    cols = [[r[c] for r in rows if c < len(r)] for c in range(n)]
    if g["head_rows"] and len(rows) > g["head_rows"] and not any(x for r in rows[g["head_rows"]:] for x in r):
        # 빈 목록표 — 첫 빈 행에 누름틀 안내 글(「<머리> 입력」, webhwp_fields)이 들어가므로 그 길이도 열 폭에 센다
        cols = [col + [f"{col[g['head_rows'] - 1]} 입력"] for col in cols]
    widths = hs.fit_widths(cols, TEXT_W, g.get("weights"), pt=hs.TYPE["table"]["pt"], min_w=g.get("min_w"))
    try:
        tbl.set_column_widths(widths)
    except Exception:
        pass
    marks = list(g.get("marks") or [])
    if g.get("gantt"):                                        # 정의의 간트 표 — 달 머리 칸만 연남색(빈 일정표에서도 달 칸이 보이게)
        marks += [(0, c) for c, h in enumerate(rows[0]) if re.fullmatch(r"\d{1,2}(월)?", h.strip())]
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


def build(spec: dict) -> bytes:
    """양식 정의 하나 → hwpx 바이트. 정의 꼴(공통 양식·양식 만들기가 같이 쓴다):
    cover: [제목, 부제…] 또는 {title, subtitle, date, org, page(한 쪽 표지, 기본 참)}
    blocks: {"h": 단계, "text"} 제목 · {"guide"} 작성방법 상자 · {"p"} 문단 · {"toc": true} 목차 자리 · {"page_break": true} 쪽 나눔 ·
            {"table": {columns, rows, widths, head, merges, form, labels, tall, gantt}} 또는 갈래 표
            {"table": {"kind": "summary"|"schedule"|"budget", …}} (summary_table·schedule_table·budget_table 이 만든다).
    모양은 house_style 한 곳 — 글자 체계(TYPE)·표 디자인(TABLE·HOUSE)·색(PALETTE)."""
    from hwpx import HwpxDocument

    doc = HwpxDocument.new()
    plan: list[tuple[str, str]] = []                   # 글 갈래별 모양은 다 만든 뒤 webhwp_style.dress 가 입힌다
    blocks = spec.get("blocks") or []
    chapter_mode = any("h" in b and int(b["h"]) == 1 and re.match(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.?\s", str(b["text"])) for b in blocks)
    page_next = _cover(doc, spec.get("cover"), plan)
    if not page_next:
        doc.add_paragraph("", style="바탕글")
    per_table: dict[int, dict] = {}
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
        elif "table" in b:
            if page_next:
                para("")
            opts = _table(doc, b["table"])
            if opts is not None:
                n_tables += 1
                per_table[n_tables] = opts
    # 쓸 자리(절 다음 빈 문단·라벨 칸·목록표)에 누름틀 — 기안기에서 사람·에이전트가 같은 이름으로 짚는다(webhwp_fields)
    # 표 모양은 학교 서식에서 캔 기본(house_style.HOUSE·TABLE), 글 모양은 TYPE(바탕글 = 본문)
    from zzaimy.app import webhwp_fields, webhwp_style
    data = webhwp_style.body_base(doc.to_bytes())
    data = webhwp_fields.fieldize(data)[0]
    data = webhwp_style.apply(data, per_table=per_table)
    return webhwp_style.dress(data, plan)


# ── 기안기용 공통 공문 ── 행정업무규정 공문서 구성(두문·본문·결문)을 따른 학교 공문 서식. 쓸 자리는 build 가 누름틀로 박는다
# (표 라벨 칸 → 「칸:<라벨>」, 결재란 머리행 → 「표n:<직위>」, 번호 붙은 본문 항목 → 「절:<항목>」). 모르는 값(주소·전화)은 빈칸.
def _kv(rows):
    return {"table": {"columns": ["항목", "내용"], "rows": [list(r) for r in rows], "widths": [1, 4]}}


def _grid(rows, widths):
    return {"table": {"columns": list(rows[0]), "form": True, "rows": [list(r) for r in rows], "widths": widths}}


def _list(head, n=1, widths=None):
    return {"table": {"columns": head, "rows": [[""] * len(head) for _ in range(n)], "widths": widths or [1] * len(head)}}


_SIGN = _list(["기안자", "검토자", "결재권자"], 1)                       # 결재란 — 직위 머리행 + 서명 칸(서명이 들어갈 높이)
_SIGN["table"]["tall"] = {1: 36}
_FOOT = _grid([["시행(번호·일자)", "", "접수(번호·일자)", ""], ["우편번호", "", "주소", ""], ["전화", "", "전송", ""],
               ["전자우편", "", "공개구분", ""]], [1, 2, 1, 2])

OFFICIAL = {
    "draft": {"id": "draft", "title": "기안문(내부결재) 공통 양식", "cover": ["영남이공대학교"], "blocks": [
        _kv([["수신", "내부결재"], ["(경유)", ""], ["제목", ""]]),
        {"p": ""},
        {"h": 1, "text": "1. 관련"}, {"h": 1, "text": "2. 추진 내용"},
        {"p": "붙임  1. (붙임 문서 이름) 1부.  끝."},
        {"p": ""}, _SIGN, {"p": ""}, _FOOT]},
    "outgoing": {"id": "outgoing", "title": "시행문(대외 발송) 공통 양식", "cover": ["영남이공대학교"], "blocks": [
        _kv([["수신", ""], ["(경유)", ""], ["제목", ""]]),
        {"p": ""},
        {"h": 1, "text": "1. 관련"}, {"h": 1, "text": "2. 요청 사항"},
        {"p": "붙임  1. (붙임 문서 이름) 1부.  끝."},
        {"p": ""}, {"p": "영남이공대학교총장"}, {"p": ""}, _SIGN, {"p": ""}, _FOOT]},
    "cooperation": {"id": "cooperation", "title": "협조문(부서 간) 공통 양식", "cover": ["업무 협조 요청"], "blocks": [
        _kv([["수신", ""], ["발신", ""], ["제목", ""], ["회신 기한", ""]]),
        {"p": ""},
        {"h": 1, "text": "1. 협조 배경"}, {"h": 1, "text": "2. 협조 요청 사항"},
        _list(["구분", "요청 내용", "기한", "담당"], 3, [1, 3, 1, 1]),
        {"h": 1, "text": "3. 문의"},
        {"p": "끝."}]},
    "brief": {"id": "brief", "title": "1쪽 요약 보고서 공통 양식", "cover": ["(보고서 제목)"], "blocks": [
        _kv([["보고 일자", ""], ["보고 부서", ""], ["보고자", ""]]),
        {"h": 1, "text": "1. 개요"}, {"h": 1, "text": "2. 현황"}, {"h": 1, "text": "3. 문제점 및 개선 방안"},
        {"h": 1, "text": "4. 향후 계획"},
        _list(["추진 과제", "일정", "담당"], 3, [3, 1, 1])]},
}


# ── 국고 사업 운영 문서 ── 사업을 하는 동안 늘 만드는 문서(예산 변경·장비 구입·성과지표 증빙·자체성과분석·협약 신청·출장 결과).
# 금액은 천원 단위, 사업명·과제·지표 같은 머리 값은 라벨 칸(「칸:…」), 본문 항목은 「절:…」, 내역 표는 「표n:…」 필드가 된다
GRANT = {
    "budget_change": {"id": "budget_change", "title": "예산 변경 신청서 공통 양식", "cover": ["예산 변경 신청서"], "blocks": [
        _kv([["사업명", ""], ["세부 과제", ""], ["신청 부서", ""], ["담당자", ""], ["신청 일자", ""]]),
        {"h": 1, "text": "1. 변경 사유"},
        {"h": 1, "text": "2. 변경 내역"},
        _list(["비목", "세목", "당초(천원)", "변경(천원)", "증감(천원)", "사유"], 3, [1, 1, 1, 1, 1, 2]),
        {"h": 1, "text": "3. 성과지표·사업 추진에 미치는 영향"},
        {"p": ""}, _SIGN]},
    "equipment": {"id": "equipment", "title": "기자재 구입 계획서 공통 양식", "cover": ["기자재 구입 계획서"], "blocks": [
        _kv([["사업명", ""], ["세부 과제", ""], ["사용 부서", ""], ["담당자", ""]]),
        {"h": 1, "text": "1. 구입 목적"},
        {"h": 1, "text": "2. 구입 내역"},
        _list(["품명", "규격", "수량", "단가(천원)", "금액(천원)", "활용 계획"], 3, [1.5, 2, 0.6, 1, 1, 2]),
        {"h": 1, "text": "3. 활용 및 관리 계획"},
        {"h": 1, "text": "4. 기대 효과"}]},
    "kpi_evidence": {"id": "kpi_evidence", "title": "성과지표 실적·증빙 공통 양식", "cover": ["성과지표 실적 및 증빙"], "blocks": [
        _kv([["성과지표", ""], ["지표 정의", ""], ["산출식", ""], ["목표값", ""], ["실적값", ""], ["달성률(%)", ""]]),
        {"h": 1, "text": "1. 실적 산출 근거"},
        _list(["연번", "증빙 자료", "내용", "수량", "비고"], 3, [0.6, 2, 3, 0.8, 1]),
        {"h": 1, "text": "2. 미달성 사유 및 개선 계획"}]},
    "self_review": {"id": "self_review", "title": "운영 결과 자체성과분석 공통 양식", "cover": ["운영 결과 자체성과분석"], "blocks": [
        _kv([["프로그램명", ""], ["운영 기간", ""], ["참여 인원", ""], ["집행 예산(천원)", ""], ["운영 부서", ""]]),
        {"h": 1, "text": "1. 운영 개요"},
        {"h": 1, "text": "2. 목표 대비 성과"},
        _list(["성과지표", "목표", "실적", "달성률(%)", "분석"], 3, [2, 1, 1, 1, 3]),
        {"h": 1, "text": "3. 만족도 조사 결과"},
        {"h": 1, "text": "4. 개선 사항 및 환류 계획"}]},
    "partner_apply": {"id": "partner_apply", "title": "산학협력 협약 체결 신청서 공통 양식", "cover": ["산학협력 협약 체결 신청서"], "blocks": [
        _grid([["기관명", "", "대표자", ""], ["사업자등록번호", "", "업종", ""], ["주소", "", "전화", ""],
               ["담당자", "", "전자우편", ""]], [1.2, 2, 1, 2]),
        {"h": 1, "text": "1. 협약 목적"},
        {"h": 1, "text": "2. 협력 내용"},
        _list(["협력 분야", "내용", "기간"], 3, [1.5, 4, 1.5]),
        {"p": "위와 같이 산학협력 협약 체결을 신청합니다."},
        {"p": "20    .    .    ."},
        _kv([["신청 기관", ""], ["대표자(서명)", ""]])]},
    "trip_report": {"id": "trip_report", "title": "출장·회의 결과 보고서 공통 양식", "cover": ["출장(회의) 결과 보고"], "blocks": [
        _kv([["관련 사업", ""], ["출장자", ""], ["기간", ""], ["장소", ""], ["목적", ""]]),
        {"h": 1, "text": "1. 주요 내용"},
        {"h": 1, "text": "2. 결과 및 후속 조치"},
        {"h": 1, "text": "3. 경비"},
        _list(["항목", "금액(원)", "비고"], 3, [2, 1, 2]),
        {"p": "붙임  1. 증빙 자료 1부.  끝."}]},
}


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
