"""DGX 학습셋 파일(197 이 ~/parsed/trainsets 로 복사, VM 이 받음) — 개발자만 목록의 이름으로 내려받고 표본을 본다."""
from __future__ import annotations

import json
from pathlib import Path

from zzaimy.export import bundle


def _setup(tmp_path, monkeypatch):
    d = tmp_path / "trainsets" / "set-a"
    d.mkdir(parents=True)
    rows = [{"prompt": [{"role": "system", "content": "지시"}, {"role": "user", "content": f"질문 {i}"}],
             "completion": [{"role": "assistant", "content": f"답 {i}"}]} for i in range(25)]
    (d / "train.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    (tmp_path / "trainsets.json").write_text(json.dumps({"at": "x", "items": [
        {"kind": "staging", "name": "set-a", "current": True, "status": "candidate_not_sft_approved", "mirrored": ["train.jsonl", "secret.jsonl"]},
    ]}), encoding="utf-8")
    monkeypatch.setattr(bundle, "TRAINSETS", tmp_path / "trainsets.json")
    monkeypatch.setattr(bundle, "TRAINSET_DIR", tmp_path / "trainsets")


def test_only_listed_existing_files_and_no_paths(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert bundle.trainsets()[0]["files"] == ["train.jsonl"]          # 목록에 있어도 실제로 없는 파일은 빠진다
    assert bundle.trainset_file("set-a", "train.jsonl") is not None
    for name, f in (("set-a", "secret.jsonl"), ("set-a", "../trainsets.json"), ("..", "train.jsonl"), ("set-b", "train.jsonl")):
        assert bundle.trainset_file(name, f) is None


def test_sample_pages(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    rows, total = bundle.trainset_sample(bundle.trainset_file("set-a", "train.jsonl"), 20, 20)
    assert total == 25 and len(rows) == 5 and rows[0]["prompt"][1]["content"] == "질문 20"
