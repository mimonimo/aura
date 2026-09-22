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


def outline(document: dict) -> dict:
    """documents.get 결과 → {title, end, sections:[{index, level, heading, start, end, chars}], text}.

    절은 제목(TITLE·HEADING_n)에서 다음 같은 급 이상의 제목 전까지다. 제목 없는 앞머리는 index 0 '(앞머리)'.
    end 는 그 절의 마지막 문단 끝 인덱스(다음 절 시작 직전).
    """
    body = document.get("body", {}).get("content", [])
    paras: list[tuple[int, int, str, str]] = []       # (start, end, style, text)
    for el in body:
        p = el.get("paragraph")
        if not p:
            continue
        paras.append((int(el.get("startIndex", 0)), int(el.get("endIndex", 0)),
                      p.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT"), _para_text(p)))
    doc_end = int(body[-1].get("endIndex", 1)) if body else 1
    sections: list[dict] = []
    cur = {"index": 0, "level": 0, "heading": "(앞머리)", "start": 1, "end": 1, "chars": 0}
    for start, end, style, text in paras:
        lvl = HEADING_LEVELS.get(style)
        if lvl is not None and text.strip():
            if cur["chars"] or cur["index"] > 0:        # 글 없는 앞머리는 절로 세지 않는다
                sections.append(cur)
            cur = {"index": len(sections) + 1, "level": lvl, "heading": text.strip()[:80],
                   "start": start, "end": end, "chars": 0}
            continue
        cur["end"] = end
        cur["chars"] += len(text.strip())
    sections.append(cur)
    text = "\n".join(t.rstrip("\n") for _s, _e, _st, t in paras)
    return {"title": document.get("title", ""), "end": doc_end, "sections": sections, "text": text}


def get(email: str, doc: str, http=None) -> dict:
    """문서를 읽어 구조와 평문을 돌려준다."""
    http = http or _http()
    r = http.get(f"{DOCS_API}/{doc_id(doc)}", headers=_headers(email, http))
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
    # 절의 마지막 문단 끝(줄바꿈 앞)에 새 문단으로 넣는다. 문서 끝이면 끝 인덱스 - 1.
    at = max(1, min(int(sec["end"]) - 1, int(info["end"]) - 1))
    payload = "\n" + text
    res = _batch(email, doc, [{"insertText": {"location": {"index": at}, "text": payload}}], http)
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


def embed_url(doc: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id(doc)}/edit?rm=minimal"


def data_dir_default() -> Path:
    return Path(os.environ.get("ZZAIMY_DATA_DIR", "data/platform"))
