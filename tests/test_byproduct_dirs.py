"""부산물 폴더(변환기 출력·웹 페이지 저장 자료) 판정과 처리 단계(167·168·170·175)·소급 정리(183), 장부 실패 표시 빈틈 — 가짜 경로만 쓴다."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from zzaimy import doc_formats
from zzaimy.app import archive, archive_state
from zzaimy.app.db import Database

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace(".", "_") + "_bp", ROOT / "scripts" / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 판정 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    "가상과/명단 보기_files/sheet001.htm",            # 브라우저 저장
    "가상과/명단 보기.files/sheet001.htm",            # 오피스(엑셀)를 웹 페이지로 저장
    "가상과/깊은/검색 결과-2_files/outer.html",
    "가상과/검색_files/하위/그림.png",                 # 자료 폴더 아래 더 깊은 곳
    "가상과/계획서_imgs/p1.png",                       # 변환기 출력
    "가상과/계획서_kordoc/본문.md",
])
def test_byproduct_paths(rel):
    assert doc_formats.is_byproduct(rel)
    assert not archive_state.is_original(rel)


@pytest.mark.parametrize("rel", [
    "가상과/명단 보기.htm",                            # 본문 페이지 자체는 원본
    "가상과/files/계획.hwp",                           # 이름이 files 인 폴더(접미 아님)
    "가상과/upload_files.hwp",                         # 파일 이름의 접미는 보지 않는다
    "가상과/자료 files 모음/계획.pdf",
    "sheet001.htm",
])
def test_not_byproduct_paths(rel):
    assert not doc_formats.is_byproduct(rel)
    assert archive_state.is_original(rel)


def test_sql_rule_matches_python_rule(tmp_path):
    db = Database(tmp_path / "t.db")
    rels = ["가/보고_files/sheet001.htm", "가/보고.files/tabstrip.htm", "가/보고.htm", "가/files/a.hwp", "가/a_imgs/p.png",
            "가/x_filesy/a.hwp", archive.UPLOAD_PREFIX + "a.hwp"]
    archive.load(db, [{"rel": r, "ext": doc_formats.file_ext(r)} for r in rels])
    cond, args = archive_state.original_sql()
    with db._conn() as conn:
        got = {r[0] for r in conn.execute(f"SELECT rel FROM archive_files WHERE {cond}", args).fetchall()}
    assert got == {r for r in rels if archive_state.is_original(r)} == {"가/보고.htm", "가/files/a.hwp", "가/x_filesy/a.hwp"}


# ── 167: 대상에서 빼기·empty 사유 ─────────────────────────────────────────

class _Recorder:
    seen: list = []

    def __init__(self, target=None, args=()):
        _Recorder.seen.extend(it["rel"] for it in args[1])
        self.exitcode = None

    def start(self):
        self.exitcode = 9

    def join(self):
        pass

    def is_alive(self):
        return False


def test_dgx_parse_skips_byproduct_dirs(tmp_path, monkeypatch):
    job = _load("167_dgx_parse.py")
    inv = tmp_path / "inv.jsonl"
    rels = ["p/명단.htm", "p/명단.files/sheet001.htm", "p/검색_files/channel.html", "p/계획_imgs/a.pdf"]
    inv.write_text("".join(json.dumps({"rel": r, "ext": doc_formats.file_ext(r), "size": 10, "mtime": 1}) + "\n" for r in rels),
                   encoding="utf-8")
    _Recorder.seen = []
    monkeypatch.setattr(job.mp, "Process", _Recorder)
    monkeypatch.setattr(sys, "argv", ["167", "--inventory", str(inv), "--out", str(tmp_path / "out"), "--workers", "1"])
    job.main()
    assert _Recorder.seen == ["p/명단.htm"]


def test_dgx_parse_empty_state_always_has_reason(tmp_path, monkeypatch):
    """처리는 됐지만 조각이 없는 문서 — 처리기가 오류 글을 남기지 않아도 같은 사유 글을 적는다."""
    from zzaimy.app import pipeline

    job = _load("167_dgx_parse.py")
    monkeypatch.setattr(job, "_limit_mineru", lambda: None)
    monkeypatch.setattr(job, "_wait_for_memory", lambda n: None)
    root = tmp_path / "root"
    (root / "p").mkdir(parents=True)
    for name in ("빈 표.html", "빈 글.htm"):
        (root / "p" / name).write_text("<html></html>", encoding="utf-8")

    class _Proc:
        def process(self, db, did, path):
            err = "문서 판독 실패 (글이 없는 문서 — HTML)" if path.suffix == ".html" else None
            db.update_document(did, status="reviewed", masked_text="", error=err)

    monkeypatch.setattr(pipeline, "DocumentProcessor", _Proc)
    out = tmp_path / "out"
    items = [{"rel": "p/빈 표.html", "ext": "html", "size": 1, "mtime": 1}, {"rel": "p/빈 글.htm", "ext": "htm", "size": 1, "mtime": 1}]
    job.worker(0, items, str(root), str(out), 60)
    recs = [json.loads(x) for x in (out / "parsed-0.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["state"] for r in recs] == ["empty", "empty"]
    assert recs[0]["error"] == "문서 판독 실패 (글이 없는 문서 — HTML)"
    assert recs[1]["error"] == doc_formats.empty_reason("빈 글.htm") == "문서 판독 실패 (글이 없는 문서 — HTM)"


def test_old_empty_failures_get_reason(tmp_path):
    p = tmp_path / "parse_failures.jsonl"
    p.write_text(json.dumps({"rel": "가/빈.htm", "state": "empty", "error": ""}, ensure_ascii=False) + "\n"
                 + json.dumps({"rel": "가/깨짐.hwp", "state": "failed", "error": "암호 문서"}, ensure_ascii=False) + "\n",
                 encoding="utf-8")
    got = archive_state.read_failures(p)
    assert got == {"가/빈.htm": "문서 판독 실패 (글이 없는 문서 — HTM)", "가/깨짐.hwp": "암호 문서"}
    audit = _load("175_pipeline_audit.py")
    assert audit.reason(got["가/빈.htm"], "empty") == "글 없는 문서"


# ── 168: 부산물 기록은 들이지 않는다 ─────────────────────────────────────

def test_import_skips_byproduct_records(tmp_path, monkeypatch, capsys):
    job = _load("168_import_parsed.py")
    monkeypatch.setattr(job, "ROOT", tmp_path)
    (tmp_path / "data" / "platform").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ZZAIMY_PLATFORM_SQLITE_PATH", str(tmp_path / "t.db"))
    chunk = [{"seq": 0, "kind": "text", "content": "가상 표"}]
    recs = [{"rel": "p/명단.files/sheet001.htm", "ok": True, "state": "parsed", "size": 1, "mtime": 1, "chunks": chunk},
            {"rel": "p/검색_files/inner.html", "ok": False, "state": "empty", "size": 1, "mtime": 1, "chunks": []},
            {"rel": "p/명단.htm", "ok": True, "state": "parsed", "size": 1, "mtime": 1, "chunks": chunk}]
    f = tmp_path / "parsed-0.jsonl"
    f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["168", str(f)])
    assert job.main() == 0
    paths = {d["stored_path"] for d in Database(tmp_path / "t.db").list_documents()}
    assert paths == {"dgx://p/명단.htm"}
    assert not (tmp_path / "data" / "platform" / "parse_failures.jsonl").read_text(encoding="utf-8").strip()
    assert "부산물 폴더(건너뜀) 2" in capsys.readouterr().out


# ── 170: 새로 들어온 원본에 실패 표시 ─────────────────────────────────────

def test_vm_sync_marks_failures_for_rows_added_after_import(tmp_path):
    """168 이 장부에 없는 원본의 실패를 먼저 적으면(0행 갱신) 170 이 그 원본을 넣을 때 실패를 옮겨 적는다."""
    job = _load("170_vm_sync.py")
    db = Database(tmp_path / "t.db")
    fails = tmp_path / "parse_failures.jsonl"
    fails.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in [
        {"rel": "가/깨진 계획.hwp", "state": "failed", "error": "암호 문서"},
        {"rel": "가/빈 표.htm", "state": "empty", "error": ""},
        {"rel": "가/보고_files/sheet001.htm", "state": "empty", "error": ""},
    ]), encoding="utf-8")
    archive.ensure(db)
    assert archive_state.mark_failures(db, archive_state.read_failures(fails)) == 0      # 장부에 아직 없다 — 0행
    rows = [{"rel": r, "ext": doc_formats.file_ext(r), "size": 1, "mtime": 1}
            for r in ("가/깨진 계획.hwp", "가/빈 표.htm", "가/보고_files/sheet001.htm", "가/멀쩡.pdf")]
    diff = archive.sync(db, rows, {})
    assert job.mark_new_failures(db, diff["added"], fails) == 2
    with db._conn() as conn:
        got = dict(conn.execute("SELECT rel, analysis FROM archive_files").fetchall())
    assert got["가/깨진 계획.hwp"] == "처리 실패: 암호 문서"
    assert got["가/빈 표.htm"] == "처리 실패: 문서 판독 실패 (글이 없는 문서 — HTM)"
    assert got["가/보고_files/sheet001.htm"] == "" and got["가/멀쩡.pdf"] == ""
    st, args = archive_state.state_sql()
    with db._conn() as conn:
        states = dict(conn.execute(f"SELECT rel, {st} FROM archive_files", args).fetchall())
    assert states["가/깨진 계획.hwp"] == states["가/빈 표.htm"] == "failed"


# ── 175: 부산물은 처리 대상이 아니다 ─────────────────────────────────────

def test_audit_counts_byproducts_apart(tmp_path, monkeypatch):
    job = _load("175_pipeline_audit.py")
    plat = tmp_path / "platform"
    plat.mkdir()
    monkeypatch.setattr(job, "PLAT", plat)
    db = Database(plat / "platform.db")
    d = db.add_document("명단.htm", "x", doc_type="grant")
    with db._conn() as conn:
        conn.execute("UPDATE documents SET id = 3 WHERE id = ?", (d,))
    archive.load(db, [{"rel": r, "ext": doc_formats.file_ext(r), "size": 1, "mtime": 1}
                      for r in ("가/명단.htm", "가/명단.files/sheet001.htm", "가/검색_files/outer.html", "가/계획_imgs/p.png")],
                 {"가/명단.htm": 3})
    monkeypatch.setenv("ZZAIMY_PLATFORM_SQLITE_PATH", str(plat / "platform.db"))
    monkeypatch.setattr(sys, "argv", ["175"])
    assert job.main() == 0
    out = json.loads((plat / "pipeline_audit.json").read_text(encoding="utf-8"))
    assert out["targets"] == 1 and out["in_docbox"] == 1 and out["missed"] == 0
    assert out["composition"]["부산물(변환 출력·웹 페이지 저장 자료)"] == 3


# ── 183: 소급 정리 ───────────────────────────────────────────────────────

def test_cleanup_preview_then_apply(tmp_path, monkeypatch):
    job = _load("183_byproduct_cleanup.py")
    db = Database(tmp_path / "platform.db")
    archive.ensure(db)
    keep = db.add_document(filename="명단.htm", stored_path="dgx://가/명단.htm", doc_type="grant")
    part = db.add_document(filename="sheet001.htm", stored_path="dgx://가/명단.files/sheet001.htm", doc_type="grant")
    both = db.add_document(filename="공용.htm", stored_path="dgx://가/공용_files/a.htm", doc_type="grant")
    for d in (keep, part, both):
        db.replace_doc_chunks(d, [{"seq": 0, "kind": "text", "content": "가상 명단 내용"}])
    archive.load(db, [{"rel": r, "ext": doc_formats.file_ext(r)} for r in
                      ("가/명단.htm", "가/명단.files/sheet001.htm", "가/검색_files/channel.html", "가/공용_files/a.htm", "가/공용.htm")],
                 {"가/명단.htm": keep, "가/명단.files/sheet001.htm": part, "가/공용.htm": both})
    with db._conn() as conn:                                     # 규칙 전에 적힌 실패 표시
        conn.execute("UPDATE archive_files SET analysis = '처리 실패: empty' WHERE rel = ?", ("가/검색_files/channel.html",))
    assert archive_state.mark_failures(db, {"가/검색_files/channel.html": "empty"}) == 0   # 이제는 부산물에 적지 않는다
    origins = tmp_path / "origins.jsonl"
    origins.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in [
        {"doc_id": keep, "origin": "가/명단.htm"}, {"doc_id": part, "origin": "가/명단.files/sheet001.htm"},
        {"doc_id": both, "origin": "가/공용.htm"}]) + "깨진 줄\n", encoding="utf-8")

    p = job.plan(db, origins)
    assert p["delete"] == [part] and p["kept"] == [both]
    assert {r[0] for r in p["ledger"]} == {"가/명단.files/sheet001.htm", "가/검색_files/channel.html"}
    assert len(p["origin_drop"]) == 1
    assert db.get_document(part) is not None                    # 미리 보기는 쓰지 않는다

    deleted = []
    from zzaimy.app import storage
    real = storage.remove_intake_dir
    monkeypatch.setattr(storage, "remove_intake_dir", lambda db_, doc, base=None: (deleted.append(doc["id"]), real(db_, doc, base)))
    bk = job.backup(db, p, origins, tmp_path / "bk")
    got = job.apply(db, p, origins)
    assert got == {"deleted": 1, "ledger": 2, "origins": 1}
    assert deleted == [part]                                     # 라이브러리 삭제와 같은 함수
    assert db.get_document(part) is None and not db.list_doc_chunks(part)
    assert db.get_document(keep) and db.get_document(both)
    with db._conn() as conn:
        led = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT rel, doc_id, analysis FROM archive_files").fetchall()}
    assert led["가/명단.files/sheet001.htm"] == (None, job.BYPRODUCT_MARK)
    assert led["가/검색_files/channel.html"] == (None, job.BYPRODUCT_MARK)
    assert led["가/명단.htm"][0] == keep
    lines = origins.read_text(encoding="utf-8").splitlines()
    assert "깨진 줄" in lines and not any("명단.files" in x for x in lines) and len(lines) == 3
    saved = json.loads((bk / "documents.json").read_text(encoding="utf-8"))
    assert [d["id"] for d in saved] == [part] and saved[0]["chunks"]
    assert (bk / "origins.jsonl").read_text(encoding="utf-8").count("\n") == 4
    assert job.plan(db, origins)["delete"] == []                 # 다시 돌려도 지울 것 없음
