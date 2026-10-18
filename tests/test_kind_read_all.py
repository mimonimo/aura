"""서류 갈래 일괄 판정(scripts/191) — 어느 판정 파일에 있든 같은 판은 다시 하지 않고, 실패는 적어 두어 되풀이하지 않는다."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("s191", ROOT / "scripts" / "191_kind_read_all.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_skips_judged_anywhere_and_remembers_failures(tmp_path, monkeypatch):
    m = _load()
    rows = [{"rel": f"a/{n}.hwp", "state": "parsed", "masked_text": "본문", "version": "v1", "filename": f"{n}.hwp"} for n in ("old", "new", "bad")]
    (tmp_path / "parsed-0.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    # DGX 일괄 판정 파일에 이미 있는 판
    (tmp_path / "kind27b-0.jsonl").write_text(json.dumps({"rel": "a/old.hwp", "version": "v1", "kind": "plan"}) + "\n", encoding="utf-8")
    seen: list[str] = []

    def judge(complete, title, text, folder=""):
        seen.append(title)
        return ("report", "근거") if title == "new" else ("", "모름")

    monkeypatch.setattr(m.kind_read, "judge", judge)
    monkeypatch.setattr(m.s190, "make_complete", lambda url, model, role: (lambda msgs: "", {"name": "t"}))
    monkeypatch.setattr(sys, "argv", ["191", "--parsed", str(tmp_path), "--out-name", "kind27b-vm.jsonl"])
    assert m.main() == 0
    assert sorted(seen) == ["bad", "new"]
    got = [json.loads(l) for l in (tmp_path / "kind27b-vm.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [g["rel"] for g in got] == ["a/new.hwp"] and got[0]["kind"] == "report"
    # 다음 회차: 판정한 것도, 실패로 적어 둔 것도 다시 보지 않는다
    seen.clear()
    assert m.main() == 0
    assert seen == []


def test_meta_scan_reads_only_new_lines(tmp_path):
    m = _load()
    f = tmp_path / "parsed-0.jsonl"
    f.write_text(json.dumps({"rel": "a", "state": "parsed", "masked_text": "본문 a", "version": "1"}) + "\n", encoding="utf-8")
    first = m.scan_meta(str(tmp_path))
    assert set(first) == {"a"} and m.load_text(str(tmp_path), first["a"])["masked_text"] == "본문 a"
    with f.open("a", encoding="utf-8") as fh:            # 새 줄 + 쓰는 중인 줄(줄바꿈 없음)
        fh.write(json.dumps({"rel": "b", "state": "parsed", "masked_text": "본문 b", "version": "1"}) + "\n")
        fh.write('{"rel": "c", "state": "par')
    again = m.scan_meta(str(tmp_path))
    assert set(again) == {"a", "b"} and m.load_text(str(tmp_path), again["b"])["masked_text"] == "본문 b"
