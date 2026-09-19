"""반입 결과 자가 점검 — 문서를 하나하나 열어 보지 않고도 상태를 판정한다.

문서가 수백·수천 건이면 사람이 전수 검수할 수 없다. 그래서 기계가 스스로
반입 결과를 채점하고, 손볼 곳만 짧게 짚는다. 모든 수치는 적재된 자료에서
직접 세며, 어느 것도 지어내지 않는다.

판정하는 신호
  조각 없음        본문을 하나도 못 뽑았다. 파서가 실패했을 가능성이 크다.
  본문이 잘다      실질 글자 중앙값이 낮다. 조각이 파편으로 쪼개졌다는 뜻이다.
  표제 없음        구조 표제를 하나도 못 붙였다. 조문 구조를 못 읽었다는 뜻이다.
  글자 손상 의심   OCR 손상 신호가 걸린 조각 비율이 높다.
  잡음 조각 많음   검색에 쓸 수 없는 조각 비율이 높다.
  정체 미상        무엇에 관한 문서인지 정하지 못했다.
"""
from __future__ import annotations

from statistics import median

from zzaimy.app import chunk_quality as cq

# 경계값 — 낮출수록 더 많이 짚는다. 근거는 아래 주석에 남긴다.
MIN_CHUNKS = 1                 # 조각이 없으면 검색·인용 자체가 불가능하다
LOW_MEDIAN_SUBSTANTIVE = 40    # chunk_quality 가 "본문이 짧음"을 가르는 값과 같다
HIGH_DAMAGE_RATIO = 0.20       # 다섯에 하나가 손상 의심이면 원문을 다시 봐야 한다
HIGH_NOISE_RATIO = 0.50        # 절반 넘게 못 쓰면 그 문서는 검색에 기여하지 못한다


def _doc_blocks(db, doc_id: int) -> tuple[list[dict], bool]:
    """(조각 목록, 표제 개념이 있는 자료인지). 파서 산출 조각에는 표제 칸이 없다."""
    rows = [{"doc_id": doc_id, "content": r.get("content") or "",
             "heading": (r.get("heading") or ""), "kind": "text"}
            for r in db.list_regulation_chunks() if r["doc_id"] == doc_id]
    if rows:
        return rows, True
    return [{"doc_id": doc_id, "content": c.get("content") or "", "heading": "",
             "kind": c.get("kind") or "text"}
            for c in db.list_doc_chunks(doc_id)], False


def _shape(db, doc: dict, blocks: list[dict]) -> dict:
    """문서의 겉모습 — 원본이 남아 있는지, 어떤 형식인지, 무엇으로 이루어졌는지."""
    from pathlib import Path

    stored = Path(doc.get("stored_path") or "")
    kinds: dict[str, int] = {}
    for b in blocks:
        k = b.get("kind", "text")
        kinds[k] = kinds.get(k, 0) + 1
    return {
        "suffix": stored.suffix.lower().lstrip(".") or "없음",
        "has_original": bool(stored.name) and stored.exists(),
        "kinds": kinds,
        "text_only": set(kinds) <= {"text"},
        "doc_type": doc.get("doc_type", ""),
        "status": doc.get("status", ""),
    }


def audit_document(db, doc: dict) -> dict:
    """문서 한 건의 반입 상태. 손볼 일이 없으면 issues 가 빈 목록이다."""
    doc_id = doc["id"]
    blocks, has_headings = _doc_blocks(db, doc_id)
    texts = [b["content"] for b in blocks if (b["content"] or "").strip()]
    issues: list[str] = []

    shape = _shape(db, doc, blocks)
    if len(texts) < MIN_CHUNKS:
        return {"id": doc_id, "filename": doc.get("filename", ""), "n_chunks": 0,
                **shape,
                "median_substantive": 0, "heading_ratio": 0.0, "damage_ratio": 0.0,
                "noise_ratio": 1.0, "has_identity": False,
                "issues": ["본문 조각이 없습니다"], "severity": 3}

    # 표는 칸이 짧은 것이 정상이다 — 줄글 조각만으로 길이를 잰다
    prose = [b["content"] for b in blocks
             if b.get("kind", "text") == "text" and (b["content"] or "").strip()]
    lengths = [cq.substantive_len(t) for t in (prose or texts)]
    mid = int(median(lengths))
    headed = sum(1 for b in blocks if (b["heading"] or "").strip())
    damaged = sum(1 for t in texts if cq.ocr_damage_signals(t))
    kept, removed = cq.filter_chunks(blocks, cq.Strictness.SEARCH)
    noise = len(removed) / len(blocks) if blocks else 0.0
    identity = db.get_doc_identity(doc_id) if hasattr(db, "get_doc_identity") else {}

    if prose and mid < LOW_MEDIAN_SUBSTANTIVE:
        issues.append(f"본문이 잘게 쪼개졌습니다 (실질 중앙값 {mid}자)")
    if has_headings and headed == 0:
        issues.append("구조 표제를 붙이지 못했습니다")
    if damaged / len(texts) >= HIGH_DAMAGE_RATIO:
        issues.append(f"글자 손상이 의심됩니다 ({damaged}/{len(texts)} 조각)")
    if noise >= HIGH_NOISE_RATIO:
        issues.append(f"검색에 쓸 수 없는 조각이 많습니다 ({len(removed)}/{len(blocks)})")
    # 심각도 — 본문을 못 읽은 쪽이 정체를 못 정한 것보다 무겁다
    severity = 0
    if any("손상" in i or "쪼개" in i for i in issues):
        severity = 2
    elif issues:
        severity = 1
    if not shape["has_original"]:
        issues.append("원본 파일이 없습니다")
    return {
        "id": doc_id, "filename": doc.get("filename", ""), "n_chunks": len(blocks),
        **shape,
        "median_substantive": mid,
        "heading_ratio": round(headed / len(blocks), 3),
        "damage_ratio": round(damaged / len(texts), 3),
        "noise_ratio": round(noise, 3),
        "has_identity": bool(identity),
        "issues": issues, "severity": severity,
    }


def audit(db, limit: int = 0) -> dict:
    """반입 전체 상태. rows 는 손볼 것이 있는 문서만 심각한 순으로 담는다."""
    docs = [d for d in db.list_documents() if d.get("doc_type") != "ocr"]
    rows = [audit_document(db, d) for d in docs]
    flagged = sorted((r for r in rows if r["issues"]),
                     key=lambda r: (-r["severity"], -r["noise_ratio"]))
    with_identity = sum(1 for r in rows if r["has_identity"])
    by_suffix: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for r in rows:
        by_suffix[r.get("suffix", "?")] = by_suffix.get(r.get("suffix", "?"), 0) + 1
        by_type[r.get("doc_type", "?")] = by_type.get(r.get("doc_type", "?"), 0) + 1
    missing_original = sum(1 for r in rows if not r.get("has_original"))
    text_only = sum(1 for r in rows if r.get("text_only"))
    counts: dict[str, int] = {}
    for r in rows:
        for issue in r["issues"]:
            key = issue.split("(")[0].strip()
            counts[key] = counts.get(key, 0) + 1
    healthy = len(rows) - len(flagged)
    return {
        "total": len(rows),
        "healthy": healthy,
        "flagged": len(flagged),
        "rate": round(healthy / len(rows), 3) if rows else 0.0,
        "with_identity": with_identity,
        "by_suffix": sorted(by_suffix.items(), key=lambda kv: -kv[1]),
        "by_type": sorted(by_type.items(), key=lambda kv: -kv[1]),
        "missing_original": missing_original,
        "text_only": text_only,
        "identity_rate": round(with_identity / len(rows), 3) if rows else 0.0,
        "counts": sorted(counts.items(), key=lambda kv: -kv[1]),
        "rows": flagged[:limit] if limit else flagged,
    }
