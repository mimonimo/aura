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


def test_catalog_shows_latest_folder_and_links_approval_to_set(tmp_path):
    import os
    m = _cat()

    def folder(name, status, sha, when):
        train = tmp_path / name / "data" / "train"
        st = train / "staging" / f"set-{name}"
        st.mkdir(parents=True)
        (st / "manifest.json").write_text(json.dumps({"status": "candidate_not_sft_approved", "required_before_training": ["baseline"],
                                                      "counts": {"train": {"rows": 1, "tracks": {}, "sha256": sha}}}))
        (train / "releases").mkdir()
        rel = train / "releases" / "writer-v1-approval.json"
        rel.write_text(json.dumps({"status": status, "train_sha256": sha, "training_started": False}))
        for f in (rel, st / "manifest.json"):
            os.utime(f, (when, when))
        return train

    old = folder("sft-ready-old", "quality_review_pending", "aa", 1_000)
    new = folder("sft-ready-new", "sft_approved", "bb", 2_000)
    (new / "models" / "writer" / "v1-adapter" / "checkpoint-20").mkdir(parents=True)
    out = tmp_path / "trainsets.json"
    out.write_text(json.dumps({"at": "2026-10-18 15:00", "items": m.collect([old, new])}, ensure_ascii=False))

    from zzaimy.export.bundle import trainsets
    got = trainsets(out)
    assert {g["root"] for g in got} == {"sft-ready-new"} and got[0]["older"] == 2
    rel = next(g for g in got if g["kind"] == "release")
    st = next(g for g in got if g["kind"] == "staging")
    assert rel["status_label"] == "SFT 승인" and rel["training_label"].startswith("학습 멈춤")
    assert st["status_label"] == "SFT 승인본" and st["required"] == []


def test_mirror_copies_current_set_files_only(tmp_path):
    m = _cat()
    train = tmp_path / "sft-ready-x" / "data" / "train"
    st = train / "staging" / "set-a"
    st.mkdir(parents=True)
    (st / "train.jsonl").write_text("{}\n")
    (st / "manifest.json").write_text("{}")
    dest = tmp_path / "parsed" / "trainsets"
    (dest / "old-set").mkdir(parents=True)
    items = [{"kind": "staging", "name": "set-a", "current": True}, {"kind": "staging", "name": "old", "current": False}]
    assert m.mirror_sets(items, [train], dest) == 2
    assert sorted(p.name for p in (dest / "set-a").iterdir()) == ["manifest.json", "train.jsonl"]
    assert not (dest / "old-set").exists() and items[0]["mirrored"] == ["manifest.json", "train.jsonl"]
    assert m.mirror_sets(items, [train], dest) == 0          # 바뀐 게 없으면 다시 복사하지 않는다
