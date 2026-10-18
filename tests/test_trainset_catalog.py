"""DGX 학습셋 목록 — 설명 정보만 모아(197) VM 화면이 읽는다(export.bundle.trainsets). 문답 원문은 목록에 들지 않는다."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _cat():
    spec = importlib.util.spec_from_file_location("s197", ROOT / "scripts" / "197_trainset_catalog.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_catalog_keeps_metadata_only_and_screen_reads_it(tmp_path):
    train = tmp_path / "sft-ready-x" / "data" / "train"
    st = train / "staging" / "set-a"
    st.mkdir(parents=True)
    (st / "train.jsonl").write_text(json.dumps({"messages": [{"role": "user", "content": "비밀 문답 원문"}]}, ensure_ascii=False) + "\n")
    (st / "manifest.json").write_text(json.dumps({"status": "candidate_not_sft_approved", "required_before_training": ["baseline"],
                                                  "counts": {"train": {"rows": 1, "tracks": {"policy": 1}, "sha256": "aa"}}}))
    (train / "releases").mkdir()
    (train / "releases" / "writer-v1-approval.json").write_text(json.dumps({"status": "sft_approved", "scope": "controlled",
                                                                           "counts": {"train": 1}, "training_started": False,
                                                                           "review": {"limitations": "AI 검토"}}))
    out = tmp_path / "trainsets.json"
    m = _cat()
    items = m.collect([train])
    out.write_text(json.dumps({"at": "2026-10-18 10:00", "items": items}, ensure_ascii=False))
    assert "비밀 문답 원문" not in out.read_text()

    from zzaimy.export.bundle import trainsets
    got = trainsets(out)
    assert [g["kind"] for g in got] == ["release", "staging"]
    assert got[0]["status_label"] == "SFT 승인" and got[0]["training_started"] is False and "학습 1" in got[0]["summary"]
    assert got[1]["status_label"] == "후보(승인 전)" and got[1]["tracks"] == {"policy": 1} and got[1]["required"] == ["baseline"]
    assert trainsets(tmp_path / "없음.json") == []
