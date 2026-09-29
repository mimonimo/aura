"""구글 독스 API — 문서 작업 화면에서 에이전트가 같은 문서를 읽고 고친다(2단계, ADR-0029).

읽기는 `documents.get`, 쓰기는 `documents.batchUpdate` 다. 위치는 문자 인덱스이므로 먼저 구조(제목 목록과 각
절의 범위)를 읽고 계산해 넣는다. 제안 모드(추적 변경)는 API 가 지원하지 않아 직접 편집만 된다.

쓰기 규칙(ADR-0029): 담당자가 화면에서 누른 삽입·치환만 보낸다(에이전트가 스스로 쓰지 않는다). 보내는 글은 개인정보
검사기를 한 번 더 거치고, 검색 근거 조각의 원문은 넣지 않는다(초안 글만). 모든 쓰기는 `gdocs_audit.jsonl` 에 남는다.
인증·토큰은 gdrive 와 같은 파일을 쓰고 허용 범위에 documents 가 있어야 한다(없으면 다시 허용을 안내).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from zzaimy.ingest import gdrive

log = logging.getLogger(__name__)

DOCS_API = "https://docs.googleapis.com/v1/documents"
DOCS_SCOPE = "https://www.googleapis.com/auth/documents"
_DOC_URL = re.compile(r"/document/d/([A-Za-z0-9_-]{4,})")
HEADING_LEVELS = {"TITLE": 0, "HEADING_1": 1, "HEADING_2": 2, "HEADING_3": 3, "HEADING_4": 4,
                  "HEADING_5": 5, "HEADING_6": 6}


def doc_id(text: str) -> str:
    s = (text or "").strip()
    m = _DOC_URL.search(s)
    if m:
        return m.group(1)
    if "/" in s or "?" in s:
        raise ValueError("구글 독스 주소(…/document/d/ID/edit) 또는 문서 ID 를 적어 주세요")
    return s


def has_docs_scope(email: str) -> bool:
    for a in gdrive.list_accounts():
        if a["email"] == email:
            return DOCS_SCOPE in (a.get("scopes") or [])
    return False


def _http():
    return gdrive._http()


def _headers(email: str, http) -> dict:
    return {"Authorization": f"Bearer {gdrive.access_token(email, http)}"}


def _raise(r) -> None:
    if r.status_code == 401:
        raise PermissionError("구글 접근 권한이 없습니다 — 계정 허용을 다시 해 주세요")
    if r.status_code == 403:
        raise PermissionError("이 문서를 고칠 권한이 없거나 허용 범위에 문서 편집이 없습니다 — 계정 허용을 다시 해 주세요")
    if r.status_code == 404:
        raise FileNotFoundError("구글 독스에서 문서를 찾지 못했습니다 — 주소와 공유 여부를 확인해 주세요")
    if r.status_code != 200:
        raise RuntimeError(f"구글 독스 응답 {r.status_code}: {r.text[:120]}")


def _para_text(p: dict) -> str:
    # 문단 안 줄 바꿈(Shift+Enter)은 API 에서 \x0b 로 온다 — 줄로 바꿔 둔다(그대로 hwpx 에 들어가면 한글이 문서를 못 연다)
    return "".join(e.get("textRun", {}).get("content", "") for e in p.get("elements", [])).replace("\x0b", "\n")


def body_content(document: dict) -> list[dict]:
    """본문 요소 목록 — 탭이 있는 문서(가져온·변환한 문서는 다 그렇다, 실측 2026-09-24)는 body 가 비고 tabs[0] 에 있다."""
    body = document.get("body", {}).get("content", [])
    if body:
        return body
    for tab in document.get("tabs", []) or []:
        content = tab.get("documentTab", {}).get("body", {}).get("content", [])
        if content:
            return content
    return []


# 제목 스타일이 하나도 없는 문서(한글 양식 변환본)에서는 번호 붙은 짧은 문단을 절 제목으로 본다 — Ⅰ. / 1. / 1.1. / 【…】 / 가.
_NUMBERED = re.compile(r"^\s*(?:(?P<roman>[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)\s*[.．]|(?P<num>\d+(?:\.\d+)*)[.．]?|(?P<box>【[^】]{1,40}】)|(?P<ga>[가-힣])[.．])\s*(?P<rest>\S.*)?$")


def _numbered_level(text: str) -> int | None:
    t = text.strip()
    if not t or len(t) > 70 or re.search(r"[.다요음임함]\s*$", t) and not t.endswith("】"):
        return None
    m = _NUMBERED.match(t)
    if not m:
        return None
    if m.group("roman"):
        return 1
    if m.group("num"):
        if m.group("rest") is None:
            return None                               # '2026.' 같은 숫자만 있는 줄은 제목이 아니다
        depth = m.group("num").count(".") + 1
        return min(1 + depth, 4)
    if m.group("box"):
        return 2
    if m.group("ga"):
        return 3
    return None


def _table_text(tbl: dict) -> str:
    rows = []
    for row in tbl.get("tableRows", []):
        cells = []
        for cell in row.get("tableCells", []):
            cells.append(" ".join(_para_text(e["paragraph"]).strip() for e in cell.get("content", []) if "paragraph" in e).strip())
        rows.append(" | ".join(c for c in cells))
    return "\n".join(r for r in rows if r.strip(" |"))


def outline(document: dict) -> dict:
    """documents.get 결과 → {title, end, sections:[{index, level, heading, start, end, chars}], text}.

    절은 제목(TITLE·HEADING_n)에서 다음 같은 급 이상의 제목 전까지다. 제목 없는 앞머리는 index 0 '(앞머리)'.
    end 는 그 절의 마지막 문단 끝 인덱스(다음 절 시작 직전). 표는 절의 글에 들어가지만(칸을 ' | ' 로) 삽입 위치(end)는
    문단에만 둔다. 제목 스타일이 전혀 없으면 번호 붙은 문단을 절로 본다(한글 양식 변환본).
    """
    body = body_content(document)
    items: list[tuple[int, int, str, str, bool]] = []       # (start, end, style, text, is_table)
    for el in body:
        if "paragraph" in el:
            p = el["paragraph"]
            items.append((int(el.get("startIndex", 0)), int(el.get("endIndex", 0)),
                          p.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT"), _para_text(p), False))
        elif "table" in el:
            items.append((int(el.get("startIndex", 0)), int(el.get("endIndex", 0)), "TABLE", _table_text(el["table"]), True))
    doc_end = int(body[-1].get("endIndex", 1)) if body else 1
    styled = any(HEADING_LEVELS.get(st) is not None and t.strip() for _s, _e, st, t, tb in items if not tb)
    sections: list[dict] = []
    cur = {"index": 0, "level": 0, "heading": "(앞머리)", "start": 1, "end": 1, "chars": 0, "table_end": 0, "text": "", "body_chars": 0}
    for start, end, style, text, is_table in items:
        # 제목 스타일이 있어도 번호 문단(Ⅰ. / 1.1.)은 절이다 — 변환한 한글 문서는 개요 스타일이 몇 개뿐이고 절 제목이 굵은 보통
        # 문단이라(실측 2026-09-24 사업계획서: 스타일 제목 3개, 번호 절 수십 개) 스타일만 보면 절이 3개로 잡힌다
        lvl = None if is_table else HEADING_LEVELS.get(style)
        if lvl is None and not is_table:
            lvl = _numbered_level(text)
            if lvl is not None and styled:
                lvl = max(lvl, 2)                     # 스타일 제목 아래 급으로
        if lvl is not None and text.strip() and not is_table:
            if cur["chars"] or cur["index"] > 0:        # 글 없는 앞머리는 절로 세지 않는다
                sections.append(cur)
            cur = {"index": len(sections) + 1, "level": lvl, "heading": text.strip()[:80],
                   "start": start, "end": end, "chars": 0, "table_end": 0, "text": text.strip(), "body_chars": 0}
            continue
        if is_table:
            cur["table_end"] = end                     # 절이 표(작성방법 상자)로 끝나면 글은 그 표 뒤에 들어가야 한다
        else:
            cur["end"] = end
            if text.strip():
                cur["table_end"] = 0                   # 표 뒤에 글 문단이 있으면 그 문단 끝이 삽입 자리
        cur["chars"] += len(text.strip())
        if text.strip():
            cur["text"] = (cur["text"] + "\n" + text.rstrip("\n")).strip()      # 절의 글(표는 칸을 ' | ' 로) — 목차의 같은 제목과 헷갈리지 않게 구조에서 자른다
            if not (is_table and "작성방법" in text):
                cur["body_chars"] += len(text.strip())                          # 본문 글자 — 양식의 작성방법 상자는 본문이 아니다
                if is_table:
                    # 양식 표의 빈 칸 비율 — 머리 칸만 있고 값 칸이 빈 표는 '아직 안 쓴 절'의 표시(2026-09-29: 표 채우기)
                    cells = [c for row in text.split("\n") for c in row.split(" | ")]
                    cur["tbl_cells"] = cur.get("tbl_cells", 0) + len(cells)
                    cur["tbl_empty"] = cur.get("tbl_empty", 0) + sum(1 for c in cells if not c.strip())
                else:
                    cur["para_chars"] = cur.get("para_chars", 0) + len(text.strip())
    sections.append(cur)
    anchors = {int(el.get("startIndex", 0)): el.get("paragraph", {}).get("paragraphStyle", {}).get("headingId") for el in body}
    tab_id = next((tab.get("tabProperties", {}).get("tabId") for tab in document.get("tabs", []) or []
                   if tab.get("documentTab", {}).get("body", {}).get("content") is body), None)
    for section in sections:
        section["heading_id"] = anchors.get(section["start"])
        section["tab_id"] = tab_id
    text = "\n".join(t.rstrip("\n") for _s, _e, _st, t, _tb in items)
    return {"title": document.get("title", ""), "end": doc_end, "sections": sections, "text": text}


def get(email: str, doc: str, http=None) -> dict:
    """문서를 읽어 구조와 평문을 돌려준다."""
    http = http or _http()
    r = http.get(f"{DOCS_API}/{doc_id(doc)}", headers=_headers(email, http), params={"includeTabsContent": "true"})
    _raise(r)
    return outline(r.json())


def _audit(data_dir: Path, rec: dict) -> None:
    p = Path(data_dir) / "gdocs_audit.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **rec}, ensure_ascii=False) + "\n")


def recent_writes(data_dir: Path, limit: int = 20) -> list[dict]:
    p = Path(data_dir) / "gdocs_audit.jsonl"
    if not p.exists():
        return []
    rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return rows[-limit:][::-1]


def _batch(email: str, doc: str, requests: list[dict], http) -> dict:
    r = http.post(f"{DOCS_API}/{doc_id(doc)}:batchUpdate", headers=_headers(email, http),
                  json={"requests": requests})
    _raise(r)
    return r.json()


def _body_style(start: int, end: int) -> dict:
    """넣은 글의 문단 모양을 본문으로 — 변환본의 표 사이 얇은 문단(고정 1pt)이나 제목 모양을 물려받아 글이 겹치던 문제(실측 2026-09-29, 1.2 절)."""
    return {"updateParagraphStyle": {"range": {"startIndex": start, "endIndex": end},
                                     "paragraphStyle": {"namedStyleType": "NORMAL_TEXT", "lineSpacing": 115,
                                                        "spaceAbove": {"magnitude": 0, "unit": "PT"}, "spaceBelow": {"magnitude": 4, "unit": "PT"}},
                                     "fields": "namedStyleType,lineSpacing,spaceAbove,spaceBelow"}}


def insert_into_section(email: str, doc: str, section_index: int, text: str, *, user: str,
                        data_dir: Path, scrub=None, http=None) -> dict:
    """절(제목 아래) 끝에 글을 넣는다. 글은 scrub(개인정보 검사기)을 거친다. 감사 기록을 남긴다."""
    http = http or _http()
    text = (text or "").strip()
    if not text:
        raise ValueError("넣을 글이 없습니다")
    if scrub is not None:
        text = scrub(text)
    info = get(email, doc, http)
    secs = {s["index"]: s for s in info["sections"]}
    sec = secs.get(int(section_index))
    if sec is None:
        raise ValueError("절을 다시 골라 주세요 — 문서 구조가 바뀌었습니다")
    if int(sec.get("table_end") or 0) > int(sec["end"]) - 1:
        # 절이 표(작성방법 상자)로 끝난다 — 표 바로 뒤(다음 문단 앞)에 새 문단으로 넣고, 그 문단의 모양은 본문으로 되돌린다
        # (표 뒤 문단이 다음 절 제목이면 넣은 글이 제목 모양을 물려받는다)
        at = min(int(sec["table_end"]), int(info["end"]) - 1)
        payload = text + "\n"
        reqs = [{"insertText": {"location": {"index": at}, "text": payload}},
                _body_style(at, at + len(payload))]
    else:
        # 절의 마지막 문단 끝(줄바꿈 앞)에 새 문단으로 넣는다. 문서 끝이면 끝 인덱스 - 1.
        at = max(1, min(int(sec["end"]) - 1, int(info["end"]) - 1))
        payload = "\n" + text
        reqs = [{"insertText": {"location": {"index": at}, "text": payload}},
                _body_style(at + 1, at + len(payload))]
    res = _batch(email, doc, reqs, http)
    _audit(data_dir, {"user": user, "doc": doc_id(doc), "action": "insert", "section": sec["heading"],
                      "chars": len(text)})
    return {"ok": True, "at": at, "chars": len(text), "section": sec["heading"], "reply": res.get("documentId", "")}


def replace_text(email: str, doc: str, old: str, new: str, *, user: str, data_dir: Path, scrub=None,
                 http=None) -> dict:
    """문서 안의 글을 모두 바꾼다(대소문자 구분). 바뀐 횟수를 돌려준다."""
    http = http or _http()
    old, new = (old or "").strip(), (new or "").strip()
    if not old:
        raise ValueError("바꿀 글을 적어 주세요")
    if scrub is not None:
        new = scrub(new)
    res = _batch(email, doc, [{"replaceAllText": {"containsText": {"text": old, "matchCase": True},
                                                   "replaceText": new}}], http)
    n = 0
    for rep in res.get("replies", []):
        n += int(rep.get("replaceAllText", {}).get("occurrencesChanged", 0))
    _audit(data_dir, {"user": user, "doc": doc_id(doc), "action": "replace", "chars": len(new), "count": n})
    return {"ok": True, "count": n}


def embed_url(doc: str, toolbar: bool = False) -> str:
    """편집기 주소 — 기본은 최소 모드(도구 모음 숨김). toolbar=True 면 구글 독스의 서식 도구 모음이 보인다."""
    base = f"https://docs.google.com/document/d/{doc_id(doc)}/edit"
    return base if toolbar else base + "?rm=minimal"


STYLES = {"TITLE", "HEADING_1", "HEADING_2", "HEADING_3", "NORMAL_TEXT"}


def _section_heading_range(info: dict, section_index: int) -> tuple[int, int] | None:
    sec = next((s for s in info["sections"] if s["index"] == int(section_index)), None)
    if not sec or sec["level"] == 0 and sec["heading"] == "(앞머리)":
        return None
    return int(sec["start"]), int(sec["start"]) + len(sec["heading"]) + 1


def set_section_style(email: str, doc: str, section_index: int, style: str, *, user: str, data_dir: Path,
                      http=None) -> dict:
    """절 제목의 문단 서식(제목 단계)을 바꾼다."""
    http = http or _http()
    style = (style or "").upper()
    if style not in STYLES:
        raise ValueError("서식은 TITLE·HEADING_1·HEADING_2·HEADING_3·NORMAL_TEXT 중 하나여야 합니다")
    info = get(email, doc, http)
    rng = _section_heading_range(info, section_index)
    if not rng:
        raise ValueError("절을 다시 골라 주세요")
    _batch(email, doc, [{"updateParagraphStyle": {"range": {"startIndex": rng[0], "endIndex": rng[1]},
                                                  "paragraphStyle": {"namedStyleType": style}, "fields": "namedStyleType"}}], http)
    _audit(data_dir, {"user": user, "doc": doc_id(doc), "action": "style", "section": info["sections"][0]["heading"] if False else next(s["heading"] for s in info["sections"] if s["index"] == int(section_index)), "style": style})
    return {"ok": True, "style": style}


def emphasize(email: str, doc: str, phrase: str, *, user: str, data_dir: Path, bold: bool = True, http=None) -> dict:
    """문서 안의 글귀를 굵게(또는 해제) — 첫 등장 위치부터 모두."""
    http = http or _http()
    phrase = (phrase or "").strip()
    if not phrase:
        raise ValueError("굵게 할 글귀를 적어 주세요")
    r = http.get(f"{DOCS_API}/{doc_id(doc)}", headers=_headers(email, http), params={"includeTabsContent": "true"})
    _raise(r)
    body = body_content(r.json())
    reqs = []
    for el in body:
        p = el.get("paragraph")
        if not p:
            continue
        offset = int(el.get("startIndex", 0))
        for e in p.get("elements", []):
            tr = e.get("textRun")
            if not tr:
                continue
            text = tr.get("content", "")
            start = int(e.get("startIndex", offset))
            offset = start + len(text)
            pos = text.find(phrase)
            while pos >= 0:
                a = start + pos
                reqs.append({"updateTextStyle": {"range": {"startIndex": a, "endIndex": a + len(phrase)},
                                                 "textStyle": {"bold": bool(bold)}, "fields": "bold"}})
                pos = text.find(phrase, pos + len(phrase))
    if not reqs:
        return {"ok": True, "count": 0}
    _batch(email, doc, reqs, http)
    _audit(data_dir, {"user": user, "doc": doc_id(doc), "action": "bold" if bold else "unbold", "chars": len(phrase), "count": len(reqs)})
    return {"ok": True, "count": len(reqs)}


def insert_table(email: str, doc: str, section_index: int, rows: list[list[str]], *, user: str, data_dir: Path,
                 scrub=None, http=None) -> dict:
    """절 끝에 표를 넣고 셀을 채운다. 셀은 뒤에서부터 채워 앞 인덱스가 밀리지 않게 한다."""
    http = http or _http()
    rows = [[scrub(str(c)) if scrub else str(c) for c in r] for r in rows if r]
    if not rows:
        raise ValueError("표 내용이 없습니다")
    n_cols = max(len(r) for r in rows)
    info = get(email, doc, http)
    sec = next((s for s in info["sections"] if s["index"] == int(section_index)), None)
    if sec is None:
        raise ValueError("절을 다시 골라 주세요")
    at = max(1, min(int(sec["end"]) - 1, int(info["end"]) - 1))
    _batch(email, doc, [{"insertTable": {"location": {"index": at}, "rows": len(rows), "columns": n_cols}}], http)
    r = http.get(f"{DOCS_API}/{doc_id(doc)}", headers=_headers(email, http), params={"includeTabsContent": "true"})
    _raise(r)
    body = body_content(r.json())
    table = next((el["table"] for el in body if el.get("table") and int(el.get("startIndex", -1)) >= at), None)
    if not table:
        raise RuntimeError("표를 넣었지만 자리를 찾지 못했습니다")
    fills = []
    for ri, row in enumerate(table.get("tableRows", [])):
        for ci, cell in enumerate(row.get("tableCells", [])):
            text = rows[ri][ci] if ri < len(rows) and ci < len(rows[ri]) else ""
            if text:
                idx = int(cell["content"][0]["startIndex"]) if cell.get("content") else None
                if idx is not None:
                    fills.append((idx, text))
    reqs = [{"insertText": {"location": {"index": idx}, "text": text}} for idx, text in sorted(fills, reverse=True)]
    if reqs:
        _batch(email, doc, reqs, http)
    # 표의 꼴 — 머리 행은 옅은 음영과 굵은 글자(완성본의 표처럼 보이게, 2026-09-29). 실패해도 표 자체는 들어갔으니 조용히 넘어간다
    try:
        _style_table_header(email, doc, at, n_cols, http)
    except Exception:
        pass
    _audit(data_dir, {"user": user, "doc": doc_id(doc), "action": "table", "section": sec["heading"],
                      "rows": len(rows), "cols": n_cols})
    return {"ok": True, "rows": len(rows), "cols": n_cols, "section": sec["heading"]}


def _style_table_header(email: str, doc: str, at: int, n_cols: int, http) -> None:
    """방금 넣은 표(위치 at 이후 첫 표)의 머리 행에 음영·굵게."""
    r = http.get(f"{DOCS_API}/{doc_id(doc)}", headers=_headers(email, http), params={"includeTabsContent": "true"})
    _raise(r)
    body = body_content(r.json())
    el = next((e for e in body if e.get("table") and int(e.get("startIndex", -1)) >= at), None)
    if not el:
        return
    reqs: list[dict] = [{"updateTableCellStyle": {
        "tableRange": {"tableCellLocation": {"tableStartLocation": {"index": int(el["startIndex"])}, "rowIndex": 0, "columnIndex": 0},
                       "rowSpan": 1, "columnSpan": n_cols},
        "tableCellStyle": {"backgroundColor": {"color": {"rgbColor": {"red": 0.93, "green": 0.93, "blue": 0.93}}}},
        "fields": "backgroundColor"}}]
    head = (el["table"].get("tableRows") or [{}])[0]
    for cell in head.get("tableCells", []):
        paras = [e for e in cell.get("content", []) if "paragraph" in e and e.get("endIndex")]
        if paras:
            a, b = int(paras[0]["startIndex"]), int(paras[-1]["endIndex"]) - 1
            if b > a:
                reqs.append({"updateTextStyle": {"range": {"startIndex": a, "endIndex": b}, "textStyle": {"bold": True}, "fields": "bold"}})
    _batch(email, doc, reqs, http)


def _section_tables(body: list[dict], info: dict, section_index: int) -> list[dict]:
    """절 안의 표(작성방법 상자 제외) — [{n, el}] (n 은 절 안 차례, 1부터)."""
    secs = sorted(info["sections"], key=lambda s: s.get("start", 0))
    sec = next((s for s in secs if s["index"] == int(section_index)), None)
    if sec is None:
        return []
    i = secs.index(sec)
    end = int(secs[i + 1]["start"]) if i + 1 < len(secs) else int(info["end"])
    out = []
    for el in body:
        st = int(el.get("startIndex", 0))
        if "table" in el and int(sec["start"]) <= st < end and not _is_instruction_box(el["table"]):
            out.append({"n": len(out) + 1, "el": el, "sec": sec})
    return out


def _covered_cells(tbl: dict) -> dict[tuple[int, int], tuple[int, int]]:
    """병합에 덮인 칸 → 병합 원점 칸. 독스 API 는 덮인 칸도 tableCells 에 두므로(실측 2026-09-29: 총괄표 14열 중 절반이 덮인 칸)
    거기에 글을 넣으면 보이지 않는다. 원점 칸의 rowSpan·columnSpan 으로 덮인 좌표를 센다."""
    covered: dict[tuple[int, int], tuple[int, int]] = {}
    for ri, row in enumerate(tbl.get("tableRows", [])):
        for ci, cell in enumerate(row.get("tableCells", [])):
            st = cell.get("tableCellStyle") or {}
            rs, cs = int(st.get("rowSpan") or 1), int(st.get("columnSpan") or 1)
            for dr in range(rs):
                for dc in range(cs):
                    if (dr, dc) != (0, 0):
                        covered[(ri + dr, ci + dc)] = (ri, ci)
    return covered


def table_grids(email: str, doc: str, section_index: int, http=None, info: dict | None = None) -> list[dict]:
    """절의 표 격자 — [{n, rows:[[글…]], covered:{(r,c)}, sec}] 모델이 fill 로 칸을 지목할 수 있게(row·col 은 0부터).
    covered 는 병합에 덮인 칸 — 값을 넣을 수 없어 격자에서 뺀다."""
    http = http or _http()
    info = info or get(email, doc, http)
    r = http.get(f"{DOCS_API}/{doc_id(doc)}", headers=_headers(email, http), params={"includeTabsContent": "true"})
    _raise(r)
    body = body_content(r.json())
    out = []
    for t in _section_tables(body, info, section_index):
        rows = [[" ".join(_para_text(e["paragraph"]).strip() for e in c.get("content", []) if "paragraph" in e).strip()
                 for c in row.get("tableCells", [])] for row in t["el"]["table"].get("tableRows", [])]
        out.append({"n": t["n"], "rows": rows, "covered": set(_covered_cells(t["el"]["table"]).keys()), "section": t["sec"]["heading"]})
    return out


def render_table_grids(grids: list[dict], max_rows: int = 40) -> str:
    """표 격자를 모델이 읽을 글로 — 보이는 칸만 [c열] 번호와 함께, 빈 칸은 '_'. 병합에 덮인 칸은 적지 않는다(넣어도 안 보인다)."""
    if not grids:
        return ""
    lines = ["[이 절에 이미 있는 양식 표 — 새 표를 만들지 말고 fill 로 빈 칸(_)에 값을 넣는다. row 는 r번호, col 은 칸 앞의 [c번호]만 쓴다]"]
    for g in grids:
        rows = g["rows"]
        covered = g.get("covered") or set()
        n_cols = max((len(r) for r in rows), default=0)
        lines.append(f"표 {g['n']} ({len(rows)}행×{n_cols}열)")
        for ri, row in enumerate(rows[:max_rows]):
            cells = [f"[c{ci}] " + (c[:24] if c else "_") for ci, c in enumerate(row) if (ri, ci) not in covered]
            lines.append(f"  r{ri}: " + " | ".join(cells))
        if len(rows) > max_rows:
            lines.append(f"  … ({len(rows) - max_rows}행 더)")
    return "\n".join(lines)


def fill_table(email: str, doc: str, section_index: int, table_n: int, cells: list[dict], *, user: str, data_dir: Path,
               scrub=None, http=None) -> dict:
    """절의 n 번째 양식 표의 칸에 값을 넣는다(cells = [{row, col, text}], 0부터). 칸에 글이 있으면 바꾼다.
    뒤 칸부터 써서 앞 인덱스가 밀리지 않게 한다. 표 구조(병합·테두리)는 그대로다."""
    http = http or _http()
    info = get(email, doc, http)
    r = http.get(f"{DOCS_API}/{doc_id(doc)}", headers=_headers(email, http), params={"includeTabsContent": "true"})
    _raise(r)
    body = body_content(r.json())
    tables = _section_tables(body, info, section_index)
    t = next((x for x in tables if x["n"] == int(table_n)), None)
    if t is None:
        raise ValueError(f"절에 표 {table_n} 이 없습니다(표 {len(tables)}개)")
    rows = t["el"]["table"].get("tableRows", [])
    covered = _covered_cells(t["el"]["table"])
    edits: list[tuple[int, int, str]] = []          # (start, end(지울 끝, 없으면 start), 글)
    skipped = 0
    seen: set[tuple[int, int]] = set()
    for c in cells:
        try:
            ri, ci = int(c.get("row")), int(c.get("col"))
        except (TypeError, ValueError):
            skipped += 1
            continue
        text = str(c.get("text") or "").strip()
        if scrub:
            text = scrub(text)
        if ri < 0 or ri >= len(rows):
            skipped += 1
            continue
        ri, ci = covered.get((ri, ci), (ri, ci))         # 덮인 칸을 지목했으면 병합 원점 칸에 넣는다
        if (ri, ci) in seen:
            continue
        seen.add((ri, ci))
        tcs = rows[ri].get("tableCells", [])
        if ci < 0 or ci >= len(tcs):
            skipped += 1
            continue
        content = [e for e in tcs[ci].get("content", []) if "paragraph" in e]
        if not content:
            skipped += 1
            continue
        start = int(content[0]["startIndex"])
        existing = " ".join(_para_text(e["paragraph"]) for e in content).strip()
        end = int(content[-1]["endIndex"]) - 1 if existing else start        # 마지막 줄바꿈은 칸의 것 — 지우지 않는다
        if not text and not existing:
            skipped += 1                                      # 빈 칸을 비우라는 것 — 할 일이 없다
            continue
        edits.append((start, end, text))                     # text 가 비면 칸을 비운다(모델이 잘못 든 값을 지울 때)
        # 원점 칸이 덮은 칸에 남은(보이지 않는) 글은 지운다 — 예전 채우기가 덮인 칸에 넣은 값이 완성본으로 새지 않게
        for (cr, cc), origin in covered.items():
            if origin == (ri, ci) and cr < len(rows) and cc < len(rows[cr].get("tableCells", [])):
                hid = [e for e in rows[cr]["tableCells"][cc].get("content", []) if "paragraph" in e]
                if hid and " ".join(_para_text(e["paragraph"]) for e in hid).strip():
                    edits.append((int(hid[0]["startIndex"]), int(hid[-1]["endIndex"]) - 1, ""))
    reqs: list[dict] = []
    for start, end, text in sorted(edits, key=lambda x: -x[0]):
        if end > start:
            reqs.append({"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}})
        if text:
            reqs.append({"insertText": {"location": {"index": start}, "text": text}})
    if reqs:
        _batch(email, doc, reqs, http)
    n_written = sum(1 for _s, _e, tx in edits if tx)
    n_cleared = sum(1 for _s, e_, tx in edits if not tx and e_ > _s)
    _audit(data_dir, {"user": user, "doc": doc_id(doc), "action": "fill", "section": t["sec"]["heading"], "table": int(table_n),
                      "cells": n_written, "cleared": n_cleared, "skipped": skipped})
    return {"ok": True, "section": t["sec"]["heading"], "table": int(table_n), "cells": n_written, "cleared": n_cleared, "skipped": skipped}


def insert_image(email: str, doc: str, section_index: int, uri: str, *, user: str, data_dir: Path, width_pt: float = 450.0,
                 http=None, caption: str = "") -> dict:
    """절 끝에 그림(공개 URL)을 넣는다 — 도식(인포그래픽). 폭만 주면 독스가 비율을 지킨다."""
    http = http or _http()
    info = get(email, doc, http)
    sec = next((s for s in info["sections"] if s["index"] == int(section_index)), None)
    if sec is None:
        raise ValueError("절을 다시 골라 주세요")
    at = max(1, min(int(sec["end"]) - 1, int(info["end"]) - 1))
    reqs = [{"insertInlineImage": {"location": {"index": at}, "uri": uri, "objectSize": {"width": {"magnitude": float(width_pt), "unit": "PT"}}}}]
    if caption:
        reqs.insert(0, {"insertText": {"location": {"index": at}, "text": "\n" + caption.strip()}})
    _batch(email, doc, reqs, http)
    _audit(data_dir, {"user": user, "doc": doc_id(doc), "action": "figure", "section": sec["heading"], "caption": caption[:60]})
    return {"ok": True, "section": sec["heading"]}


def data_dir_default() -> Path:
    return Path(os.environ.get("ZZAIMY_DATA_DIR", "data/platform"))


def _is_instruction_box(tbl: dict) -> bool:
    return "작성방법" in _table_text(tbl)


def clear_section_body(email: str, doc: str, section_index: int, *, user: str, data_dir: Path, http=None,
                       end_index: int | None = None, keep_headings: set[str] | None = None) -> dict:
    """절의 본문을 지운다 — 제목·작성방법 상자(【작성방법】이 든 표)·소제목 문단(keep_headings)은 남기고, 상자 뒤(상자가 없으면
    제목 뒤)의 문단·표를 지운다. end_index 를 주면 그 앞까지(소제목 절들까지 한 절로 볼 때). '다시 써 줘' 가 덧붙이지 않게.
    독스는 표 바로 앞의 빈 문단을 지우지 못하므로 상자 앞은 건드리지 않는다(실측 2026-09-27 400)."""
    http = http or _http()
    info = get(email, doc, http)
    sec = next((s for s in info["sections"] if s["index"] == int(section_index)), None)
    if sec is None:
        raise ValueError("절을 다시 골라 주세요 — 문서 구조가 바뀌었습니다")
    r = http.get(f"{DOCS_API}/{doc_id(doc)}", headers=_headers(email, http), params={"includeTabsContent": "true"})
    _raise(r)
    body = body_content(r.json())
    nxt = next((s for s in info["sections"] if s.get("start", 0) > sec["start"]), None)
    end_limit = int(end_index) if end_index else (int(nxt["start"]) if nxt else int(info["end"]) - 1)
    end_limit = min(end_limit, int(info["end"]) - 1)
    els = [el for el in body if int(sec["start"]) <= int(el.get("startIndex", 0)) < end_limit]
    if not els:
        return {"ok": True, "chars": 0}
    box = next((el for el in els[1:] if "table" in el and _is_instruction_box(el["table"])), None)
    cursor = int(box["endIndex"]) if box is not None else int(els[0].get("endIndex", 0))
    keep = {h.strip() for h in (keep_headings or set())}
    ranges: list[tuple[int, int]] = []
    for el in els:
        st, en = int(el.get("startIndex", 0)), int(el.get("endIndex", 0))
        if st < cursor:
            continue
        if "paragraph" in el and _para_text(el["paragraph"]).strip() in keep:
            if st > cursor:
                ranges.append((cursor, st))
            cursor = en                                     # 소제목은 남긴다
    if end_limit - 1 > cursor:
        ranges.append((cursor, end_limit - 1))
    ranges = [(a, b) for a, b in ranges if b - a >= 2]
    if not ranges:
        return {"ok": True, "chars": 0}
    reqs = [{"deleteContentRange": {"range": {"startIndex": a, "endIndex": b}}} for a, b in sorted(ranges, reverse=True)]
    _batch(email, doc, reqs, http)
    n = sum(b - a for a, b in ranges)
    _audit(data_dir, {"user": user, "doc": doc_id(doc), "action": "clear", "section": sec["heading"], "chars": n})
    return {"ok": True, "chars": n}


_GUIDE_BOX = re.compile(r"【\s*(작성방법|증빙자료|작성\s*가이드|작성\s*요령|유의사항)\s*】")


def remove_instruction_boxes(email: str, doc: str, *, user: str, data_dir: Path, http=None, dry_run: bool = False) -> dict:
    """양식의 안내 상자를 지운다 — 【작성방법】·【증빙자료】 같은 표와 그 표시만 있는 문단. 제출 전 마무리 단계
    (양식 지침: '본문에 제시된 【작성방법】,【증빙자료】, 작성 가이드 박스 등은 삭제한 후 작성'). dry_run 이면 세기만."""
    http = http or _http()
    r = http.get(f"{DOCS_API}/{doc_id(doc)}", headers=_headers(email, http), params={"includeTabsContent": "true"})
    _raise(r)
    body = body_content(r.json())
    ranges: list[tuple[int, int]] = []
    for el in body:
        st, en = int(el.get("startIndex", 0)), int(el.get("endIndex", 0))
        if "table" in el and _GUIDE_BOX.search(_table_text(el["table"])):
            ranges.append((st, en))
        elif "paragraph" in el:
            t = _para_text(el["paragraph"]).strip()
            if t and _GUIDE_BOX.fullmatch(t):
                ranges.append((st, en))
    n = len(ranges)
    if n and not dry_run:
        # 표는 그 범위 전체를, 문단은 줄 끝까지 — 뒤에서부터 지워 앞 인덱스가 밀리지 않게
        reqs = [{"deleteContentRange": {"range": {"startIndex": a, "endIndex": b}}} for a, b in sorted(ranges, reverse=True)]
        _batch(email, doc, reqs, http)
        _audit(data_dir, {"user": user, "doc": doc_id(doc), "action": "remove_boxes", "count": n})
    return {"ok": True, "count": n}


def _norm_heading(h: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (h or "").lower().replace("\ue907", "").replace("ž", "").replace("･", "").replace("·", ""))


def _inline_objects(document: dict) -> dict:
    """인라인 개체(그림) 표 — body_content 와 같은 자리(탭 문서면 첫 탭)에서."""
    if document.get("body", {}).get("content"):
        return document.get("inlineObjects") or {}
    for tab in document.get("tabs", []) or []:
        dt = tab.get("documentTab", {})
        if dt.get("body", {}).get("content"):
            return dt.get("inlineObjects") or {}
    return {}


def _inline_image(email: str, http, objects: dict, oid: str) -> dict | None:
    """인라인 그림 하나를 내려받는다 — {"data", "width_pt", "height_pt"}. contentUri 는 짧게 사는 인증 주소라 바로 받는다."""
    emb = ((objects.get(oid) or {}).get("inlineObjectProperties") or {}).get("embeddedObject") or {}
    uri = (emb.get("imageProperties") or {}).get("contentUri")
    if not uri:
        return None
    try:
        r = http.get(uri, headers=_headers(email, http))
        _raise(r)
    except Exception:
        log.warning("작업본 그림 %s 를 받지 못했다", oid, exc_info=True)
        return None
    size = emb.get("size") or {}

    def pt(d: dict) -> float:
        v = float((d or {}).get("magnitude") or 0)
        return v if (d or {}).get("unit", "PT") == "PT" else v * 72 / 914400      # EMU → pt
    return {"data": r.content, "width_pt": pt(size.get("width")), "height_pt": pt(size.get("height"))}


def section_bodies(email: str, doc: str, http=None, images: bool = True) -> list[dict]:
    """절마다 담당자·에이전트가 쓴 본문 — [{heading, items:[("text", 글)|("table", 행렬)|("image", {data,width_pt,height_pt})]}].
    제목과 【작성방법】 상자는 뺀다. images 면 본문 그림(도식)도 내려받아 제자리에 낸다(한글 완성본용 — 옮기기는 images=False)."""
    http = http or _http()
    info = get(email, doc, http)
    r = http.get(f"{DOCS_API}/{doc_id(doc)}", headers=_headers(email, http), params={"includeTabsContent": "true"})
    _raise(r)
    document = r.json()
    body = body_content(document)
    objects = _inline_objects(document) if images else {}
    secs = sorted(info["sections"], key=lambda s: s.get("start", 0))
    out: list[dict] = []
    for i, sec in enumerate(secs):
        if sec.get("index", 0) == 0:
            continue
        end_limit = int(secs[i + 1]["start"]) if i + 1 < len(secs) else int(info["end"])
        items: list[tuple[str, object]] = []
        buf: list[str] = []
        for el in body:
            st = int(el.get("startIndex", 0))
            if st < int(sec["start"]) or st >= end_limit:
                continue
            if "paragraph" in el:
                t = _para_text(el["paragraph"]).strip()
                oids = [e["inlineObjectElement"].get("inlineObjectId") for e in el["paragraph"].get("elements", []) if "inlineObjectElement" in e]
                if objects and oids and st != int(sec["start"]):
                    if t:
                        buf.append(t)
                    if buf:
                        items.append(("text", "\n".join(buf))); buf = []
                    for oid in oids:
                        img = _inline_image(email, http, objects, oid)
                        if img:
                            items.append(("image", img))
                    continue
                if st == int(sec["start"]) or not t:
                    continue
                buf.append(t)
            elif "table" in el:
                if _is_instruction_box(el["table"]):
                    continue
                if buf:
                    items.append(("text", "\n".join(buf))); buf = []
                rows = []
                for row in el["table"].get("tableRows", []):
                    rows.append([" ".join(_para_text(e["paragraph"]).strip() for e in cell.get("content", []) if "paragraph" in e).strip()
                                 for cell in row.get("tableCells", [])])
                if any(any(c for c in rw) for rw in rows):
                    # 열 너비(pt) — 독스가 고정 폭을 주면 함께 낸다. 서식 채우기(hwpx_fill)가 새 표의 열 비율로 쓴다; 옮기기는 무시
                    cols = (el["table"].get("tableStyle") or {}).get("tableColumnProperties") or []
                    widths = [float((c.get("width") or {}).get("magnitude") or 0) for c in cols]
                    if widths and all(w > 0 for w in widths) and len(widths) == max(len(r) for r in rows):
                        items.append(("widths", widths))
                    items.append(("table", rows))
        if buf:
            items.append(("text", "\n".join(buf)))
        # 본문이 빈 절도 차례에 넣는다 — 서식 채우기(hwpx_fill)가 소제목에 나눠 쓴 절의 부모를 알 수 있게(2026-09-29: 1.1 이 빠지자
        # 그 소제목들이 앞 절로 들어갔다). 옮기기(migrate_bodies)는 빈 절을 건너뛴다.
        out.append({"heading": sec["heading"], "items": items})
    return out


def migrate_bodies(email: str, src: str, dst: str, *, user: str, data_dir: Path, scrub=None, http=None,
                   only_headings: set[str] | None = None) -> list[dict]:
    """옛 작업본의 절 본문을 새 작업본의 같은 제목 절로 옮긴다(글은 insert, 표는 insert_table, 순서대로). 절마다 결과를 돌려준다.
    only_headings 를 주면 그 제목의 절만 옮긴다(앞선 이관에서 빠진 절을 다시 옮길 때) — 직전 절 추적은 전체를 본다."""
    http = http or _http()
    bodies = section_bodies(email, src, http, images=False)
    src_order = [s["heading"] for s in sorted(get(email, src, http)["sections"], key=lambda s: s.get("start", 0)) if s.get("index", 0) > 0]
    results: list[dict] = []

    def parent_of(heading: str, dst_secs: list[dict]) -> dict | None:
        """새 작업본에 없는 소제목의 부모 — 옛 작업본 차례에서 바로 앞쪽으로 올라가며 새 작업본에 있는 첫 제목
        (실측 2026-09-28: '직전에 옮긴 절'로 잡자 1.1 의 SWOT 소제목이 1.2 아래로 들어갔다)."""
        try:
            i = src_order.index(heading)
        except ValueError:
            return None
        keys = {_norm_heading(s["heading"]): s for s in dst_secs}
        for h in reversed(src_order[:i]):
            hit = keys.get(_norm_heading(h))
            if hit is not None:
                return hit
        return None

    for b in bodies:
        if only_headings is not None and b["heading"] not in only_headings:
            continue
        if not b["items"]:
            continue                                                   # 본문 없는 절은 옮길 것이 없다
        info = get(email, dst, http)
        key = _norm_heading(b["heading"])
        sec = next((s for s in info["sections"] if _norm_heading(s["heading"]) == key), None)
        items = list(b["items"])
        fallback = False
        if sec is None:
            sec = parent_of(b["heading"], info["sections"])
            if sec is not None:
                # 소제목 글줄과 첫 본문을 한 번에 넣는다 — 따로 넣으면 소제목 줄이 새 절 경계가 돼 본문이 그 위에 들어간다(실측 2026-09-28)
                if items and items[0][0] == "text":
                    items = [("text", b["heading"] + "\n" + str(items[0][1]))] + items[1:]
                else:
                    items = [("text", b["heading"])] + items
                fallback = True
        if sec is None:
            results.append({"heading": b["heading"], "done": "skip", "why": "새 작업본에 같은 절이 없음"}); continue
        chars = tables = 0
        # 새 작업본(서식 변환본)에 이미 있는 글·표는 옮기지 않는다 — 옛 작업본도 서식 변환본이라 서식 자체의 표·문단이 들어 있고, 그대로 옮기면
        # 같은 표가 두 벌이 된다(실측 2026-09-29: 재생성 뒤 표 29개 이동, 대부분 서식 표). 문단은 글로, 표는 칸 글자 집합으로 견준다
        dst_norm = _norm_heading(info.get("text") or "")
        dst_cells = {_norm_heading(c) for ln in (info.get("text") or "").split("\n") if " | " in ln for c in ln.split(" | ") if c.strip()}
        for kind, payload in items:
            if kind == "widths":
                continue                                               # 열 너비는 서식 채우기용 — 옮기기에는 안 쓴다
            if kind == "text":
                keep = [ln for ln in str(payload).split("\n") if ln.strip() and not (len(_norm_heading(ln)) > 8 and _norm_heading(ln) in dst_norm)]
                if not keep:
                    continue
                r = insert_into_section(email, dst, sec["index"], "\n".join(keep), user=user, data_dir=data_dir, scrub=scrub, http=http)
                chars += int(r.get("chars") or 0)
            elif kind == "table":
                cells = {_norm_heading(str(c)) for r in payload for c in r if str(c).strip()}
                if cells and cells <= dst_cells:
                    continue                                           # 서식에 있는 표 그대로 — 새 작업본에도 이미 있다
                insert_table(email, dst, sec["index"], payload, user=user, data_dir=data_dir, scrub=scrub, http=http)
                tables += 1
        results.append({"heading": b["heading"], "done": "ok", "chars": chars, "tables": tables, "under": sec["heading"] if fallback else ""})
    return results
