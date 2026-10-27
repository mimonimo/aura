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


def test_from_db_judges_documents_missing_from_parsed_files(tmp_path, monkeypatch):
    """처리 결과 파일이 생기기 전에 들인 DGX 보관 문서도 --from-db 면 문서함 본문으로 판정한다 — 판은 원본 장부의 크기:시각."""
    from zzaimy.app import archive
    from zzaimy.app.db import Database
    m = _load()
    db = Database(tmp_path / "p.db")
    archive.ensure(db)
    ids = {n: db.add_document(f"{n}.pdf", f"dgx://x/{n}.pdf") for n in ("orphan", "parsed", "ruled", "gone", "hand")}
    with db._conn() as conn:
        for n, did in ids.items():
            conn.execute("UPDATE documents SET status = 'reviewed', masked_text = ? WHERE id = ?", (f"{n} 본문", did))
            if n != "gone":
                conn.execute("INSERT INTO archive_files (rel, size, mtime) VALUES (?, 10, 20)", (f"x/{n}.pdf",))
        conn.execute("UPDATE documents SET kind = 'plan' WHERE id = ?", (ids["ruled"],))
        conn.execute("UPDATE documents SET identity = ? WHERE id = ?", (json.dumps({"kind_by_hand": True}), ids["hand"]))
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    (parsed / "parsed-0.jsonl").write_text(json.dumps({"rel": "x/parsed.pdf", "state": "parsed", "masked_text": "본문", "version": "10:20",
                                                       "filename": "parsed.pdf"}) + "\n", encoding="utf-8")
    seen: dict[str, str] = {}

    def judge(complete, title, text, folder=""):
        seen[title] = text
        return ("report", "근거")

    monkeypatch.setattr(m.kind_read, "judge", judge)
    monkeypatch.setattr(m.s190, "make_complete", lambda url, model, role: (lambda msgs: "", {"name": "t"}))
    monkeypatch.setenv("ZZAIMY_PLATFORM_SQLITE_PATH", str(tmp_path / "p.db"))
    monkeypatch.setattr(sys, "argv", ["191", "--parsed", str(parsed), "--out-name", "kind27b-vm.jsonl"])
    assert m.main() == 0
    assert set(seen) == {"parsed"}                       # 기본은 처리 결과 파일만
    seen.clear()
    monkeypatch.setattr(sys, "argv", ["191", "--parsed", str(parsed), "--out-name", "kind27b-vm.jsonl", "--from-db"])
    assert m.main() == 0
    assert seen == {"orphan": "orphan 본문"}             # 갈래 있음·지워진 원본·담당자가 고친 갈래·이미 판정한 것은 뺀다
    got = [json.loads(l) for l in (parsed / "kind27b-vm.jsonl").read_text(encoding="utf-8").splitlines()]
    row = next(g for g in got if g["rel"] == "x/orphan.pdf")
    assert row["version"] == "10:20" and row["kind"] == "report"
    # 192 가 같은 판으로 받아 반영한다
    spec = importlib.util.spec_from_file_location("s192", ROOT / "scripts" / "192_kind_apply.py")
    s192 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s192)
    assert (ids["orphan"], "", "report") in s192.plan(db, s192.load([str(parsed / "kind27b-vm.jsonl")]))
