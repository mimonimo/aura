"""데이터 체계(ADR-0031) — 세 층이고, 경로는 전부 여기서만 정한다.

  1층 문서   data/platform/documents/        반입·첨부·생성·보고 (storage.py, ADR-0030)
  2층 지식   data/platform/knowledge/        문서에서 뽑은 파생 자료 — 밖으로 내보내 쓸 수 있어야 한다
               index/      조각 임베딩(chunk_embeddings.npz + .meta.json)·문서 벡터·질문 색인
               eval/       검색 품질 측정 산출물(retrieval-*.json, llm-rerank-*)
               exports/    외부 이용 묶음(scripts/140) — 문서·조각·개체·간선·임베딩을 jsonl/npz 로
               corpus_pilot/  옛 국고 파일럿 DB 와 색인(참고)
  3층 모델   data/train/                     학습 자료와 학습본 — DGX 의 ~/zzaimy/train 과 같은 모양
               datasets/<모델>/<판>/  baselines/<모델>/  models/<모델>/<판>/  runs/<모델>/<판>/
  캐시       data/platform/cache/            재생성 가능(글자 좌표 lines·쪽 그림 pagecache·복원 PDF restored)
  원본 DB    data/platform/platform.db       설정·감사 파일(accounts·llm_connections·nas·gdrive·*_audit)도 platform/ 바로 아래

왜 한 곳인가. 26개 파일이 제각기 "data/platform/…" 을 적고 있어 옮기거나 늘릴 때마다 전부 고쳐야 했다. 이제 뿌리 둘
(ZZAIMY_DATA_DIR, ZZAIMY_TRAIN_DIR)만 바꾸면 전체가 따라온다. 색인 파일만은 옛 변수(ZZAIMY_EMBED_INDEX)도 받는다.
"""

from __future__ import annotations

import os
from pathlib import Path


def platform_dir(base: Path | None = None) -> Path:
    return Path(base) if base is not None else Path(os.environ.get("ZZAIMY_DATA_DIR", "data/platform"))


# ---- 1층 문서 ----
def documents_dir(base: Path | None = None) -> Path:
    return platform_dir(base) / "documents"


# ---- 2층 지식 ----
def knowledge_dir(base: Path | None = None) -> Path:
    return platform_dir(base) / "knowledge"


def index_dir(base: Path | None = None) -> Path:
    return knowledge_dir(base) / "index"


def index_npz(base: Path | None = None) -> Path:
    env = os.environ.get("ZZAIMY_EMBED_INDEX", "").strip()
    return Path(env) if env and base is None else index_dir(base) / "chunk_embeddings.npz"


def index_meta(base: Path | None = None) -> Path:
    p = index_npz(base)
    return p.with_name(p.name.replace(".npz", ".meta.json"))


def doc_vectors_npz(base: Path | None = None) -> Path:
    return index_dir(base) / "doc_vectors.npz"


def question_index_npz(base: Path | None = None) -> Path:
    return index_dir(base) / "question_embeddings.npz"


def eval_dir(base: Path | None = None) -> Path:
    return knowledge_dir(base) / "eval"


def exports_dir(base: Path | None = None) -> Path:
    return knowledge_dir(base) / "exports"


def corpus_dir(base: Path | None = None) -> Path:
    return knowledge_dir(base) / "corpus_pilot"


def corpus_db(base: Path | None = None) -> Path:
    return corpus_dir(base) / "corpus_pilot.db"


def corpus_db_existing(base: Path | None = None) -> Path:
    """파일럿 DB — 새 자리(knowledge/corpus_pilot)에 없으면 옛 자리(platform/ 바로 아래)도 본다(이관 전 호환)."""
    new = corpus_db(base)
    old = platform_dir(base) / "corpus_pilot.db"
    return new if new.exists() or not old.exists() else old


def corpus_npz(base: Path | None = None) -> Path:
    return corpus_dir(base) / "corpus_pilot_embeddings.npz"


def corpus_meta(base: Path | None = None) -> Path:
    return corpus_dir(base) / "corpus_pilot_embeddings.meta.json"


# ---- 캐시(재생성 가능) ----
def cache_dir(base: Path | None = None) -> Path:
    return platform_dir(base) / "cache"


def lines_dir(base: Path | None = None) -> Path:
    return cache_dir(base) / "lines"


def pagecache_dir(base: Path | None = None) -> Path:
    return cache_dir(base) / "pagecache"


def restored_dir(base: Path | None = None) -> Path:
    return cache_dir(base) / "restored"


# ---- 3층 모델 ----
def train_dir() -> Path:
    return Path(os.environ.get("ZZAIMY_TRAIN_DIR", "data/train"))


def train_datasets(model: str, version: str = "") -> Path:
    p = train_dir() / "datasets" / model
    return p / version if version else p


def train_baselines(model: str) -> Path:
    return train_dir() / "baselines" / model


def train_models(model: str, version: str = "") -> Path:
    p = train_dir() / "models" / model
    return p / version if version else p


def train_runs(model: str, version: str = "") -> Path:
    p = train_dir() / "runs" / model
    return p / version if version else p


def writer_baseline() -> Path:
    return train_baselines("writer") / "baseline.json"


LAYOUT = {
    "documents": "1층 문서 — 반입·첨부·생성·보고",
    "knowledge/index": "2층 지식 — 조각 임베딩·문서 벡터·질문 색인",
    "knowledge/eval": "2층 지식 — 검색 품질 측정",
    "knowledge/exports": "2층 지식 — 외부 이용 묶음",
    "knowledge/corpus_pilot": "2층 지식 — 옛 국고 파일럿(참고)",
    "cache": "재생성 가능한 캐시",
    "backup": "DB·조각·색인 백업",
}


def ensure_layout(base: Path | None = None) -> None:
    for rel in ("documents", "knowledge/index", "knowledge/eval", "knowledge/exports", "cache/lines",
                "cache/pagecache", "cache/restored", "backup"):
        (platform_dir(base) / rel).mkdir(parents=True, exist_ok=True)
