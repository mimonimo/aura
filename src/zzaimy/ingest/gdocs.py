"""구글 독스 API — 문서 작업 화면에서 에이전트가 같은 문서를 읽고 고친다(2단계, ADR-0029).

읽기는 `documents.get`, 쓰기는 `documents.batchUpdate` 다. 위치는 문자 인덱스이므로 먼저 구조(제목 목록과 각
절의 범위)를 읽고 계산해 넣는다. 제안 모드(추적 변경)는 API 가 지원하지 않아 직접 편집만 된다.

쓰기 규칙(ADR-0029): 담당자가 화면에서 누른 삽입·치환만 보낸다(에이전트가 스스로 쓰지 않는다). 보내는 글은 개인정보
검사기를 한 번 더 거치고, 검색 근거 조각의 원문은 넣지 않는다(초안 글만). 모든 쓰기는 `gdocs_audit.jsonl` 에 남는다.
인증·토큰은 gdrive 와 같은 파일을 쓰고 허용 범위에 documents 가 있어야 한다(없으면 다시 허용을 안내).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from zzaimy.ingest import gdrive

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
    return "".join(e.get("textRun", {}).get("content", "") for e in p.get("elements", []))


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
    sections.append(cur)
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
                {"updateParagraphStyle": {"range": {"startIndex": at, "endIndex": at + len(payload)},
                                          "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}, "fields": "namedStyleType"}}]
    else:
        # 절의 마지막 문단 끝(줄바꿈 앞)에 새 문단으로 넣는다. 문서 끝이면 끝 인덱스 - 1.
        at = max(1, min(int(sec["end"]) - 1, int(info["end"]) - 1))
        payload = "\n" + text
        reqs = [{"insertText": {"location": {"index": at}, "text": payload}}]
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
    _audit(data_dir, {"user": user, "doc": doc_id(doc), "action": "table", "section": sec["heading"],
                      "rows": len(rows), "cols": n_cols})
    return {"ok": True, "rows": len(rows), "cols": n_cols, "section": sec["heading"]}


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


def section_bodies(email: str, doc: str, http=None) -> list[dict]:
    """절마다 담당자·에이전트가 쓴 본문 — [{heading, items:[("text", 글)|("table", 행렬)]}]. 제목과 【작성방법】 상자는 뺀다.
    작업본을 새 서식 변환본으로 옮길 때(migrate_bodies) 쓴다."""
    http = http or _http()
    info = get(email, doc, http)
    r = http.get(f"{DOCS_API}/{doc_id(doc)}", headers=_headers(email, http), params={"includeTabsContent": "true"})
    _raise(r)
    body = body_content(r.json())
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
                    items.append(("table", rows))
        if buf:
            items.append(("text", "\n".join(buf)))
        if items:
            out.append({"heading": sec["heading"], "items": items})
    return out


def migrate_bodies(email: str, src: str, dst: str, *, user: str, data_dir: Path, scrub=None, http=None) -> list[dict]:
    """옛 작업본의 절 본문을 새 작업본의 같은 제목 절로 옮긴다(글은 insert, 표는 insert_table, 순서대로). 절마다 결과를 돌려준다."""
    http = http or _http()
    bodies = section_bodies(email, src, http)
    results: list[dict] = []
    for b in bodies:
        info = get(email, dst, http)
        key = _norm_heading(b["heading"])
        sec = next((s for s in info["sections"] if _norm_heading(s["heading"]) == key), None)
        if sec is None:
            results.append({"heading": b["heading"], "done": "skip", "why": "새 작업본에 같은 절이 없음"}); continue
        chars = tables = 0
        for kind, payload in b["items"]:
            if kind == "text":
                r = insert_into_section(email, dst, sec["index"], str(payload), user=user, data_dir=data_dir, scrub=scrub, http=http)
                chars += int(r.get("chars") or 0)
            else:
                insert_table(email, dst, sec["index"], payload, user=user, data_dir=data_dir, scrub=scrub, http=http)
                tables += 1
        results.append({"heading": b["heading"], "done": "ok", "chars": chars, "tables": tables})
    return results
