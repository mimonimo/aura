#!/usr/bin/env python3
"""지식 내보내기(ADR-0031) — 문서 목록·조각·개체·문서-개체 간선·조각 임베딩을 외부에서 쓸 수 있는 형식으로 묶는다.

산출: data/platform/knowledge/exports/<날짜-시각>/
  manifest.json      무엇을 언제 어떤 모델로 뽑았는지, 건수, 열람 등급 범위
  documents.jsonl    문서 메타데이터(접수번호·제목·유형·갈래·영역·부서·등급·판본·생성 시각)와 본문(마스킹본)
  chunks.jsonl       검색 조각(문서·표제·본문·부서·등급)
  entities.jsonl     개체(지식 그래프 마디), doc_entities.jsonl 문서-개체 간선
  embeddings.npz     내보낸 조각의 임베딩(ids, vectors) + embeddings.meta.json(모델 이름·차원)
기본은 전체 공개(public) 문서만이다(--levels public,dept 로 넓힐 수 있다 — 부서 제한 자료를 밖으로 낼 때는 승인이 먼저다).
--zip 이면 같은 이름의 zip 도 만든다.

실행(VM): env PYTHONPATH=src .venv/bin/python scripts/140_export_knowledge.py [--levels public] [--zip]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zzaimy.app import paths  # noqa: E402

DOC_COLS = ("id", "receipt_no", "filename", "doc_type", "kind", "sector", "dept", "access_level", "family", "version_of",
            "related_criteria_id", "created_at", "parse_note", "masked_text")


def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def export(db_path: Path, out_dir: Path, levels: tuple[str, ...], base: Path | None = None, make_zip: bool = False) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    marks = ",".join("?" for _ in levels)
    docs = _rows(conn, f"SELECT * FROM documents WHERE status <> 'failed' AND COALESCE(access_level,'public') IN ({marks})", levels)
    doc_ids = {d["id"] for d in docs}
    chunks = _rows(conn, f"SELECT id, doc_id, reg_title, heading, content, sector, dept, access_level FROM regulation_chunks"
                         f" WHERE COALESCE(access_level,'public') IN ({marks})", levels)
    chunks = [c for c in chunks if c["doc_id"] in doc_ids]
    ents = _rows(conn, "SELECT * FROM entities") if _table(conn, "entities") else []
    edges = [e for e in (_rows(conn, "SELECT * FROM doc_entities") if _table(conn, "doc_entities") else []) if e.get("doc_id") in doc_ids]
    out_dir.mkdir(parents=True, exist_ok=True)
    _jsonl(out_dir / "documents.jsonl", [{k: d.get(k) for k in DOC_COLS} for d in docs])
    _jsonl(out_dir / "chunks.jsonl", chunks)
    _jsonl(out_dir / "entities.jsonl", ents)
    _jsonl(out_dir / "doc_entities.jsonl", edges)
    emb_meta: dict = {}
    npz = paths.index_npz(base)
    if npz.exists():
        import numpy as np

        data = np.load(npz)
        ids = data["ids"]
        keep = np.isin(ids, np.array(sorted(c["id"] for c in chunks)))
        np.savez_compressed(out_dir / "embeddings.npz", ids=ids[keep], vectors=data["vectors"][keep])
        meta_path = paths.index_meta(base)
        emb_meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        emb_meta["n_vectors"] = int(keep.sum())
        (out_dir / "embeddings.meta.json").write_text(json.dumps(emb_meta, ensure_ascii=False, indent=1), encoding="utf-8")
    manifest = {"exported_at": datetime.now().isoformat(timespec="seconds"), "levels": list(levels), "schema": 1,
                "documents": len(docs), "chunks": len(chunks), "entities": len(ents), "doc_entities": len(edges),
                "embedding_model": emb_meta.get("model") or emb_meta.get("model_name") or "", "embeddings": emb_meta.get("n_vectors", 0),
                "source_db": str(db_path)}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    if make_zip:
        shutil.make_archive(str(out_dir), "zip", root_dir=out_dir.parent, base_dir=out_dir.name)
    return manifest


def _table(conn, name: str) -> bool:
    return conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/platform/platform.db")
    ap.add_argument("--levels", default="public", help="쉼표로 여럿: public,dept")
    ap.add_argument("--out", default="")
    ap.add_argument("--zip", action="store_true")
    args = ap.parse_args()
    levels = tuple(x.strip() for x in args.levels.split(",") if x.strip())
    if "owner" in levels:
        print("담당자 한정 자료는 내보내지 않습니다(ADR-0024·0031)", file=sys.stderr)
        return 2
    base = Path(args.db).parent
    out = Path(args.out) if args.out else paths.exports_dir(base) / datetime.now().strftime("%Y%m%d-%H%M%S")
    m = export(Path(args.db), out, levels, base=base, make_zip=args.zip)
    print(json.dumps(m, ensure_ascii=False, indent=1))
    print("→", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
