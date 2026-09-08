"""학습 산출물 반출 번들 (ADR-0012).

RAG 인덱스·학습 데이터·(있으면) 모델 어댑터를 개방 표준 형식으로 모아
자기설명적 zip으로 묶는다. 매니페스트만 보고 다른 환경에서 재조립할 수 있게
한다. 벤더 고유 포맷을 쓰지 않는다 — JSONL·numpy npz·safetensors·JSON만.

민감도 게이트: 기준(규정·공고)은 원문 반출 가능(공개 성격). 인풋 문서 유래
데이터는 마스킹본만. 실명·원문이 든 것은 번들에 넣지 않는다.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def _git_commit() -> str:
    try:
        import subprocess

        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def build_bundle(db, sft_dir: Path | str = "data/interim/sft",
                 model_dir: Path | str | None = None) -> tuple[bytes, dict]:
    """반출 zip 바이트와 매니페스트를 만든다.

    - RAG: 조각 JSONL + 임베딩 npz + 임베딩 모델 id
    - 학습 데이터: data/interim/sft/*.jsonl (이미 마스킹·검증 통과분)
    - 모델: model_dir이 주어지고 존재하면 그 안의 safetensors·config 포함
      (LoRA 어댑터 권장 — 베이스는 오픈웨이트 참조)
    """
    files: dict[str, bytes] = {}
    artifacts: list[dict] = []

    # RAG
    chunks = db.list_regulation_chunks()
    rag_jsonl = "\n".join(
        json.dumps({
            "id": c["id"], "doc_id": c["doc_id"], "reg_title": c["reg_title"],
            "heading": c["heading"], "content": c["content"],
            "sector": c.get("sector", "common"), "dept": c.get("dept", "공통"),
        }, ensure_ascii=False)
        for c in chunks
    )
    files["rag/chunks.jsonl"] = rag_jsonl.encode("utf-8")
    from zzaimy.app.embed_search import INDEX_PATH, MODEL_NAME

    if Path(INDEX_PATH).exists():
        files["rag/chunk_embeddings.npz"] = Path(INDEX_PATH).read_bytes()
    artifacts.append({
        "kind": "rag_index", "format": "jsonl + npz(numpy)",
        "n_chunks": len(chunks), "embedding_model": MODEL_NAME,
        "vectors_included": Path(INDEX_PATH).exists(),
        "reproduce": "chunks.jsonl을 embedding_model로 임베딩해 재색인",
    })

    # 학습 데이터 (마스킹·수치검증 통과분)
    sft = Path(sft_dir)
    datasets = []
    if sft.exists():
        for jf in sorted(sft.glob("*.jsonl")):
            files[f"datasets/{jf.name}"] = jf.read_bytes()
            n = sum(1 for _ in jf.open(encoding="utf-8"))
            datasets.append({"file": jf.name, "n_pairs": n})
    if datasets:
        artifacts.append({
            "kind": "training_data", "format": "jsonl(sharegpt)",
            "files": datasets, "note": "인풋 유래는 마스킹본·수치검증 통과분만",
        })

    # 모델 (있을 때만 — DGX 학습 후)
    if model_dir:
        md = Path(model_dir)
        if md.exists():
            model_files = []
            for f in md.rglob("*"):
                if f.is_file() and f.suffix in (
                    ".safetensors", ".json", ".model", ".txt"
                ):
                    rel = f.relative_to(md)
                    files[f"model/{rel}"] = f.read_bytes()
                    model_files.append(str(rel))
            if model_files:
                artifacts.append({
                    "kind": "model", "format": "safetensors(HF)",
                    "files": model_files,
                    "note": "LoRA 어댑터면 베이스 오픈웨이트와 재조립",
                })

    manifest = {
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(
            timespec="seconds"),
        "platform": "ZZAIMY",
        "git_commit": _git_commit(),
        "artifacts": artifacts,
        "base_models": {
            "embedding": MODEL_NAME,
            "reranker": "BAAI/bge-reranker-v2-m3",
            "writer_base": "Qwen/Qwen3.8-27B",
        },
        "portability": "개방 표준만(JSONL·npz·safetensors·JSON). ADR-0012.",
        "sensitivity": "기준 문서는 원문, 인풋 유래는 마스킹본만.",
    }
    files["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, indent=2
    ).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(files.items()):
            z.writestr(name, data)
    buf.seek(0)
    return buf.read(), manifest
