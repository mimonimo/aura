"""DGX 대량 추출(167)·VM 들이기(168) 신뢰성 — C-183. 실제 원본·DGX·VM 없이 임시 파일과 가짜 작업자로."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from zzaimy.app.db import Database

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), ROOT / "scripts" / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _inventory(tmp_path, n=2):
    inv = tmp_path / "inv.jsonl"
    inv.write_text("".join(json.dumps({"rel": f"p/{i}.hwp", "ext": "hwp", "size": 10 + i, "mtime": 1, "kind": "plan"}) + "\n"
                           for i in range(n)), encoding="utf-8")
    return inv


class _DeadWorker:
    """아무것도 쓰지 않고 비정상 종료하는 작업자."""
    def __init__(self, target=None, args=()):
        self.exitcode = None

    def start(self):
        self.exitcode = 9

    def join(self):
        pass


def test_parse_reports_incomplete_when_worker_dies(tmp_path, monkeypatch, capsys):
    job = _load("167_dgx_parse.py")
    monkeypatch.setattr(job.mp, "Process", _DeadWorker)
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["167", "--inventory", str(_inventory(tmp_path)), "--out", str(out), "--workers", "2"])
    assert job.main() == 1
    text = capsys.readouterr().out
    assert "PARSE_INCOMPLETE" in text and "PARSE_DONE" not in text and "미처리 2" in text


def test_parse_rejects_zero_workers(tmp_path, monkeypatch):
    job = _load("167_dgx_parse.py")
    monkeypatch.setattr(sys, "argv", ["167", "--inventory", str(_inventory(tmp_path)), "--out", str(tmp_path / "o"), "--workers", "0"])
    with pytest.raises(SystemExit):
        job.main()


def test_changed_version_is_parsed_again(tmp_path, monkeypatch, capsys):
    job = _load("167_dgx_parse.py")
    out = tmp_path / "out"
    out.mkdir()
    # 0번은 같은 판으로 이미 처리됨, 1번은 예전 판(크기 다름)으로 처리됨 → 1번만 다시
    (out / "parsed-0.jsonl").write_text(json.dumps({"rel": "p/0.hwp", "ok": True, "size": 10, "mtime": 1}) + "\n"
                                        + json.dumps({"rel": "p/1.hwp", "ok": True, "size": 99, "mtime": 1}) + "\n", encoding="utf-8")
    seen = []

    class _Recorder(_DeadWorker):
        def __init__(self, target=None, args=()):
            super().__init__()
            seen.extend(it["rel"] for it in args[1])

    monkeypatch.setattr(job.mp, "Process", _Recorder)
    monkeypatch.setattr(sys, "argv", ["167", "--inventory", str(_inventory(tmp_path)), "--out", str(out), "--workers", "1"])
    job.main()
    assert seen == ["p/1.hwp"]


def _import(tmp_path, monkeypatch, recs, name):
    job = _load("168_import_parsed.py")
    monkeypatch.setattr(job, "ROOT", tmp_path)
    (tmp_path / "data" / "platform").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ZZAIMY_PLATFORM_SQLITE_PATH", str(tmp_path / "t.db"))
    f = tmp_path / name
    f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["168", str(f)])
    assert job.main() == 0
    return Database(tmp_path / "t.db")


def _rec(**kw):
    base = {"rel": "p/a.hwp", "ok": True, "state": "parsed", "size": 10, "mtime": 1, "filename": "a.hwp", "program_name": "시험",
            "chunks": [{"seq": 0, "kind": "text", "content": "첫 판"}]}
    base.update(kw)
    return base


def test_import_updates_same_document_for_new_version_and_marks_state(tmp_path, monkeypatch):
    db = _import(tmp_path, monkeypatch, [_rec()], "parsed-0.jsonl")
    docs = [d for d in db.list_documents() if d["stored_path"] == "dgx://p/a.hwp"]
    assert len(docs) == 1
    did = docs[0]["id"]
    assert "OCR 품질 미검사" in (db.get_document(did)["parse_note"] or "")
    new = _rec(size=20, state="partial", truncated=True, text_len=3_000_000, chunks=[{"seq": 0, "kind": "text", "content": "새 판"}])
    db = _import(tmp_path, monkeypatch, [new], "parsed-1.jsonl")
    docs = [d for d in db.list_documents() if d["stored_path"] == "dgx://p/a.hwp"]
    assert [d["id"] for d in docs] == [did]                        # 같은 문서 번호
    assert [c["content"] for c in db.list_doc_chunks(did)] == ["새 판"]
    note = db.get_document(did)["parse_note"]
    assert "일부만 읽음" in note and "본문 잘림" in note


def test_import_skips_failed_and_empty_states(tmp_path, monkeypatch):
    db = _import(tmp_path, monkeypatch, [_rec(rel="p/x.pdf", ok=False, state="failed", chunks=[]),
                                         _rec(rel="p/y.pdf", state="empty", chunks=[])], "parsed-0.jsonl")
    assert not [d for d in db.list_documents() if str(d["stored_path"]).startswith("dgx://")]


def test_old_version_read_later_does_not_revert(tmp_path, monkeypatch):
    from zzaimy.app import archive
    db = Database(tmp_path / "t.db")
    archive.load(db, [dict(rel="p/a.hwp", size=20, mtime=1)], {})      # 장부의 현재 판 = 20:1
    new = _rec(size=20, chunks=[{"seq": 0, "kind": "text", "content": "새 판"}])
    old = _rec(size=10, chunks=[{"seq": 0, "kind": "text", "content": "옛 판"}])
    db = _import(tmp_path, monkeypatch, [new, old], "parsed-0.jsonl")    # 새 판이 먼저, 옛 판이 뒤에 읽힘
    did = next(d["id"] for d in db.list_documents() if d["stored_path"] == "dgx://p/a.hwp")
    assert [c["content"] for c in db.list_doc_chunks(did)] == ["새 판"]


def test_rerun_after_crash_reuses_document(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    did = db.add_document("a.hwp", "dgx://p/a.hwp", doc_type="grant")   # 문서는 만들었고 장부 줄은 못 쓴 채 멈춤
    db = _import(tmp_path, monkeypatch, [_rec()], "parsed-0.jsonl")
    assert [d["id"] for d in db.list_documents() if d["stored_path"] == "dgx://p/a.hwp"] == [did]
