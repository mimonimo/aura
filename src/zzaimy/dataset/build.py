"""데이터 공방 — 플랫폼에 쌓인 기록을 학습 데이터(JSONL)로 변환한다.

원칙(사용자 확정, 2026-09-08): 학습 쌍은 "질문→외운 답"이 아니라
"근거+지시→산출" 형태다. 능력만 학습시키고 지식은 검색으로 공급한다.
그래서 모든 쌍은 내보내기 전에 수치 검증(verify_numbers)을 통과해야 하며,
출력의 수치가 입력 근거에 없으면 그 쌍은 폐기한다(정제 규칙 — 건너뛰면
모델이 수치를 외워서 지어낸다). 판정은 서빙 검증기와 같은 코드를 쓴다.

출력 형식은 sharegpt 대화형 JSONL — LLaMA-Factory가 바로 읽는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from zzaimy.verify.numbers import verify_numbers

SFT_DIR = Path("data/interim/sft")

# 소스별 지시문 — 능력(검토·작성·응답)을 지시하고 근거를 입력에 동봉한다
_REVIEW_INSTRUCTION = (
    "다음 접수 문서를 행정 담당자의 관점에서 검토하고, 형식·요건·보완점을 "
    "짚은 검토 의견을 작성하라. 의견 속 모든 수치는 문서에 있는 것만 쓴다."
)
_DRAFT_INSTRUCTION = (
    "아래 근거 자료를 바탕으로 계획서 초안을 작성하라. 근거에 없는 수치를 "
    "만들어 넣지 않는다."
)


@dataclass
class BuildResult:
    source: str
    pairs: list[dict] = field(default_factory=list)
    n_dropped_numbers: int = 0   # 수치 검증 탈락
    n_dropped_short: int = 0     # 내용 빈약 탈락


def _pair(instruction: str, evidence: str, output: str, meta: dict) -> dict:
    human = instruction + ("\n\n" + evidence if evidence else "")
    return {
        "conversations": [
            {"from": "human", "value": human},
            {"from": "gpt", "value": output},
        ],
        "meta": meta,
    }


def _passes(result: BuildResult, output: str, evidence: list[str]) -> bool:
    """정제 규칙 — 짧은 산출·근거 없는 수치는 폐기하고 사유를 센다."""
    if len(output.strip()) < 40:
        result.n_dropped_short += 1
        return False
    if not verify_numbers(output, evidence).ok:
        result.n_dropped_numbers += 1
        return False
    return True


def build_review_pairs(db) -> BuildResult:
    """검토 능력 쌍 — (마스킹 본문 → AI 검토 의견). 근거 = 본문."""
    out = BuildResult("review")
    for d in db.list_documents():
        if d.get("doc_type") in ("regulation", "ocr"):
            continue
        body, review = d.get("masked_text") or "", d.get("ai_review") or ""
        if not body.strip() or not review.strip():
            continue
        if not _passes(out, review, [body]):
            continue
        out.pairs.append(_pair(
            _REVIEW_INSTRUCTION, f"[접수 문서]\n{body[:6000]}", review,
            {"source": "review", "doc_id": d["id"]},
        ))
    return out


def build_draft_pairs(db) -> BuildResult:
    """작성 능력 쌍 — (기준 조각+재료 → 초안). 근거 = 본문+연결 기준."""
    out = BuildResult("draft")
    for d in db.list_documents():
        draft = d.get("draft") or ""
        body = d.get("masked_text") or ""
        if not draft.strip() or not body.strip():
            continue
        evidence_parts = [body]
        crit_id = d.get("related_criteria_id")
        if crit_id:
            for c in db.chunks_for_docs([int(crit_id)]):
                evidence_parts.append(c["content"])
        if not _passes(out, draft, evidence_parts):
            continue
        evidence = f"[재료 문서]\n{body[:5000]}"
        if len(evidence_parts) > 1:
            joined = "\n\n".join(p[:800] for p in evidence_parts[1:6])
            evidence += f"\n\n[적용 기준 발췌]\n{joined}"
        out.pairs.append(_pair(
            _DRAFT_INSTRUCTION, evidence, draft,
            {"source": "draft", "doc_id": d["id"]},
        ))
    return out


def build_chat_pairs(db) -> BuildResult:
    """응답 능력 쌍 — 세션 전체를 멀티턴 대화로. 근거 = 사용자 발화 전체.

    답변에 인용된 규정 조각은 세션에 저장돼 있지 않아 근거로 넣지 못한다.
    그래서 수치 검증이 보수적으로 떨어뜨린다 — 근거 추적 저장이 붙으면
    통과율이 올라간다(예정 과제).
    """
    out = BuildResult("chat")
    for s in db.list_chat_sessions(limit=500):
        msgs = db.list_chats(s["id"], limit=200)
        turns = [
            {"from": "human" if m["role"] == "user" else "gpt",
             "value": m["content"]}
            for m in msgs if (m["content"] or "").strip()
        ]
        # human으로 시작해 gpt로 끝나는 구간만
        while turns and turns[0]["from"] != "human":
            turns.pop(0)
        while turns and turns[-1]["from"] != "gpt":
            turns.pop()
        if len(turns) < 2:
            continue
        evidence = [t["value"] for t in turns if t["from"] == "human"]
        answers = "\n".join(t["value"] for t in turns if t["from"] == "gpt")
        if not _passes(out, answers, evidence):
            continue
        out.pairs.append({
            "conversations": turns,
            "meta": {"source": "chat", "session_id": s["id"]},
        })
    return out


_BUILDERS = {
    "review": build_review_pairs,
    "draft": build_draft_pairs,
    "chat": build_chat_pairs,
}


def export_dataset(db, sources: list[str], name: str) -> dict:
    """소스들을 변환·정제해 JSONL로 쓰고 대장(datasets)에 기록한다."""
    sources = [s for s in sources if s in _BUILDERS]
    if not sources:
        raise ValueError("소스가 없다 (review/draft/chat 중 선택)")
    results = [_BUILDERS[s](db) for s in sources]
    pairs = [p for r in results for p in r.pairs]
    n_num = sum(r.n_dropped_numbers for r in results)
    n_short = sum(r.n_dropped_short for r in results)

    SFT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_") or "dataset"
    path = SFT_DIR / f"{safe}-{stamp}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    ledger_id = db.add_dataset(
        name=name, sources=",".join(sources), path=str(path),
        n_pairs=len(pairs), n_dropped_numbers=n_num, n_dropped_short=n_short,
    )
    return db.get_dataset(ledger_id)


def rag_status(db) -> list[dict]:
    """문서별 변환 상태 — 조각 수와 색인 등재 여부 (현황판 재료)."""
    reg_counts = db.regulation_chunk_counts()
    indexed_ids: set[int] | None = None
    try:
        import numpy as np

        from zzaimy.app.embed_search import INDEX_PATH

        if Path(INDEX_PATH).exists():
            indexed_ids = {int(i) for i in np.load(INDEX_PATH)["ids"]}
    except Exception:
        indexed_ids = None

    rows = []
    for d in db.list_documents():
        if d.get("doc_type") == "ocr":
            continue
        is_reg = d.get("doc_type") == "regulation"
        if is_reg:
            n_chunks = reg_counts.get(d["id"], 0)
            if indexed_ids is None:
                indexed = None
            else:
                chunk_ids = {c["id"] for c in db.chunks_for_docs([d["id"]])}
                indexed = bool(chunk_ids) and chunk_ids <= indexed_ids
        else:
            n_chunks = len(db.list_doc_chunks(d["id"]))
            indexed = None  # 인풋 문서는 벡터 색인 대상이 아니다
        rows.append({
            "id": d["id"], "filename": d["filename"],
            "doc_type": d.get("doc_type"), "status": d.get("status"),
            "n_chunks": n_chunks, "indexed": indexed,
            "has_review": bool((d.get("ai_review") or "").strip()),
            "has_draft": bool((d.get("draft") or "").strip()),
        })
    return rows
