"""반입한 규정을 기준 문서로 — 후보 찾기·등록·해제(RAG 공간 「규정·기준 문서」).

반입 자료(DGX 원본)의 교내 규정·지침은 사업 문서로 들어와 사업 문서 검색에만 쓰였다. 규정 검색(조문 단위 조각·재순위)과
학생 공개는 기준 문서(doc_type regulation)만 된다. 그래서 담당자가 후보 가운데 골라 기준 문서로 올린다 — 다시 판독하지 않고
저장된 본문(masked_text)을 반입과 같은 조문 청킹·잡음 관문(regulations.chunk_document·index_ready)으로 조각낸다(scripts/123 과 같은 길).

후보: 처리 완료 사업 문서 가운데 서류 갈래가 규정·지침이거나 제목이 규정 갈래 낱말(규정·규칙·내규·세칙·학칙·정관·지침)로
끝나는 것. 협약서·공문·개정안 대비표처럼 규정을 다루는 다른 서류는 뺀다. 같은 판본 묶음(family)은 최신 판 하나만 후보로
보이고 판 수를 붙인다(옛 판은 사업 문서로 남아 연도를 묻는 질문에 쓰인다).
등록·해제는 기록(regulation_register.jsonl)에 남고, 등록 뒤에는 규정 임베딩을 다시 만들어야 뜻 검색에 든다(.reindex-needed).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

GENRE = re.compile(r"(규정|규칙|내규|세칙|학칙|정관|지침)(?:\s*\(.*\))?(?:\s*·.*)?$")
NOT_RULE = re.compile(r"협약|계약서|공문|요청|안내|개정\s*\(?안\)?|신구\s*조문|대비표|검토|회의|결과|신청서")
KINDS = ("regulation", "guideline")
MIN_CHARS = 80
_DATE = re.compile(r"((?:19|20)\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]?\s*(\d{1,2})?")


def _when(title: str, created: str) -> tuple:
    m = None
    for m in _DATE.finditer(title or ""):
        pass
    if m:
        return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0), created or "")
    return (0, 0, 0, created or "")


def candidates(db, q: str = "", limit: int = 400) -> dict:
    """{items:[{id,title,kind,versions,dept}], total, registered:[...]} — 후보는 판본 묶음마다 최신 판 하나."""
    words = [w for w in re.split(r"\s+", (q or "").strip()) if w]
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, filename, family, kind, dept, created_at, LENGTH(COALESCE(masked_text, '')) AS n FROM documents"
            " WHERE doc_type = 'grant' AND status = 'reviewed'").fetchall()]
        reg = [dict(r) for r in conn.execute(
            "SELECT d.id, d.filename, d.kind, d.audience, d.dept, COUNT(c.id) AS chunks FROM documents d"
            " LEFT JOIN regulation_chunks c ON c.doc_id = d.id WHERE d.doc_type = 'regulation' AND d.status = 'reviewed'"
            " GROUP BY d.id, d.filename, d.kind, d.audience, d.dept ORDER BY d.filename").fetchall()]
    pool = [r for r in rows if (r["n"] or 0) >= MIN_CHARS and not NOT_RULE.search(r["filename"] or "")
            and (r["kind"] in KINDS or GENRE.search(r["filename"] or ""))]
    fam: dict[str, list[dict]] = {}
    for r in pool:
        fam.setdefault(r["family"] or f"#{r['id']}", []).append(r)
    items = []
    for group in fam.values():
        group.sort(key=lambda r: _when(r["filename"], r["created_at"]), reverse=True)
        top = group[0]
        title = top["filename"] or ""
        if words and not all(w.replace(" ", "") in title.replace(" ", "") for w in words):
            continue
        items.append({"id": top["id"], "title": title, "kind": top["kind"] or "", "versions": len(group), "dept": top["dept"] or ""})
    items.sort(key=lambda x: x["title"])
    return {"items": items[:limit], "total": len(items), "registered": reg}


def _log(data_dir: Path, rec: dict) -> None:
    with (data_dir / "regulation_register.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **rec}, ensure_ascii=False) + "\n")


def register(db, doc_ids: list[int], data_dir: Path, by: str = "") -> dict:
    """고른 사업 문서를 기준 문서로 — 조각을 만들고 doc_type 을 regulation 으로. 본문이 없거나 조각이 안 나오면 건너뛴다."""
    from zzaimy.app.regulations import chunk_document, index_ready
    done, skipped, made = [], [], 0
    for did in dict.fromkeys(int(x) for x in doc_ids):
        d = db.get_document(did)
        if not d or d.get("doc_type") != "grant" or d.get("status") != "reviewed":
            skipped.append(did)
            continue
        chunks = chunk_document(d.get("masked_text") or "")
        if not chunks:
            skipped.append(did)
            continue
        use, _ = index_ready(did, chunks)
        db.add_regulation_chunks(did, d.get("filename") or "", use, sector=d.get("sector") or "common",
                                 dept=d.get("dept"), access_level=d.get("access_level") or "public")
        db.set_document_type(did, "regulation")
        done.append(did)
        made += len(use)
    if done:
        (data_dir / ".reindex-needed").touch()
        _log(data_dir, {"action": "register", "by": by, "docs": done, "chunks": made})
    return {"registered": len(done), "skipped": len(skipped), "chunks": made}


def unregister(db, doc_ids: list[int], data_dir: Path, by: str = "") -> dict:
    """등록 해제 — 규정 조각을 지우고 사업 문서로 되돌린다. 처음부터 기준 문서로 올린 문서(기록에 없는 것)는 건드리지 않는다."""
    mine: set[int] = set()
    log = data_dir / "regulation_register.jsonl"
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ids = set(rec.get("docs") or [])
            mine = (mine | ids) if rec.get("action") == "register" else (mine - ids)
    done = []
    for did in dict.fromkeys(int(x) for x in doc_ids):
        if did not in mine:
            continue
        with db._conn() as conn:
            conn.execute("DELETE FROM regulation_chunks WHERE doc_id = ?", (did,))
        db.set_document_type(did, "grant")
        done.append(did)
    if done:
        (data_dir / ".reindex-needed").touch()
        _log(data_dir, {"action": "unregister", "by": by, "docs": done})
    return {"unregistered": len(done), "kept": len(doc_ids) - len(done)}
