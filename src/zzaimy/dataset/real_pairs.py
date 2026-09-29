"""실문서로 Writer 학습쌍 만들기 — 양식(작성서식) × 완성본(사업계획서) 한 쌍에서 절 작성·표 채우기 쌍을 낸다(사용자 지시 2026-09-29).

원리(브리프 규칙 1·8, model-plan §2 정제 규칙):
- 입력 = 양식 절의 안내(작성방법) + 평가지표·공고·기본계획 근거 조각 + 그 절의 사실 목록(수치·명칭, 완성본에서 뽑음) + 양식 표 격자.
  출력 = 완성본의 그 절을 에이전트 편집 계획(JSON: insert·table·fill)으로 옮긴 것. 서빙 때 모델이 보는 것과 같은 꼴이라 학습이 곧 능력이다.
- 사실 목록이 없으면 모델이 수치를 지어내게 가르치는 셈이다. 그래서 완성본의 수치·명칭을 입력에 사실 목록으로 준다 — 모델은 '사실을
  양식에 맞게 구성하는 능력'을 배우고, 지식(수치)은 검색·자료가 준다(규칙 8).
- 출력의 수치가 입력 어디에도 없으면 그 쌍은 폐기한다(정제 규칙).
- 양식 표 격자는 독스가 보는 격자(우리 docx 변환의 tblGrid)와 같게 docx 에서 읽는다 — fill 의 row·col 이 서빙 때와 같다.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from zzaimy.app import section_context as sc

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
_HEAD = re.compile(r"^\s*(?:(?P<roman>[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)\s*[.．]|(?P<num>\d+(?:\.\d+)*)[.．]?)\s*(?P<rest>\S.*)?$")
_BOX = re.compile(r"【\s*(작성방법|증빙자료|작성\s*가이드|작성\s*요령)\s*】")


@dataclass
class FormSection:
    index: int
    heading: str
    level: int
    instructions: str = ""
    grids: list[dict] = field(default_factory=list)          # [{n, rows, covered}]


def _norm(s: str) -> str:
    return re.sub(r"[^\w]", "", (s or "").lower().replace("", ""))


def form_model(hwpx: Path | str) -> list[FormSection]:
    """양식 hwpx → 절 목록(제목·작성방법·양식 표 격자). 격자는 우리 docx 변환을 python-docx 로 읽어 독스와 같은 좌표를 쓴다."""
    from docx import Document
    from docx.oxml.ns import qn

    from zzaimy.ingest import hwpx_docx

    data, _ = hwpx_docx.convert(Path(hwpx))
    doc = Document(io.BytesIO(data))
    sections: list[FormSection] = []
    cur: FormSection | None = None
    body = doc.element.body
    for el in body.iterchildren():
        if el.tag == qn("w:p"):
            text = "".join(t.text or "" for t in el.iter(qn("w:t"))).strip()
            m = _HEAD.match(text) if text and len(text) < 80 else None
            rest = (m.group("rest") or "").strip() if m else ""
            # 절 제목은 번호 + 글(한글·영문) — '2026. 4.' 같은 날짜, '2cm' 같은 치수는 제목이 아니다
            if m and re.search(r"[가-힣A-Za-z]", rest) and not re.match(r"^\d{4}$", m.group("num") or "") and not re.match(r"^(cm|mm|pt)\b", rest):
                level = 1 if m.group("roman") else (m.group("num") or "").count(".") + 1
                cur = FormSection(index=len(sections) + 1, heading=text, level=level)
                sections.append(cur)
        elif el.tag == qn("w:tbl") and cur is not None:
            from docx.table import Table

            t = Table(el, doc)
            rows: list[list[str]] = []
            covered: set = set()
            spans: dict = {}
            for ri, row in enumerate(t.rows):
                seen: dict[int, int] = {}
                cells: list[str] = []
                for ci, cell in enumerate(row.cells):
                    key = id(cell._tc)
                    txt = " ".join(p.text.strip() for p in cell.paragraphs if p.text.strip())
                    if key in seen:
                        covered.add((ri, ci))                        # 가로 병합으로 같은 칸이 되풀이된다
                        spans[(ri, seen[key])] = (seen[key], ci)
                        cells.append("")
                        continue
                    seen[key] = ci
                    spans[(ri, ci)] = (ci, ci)
                    vm = cell._tc.tcPr.find(qn("w:vMerge")) if cell._tc.tcPr is not None else None
                    if vm is not None and vm.get(qn("w:val")) is None:
                        covered.add((ri, ci))                        # 세로 병합의 이어지는 칸
                        cells.append("")
                        continue
                    cells.append(txt)
                rows.append(cells)
            flat = " ".join(c for r in rows for c in r)
            if _BOX.search(flat):
                cur.instructions = (cur.instructions + "\n" + flat).strip()
            elif len(rows) == 1 and len(rows[0]) == 1:
                continue                                                 # 1×1 상자(그림 자리·안내 틀)는 채우는 표가 아니다
            else:
                cur.grids.append({"n": len(cur.grids) + 1, "rows": rows, "covered": covered, "spans": spans})
    return sections


def render_grids(grids: list[dict], max_rows: int = 40) -> str:
    from zzaimy.ingest.gdocs import render_table_grids

    return render_table_grids(grids, max_rows=max_rows)


def fact_sheet(parts: list[tuple[str, object]], limit: int = 400) -> list[str]:
    """완성본 절의 사실 목록 — 수치는 앞뒤 낱말과 함께(무슨 수치인지 알게), 표 값은 '행 이름 · 열 머리: 값'."""
    facts: list[str] = []
    seen: set[str] = set()
    n_text = 0
    for kind, payload in parts:
        if kind == "text":
            for m in _NUM.finditer(str(payload)):
                if not any(ch.isdigit() for ch in m.group(0)) or (m.group(0).isdigit() and len(m.group(0)) <= 1):
                    continue
                a, b = max(0, m.start() - 14), min(len(str(payload)), m.end() + 6)
                phrase = " ".join(str(payload)[a:b].split())
                if phrase not in seen and n_text < limit:
                    seen.add(phrase)
                    facts.append(phrase)
                    n_text += 1
        else:
            cells = payload if payload and isinstance(payload[0], tuple) else table_cells("\n".join(" | ".join(r) for r in payload))
            groups, head_n = column_groups(cells)
            labels = [g for g in groups if not g["numeric"]]
            by_row: dict[int, list] = {}
            for c in cells:
                if c[0] >= head_n:
                    by_row.setdefault(c[0], []).append(c)
            for ri, cs in by_row.items():
                key = " ".join(t for r, c, rs, csn, t in cs if t.strip() and not _is_value(t)
                               and any(not (c + csn - 1 < g["x0"] or c > g["x1"]) for g in labels))[:28]
                for r, c, rs, csn, t in cs:
                    if not _is_value(t):
                        continue
                    g = next((g for g in groups if g["numeric"] and not (c + csn - 1 < g["x0"] or c > g["x1"])), None)
                    phrase = f"{key} · {(g or {}).get('label', '')[:16]}: {t}".strip()
                    if phrase not in seen:
                        seen.add(phrase)
                        facts.append(phrase)
            # 글 칸(머리 행 포함) 속 수치 — '82개 AI교과목', '[증빙 2-35]', '32건' 도 문맥 구절로(실적 표는 값 칸 없이 글 칸뿐이다)
            for r, c, rs, csn, t in cells:
                if _is_value(t) and r >= head_n:
                    continue
                for m in _NUM.finditer(t):
                    if not any(ch.isdigit() for ch in m.group(0)):
                        continue
                    a, b = max(0, m.start() - 14), min(len(t), m.end() + 6)
                    phrase = " ".join(t[a:b].split())
                    if phrase not in seen:
                        seen.add(phrase)
                        facts.append(phrase)
    # 글의 수치 구절은 limit 까지, 표의 값은 전부(값을 빼면 출력의 수치가 입력에 없게 된다)
    return facts


def table_cells(raw: str) -> list[tuple[int, int, int, int, str]]:
    """표 조각 JSON → [(row, col, rowspan, colspan, text)]. JSON 이 아니면 ' | ' 행렬로 읽어 칸마다 1×1."""
    try:
        data = json.loads(raw)
        return [(int(c[0]), int(c[1]), int(c[2] or 1), int(c[3] or 1), " ".join(str(c[5] if len(c) > 5 else c[-1]).split()))
                for c in data.get("cells", [])]
    except Exception:
        out = []
        for ri, ln in enumerate(x for x in raw.replace("\r", "\n").split("\n") if x.strip()):
            for ci, c in enumerate(ln.split(" | ")):
                out.append((ri, ci, 1, 1, c.strip()))
        return out


def _grid_cells(grid: dict) -> list[tuple[int, int, int, int, str]]:
    """양식 격자(rows·covered·spans) → 보이는 칸 목록(범위 포함)."""
    out = []
    for ri, row in enumerate(grid["rows"]):
        for ci, txt in enumerate(row):
            if (ri, ci) in grid["covered"]:
                continue
            c0, c1 = grid.get("spans", {}).get((ri, ci), (ci, ci))
            out.append((ri, ci, 1, c1 - c0 + 1, "" if _is_placeholder(txt) else txt))
    return out


def _is_value(txt: str) -> bool:
    """값 칸 — 수치가 주(단위 두어 자 이내): 5.8, 1,200명, 32건, 100%. '1차년도'·'2026년' 같은 머리 글도 걸리므로 머리 행 판정에는 안 쓴다."""
    return bool(txt.strip()) and bool(_NUM.search(txt)) and len(re.sub(r"[\d.,%\s()~\-]", "", txt)) <= 6


_PLACEHOLDER = re.compile(r"^\(?\s*예\s*시\s*\)?|^[0○◯□]{2,}[가-힣%]{0,4}$|^[○◯]{1,3}[가-힣]{0,2}$")


def _is_placeholder(txt: str) -> bool:
    """양식의 자리 표시 — '(예시) …', '000백만원', '○○○', '00명'. 빈 칸으로 본다."""
    return bool(txt.strip()) and _PLACEHOLDER.match(txt.strip()) is not None


def _pure_number(txt: str) -> bool:
    return any(ch.isdigit() for ch in txt) and re.fullmatch(r"[\d.,%~\-\s()]+", txt.strip()) is not None


def _header_row_ok(texts: list[str], form_texts: set[str] | None) -> bool:
    """머리 행의 글인가 — 순수 수치가 없고, 전부 짧은 이름표(≤8자: 단위·기준값·2026년·1차년도)이거나 양식 머리 글과 절반 넘게 겹친다."""
    texts = [t for t in texts if t.strip()]
    if not texts or any(_pure_number(t) for t in texts):
        return False
    if all(len(t.strip()) <= 8 for t in texts):
        return True
    if form_texts:
        hit = sum(1 for t in texts if _norm(t) and any(_norm(t) in f or f in _norm(t) for f in form_texts))
        return hit >= 0.5 * len(texts)
    return False


def column_groups(cells: list[tuple[int, int, int, int, str]], head_n: int | None = None,
                  form_texts: set[str] | None = None) -> tuple[list[dict], int]:
    """머리 행(수치 칸이 없고 글 칸이 과반인 위쪽 행들)에서 열 묶음을 뽑는다 — [{label, x0, x1, blank, filled, numeric}], 머리 행 수.
    열 묶음의 label 은 그 x 범위를 덮는 가장 아래 머리 칸의 글. blank = 자료 행이 대개 빈 열(양식의 채울 자리),
    filled = 대개 글이 있는 열(완성본의 값), numeric = 그 값이 대개 수치."""
    if not cells:
        return [], 0
    n_rows = max(r for r, *_ in cells) + 1
    n_cols = max(c + cs for _, c, _, cs, _ in cells)
    by_row: dict[int, list] = {}
    for c in cells:
        by_row.setdefault(c[0], []).append(c)
    shadow = {(r + dr, c + dc) for r, c, rs, cs, _ in cells for dr in range(1, rs) for dc in range(cs)}
    if head_n is None:
        # 머리 행: 순수 수치 칸이 없고, 빈 칸이 있다면 그 자리 위에 이미 머리 글이 있는 행(세로 병합 아래 자리).
        # '2026년'·'1차년도' 는 머리 글이다. 글뿐인 표(실적표)는 모든 행이 머리처럼 보이므로 첫 행만 머리로 둔다
        head_n = 0
        seen_label = [False] * n_cols
        for ri in range(n_rows):
            row = [c for c in by_row.get(ri, []) if (c[0], c[1]) not in shadow]
            if not _header_row_ok([t for *_, t in row], form_texts):
                break
            if any(not t.strip() and not all(seen_label[x] for x in range(c, min(c + cs, n_cols))) for _, c, rs, cs, t in row):
                break
            head_n += 1
            for _, c, rs, cs, t in row:
                if t.strip():
                    for x in range(c, min(c + cs, n_cols)):
                        seen_label[x] = True
        head_n = 1 if head_n >= n_rows else max(1, min(head_n, 3))
    label_at = ["" for _ in range(n_cols)]
    for ri in range(head_n):
        for _, c, rs, cs, t in by_row.get(ri, []):
            for x in range(c, min(c + cs, n_cols)):
                label_at[x] = t or label_at[x]
    groups: list[dict] = []
    for x, lb in enumerate(label_at):
        if groups and groups[-1]["label"] == lb:
            groups[-1]["x1"] = x
        else:
            groups.append({"label": lb, "x0": x, "x1": x})
    for g in groups:
        vals = [t for r, c, rs, cs, t in cells if r >= head_n and not (c + cs - 1 < g["x0"] or c > g["x1"])]
        blanks = sum(1 for t in vals if not t.strip())
        nums = sum(1 for t in vals if _is_value(t))
        g["blank"] = bool(vals) and blanks >= len(vals) / 2
        g["filled"] = bool(vals) and (len(vals) - blanks) >= len(vals) / 2
        g["numeric"] = bool(vals) and nums >= len(vals) / 2
        g["value"] = g["blank"] or g["numeric"]
    return groups, head_n


def _pair_groups(form_g: list[dict], fin_g: list[dict]) -> list[tuple[dict, dict]]:
    """열 묶음 대응 — 이름이 같거나 품으면 그것, 남은 양식의 빈 열은 완성본의 남은 값 열과 순서대로
    (1차년도↔2026년 같은 표기 차이는 이름으로 못 맞춘다)."""
    pairs: list[tuple[dict, dict]] = []
    used: set[int] = set()
    for fg in form_g:
        a = _norm(fg["label"])
        if not a:
            continue
        for j, gg in enumerate(fin_g):
            b = _norm(gg["label"])
            if j not in used and b and (a == b or a in b or b in a):
                pairs.append((fg, gg)); used.add(j)
                break
    rest_form = [g for g in form_g if g["blank"] and not any(g is x for x, _ in pairs)]
    rest_fin = [g for j, g in enumerate(fin_g) if g["filled"] and j not in used]
    for fg, gg in zip(rest_form, rest_fin):
        pairs.append((fg, gg))
    return pairs


def _row_key(cells_of_row: list, label_groups: list[dict]) -> str:
    parts = []
    for r, c, rs, cs, t in cells_of_row:
        if any(not (c + cs - 1 < g["x0"] or c > g["x1"]) for g in label_groups) and t.strip() and not _is_value(t):
            parts.append(t)
    return _norm(" ".join(parts))


def _row_score(a: str, b: str) -> float:
    """행 이름의 닮음 — 같으면 1, 품으면 0.9, 그 밖에는 글자 열 비율(0.75 미만은 0)."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 6 and (a in b or b in a):
        return 0.9
    import difflib

    r = difflib.SequenceMatcher(None, a, b).ratio()
    return r if r >= 0.75 else 0.0


def _fill_from_table(grid: dict, fin_cells: list[tuple[int, int, int, int, str]]) -> list[dict]:
    """완성본 표의 값 → 양식 격자의 (row, col). 열은 머리 이름·순서로, 행은 이름 칸 글로 맞추고, 칸은 x 범위 겹침으로 고른다.
    값은 수치든 글이든(추진체계표의 위원회 이름처럼) 양식의 빈 열에 해당하는 것만 옮긴다."""
    form_cells = _grid_cells(grid)
    # 머리 행 수는 완성본에서 세고(양식 위쪽 세 행의 글을 참고) 양식에 물려준다 — 양식은 빈 칸·견본 글 때문에 스스로 못 센다
    top_texts = {_norm(t) for r, c, rs, cs, t in form_cells if r < 3 and t.strip()}
    fin_g, fin_h = column_groups(fin_cells, form_texts=top_texts)
    form_g, form_h = column_groups(form_cells, head_n=fin_h)
    # 완성본의 열이 양식의 이름 칸 글(혁신지원·RISE·소계 …)로 차 있으면 값 열이 아니라 이름 열이다 — 빈 칸에 옮기지 않는다
    form_texts = {_norm(t) for *_, t in form_cells if t.strip()}
    for g in fin_g:
        vals = [_norm(t) for r, c, rs, cs, t in fin_cells if r >= fin_h and t.strip() and not (c + cs - 1 < g["x0"] or c > g["x1"])]
        g["label_like"] = bool(vals) and sum(1 for v in vals if v in form_texts) >= len(vals) / 2
    all_pairs = [(a, b) for a, b in _pair_groups(form_g, fin_g) if not (a["blank"] and b.get("label_like"))]
    gpairs = [(a, b) for a, b in all_pairs if a["blank"]]
    if not gpairs:
        return []
    form_labels = [g for g in form_g if not g["blank"]]
    fin_labels = [b for a, b in all_pairs if not a["blank"]]
    if form_labels and not fin_labels:
        fin_labels = [g for g in fin_g if not g["numeric"]]
    form_rows: dict[int, list] = {}
    for c in form_cells:
        if c[0] >= form_h:
            form_rows.setdefault(c[0], []).append(c)
    fin_rows: dict[int, list] = {}
    for c in fin_cells:
        if c[0] >= fin_h:
            fin_rows.setdefault(c[0], []).append(c)
    # 세로 병합된 이름 칸(’25년·교육부 같은 묶음 이름)은 아래 행에도 이어진다 — 행 이름에 물려준다
    fin_inherit: dict[int, list[str]] = {}
    for r, c, rs, cs, t in fin_cells:
        if rs > 1 and t.strip() and any(not (c + cs - 1 < g["x0"] or c > g["x1"]) for g in fin_labels):
            for rr in range(r + 1, r + rs):
                fin_inherit.setdefault(rr, []).append(t)
    form_inherit: dict[int, list[str]] = {}
    for (ri, ci) in grid.get("covered", set()):
        if ri < form_h or not any(g["x0"] <= ci <= g["x1"] for g in form_labels):
            continue
        rj = ri - 1
        while rj >= 0 and (rj, ci) in grid["covered"]:
            rj -= 1
        if rj >= 0 and (ri, ci) not in grid.get("spans", {}) and ci < len(grid["rows"][rj]) and grid["rows"][rj][ci].strip():
            form_inherit.setdefault(ri, []).append(grid["rows"][rj][ci])
    form_keys = {ri: _norm(" ".join(form_inherit.get(ri, []))) + _row_key(cs, form_labels) for ri, cs in form_rows.items()}
    fin_keys = {ri: _norm(" ".join(fin_inherit.get(ri, []))) + _row_key(cs, fin_labels) for ri, cs in fin_rows.items()}
    out: list[dict] = []
    used: set[tuple[int, int]] = set()
    used_rows: set[int] = set()
    for fri, fkey in fin_keys.items():
        if fkey:
            cand = sorted(((-_row_score(fkey, k), ri) for ri, k in form_keys.items() if ri not in used_rows))   # 닮은 정도, 그다음 위쪽 행
            if not cand or cand[0][0] >= 0:
                continue
            gri = cand[0][1]
        elif not any(form_keys.values()):
            gri = fri - fin_h + form_h                                # 이름 열이 없는 표(추진체계·일정)는 같은 순번의 행
            if gri not in form_rows:
                continue
        else:
            continue
        used_rows.add(gri)
        for fg, gg in gpairs:
            src = next((t for r, c, rs, cs, t in fin_rows[fri] if not (c + cs - 1 < gg["x0"] or c > gg["x1"]) and t.strip()), "")
            if not src:
                continue
            tgt = next(((r, c) for r, c, rs, cs, t in form_rows[gri]
                        if not (c + cs - 1 < fg["x0"] or c > fg["x1"]) and not t.strip() and (r, c) not in used), None)
            if tgt is None:
                continue
            used.add(tgt)
            out.append({"row": tgt[0], "col": tgt[1], "text": src})
    # 완성본 자료 행의 절반도 못 옮기면(양식이 견본 몇 줄뿐인 명단표 등) 채우기가 아니라 새 표가 맞다 — 빠뜨린 행이 학습에 남지 않게
    fin_data_rows = [ri for ri, cs in fin_rows.items()
                     if any(t.strip() and any(not (c + cs_ - 1 < gg["x0"] or c > gg["x1"]) for _, gg in gpairs) for r, c, rs, cs_, t in cs)]
    if fin_data_rows and len({c["row"] for c in out}) < 0.5 * len(fin_data_rows):
        return []
    return out


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[^\d.%]", "", m.group(0)) for m in _NUM.finditer(text or "") if any(ch.isdigit() for ch in m.group(0))}


def _prompt(sec: FormSection, sections: list[FormSection], title: str, materials: str, command: str) -> str:
    """서빙 때 에이전트가 보는 프롬프트와 같은 틀(gdocs_agent._PROMPT) — 학습 입력과 운영 입력이 같아야 학습이 능력이 된다."""
    from zzaimy.app.gdocs_agent import _PROMPT

    outline = "\n".join(f"{s.index} · {s.heading} · 0자" for s in sections)
    body = sec.heading + "\n" + (sec.instructions[:1200] if sec.instructions else "")
    return _PROMPT.format(title=title, outline=outline, text=body, focus_note=f" (대상 절 「{sec.heading}」 의 현재 글만)",
                          materials=materials.strip() + "\n", evidence="(없음)", command=command.strip())


def build_section_pair(sec: FormSection, sections: list[FormSection], parts: list[tuple[str, object]], criteria: list[dict],
                       title: str = "", command: str = "") -> dict | None:
    """한 절의 학습쌍 — {"human": 입력, "output": 편집 계획 JSON, "shown": 검수용 글, "stats": …}. 완성본에 그 절이 없으면 None."""
    text_parts = [str(p) for k, p in parts if k == "text" and str(p).strip()]
    table_parts = [p for k, p in parts if k == "table" and p]
    figure_parts = [p for k, p in parts if k == "figure" and p]
    if not text_parts and not table_parts and not figure_parts:
        return None
    facts = fact_sheet([("table", p["cells"]) if k == "table" else ("text", _figure_text(p) if k == "figure" else p) for k, p in parts])
    ops: list[dict] = []
    filled = 0
    used_grids: set = set()
    for k, p in parts:                                               # 절의 흐름 순서대로 — 문단·표·도식이 섞인 구성을 그대로 가르친다
        if k == "text":
            buf: list[str] = []
            for ln in str(p).split("\n"):
                if not ln.strip():
                    continue
                if sum(len(x) for x in buf) + len(ln) > 700 and buf:
                    ops.append({"op": "insert", "section": sec.index, "old": "", "text": "\n".join(buf), "table": 0, "cells": []})
                    buf = []
                buf.append(ln.strip())
            if buf:
                ops.append({"op": "insert", "section": sec.index, "old": "", "text": "\n".join(buf), "table": 0, "cells": []})
        elif k == "figure":
            ops.append({"op": "figure", "section": sec.index, "old": "", "text": json.dumps(p, ensure_ascii=False), "table": 0, "cells": []})
        elif k == "table" and p:
            rows, fcells = p["rows"], p["cells"]
            target = match_grid(sec, fcells, used_grids)
            if target is not None:
                cells = _fill_from_table(target, fcells)
                if cells:
                    ops.append({"op": "fill", "section": sec.index, "old": "", "text": "", "table": target["n"], "cells": cells})
                    filled += len(cells)
                    continue
            ops.append({"op": "table", "section": sec.index, "old": "", "text": "\n".join(" | ".join(r) for r in rows), "table": 0, "cells": []})
    n_ins = sum(1 for o in ops if o["op"] == "insert")
    n_tbl = sum(1 for o in ops if o["op"] == "table")
    n_fig = sum(1 for o in ops if o["op"] == "figure")
    parts_desc = [x for x in [f"문단 {n_ins}건" if n_ins else "", f"표 {n_tbl}개" if n_tbl else "", f"도식 {n_fig}개" if n_fig else "",
                              f"양식 표 {filled}칸 채움" if filled else ""] if x]
    reply = f"「{sec.heading}」 절을 작성방법에 맞춰 작성했습니다 — {', '.join(parts_desc)}. 수치·명칭은 지난 자료의 사실 목록에 있는 것만 썼습니다."
    output = {"reply": reply, "ops": ops, "asks": []}
    command = command or f"「{sec.heading}」 절을 작성방법에 맞춰 작성해 줘. 담당자 지시: 지난 자료의 사실만 쓰고, 양식 표는 채워 줘"
    crit_lines = "\n".join(f"- ({c.get('reg_title') or c.get('doc_title') or ''}) {str(c.get('content') or '')[:400]}" for c in criteria[:5])
    materials = "\n\n".join(x for x in [
        "[이 절의 양식 안내·작성방법]\n" + (sec.instructions[:2500] or sec.heading),
        ("[평가지표·공고·기본계획 근거]\n" + crit_lines) if crit_lines else "",
        "[문서함의 지난 사업 자료 — 이 절과 관련된 사실 목록]\n" + "\n".join(f"- {f}" for f in facts) if facts else "",
        render_grids(sec.grids) if sec.grids else "",
    ] if x)
    human = _prompt(sec, sections, title, materials, command)
    out_text = json.dumps(output, ensure_ascii=False)
    missing = _numbers(out_text) - _numbers(human)
    missing = {m for m in missing if len(m.strip("%.")) > 1}
    shown = []
    for o in ops:
        if o["op"] == "insert":
            shown.append(o["text"])
        elif o["op"] == "table":
            shown.append("[새 표]\n" + o["text"])
        elif o["op"] == "figure":
            spec = json.loads(o["text"])
            shown.append(f"[도식 {spec.get('layout')}] {spec.get('title', '')}\n" + "\n".join(
                f"  ▣ {b.get('title', '')}: " + " / ".join(b.get("items") or []) for b in spec.get("blocks") or []))
        else:
            shown.append(f"[양식 표 {o['table']} 채우기] " + ", ".join(f"r{c['row']} c{c['col']} = {c['text']}" for c in o["cells"]))
    return {"human": human, "output": out_text, "shown": "\n\n".join(shown), "missing_numbers": sorted(missing), "materials": materials,
            "stats": {"inserts": n_ins, "tables": n_tbl, "figures": n_fig, "filled": filled, "facts": len(facts), "chars": sum(len(t) for t in text_parts)}}


def _figure_text(spec: dict) -> str:
    return "\n".join([spec.get("title") or ""] + [f"{b.get('title', '')} " + " ".join(b.get("items") or []) for b in spec.get("blocks") or []])


def match_grid(sec: FormSection, fin_cells: list, used: set | None = None) -> dict | None:
    """완성본 표가 이 절의 어느 양식 표인가 — 머리 칸 글이 6할 넘게 겹치는, 아직 안 쓴 격자(같은 꼴 격자가 되풀이되면 순서대로)."""
    for g in sec.grids:
        if used is not None and g["n"] in used:
            continue
        g_cells = {_norm(c) for r in g["rows"] for c in r if c.strip()}
        fin_g, _ = column_groups(fin_cells, form_texts={_norm(c) for r in g["rows"][:3] for c in r if c.strip()})
        head = {_norm(x["label"]) for x in fin_g if x["label"].strip()}
        if head and sum(1 for h in head if any(h == x or h in x or x in h for x in g_cells)) >= max(1, int(0.6 * len(head))):
            if used is not None:
                used.add(g["n"])
            return g
    return None


_BULLET = re.compile(r"^\s*[•▪■◦∙·※\-–▶►]\s*")
_APPENDIX = re.compile(r"^(증빙\s*자료|별첨|부록|첨부\s*자료?)(\s*목차)?\s*$")
_FIGURE_MARK = "\ue000FIG:"


def figure_spec(image_text: str) -> dict | None:
    """그림 판독 조각('글자:' 줄들 + '설명:') → 도식 명세 {"title","layout","blocks":[{"title","items"}],"footer"}.
    글줄이 상자 제목(글머리 없는 줄)과 요점(• 줄)으로 읽히면 도식, 로고·사진처럼 글이 몇 줄 없으면 None."""
    body = image_text.split("설명:")[0]
    lines = [ln.strip() for ln in body.replace("글자:", "").split("\n") if ln.strip()]
    if len(lines) < 4:
        return None
    blocks: list[dict] = []
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if _BULLET.match(ln):                                        # 요점 — 현재 상자에
            if not blocks:
                blocks.append({"title": "", "items": []})
            blocks[-1]["items"].append(_BULLET.sub("", ln)[:60])
        elif ln.startswith("출처") and blocks and blocks[-1]["items"]:   # 출처 줄은 앞 요점에 붙인다
            blocks[-1]["items"][-1] = (blocks[-1]["items"][-1] + " (" + ln + ")")[:80]
        elif not blocks or (len(ln) <= 16 and _BULLET.match(nxt) and blocks[-1]["items"]):   # 짧은 이름표 + 다음 줄이 요점 = 새 상자
            blocks.append({"title": ln[:40], "items": []})
        elif blocks and blocks[-1]["items"]:                         # 글머리 없는 이어지는 줄 — 앞 요점의 계속
            blocks[-1]["items"][-1] = (blocks[-1]["items"][-1] + " " + ln)[:80]
        elif blocks and not blocks[-1]["items"] and len(blocks[-1]["title"]) < 30:
            blocks[-1]["title"] = (blocks[-1]["title"] + " " + ln).strip()[:40]
        else:
            blocks.append({"title": ln[:40], "items": []})
    title = ""
    if blocks and not blocks[0]["items"] and len(blocks) > 1:
        title = blocks.pop(0)["title"]
    blocks = [b for b in blocks if b["items"] or b["title"]]
    if not blocks or sum(len(b["items"]) for b in blocks) < 2:
        return None
    layout = "flow" if all(len(b["items"]) <= 1 for b in blocks) else "cards"
    return {"title": title, "layout": layout, "blocks": blocks[:6], "footer": ""}


def section_parts(sections: list[FormSection], chunks: list[dict]) -> list[dict]:
    """section_context.plan 과 같되 표 부분은 {"rows", "cells"}(칸 범위 포함), 그림 판독은 ("figure", 명세)로 —
    채우기 대응에 병합 범위가, 도식 학습에 그림 글이 필요하다. 그림 파일명 조각은 뺀다. 마지막 절은 부록(증빙자료·별첨) 앞에서 끝난다."""
    kept: list[dict] = []
    for ch in chunks:
        kind = ch.get("kind") or ""
        if kind == "image":
            continue
        if kind == "table":
            cells = [t for *_, t in table_cells(ch.get("content") or "") if t.strip()]
            if len(cells) <= 2 and all(len(t) <= 30 for t in cells):     # 띠 표(칸 하나둘에 짧은 글) = 꾸민 소제목
                if cells:
                    kept.append(dict(ch, kind="text", content=" ".join(cells)))
                continue
        if kind == "image_text":
            spec = figure_spec(ch.get("content") or "")
            if spec is None:
                continue
            kept.append(dict(ch, kind="text", content=_FIGURE_MARK + json.dumps(spec, ensure_ascii=False)))
            continue
        kept.append(ch)
    items = sc.stream(kept)
    running = sc.running_lines(items)
    spans = sc.align([{"index": s.index, "heading": s.heading} for s in sections], items)
    matched = [sp for sp in spans if sp.start >= 0]
    if matched:
        last = max(matched, key=lambda sp: sp.start)
        for j in range(last.start, min(last.end, len(items))):
            if items[j].kind == "text" and _APPENDIX.match(items[j].text.strip()):
                last.end = j
                break
    out = []
    for sp in spans:
        parts = sc.render(sp, items, running) if sp.start >= 0 else []
        raws = [it.text for it in items[max(sp.start, 0):min(sp.end, len(items))] if it.kind == "table"] if sp.start >= 0 else []
        fixed = []
        ti = 0
        for k, p in parts:
            if k == "table":
                raw = raws[ti] if ti < len(raws) else ""
                ti += 1
                fixed.append(("table", {"rows": p, "cells": table_cells(raw) if raw else table_cells("\n".join(" | ".join(r) for r in p))}))
                continue
            # 글 덩어리 안의 도식 표시 줄을 figure 부분으로 떼어 낸다
            buf: list[str] = []
            for ln in str(p).split("\n"):
                if ln.startswith(_FIGURE_MARK):
                    if buf:
                        fixed.append(("text", "\n".join(buf))); buf = []
                    try:
                        fixed.append(("figure", json.loads(ln[len(_FIGURE_MARK):])))
                    except json.JSONDecodeError:
                        pass
                else:
                    buf.append(ln)
            if buf:
                fixed.append(("text", "\n".join(buf)))
        out.append({"index": sp.section["index"], "heading": sp.section["heading"], "matched": sp.start >= 0, "parts": fixed,
                    "chars": sum(len(p) for k, p in fixed if k == "text"), "tables": sum(1 for k, _ in fixed if k == "table"),
                    "figures": sum(1 for k, _ in fixed if k == "figure")})
    return out


def build_fill_pair(sec: FormSection, sections: list[FormSection], grid: dict, fin_cells: list, title: str = "") -> dict | None:
    """표 채우기만 따로 — 입력은 격자와 값 목록(행 이름·열 머리: 값), 출력은 fill 한 건. 절 쌍보다 짧아 표 좌표 능력을 집중해 가르친다."""
    cells = _fill_from_table(grid, fin_cells)
    if len(cells) < 3:
        return None
    facts = fact_sheet([("table", fin_cells)])
    command = f"「{sec.heading}」 절의 표 {grid['n']} 을 지난 자료의 값으로 채워 줘"
    materials = "[문서함의 지난 사업 자료 — 이 표에 넣을 값]\n" + "\n".join(f"- {f}" for f in facts) + "\n\n" + render_grids([grid])
    human = _prompt(sec, sections, title, materials, command)
    op = {"op": "fill", "section": sec.index, "old": "", "text": "", "table": grid["n"], "cells": cells}
    reply = f"「{sec.heading}」 절의 표 {grid['n']} 에 {len(cells)}칸을 채웠습니다. 자료에 없는 칸은 비워 두었습니다."
    out_text = json.dumps({"reply": reply, "ops": [op], "asks": []}, ensure_ascii=False)
    missing = {m for m in _numbers(out_text) - _numbers(human) if len(m.strip("%.")) > 1}
    shown = f"[양식 표 {grid['n']} 채우기] " + ", ".join(f"r{c['row']} c{c['col']} = {c['text']}" for c in cells)
    return {"human": human, "output": out_text, "shown": shown, "missing_numbers": sorted(missing), "materials": materials,
            "stats": {"filled": len(cells), "facts": len(facts)}}
