"""학습 산출물 반출 번들 (ADR-0012) — 개방 표준·매니페스트·재조립 가능성."""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from zzaimy.app.db import Database
from zzaimy.export.bundle import build_bundle


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return Database(tmp_path / "e.db")


def test_bundle_has_manifest_and_rag(db, tmp_path):
    doc = db.add_document("학칙.pdf", "/x", doc_type="regulation")
    db.add_regulation_chunks(
        doc, "학칙", [SimpleNamespace(heading="1", content="휴학 규정")], dept="공통")

    data, manifest = build_bundle(db)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        assert "manifest.json" in names
        assert "rag/chunks.jsonl" in names
        m = json.loads(z.read("manifest.json"))
        assert m["portability"].startswith("개방 표준")
        assert any(a["kind"] == "rag_index" for a in m["artifacts"])
        # 조각 원문이 재색인용으로 들어 있다
        chunks = z.read("rag/chunks.jsonl").decode("utf-8")
        assert "휴학 규정" in chunks


def test_bundle_includes_datasets(db, tmp_path):
    from pathlib import Path
    sft = Path("data/interim/sft")
    sft.mkdir(parents=True)
    (sft / "set1.jsonl").write_text(
        '{"conversations": [{"from":"human","value":"q"},'
        '{"from":"gpt","value":"a"}]}\n', encoding="utf-8")

    _data, manifest = build_bundle(db)
    kinds = {a["kind"] for a in manifest["artifacts"]}
    assert "training_data" in kinds


def test_bundle_includes_model_when_present(db, tmp_path):
    from pathlib import Path
    md = tmp_path / "model"
    md.mkdir()
    (md / "adapter_config.json").write_text('{"base": "Qwen/Qwen3.8-27B"}',
                                            encoding="utf-8")
    (md / "adapter_model.safetensors").write_bytes(b"\x00\x01")

    data, manifest = build_bundle(db, model_dir=md)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert any(n.startswith("model/") for n in z.namelist())
    assert any(a["kind"] == "model" for a in manifest["artifacts"])


def test_manifest_names_base_models(db):
    _data, manifest = build_bundle(db)
    assert manifest["base_models"]["writer_base"] == "Qwen/Qwen3.8-27B"
    assert "embedding" in manifest["base_models"]
