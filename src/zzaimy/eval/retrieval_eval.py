"""규정 검색 품질 측정 — 합성 질의 세트를 실제 규정 색인에 돌려 기계 산출물로 남긴다.

대시보드(/dev)의 "규정 검색 품질" 카드는 이 모듈이 쓰는 data/platform/eval/retrieval-latest.json
하나만 읽는다. 손으로 쓴 보고서에서 수치를 긁어 오지 않는다 — 수치는 인출하고 생성하지 않는다.

측정 행 4개: 어휘(Kiwi) · 임베딩(색인 모델 이름) · 하이브리드(RRF, 운영 후보) · 하이브리드+리랭커
(= find_relevant, 운영 구성). 지표는 정본(zzaimy.eval.retrieval)의 Recall@1/5/10·MRR@10이고,
랭킹은 regulations·embed_search·rerank의 운영 함수를 그대로 부른다(평가용 재구현 없음).

정답은 조각 id가 아니라 본문으로 잇는다. 조각을 재분할하면 id가 전부 바뀌므로 질의 세트에
정답 본문(gold_text)을 같이 두고, 측정 때 현재 조각 중 본문이 가장 많이 겹치는 조각(들)로
다시 맞춘다. 본문이 없는 옛 세트는 재분할 전 스냅샷(data/platform/backup/
regulation_chunks-*.json.gz)이나 현재 DB에서 채운다(--backfill-gold).

합성 질의 자가 검색이라 절대치는 낙관적이다 — 방식 간 비교·회귀 감지용.
실행: env PYTHONPATH=src .venv/bin/python scripts/53_eval_retrieval.py [--no-rerank] [--limit N]
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import re
import shutil
import time
import traceback
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from zzaimy.eval.retrieval import mrr, recall_at_k

QUERIES_PATH = Path("data/interim/synth_queries.jsonl")
EVAL_DIR = Path("data/platform/eval")
BACKUP_DIR = Path("data/platform/backup")
# 사람이 읽는 사본. 기본은 산출물 폴더에 둔다 — 평가는 임베딩·색인이 있는 운영 VM 에서 돌리는데
# 저장소 파일(docs/)을 건드리면 그 체크아웃이 더러워져 배포가 멈춘다(실측 2026-09-20).
# 저장소에 남길 판이면 --report 로 명시한다(맥에서 커밋할 때).
MARKDOWN_PATH = Path("data/platform/eval/retrieval-baseline-mini.md")
REPO_MARKDOWN_PATH = Path("docs/retrieval-baseline-mini.md")
LATEST_NAME = "retrieval-latest.json"
RUNNING_NAME = ".running"
LOG_PATH = Path("/tmp/eval.log")
LOCK_PATH = Path("/tmp/zzaimy-heavy.lock")  # 66_reindex.sh와 같은 잠금 — 무거운 작업 동시 1개

SCHEMA = 1
TOP_K = 10
PRODUCTION_CANDIDATES = 20     # 운영 하이브리드 → 리랭커 후보 수(regulations.CANDIDATE_LIMIT 과 같아야 한다)
RERANK_SAMPLE = 300  # 크로스인코더는 CPU에서 질의당 수 초 — 고정 시드 표본으로 측정
SEED = 7
QUERY_TYPES = ("practical", "requirement", "keyword")

METHOD_LEXICAL = "어휘(Kiwi)"
METHOD_DENSE = "임베딩(KURE)"          # 기본 이름 — 실제 색인 모델이 다르면 dense_method_name() 이 바꾼다


def dense_method_name(model: str = "") -> str:
    """조밀 축 행 이름 — 실제로 쓴 임베딩 모델 이름을 넣는다.

    화면 카드의 수치는 기계 산출물만 쓴다는 규칙과 같은 이유로, 모델 이름도 손으로 적지 않는다.
    임베딩 학습본으로 갈아탄 뒤에도 'KURE' 라고 적혀 있으면 기록이 거짓이 된다(2026-09-20 실측).
    """
    name = (model or "").rsplit("/", 1)[-1]
    return f"임베딩({name})" if name else METHOD_DENSE
METHOD_HYBRID = "하이브리드"
METHOD_PRODUCTION = "하이브리드+리랭커"
METRIC_KEYS = ("recall_at_1", "recall_at_5", "recall_at_10", "mrr_at_10")

NO_QUERY_SET_MSG = "합성 질의 세트 재생성 필요 — LLM 서빙 연결 후 scripts/51_synth_queries.py"
BUSY_MSG = "다른 무거운 작업이 실행 중 — 중단"
SELF_RETRIEVAL_NOTE = "합성 질의 자가 검색 — 절대치 낙관적, 방식 간 비교용"


# ---------------------------------------------------------------- 정답 본문 재결선

_WS = re.compile(r"\s+")


def normalize_text(text: str | None) -> str:
    """공백 정규화 — 조각 본문 비교의 기준 형태."""
    return _WS.sub(" ", text or "").strip()


def gold_fields(chunk: dict) -> dict:
    """질의 세트 행에 함께 저장할 정답 본문 — 조각 id가 바뀌어도 정답을 되찾는 열쇠."""
    return {
        "gold_text": normalize_text(chunk.get("content")),
        "gold_title": chunk.get("reg_title") or "",
        "gold_heading": chunk.get("heading") or "",
    }


SHINGLE_N = 8
MATCH_MIN = 0.5  # 최고 후보의 겹침(정답 기준·후보 기준 중 큰 쪽)이 이보다 작으면 정답 소실
COVER_MIN = 0.6  # 후보 채택 — 정답 본문 대부분을 담거나(병합) 후보 대부분이 정답 안에 있음(분할)
BEST_SHARE = 0.5  # 분할 후보는 최고 후보의 절반 이상 겹쳐야 한다(표제만 남은 조각 배제)
GOLD_SHARE_MIN = 0.2  # 최고 후보라도 정답 본문의 20%는 담아야 한다


def _shingles(text: str, n: int = SHINGLE_N) -> set[int]:
    """글자 n-그램 해시 집합 — 띄어쓰기가 흔들리는 한국어 본문의 겹침 측정."""
    if not text:
        return set()
    if len(text) <= n:
        return {hash(text)}
    return {hash(text[i:i + n]) for i in range(len(text) - n + 1)}


class ChunkMatcher:
    """현재 조각 목록에서 정답 본문에 해당하는 조각(들)을 찾는다 — 조각 재분할 뒤 정답 재결선."""

    def __init__(self, chunks: Iterable[dict]) -> None:
        self.by_id: dict[int, dict] = {int(c["id"]): c for c in chunks}
        self._norm = {cid: normalize_text(c.get("content")) for cid, c in self.by_id.items()}
        self._index: dict[int, list[int]] | None = None
        self._size: dict[int, int] = {}

    def _build(self) -> dict[int, list[int]]:
        if self._index is None:
            index: dict[int, list[int]] = {}
            for cid, text in self._norm.items():
                sh = _shingles(text)
                self._size[cid] = len(sh)
                for s in sh:
                    index.setdefault(s, []).append(cid)
            self._index = index
        return self._index

    def resolve(self, gold_id: int | None, gold_text: str | None) -> set[int]:
        """정답 조각 id 집합. 빈 집합 = 현재 코퍼스에서 정답 본문을 찾지 못함(질의 제외)."""
        g = normalize_text(gold_text)
        if not g:
            # 본문 없는 옛 행 — id가 아직 살아 있으면 그대로(AUTOINCREMENT라 재사용되지 않음)
            return {gold_id} if gold_id in self.by_id else set()
        if gold_id in self.by_id and self._norm[gold_id] == g:
            return {gold_id}
        gs = _shingles(g)
        index = self._build()
        hits: Counter[int] = Counter()
        for s in gs:
            for cid in index.get(s, ()):
                hits[cid] += 1
        if not hits:
            return set()
        best_id, best_hits = hits.most_common(1)[0]
        if best_hits < GOLD_SHARE_MIN * len(gs):
            return set()

        def cover_gold(cid: int) -> float:
            return hits[cid] / len(gs)

        def cover_chunk(cid: int) -> float:
            return hits[cid] / max(self._size.get(cid, 0), 1)

        if max(cover_gold(best_id), cover_chunk(best_id)) < MATCH_MIN:
            return set()
        out: set[int] = set()
        for cid, h in hits.items():
            if cover_gold(cid) >= COVER_MIN:
                out.add(cid)  # 병합·동일: 후보가 정답 본문을 거의 다 담는다
            elif cover_chunk(cid) >= COVER_MIN and h >= BEST_SHARE * best_hits:
                out.add(cid)  # 분할: 후보 대부분이 정답 안에 있고 최고 후보에 견줄 만큼 겹친다
        return out or {best_id}


# ---------------------------------------------------------------- 질의 세트

@dataclass
class Query:
    text: str
    qtype: str
    row: int  # 질의 세트 행 번호 — 같은 행의 질의 3종은 정답을 공유한다


def load_rows(path: Path) -> list[dict]:
    """JSONL 질의 세트 — 깨진 줄은 건너뛴다."""
    rows: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def load_snapshot(path: Path) -> dict[int, dict]:
    """재분할 전 조각 스냅샷(75_rechunk_regulations.py가 남긴 .json.gz) → {옛 id: 조각}."""
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:  # type: ignore[operator]
        data = json.load(f)
    return {int(c["id"]): c for c in data}


def latest_snapshot(backup_dir: Path = BACKUP_DIR) -> Path | None:
    files = sorted(Path(backup_dir).glob("regulation_chunks-*.json.gz")) if Path(
        backup_dir).exists() else []
    return files[-1] if files else None


def _gold_id(row: dict) -> int | None:
    try:
        return int(row["chunk_id"]) if row.get("chunk_id") is not None else None
    except (TypeError, ValueError):
        return None


def resolve_golds(
    rows: list[dict], matcher: ChunkMatcher, snapshot: dict[int, dict] | None = None,
) -> tuple[list[set[int]], dict]:
    """행별 정답 id 집합. gold_text가 없으면 스냅샷(옛 id→본문)으로, 그것도 없으면 id 그대로."""
    golds: list[set[int]] = []
    stats: Counter[str] = Counter()
    for r in rows:
        gid = _gold_id(r)
        text = r.get("gold_text") or ""
        source = "gold_text"
        if not text and snapshot and gid in snapshot:
            text = snapshot[gid].get("content") or ""
            source = "snapshot"
        if not text:
            source = "chunk_id"
        ids = matcher.resolve(gid, text)
        stats[source] += 1
        if not ids:
            stats["unmapped"] += 1
        elif gid is None or ids != {gid}:
            stats["remapped"] += 1
        golds.append(ids)
    return golds, dict(stats)


def backfill_gold_text(path: Path, chunks_by_id: dict[int, dict], *, backup: bool = True) -> dict:
    """질의 세트 행에 정답 본문(gold_text)을 채운다 — 조각 재분할 전에 한 번 돌리는 이행.

    이미 채워진 행은 건드리지 않고(반복 실행 안전), 출처에 없는 id는 그대로 두고 센다.
    바뀌는 게 있으면 원본을 .bak-<시각>으로 남기고 임시 파일 교체로 쓴다.
    """
    path = Path(path)
    rows = load_rows(path)
    filled = missing = already = 0
    out_lines: list[str] = []
    for r in rows:
        if r.get("gold_text"):
            already += 1
        else:
            gid = _gold_id(r)
            c = chunks_by_id.get(gid) if gid is not None else None
            if c is None:
                missing += 1
            else:
                r.update(gold_fields(c))
                filled += 1
        out_lines.append(json.dumps(r, ensure_ascii=False))
    if filled:
        if backup:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    return {"rows": len(rows), "filled": filled, "missing": missing, "already": already}


# ---------------------------------------------------------------- 랭커 (운영 함수 그대로)

@dataclass
class Retrievers:
    """방식별 랭커 — 운영은 production_retrievers(db), 테스트는 가짜를 넣는다.

    lexical(질의) → 조각 id 순위, dense(질의) → 조각 id 순위,
    hybrid(질의, 어휘 순위, 임베딩 순위) → 운영 후보 id 순위(리랭크 직전),
    rerank(질의, 후보 id) → 재정렬 id 순위. rerank가 None이면 운영 구성 행은 미측정.
    """

    lexical: Callable[[str], list[int]]
    dense: Callable[[str], list[int]]
    hybrid: Callable[[str, list[int], list[int]], list[int]]
    rerank: Callable[[str, list[int]], list[int]] | None = None
    meta: dict = field(default_factory=dict)
    # 대안 조밀 축(예: KURE-v2 다중 벡터 서비스) — 있으면 운영 축과 나란히 같은 질의로 잰다(ADR-0022 의 채택 조건).
    alt_dense: Callable[[str], list[int]] | None = None
    alt_name: str = ""


def alt_dense_from_env() -> tuple[Callable[[str], list[int]] | None, str]:
    """ZZAIMY_ALT_DENSE_URL(…/search) 이 있으면 그 서비스로 조각 순위를 받는다. 이름은 /health 의 model."""
    import json
    import urllib.request

    url = os.environ.get("ZZAIMY_ALT_DENSE_URL", "").strip()
    if not url:
        return None, ""
    name = os.environ.get("ZZAIMY_ALT_DENSE_NAME", "").strip()
    if not name:
        try:
            base = url.rsplit("/", 1)[0]
            with urllib.request.urlopen(base + "/health", timeout=10) as r:
                name = str(json.load(r).get("model") or "")
        except Exception:
            name = ""
    name = name or "대안 조밀"
    from zzaimy.app.regulations import HYBRID_TOP_K

    def alt(q: str) -> list[int]:
        req = urllib.request.Request(url, data=json.dumps({"query": q, "top_k": HYBRID_TOP_K}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return [int(i) for i in json.load(r).get("ids", [])]

    return alt, name


def production_retrievers(db, chunks: list[dict] | None = None) -> Retrievers:
    """운영 검색 경로(find_relevant)의 구성요소를 그대로 쓴다 — 평가용 재구현 없음."""
    from zzaimy.app import embed_search as es
    from zzaimy.app import regulations as reg
    from zzaimy.app import rerank as rr

    if chunks is None:
        chunks = db.list_regulation_chunks()
    by_id = {c["id"]: c for c in chunks}

    def lexical(q: str) -> list[int]:
        return reg.lexical_rank(db, q, chunks=chunks)

    def dense(q: str) -> list[int]:
        return [cid for cid, _ in es.embed_search(q, top_k=reg.HYBRID_TOP_K)]

    def hybrid(q: str, lex: list[int], den: list[int]) -> list[int]:
        cands = reg.hybrid_candidates(db, q, chunks=chunks, lexical_ids=lex, dense_ids=den)
        return [c["id"] for c in cands]

    rerank = None
    # 적재 실패·비활성이면 rerank_chunks가 입력 순서를 그대로 돌려준다 — 그 결과를 리랭크
    # 수치로 적으면 거짓이 되므로 여기서 미적재로 못 박는다.
    # 서빙 장비의 리랭커가 켜져 있으면 VM 에 모델이 없어도 운영 구성 행을 잰다.
    remote_rerank = bool(os.environ.get("ZZAIMY_RERANK_URL", "").strip())
    if not os.environ.get("ZZAIMY_NO_RERANK") and (remote_rerank or rr._encoder() is not None):
        def rerank(q: str, cand_ids: list[int]) -> list[int]:
            cands = [by_id[c] for c in cand_ids if c in by_id]
            return [c["id"] for c in rr.rerank_chunks(q, cands)]

    # 모델 이름은 손으로 적지 않는다 — 색인 메타와 서빙 상태에서 읽는다.
    # 학습본으로 갈아탄 뒤에도 'KURE'·'bge-reranker' 라고 적히면 측정 기록이 거짓이 된다.
    rerank_model = rr._MODEL
    if remote_rerank:
        from zzaimy.app import search_serving

        for part in search_serving.status():
            if part["key"] == "rerank" and part["model"]:
                rerank_model = part["model"]
    alt, alt_name = alt_dense_from_env()
    meta = {
        "n_chunks": len(chunks),
        "embedding_model": es._index_model_name() or es.MODEL_NAME,
        "embedding_active": bool(es._index._load()),
        "rerank_model": rerank_model,
        "rerank_active": rerank is not None,
        "alt_dense_model": alt_name or None,
    }
    return Retrievers(lexical, dense, hybrid, rerank, meta, alt_dense=alt, alt_name=alt_name)


# ---------------------------------------------------------------- 지표

def metrics(runs: list[list[int]], golds: list[set[int]]) -> dict:
    """정본 지표(eval/retrieval) — 순위는 상위 TOP_K로 자른 뒤 계산한다."""
    cut = [list(r)[:TOP_K] for r in runs]
    return {
        "recall_at_1": round(recall_at_k(cut, golds, 1), 4),
        "recall_at_5": round(recall_at_k(cut, golds, 5), 4),
        "recall_at_10": round(recall_at_k(cut, golds, 10), 4),
        "mrr_at_10": round(mrr(cut, golds), 4),
    }


def _row(method: str, runs: list[list[int]], golds: list[set[int]]) -> dict:
    return {"method": method, "production": False, "n": len(runs), **metrics(runs, golds),
            "note": None}


def _unmeasured(method: str, note: str) -> dict:
    return {"method": method, "production": False, "n": 0,
            **{k: None for k in METRIC_KEYS}, "note": note}


def evaluate(
    queries: list[Query], golds: list[set[int]], retrievers: Retrievers, *,
    no_rerank: bool = False, rerank_sample: int = RERANK_SAMPLE, seed: int = SEED,
    log: Callable[[str], None] | None = None,
) -> tuple[list[dict], list[str]]:
    """질의별 4방식 순위를 만들고 행별 지표를 낸다. golds[i]는 queries[i]의 정답 집합."""
    say = log or (lambda _m: None)
    lex_runs: list[list[int]] = []
    den_runs: list[list[int]] = []
    hyb_runs: list[list[int]] = []
    cand_runs: list[list[int]] = []          # 리랭커에 넘길 후보 — 운영과 같은 개수(CANDIDATE_LIMIT)
    alt_runs: list[list[int]] = []           # 대안 조밀 축(있을 때) — 단독 · 어휘와 융합 · 후보
    alt_hyb_runs: list[list[int]] = []
    alt_cand_runs: list[list[int]] = []
    for i, q in enumerate(queries, start=1):
        lex = list(retrievers.lexical(q.text))
        den = list(retrievers.dense(q.text))
        lex_runs.append(lex[:TOP_K])
        den_runs.append(den[:TOP_K])
        hyb = list(retrievers.hybrid(q.text, lex, den))
        hyb_runs.append(hyb[:TOP_K])
        cand_runs.append(hyb[:PRODUCTION_CANDIDATES])
        if retrievers.alt_dense is not None:
            alt = list(retrievers.alt_dense(q.text))
            alt_runs.append(alt[:TOP_K])
            ahyb = list(retrievers.hybrid(q.text, lex, alt))
            alt_hyb_runs.append(ahyb[:TOP_K])
            alt_cand_runs.append(ahyb[:PRODUCTION_CANDIDATES])
        if i % 100 == 0:
            say(f"진행 {i}/{len(queries)}")

    notes: list[str] = []
    rows = [_row(METHOD_LEXICAL, lex_runs, golds)]
    if retrievers.meta.get("embedding_active", True):
        rows.append(_row(dense_method_name(retrievers.meta.get("embedding_model", "")),
                         den_runs, golds))
    else:
        rows.append(_unmeasured(dense_method_name(retrievers.meta.get("embedding_model", "")),
                                "임베딩 색인 없음"))
        notes.append("임베딩 색인 비활성 — 하이브리드 행은 어휘 단독 결과")
    hyb_row = _row(METHOD_HYBRID, hyb_runs, golds)
    hyb_row["recall_at_20"] = round(recall_at_k(cand_runs, golds, PRODUCTION_CANDIDATES), 4)   # 후보 진입률
    rows.append(hyb_row)
    alt_name = retrievers.alt_name or "대안 조밀"
    if retrievers.alt_dense is not None:
        rows.append(_row(f"다중 벡터({alt_name})", alt_runs, golds))
        arow = _row(f"하이브리드(어휘+{alt_name})", alt_hyb_runs, golds)
        arow["recall_at_20"] = round(recall_at_k(alt_cand_runs, golds, PRODUCTION_CANDIDATES), 4)
        rows.append(arow)
        notes.append(f"후보 진입률(R@{PRODUCTION_CANDIDATES}): 운영 하이브리드 {hyb_row['recall_at_20']:.3f} · "
                     f"어휘+{alt_name} {arow['recall_at_20']:.3f}")

    # 운영 구성 — 크로스인코더는 CPU에서 질의당 수 초라 고정 시드 표본으로 잰다
    if no_rerank:
        prod = _unmeasured(METHOD_PRODUCTION, "생략(--no-rerank)")
    elif retrievers.rerank is None:
        prod = _unmeasured(METHOD_PRODUCTION, "리랭커 미적재")
    else:
        n = len(queries)
        idx = sorted(random.Random(seed).sample(range(n), min(rerank_sample, n)))
        rr_runs: list[list[int]] = []
        sub_hyb: list[list[int]] = []
        sub_gold: list[set[int]] = []
        for j, k in enumerate(idx, start=1):
            # 운영은 후보 20개를 리랭커에 넘긴다. 예전에는 R@10 절단 목록(10개)을 넘겨 리랭커가 고를 폭이
            # 좁았고, 그래서 운영 구성 행이 하이브리드보다 낮게 나왔다(2026-09-21 실측: 0.550 vs 0.657,
            # 같은 표본을 후보 20개로 돌리면 0.683 → 0.770). 측정은 운영을 그대로 재현해야 한다.
            cand = cand_runs[k]
            rr_runs.append(list(retrievers.rerank(queries[k].text, cand))[:TOP_K])
            sub_hyb.append(hyb_runs[k])
            sub_gold.append(golds[k])
            if j % 25 == 0:
                say(f"리랭크 {j}/{len(idx)}")
        prod = _row(METHOD_PRODUCTION, rr_runs, sub_gold)
        prod["sample"] = {"seed": seed, "of": n}
        prod["hybrid_on_sample"] = metrics(sub_hyb, sub_gold)
        base = prod["hybrid_on_sample"]
        notes.append(
            f"운영 구성 행은 고정 시드({seed}) 무작위 표본 {len(idx)}건 · 후보 {PRODUCTION_CANDIDATES}개(운영과 같음)로 측정 — "
            f"같은 표본의 하이브리드 R@1 {base['recall_at_1']:.3f}, MRR@10 {base['mrr_at_10']:.3f}"
        )
    prod["production"] = True
    rows.append(prod)
    if retrievers.alt_dense is not None and retrievers.rerank is not None and not no_rerank:
        # 같은 표본·같은 리랭커로 대안 축의 후보를 재정렬 — 운영 구성 행과 바로 견준다
        n = len(queries)
        idx = sorted(random.Random(seed).sample(range(n), min(rerank_sample, n)))
        a_runs = [list(retrievers.rerank(queries[k].text, alt_cand_runs[k]))[:TOP_K] for k in idx]
        arow = _row(f"어휘+{alt_name}+리랭커", a_runs, [golds[k] for k in idx])
        arow["sample"] = {"seed": seed, "of": n}
        arow["hybrid_on_sample"] = metrics([alt_hyb_runs[k] for k in idx], [golds[k] for k in idx])
        rows.append(arow)
    return rows, notes


# ---------------------------------------------------------------- 실행·산출물

def run_eval(
    db=None, *, queries_path: Path = QUERIES_PATH, eval_dir: Path = EVAL_DIR,
    snapshot: Path | None = None, limit: int | None = None, no_rerank: bool = False,
    rerank_sample: int = RERANK_SAMPLE, seed: int = SEED,
    retrievers: Retrievers | None = None, chunks: list[dict] | None = None,
    write_md: Path | None = MARKDOWN_PATH, log: Callable[[str], None] | None = print,
) -> dict:
    """질의 세트 전체를 평가해 retrieval-latest.json(+날짜본)을 쓰고 결과 dict를 돌려준다."""
    from zzaimy.app.regulations import CANDIDATE_LIMIT, HYBRID_W_A

    say = log or (lambda _m: None)
    t0 = time.time()
    queries_path = Path(queries_path)
    if not queries_path.exists():
        raise FileNotFoundError(f"{NO_QUERY_SET_MSG} ({queries_path})")
    rows = load_rows(queries_path)
    if not rows:
        raise ValueError(f"질의 세트가 비어 있음 ({queries_path})")
    if chunks is None:
        if db is None:
            raise ValueError("db 또는 chunks가 필요하다")
        chunks = db.list_regulation_chunks()
    matcher = ChunkMatcher(chunks)

    snap = None
    if snapshot is None and not all(r.get("gold_text") for r in rows):
        snapshot = latest_snapshot()  # gold_text 없는 옛 세트 — 재분할 전 스냅샷으로 잇는다
    if snapshot is not None:
        snap = load_snapshot(snapshot)
        say(f"정답 본문 출처 스냅샷: {snapshot} ({len(snap)}조각)")
    golds, gold_stats = resolve_golds(rows, matcher, snap)
    queries = [
        Query(text, qtype, i)
        for i, r in enumerate(rows) if golds[i]
        for qtype in QUERY_TYPES
        if (text := (r.get(qtype) or "").strip())
    ]
    if not queries:
        raise ValueError(f"평가할 질의가 없음 — 정답 재결선 결과 {gold_stats}")
    sampled = bool(limit and limit < len(queries))
    if sampled:
        queries = sorted(random.Random(seed).sample(queries, limit), key=lambda q: q.row)
    say(f"질의 {len(queries)}건 (행 {len(rows)}개, 정답 재결선 {gold_stats})"
        f" · 조각 {len(chunks)}개")

    if retrievers is None:
        if db is None:
            raise ValueError("retrievers가 없으면 db가 필요하다")
        retrievers = production_retrievers(db, chunks)
    row_golds = [golds[q.row] for q in queries]
    result_rows, notes = evaluate(
        queries, row_golds, retrievers,
        no_rerank=no_rerank, rerank_sample=rerank_sample, seed=seed, log=say,
    )

    all_notes = [SELF_RETRIEVAL_NOTE]
    if gold_stats.get("remapped"):
        all_notes.append(
            f"조각 재분할로 정답을 본문 기준으로 재결선한 행 {gold_stats['remapped']}건")
    if gold_stats.get("unmapped"):
        all_notes.append(
            f"정답 본문을 현재 조각에서 찾지 못해 제외한 행 {gold_stats['unmapped']}건")
    if sampled:
        all_notes.append(f"질의 {len(queries)}건 표본(--limit, 시드 {seed})으로 측정")
    all_notes += notes

    meta = retrievers.meta
    result = {
        "schema": SCHEMA,
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_s": round(time.time() - t0, 1),
        "queries_path": str(queries_path),
        "n_query_rows": len(rows),
        "n_queries": len(queries),
        "n_chunks": len(chunks),
        "gold": gold_stats,
        "gold_snapshot": str(snapshot) if snapshot else None,
        "embedding_model": meta.get("embedding_model"),
        "rerank_model": meta.get("rerank_model"),
        "alt_dense_model": meta.get("alt_dense_model"),
        "lexical": "Kiwi 명사+IDF",
        "hybrid": f"RRF w_a={HYBRID_W_A} w_b=1.0 · 후보 {CANDIDATE_LIMIT}",
        "top_k": TOP_K,
        "limit": limit,
        "rows": result_rows,
        "notes": all_notes,
    }
    latest = write_artifact(result, eval_dir)
    say(f"산출물: {latest}")
    if write_md:
        write_markdown(result, write_md)
    return result


def write_artifact(result: dict, eval_dir: Path) -> Path:
    """retrieval-latest.json(임시 파일 교체) + 날짜본 retrieval-<시각>.json."""
    eval_dir = Path(eval_dir)
    eval_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(result["measured_at"]).strftime("%Y%m%d-%H%M%S")
    text = json.dumps(result, ensure_ascii=False, indent=1)
    (eval_dir / f"retrieval-{stamp}.json").write_text(text, encoding="utf-8")
    latest = eval_dir / LATEST_NAME
    tmp = eval_dir / (LATEST_NAME + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, latest)
    return latest


def _fmt(v: float | None) -> str:
    return "미측정" if v is None else f"{v:.3f}"


def write_markdown(result: dict, path: Path) -> None:
    """사람이 읽는 사본 — 주간 보고가 이 표를 읽는다. 정본은 JSON.

    기본 위치는 산출물 폴더(data/platform/eval/), 저장소 사본은 --report 로만 갱신한다.
    """
    lines = [
        "# 검색 미니 베이스라인 (규정 코퍼스 · 합성 질의)",
        "",
        f"측정일 {result['measured_at'][:10]} · 조각 {result['n_chunks']}개 · "
        f"질의 {result['n_queries']}건 · 임베딩 {result.get('embedding_model') or '미확인'} · "
        "어휘 Kiwi 명사+IDF",
        "",
        "기계 산출물 data/platform/eval/retrieval-latest.json의 사본 — 수치는 그쪽이 정본이다.",
        "",
        "| 방식 | Recall@1 | Recall@5 | Recall@10 | MRR@10 | n |",
        "|---|---|---|---|---|---|",
    ]
    for r in result["rows"]:
        name = r["method"] + ((" (운영 구성)" if os.environ.get("ZZAIMY_RERANK_URL", "").strip() else " (CPU 베이스 — 운영 아님)") if r.get("production") else "")
        lines.append(
            f"| {name} | {_fmt(r['recall_at_1'])} | {_fmt(r['recall_at_5'])} |"
            f" {_fmt(r['recall_at_10'])} | {_fmt(r['mrr_at_10'])} | {r['n']} |"
        )
    lines += [""] + [f"{n}." if not n.endswith(".") else n for n in result["notes"]]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_latest(eval_dir: Path) -> dict | None:
    p = Path(eval_dir) / LATEST_NAME
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return None
    return data


# ---------------------------------------------------------------- 실행 상태 (대시보드)

def mark_running(eval_dir: Path) -> Path:
    eval_dir = Path(eval_dir)
    eval_dir.mkdir(parents=True, exist_ok=True)
    p = eval_dir / RUNNING_NAME
    p.write_text(json.dumps({
        "pid": os.getpid(),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }), encoding="utf-8")
    return p


def clear_running(eval_dir: Path) -> None:
    try:
        (Path(eval_dir) / RUNNING_NAME).unlink()
    except OSError:
        pass


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, TypeError, ValueError, OverflowError):
        return False
    except PermissionError:
        return True
    return True


def _log_tail(path: Path) -> str:
    """로그의 마지막 비어 있지 않은 줄 — 진행 표시용."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 4000))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def running_state(eval_dir: Path, log_path: Path = LOG_PATH) -> dict | None:
    """측정 중이면 {pid, started_at, progress}, 아니면 None. 죽은 프로세스의 잔재는 무시."""
    p = Path(eval_dir) / RUNNING_NAME
    if not p.exists():
        return None
    try:
        info = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not _pid_alive(info.get("pid")):
        return None
    return {"pid": info.get("pid"), "started_at": info.get("started_at", ""),
            "progress": _log_tail(log_path)}


def _display_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return iso or ""


def dashboard_state(
    eval_dir: Path, *, queries_path: Path | None = None, log_path: Path = LOG_PATH,
) -> dict:
    """/dev 카드가 쓰는 상태 — 산출물·실행 중 여부·질의 세트 유무. 수치는 산출물에서만."""
    qp = Path(QUERIES_PATH if queries_path is None else queries_path)
    result = load_latest(eval_dir)
    running = running_state(eval_dir, log_path)
    missing = not qp.exists()
    last = "" if running else _log_tail(log_path)
    return {
        "result": result,
        "measured_at_display": _display_time(result["measured_at"]) if result else "",
        "running": running,
        "started_at_display": _display_time(running["started_at"]) if running else "",
        "query_set_missing": missing,
        "query_set_message": NO_QUERY_SET_MSG if missing else "",
        "last_error": last if last.startswith("EVAL_FAIL") else "",
        "latest_path": str(Path(eval_dir) / LATEST_NAME),
    }


# ---------------------------------------------------------------- CLI

def heavy_lock() -> int | None:
    """무거운 작업 동시 1개 — 66_reindex.sh와 같은 잠금 파일(flock). 못 잡으면 None."""
    import fcntl

    fd = os.open(LOCK_PATH, os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _print_summary(result: dict) -> None:
    print(f"측정 {result['measured_at']} · 질의 {result['n_queries']}건"
          f" · 조각 {result['n_chunks']}개")
    print("| 방식 | R@1 | R@5 | R@10 | MRR@10 | n |")
    for r in result["rows"]:
        name = r["method"] + ((" (운영 구성)" if os.environ.get("ZZAIMY_RERANK_URL", "").strip() else " (CPU 베이스 — 운영 아님)") if r.get("production") else "")
        print(f"| {name} | {_fmt(r['recall_at_1'])} | {_fmt(r['recall_at_5'])} |"
              f" {_fmt(r['recall_at_10'])} | {_fmt(r['mrr_at_10'])} | {r['n']} |")
    for n in result["notes"]:
        print(f"- {n}")
    try:
        from zzaimy.app import rerank as _rr

        st = _rr.STATS
        if os.environ.get("ZZAIMY_RERANK_URL", "").strip():
            print(f"- 리랭커: 서빙 장비 {os.environ['ZZAIMY_RERANK_URL']} · 응답 {st['remote_ok']}건 · CPU 폴백 {st['fallback']}건"
                  + (" — 폴백이 있으면 이 행은 GPU 리랭커만의 숫자가 아니다" if st["fallback"] else ""))
        else:
            # 2026-09-21 실측: .env.local 없이 띄운 측정이 CPU 베이스 리랭커(앞쪽 후보만·제목 없이)로 떨어져
            # 운영보다 0.1 낮은 값을 '운영 구성'으로 적었다. 어느 리랭커가 점수를 냈는지 항상 적는다.
            print("- 리랭커: ZZAIMY_RERANK_URL 이 없어 VM CPU 의 베이스 리랭커로 쟀다 — 운영 구성이 아니다."
                  " 운영 값은 `set -a; . ./.env.local; set +a` 뒤에 다시 잰다")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="규정 검색 품질 측정 (합성 질의 · 운영 색인)")
    ap.add_argument("--db", default="data/platform/platform.db")
    ap.add_argument("--queries", default=str(QUERIES_PATH))
    ap.add_argument("--out", default=None, help="산출물 폴더 (기본: DB 옆 eval/)")
    ap.add_argument("--snapshot", default=None,
                    help="재분할 전 조각 스냅샷(.json.gz) — gold_text 없는 옛 세트용. "
                         "미지정 시 data/platform/backup/ 최신본")
    ap.add_argument("--limit", type=int, default=None,
                    help="질의 N건만(고정 시드 표본) — 연기 시험")
    ap.add_argument("--no-rerank", action="store_true", help="운영 구성(리랭커) 행 생략")
    ap.add_argument("--rerank-sample", type=int, default=RERANK_SAMPLE,
                    help=f"리랭커 행 표본 크기 (기본 {RERANK_SAMPLE})")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--report", action="store_true",
                    help="사람이 읽는 사본을 저장소(docs/)에 쓴다. 기본은 산출물 폴더에만 둔다")
    ap.add_argument("--no-md", action="store_true",
                    help="사람이 읽는 사본 갱신 생략")
    ap.add_argument("--backfill-gold", action="store_true",
                    help="질의 세트에 정답 본문(gold_text)만 채우고 끝 — 조각 재분할 전에 실행")
    args = ap.parse_args(argv)

    queries = Path(args.queries)
    if not queries.exists():
        print(f"아직 측정 없음 ({NO_QUERY_SET_MSG}) — {queries} 없음", flush=True)
        return 2
    db_path = Path(args.db)
    eval_dir = Path(args.out) if args.out else db_path.parent / "eval"

    from zzaimy.app.db import Database

    db = Database(db_path)

    if args.backfill_gold:
        # 출처: 현재 DB 조각 우선, 없는 id는 재분할 전 스냅샷에서
        src: dict[int, dict] = {}
        snap_path = Path(args.snapshot) if args.snapshot else latest_snapshot()
        if snap_path is not None:
            src.update(load_snapshot(snap_path))
        src.update({int(c["id"]): c for c in db.list_regulation_chunks()})
        stats = backfill_gold_text(queries, src)
        print(f"정답 본문 채움 {stats} (출처: {db_path}"
              f"{' + ' + str(snap_path) if snap_path else ''})", flush=True)
        return 0

    # 자원 규율(66_reindex.sh와 동일) — 코어 절반·낮은 우선순위, 모델은 로컬 캐시만
    half = str(max(1, (os.cpu_count() or 8) // 2))
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(key, half)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("ZZAIMY_RERANK_DEVICE", "cpu")
    try:
        os.nice(10)
    except (OSError, AttributeError):
        pass

    lock = heavy_lock()
    if lock is None:
        print(BUSY_MSG, flush=True)
        return 1
    mark_running(eval_dir)
    try:
        result = run_eval(
            db, queries_path=queries, eval_dir=eval_dir,
            snapshot=Path(args.snapshot) if args.snapshot else None,
            limit=args.limit, no_rerank=args.no_rerank,
            rerank_sample=args.rerank_sample, seed=args.seed,
            write_md=(None if args.no_md
                      else (REPO_MARKDOWN_PATH if args.report else MARKDOWN_PATH)),
            log=lambda m: print(m, flush=True),
        )
        _print_summary(result)
        print("EVAL_DONE", flush=True)
        return 0
    except Exception as e:
        traceback.print_exc()
        print(f"EVAL_FAIL {type(e).__name__}: {e}", flush=True)
        return 1
    finally:
        clear_running(eval_dir)
        os.close(lock)
