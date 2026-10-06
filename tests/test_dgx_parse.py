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

    def is_alive(self):
        return False


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


def test_mineru_runs_at_most_slots_at_once(tmp_path, monkeypatch):
    import threading
    import time as _t

    job = _load("167_dgx_parse.py")
    monkeypatch.setattr(job, "MINERU_SLOTS", 2)
    from zzaimy.ingest.parsers import mineru as m

    live, peak = [0], [0]
    lock = threading.Lock()

    def fake(self, *a, **kw):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        _t.sleep(0.2)
        with lock:
            live[0] -= 1
        return "ok"

    monkeypatch.setattr(m.MineruParser, "parse", fake)
    job._limit_mineru()
    ts = [threading.Thread(target=lambda: m.MineruParser().parse(None)) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert peak[0] == 2


def test_waits_while_memory_is_low(monkeypatch):
    job = _load("167_dgx_parse.py")
    free = [5.0]
    monkeypatch.setattr(job, "_mem_available_gb", lambda: free[0])
    slept = []

    def sleep(s):
        slept.append(s)
        if len(slept) == 2:
            free[0] = 100.0                          # 두 번 기다린 뒤 여유가 생김

    monkeypatch.setattr(job.time, "sleep", sleep)
    job._wait_for_memory(0)
    assert slept == [15, 15]


def test_forced_reparse_updates_same_document_even_with_same_version(tmp_path, monkeypatch):
    db = _import(tmp_path, monkeypatch, [_rec()], "parsed-0.jsonl")
    did = next(d["id"] for d in db.list_documents() if d["stored_path"] == "dgx://p/a.hwp")
    same = _rec(chunks=[{"seq": 0, "kind": "text", "content": "kordoc 으로 다시"}])
    db = _import(tmp_path, monkeypatch, [same], "parsed-1.jsonl")
    assert [c["content"] for c in db.list_doc_chunks(did)] == ["첫 판"]                 # 표시 없으면 건너뜀
    db = _import(tmp_path, monkeypatch, [{**same, "force": True}], "parsed-2.jsonl")
    assert [c["content"] for c in db.list_doc_chunks(did)] == ["kordoc 으로 다시"]
    assert [d["id"] for d in db.list_documents() if d["stored_path"] == "dgx://p/a.hwp"] == [did]



def test_worker_over_memory_limit_is_respawned_from_progress(tmp_path, monkeypatch, capsys):
    job = _load("167_dgx_parse.py")
    monkeypatch.setattr(job.time, "sleep", lambda s: None)
    out = tmp_path / "out"
    starts = []

    class _Recycling(_DeadWorker):
        def __init__(self, target=None, args=()):
            super().__init__()
            self.args = args

        def start(self):
            n, items, _root, out_dir, _t, start = self.args
            starts.append(start)
            work = Path(out_dir) / f"w{n}"
            work.mkdir(parents=True, exist_ok=True)
            with (Path(out_dir) / f"parsed-{n}.jsonl").open("a", encoding="utf-8") as fh:
                it = items[start]
                fh.write(json.dumps({"rel": it["rel"], "ok": True, "state": "parsed", "size": it["size"], "mtime": 1}) + "\n")
            (work / "progress").write_text(str(start + 1))
            self.exitcode = job.RECYCLE if start + 1 < len(items) else 0   # 한 건마다 메모리 상한에 걸린 셈

    monkeypatch.setattr(job.mp, "Process", _Recycling)
    monkeypatch.setattr(sys, "argv", ["167", "--inventory", str(_inventory(tmp_path, 3)), "--out", str(out), "--workers", "1"])
    assert job.main() == 0
    assert starts == [0, 1, 2]
    assert "PARSE_DONE" in capsys.readouterr().out


def test_forced_reparse_is_not_blocked_by_its_own_earlier_scratch_record(tmp_path, monkeypatch):
    """작업자 임시 DB 에 지난 실행 기록이 남으면 같은 파일이 자기와 「같은 내용」으로 걸린다(10/4 kordoc 재처리 762건)."""
    import hashlib

    from zzaimy.app import pipeline

    job = _load("167_dgx_parse.py")
    monkeypatch.setattr(job, "_limit_mineru", lambda: None)
    monkeypatch.setattr(job, "_wait_for_memory", lambda n: None)
    root = tmp_path / "root"
    (root / "p").mkdir(parents=True)
    (root / "p" / "a.hwp").write_bytes(b"same bytes")

    class _Proc:
        def process(self, db, did, path):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            db.record_content_hash(did, digest)
            if db.find_same_content(digest, exclude_id=did):
                db.update_document(did, status="failed", error="같은 내용의 문서가 이미 있습니다")
                return
            db.update_document(did, status="reviewed", masked_text="본문")
            db.replace_doc_chunks(did, [{"seq": 0, "kind": "text", "content": "본문"}])

    monkeypatch.setattr(pipeline, "DocumentProcessor", _Proc)
    out = tmp_path / "out"
    item = {"rel": "p/a.hwp", "ext": "hwp", "size": 10, "mtime": 1, "force": True}
    for _ in range(2):
        job.worker(0, [item], str(root), str(out), 60)
    recs = [json.loads(l) for l in (out / "parsed-0.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["state"] for r in recs] == ["parsed", "parsed"]


def test_jsonl_lines_keep_records_with_form_feed_in_text(tmp_path):
    """본문에 폼피드가 든 기록이 splitlines 로 깨져 「이미 처리」에서 빠지던 일(10/5)."""
    job = _load("167_dgx_parse.py")
    p = tmp_path / "parsed-0.jsonl"
    recs = [{"rel": "a.pdf", "ok": True, "masked_text": "쪽1\x0c쪽2 끝"}, {"rel": "b.pdf", "ok": True}]
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    assert [json.loads(x)["rel"] for x in job.jsonl_lines(p)] == ["a.pdf", "b.pdf"]


def test_rels_mode_reimports_missed_records_ignoring_offsets(tmp_path, monkeypatch):
    """읽은 자리를 지나쳤지만 문서함에 없는 원본을 --rels 로 다시 들인다(10/6: 1,133건). 다른 원본은 건드리지 않고 읽은 자리도 그대로."""
    job = _load("168_import_parsed.py")
    monkeypatch.setattr(job, "ROOT", tmp_path)
    (tmp_path / "data" / "platform").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ZZAIMY_PLATFORM_SQLITE_PATH", str(tmp_path / "t.db"))
    f = tmp_path / "parsed-0.jsonl"
    f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in
                         [_rec(rel="p/빠진 \"문서\".pdf", filename="b.pdf"), _rec(rel="p/c.hwp", filename="c.hwp")]), encoding="utf-8")
    off = tmp_path / "data" / "inbox" / "parsed" / ".offsets.json"
    off.parent.mkdir(parents=True, exist_ok=True)
    off.write_text(json.dumps({str(f): f.stat().st_size}), encoding="utf-8")      # 이미 다 읽었다고 적힌 자리
    want = tmp_path / "rels.json"
    want.write_text(json.dumps(["p/빠진 \"문서\".pdf"], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["168", "--rels", str(want), str(f)])
    assert job.main() == 0
    paths = {d["stored_path"] for d in Database(tmp_path / "t.db").list_documents()}
    assert paths == {"dgx://p/빠진 \"문서\".pdf"}
    assert json.loads(off.read_text(encoding="utf-8")) == {str(f): f.stat().st_size}


def test_environment_failures_are_retried_automatically(tmp_path, monkeypatch, capsys):
    job = _load("167_dgx_parse.py")
    out = tmp_path / "out"
    out.mkdir()
    (out / "parsed-0.jsonl").write_text(
        json.dumps({"rel": "p/0.hwp", "ok": False, "state": "failed", "size": 10, "mtime": 1, "version": "10:1",
                    "error": "RuntimeError: 문서 판독 실패 (ModuleNotFoundError)"}, ensure_ascii=False) + "\n"
        + json.dumps({"rel": "p/1.hwp", "ok": False, "state": "failed", "size": 11, "mtime": 1, "version": "11:1",
                      "error": "RuntimeError: HWP 형식이 아닙니다"}, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["167", "--inventory", str(_inventory(tmp_path, 2)), "--out", str(out), "--workers", "1", "--dry-run"])
    assert job.main() == 0
    got = capsys.readouterr().out
    assert "대상 1건" in got and "p/0.hwp" in got and "p/1.hwp" not in got


def test_nul_bytes_are_stripped_and_one_bad_record_does_not_stop_import(tmp_path, monkeypatch):
    db = _import(tmp_path, monkeypatch, [_rec(rel="p/nul.pdf", chunks=[{"seq": 0, "kind": "text", "content": "앞\x00뒤"}]),
                                         _rec(rel="p/next.hwp")], "parsed-0.jsonl")
    docs = {d["stored_path"]: d for d in db.list_documents()}
    assert {"dgx://p/nul.pdf", "dgx://p/next.hwp"} <= set(docs)
    assert [c["content"] for c in db.list_doc_chunks(docs["dgx://p/nul.pdf"]["id"])] == ["앞뒤"]
