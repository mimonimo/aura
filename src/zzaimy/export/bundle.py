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
from zzaimy.dataset.privacy import approved_bytes


def _writer_model() -> str:
    """반출 당시 답변 연결의 모델 — 서버가 알려 주는 정보(어댑터·바탕 포함). 못 읽으면 「모름」."""
    try:
        from zzaimy.generate import model_info
        from zzaimy.generate.client import VllmClient
        return model_info.label(model_info.stamp(VllmClient(role="answer", timeout=5, max_retries=0)))
    except Exception:
        return "모름"


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


TRAINSETS = Path("data/inbox/parsed/trainsets.json")
_STATUS = {"sft_approved": "SFT 승인", "approved_for_sft": "학습 전 측정 승인", "candidate_not_sft_approved": "후보(승인 전)",
           "baseline_responses_unjudged": "측정 채점 전", "quality_review_pending": "보류 — 품질 검토 대기"}
_SPLIT = {"train": "학습", "eval": "검증", "test": "시험"}


def trainsets(path: Path | str | None = None) -> list[dict]:
    """DGX 학습셋 목록(scripts/197 → ~/parsed/trainsets.json, VM 이 170 --parsed 로 받음) — 설명 정보만. 화면에 보일 한 줄씩."""
    p = Path(path) if path else TRAINSETS
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        counts = it.get("counts") or {k: (v or {}).get("rows") for k, v in (it.get("splits") or {}).items()}
        parts = [f"{_SPLIT.get(k, k)} {int(v):,}" for k, v in counts.items() if isinstance(v, int)]
        if it.get("kind") == "baseline" and it.get("n_eval"):
            parts = [f"측정 {it['n_eval']}건 · 통과 {it.get('pass')} · 고칠 것 {it.get('needs_fix')}"]
        tracks = {}
        for sp in (it.get("splits") or {}).values():
            for k, v in ((sp or {}).get("tracks") or {}).items():
                tracks[k] = tracks.get(k, 0) + int(v or 0)
        out.append({"kind": it.get("kind"), "name": it.get("name"), "at": it.get("at"),
                    "status": it.get("status"), "status_label": _STATUS.get(str(it.get("status")), str(it.get("status") or "모름")),
                    "summary": " · ".join(parts), "tracks": tracks, "scope": it.get("scope") or "",
                    "training_started": it.get("training_started"), "limitations": it.get("limitations") or "",
                    "required": it.get("required_before_training") or [], "fetched_at": data.get("at"),
                    "hold_reason": it.get("hold_reason") or ""})
    order = {"release": 0, "staging": 1, "baseline": 2}
    return sorted(out, key=lambda x: (order.get(x["kind"], 3), str(x.get("at") or "")), reverse=False)


def preview_bundle(db, sft_dir: Path | str = "data/interim/sft",
                   model_dir: Path | str | None = None) -> list[dict]:
    """반출에 담길 항목을 그룹·파일·크기 트리로 미리 보여준다(zip 생성 없이).

    반환: [{"group","label","desc","files":[{"path","bytes"}]}...]
    """
    from zzaimy.app.embed_search import INDEX_PATH, MODEL_NAME, _index_model_name

    tree: list[dict] = []

    # RAG
    chunks = db.list_regulation_chunks()
    rag_files = [{
        "path": "rag/chunks.jsonl",
        "bytes": sum(len((c.get("content") or "")) for c in chunks) + 200 * len(chunks),
    }]
    if Path(INDEX_PATH).exists():
        rag_files.append({"path": "rag/chunk_embeddings.npz",
                          "bytes": Path(INDEX_PATH).stat().st_size})
    # 반출 대상은 규정 조각뿐 — 사업 문서 색인은 이 서버 DB 에 두고 반출하지 않는다(이름에서 드러나게)
    tree.append({"group": "rag", "label": "규정 색인",
                 # 임베딩 이름은 색인을 실제로 만든 모델(색인 메타) — 환경 기본값이 아니라 /dev/quality·/dev/db 와 같은 값
                 "desc": f"규정 조각 {len(chunks):,}개 + 임베딩({_index_model_name() or MODEL_NAME})",
                 "note": "사업 문서 색인은 이 서버 DB에 있어 반출 대상 아님",
                 "files": rag_files})

    # 학습 데이터
    sft = Path(sft_dir)
    ds_files = []
    if sft.exists():
        for jf in sorted(sft.glob("*.jsonl")):
            try:
                approved_bytes(jf)
            except ValueError:
                continue
            ds_files.append({"path": f"datasets/{jf.name}",
                             "bytes": jf.stat().st_size})
    remote = trainsets()
    approved = [x for x in remote if x.get("kind") == "release" and str(x.get("status") or "").endswith("approved")]
    if ds_files:
        desc = f"검수 승인 JSONL {len(ds_files)}개"
    elif approved:
        desc = f"DGX 에 승인 학습셋 {len(approved)}개 — 원문은 DGX 에만 두고 학습도 DGX 에서 합니다(아래 목록)"
    elif remote:
        desc = "DGX 에 학습셋 후보만 있음(승인 전) — 아래 목록"
    else:
        desc = "승인된 학습 데이터 없음 · DGX 학습셋 목록도 아직 받지 못함"
    tree.append({"group": "datasets", "label": "학습 데이터", "desc": desc, "files": ds_files, "sets": remote})

    # 모델
    md_files = []
    if model_dir and Path(model_dir).exists():
        for f in Path(model_dir).rglob("*"):
            if f.is_file() and f.suffix in (".safetensors", ".json", ".model", ".txt"):
                md_files.append({"path": f"model/{f.relative_to(model_dir)}",
                                 "bytes": f.stat().st_size})
    tree.append({"group": "model", "label": "모델",
                 "desc": f"파일 {len(md_files)}개" if md_files
                 # 운영 학습본(모델 카드)은 서빙 장비에 있다 — 여기서는 이 서버의 반출 폴더만 본다
                 else "이 서버에 반출할 모델 파일 없음 · 운영 학습본은 서빙 장비에 있음(모델 카드 참고)",
                 "files": md_files})
    return tree


def collect_files(db, sft_dir: Path | str = "data/interim/sft",
                  model_dir: Path | str | None = None,
                  include: set[str] | None = None) -> tuple[dict[str, bytes], list[dict]]:
    """반출 대상 파일(경로→바이트)과 산출물 목록. build_bundle 과 파일 하나 내려받기가 함께 쓴다."""
    include = include or {"rag", "datasets", "model"}
    files: dict[str, bytes] = {}
    artifacts: list[dict] = []

    from zzaimy.app.embed_search import INDEX_PATH, MODEL_NAME

    # RAG
    if "rag" in include:
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
        if Path(INDEX_PATH).exists():
            files["rag/chunk_embeddings.npz"] = Path(INDEX_PATH).read_bytes()
        artifacts.append({
            "kind": "rag_index", "format": "jsonl + npz(numpy)",
            "n_chunks": len(chunks), "embedding_model": MODEL_NAME,
            "vectors_included": Path(INDEX_PATH).exists(),
            "reproduce": "chunks.jsonl을 embedding_model로 임베딩해 재색인",
        })

    # 학습 데이터 (근거·구조·맥락·개인정보 검수 승인 및 자동 검사 통과분)
    if "datasets" in include:
        sft = Path(sft_dir)
        datasets = []
        if sft.exists():
            for jf in sorted(sft.glob("*.jsonl")):
                try:
                    raw = approved_bytes(jf)
                except ValueError:
                    continue
                files[f"datasets/{jf.name}"] = raw
                n = len(raw.splitlines())
                datasets.append({"file": jf.name, "n_pairs": n})
        if datasets:
            artifacts.append({
                "kind": "training_data", "format": "jsonl(sharegpt)",
                "files": datasets, "note": "파일별 근거·구조·맥락·개인정보 검수 승인 및 자동 검사 통과분. 통합 학습 전 사업별 분리·중복 재검증 필요",
            })

    # 모델 (있을 때만 — DGX 학습 후)
    if "model" in include and model_dir:
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

    return files, artifacts


def single_file(db, path: str, sft_dir: Path | str = "data/interim/sft",
                model_dir: Path | str | None = None) -> bytes | None:
    """반출 목록에 있는 파일 하나의 바이트. 목록 밖 경로는 None(경로 조작 차단)."""
    group = path.split("/", 1)[0]
    if group not in ("rag", "datasets", "model"):
        return None
    files, _ = collect_files(db, sft_dir=sft_dir, model_dir=model_dir, include={group})
    return files.get(path)


def build_bundle(db, sft_dir: Path | str = "data/interim/sft",
                 model_dir: Path | str | None = None,
                 include: set[str] | None = None) -> tuple[bytes, dict]:
    """반출 zip 바이트와 매니페스트를 만든다.

    include로 담을 항목을 고른다(기본 전체): {"rag", "datasets", "model"}.
    - RAG: 조각 JSONL + 임베딩 npz + 임베딩 모델 id
    - 학습 데이터: data/interim/sft/*.jsonl (이미 마스킹·검증 통과분)
    - 모델: model_dir이 주어지고 존재하면 그 안의 safetensors·config 포함
      (LoRA 어댑터 권장 — 베이스는 오픈웨이트 참조)
    """
    from zzaimy.app.embed_search import MODEL_NAME

    files, artifacts = collect_files(db, sft_dir=sft_dir, model_dir=model_dir, include=include)

    manifest = {
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(
            timespec="seconds"),
        "platform": "ZZAIMY",
        "git_commit": _git_commit(),
        "artifacts": artifacts,
        "base_models": {
            "embedding": MODEL_NAME,
            "reranker": "BAAI/bge-reranker-v2-m3",
            "writer_base": _writer_model(),
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
