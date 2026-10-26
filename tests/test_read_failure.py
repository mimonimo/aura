"""읽지 못한 문서 — 사유 갈래, 올린 사람의 다시 하는 길(파일 바꾸기·다시 읽기·글자 읽기 맡기기·지우기), 알림, 원본 보관소 다시 시도."""
import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_accounts import _login
from tests.test_app import FakeDrafter
from zzaimy.app import archive, archive_cleanup, read_failure
from zzaimy.app.main import create_app

LOCKED = "RuntimeError: 문서 판독 실패 (암호가 걸린 한글 문서 — 배포용 HWPX)"
SCAN = "RuntimeError: 문서 판독 실패 (스캔 PDF — MinerU 판독 없음)"


class FlakyProcessor:
    """파일 내용으로 결과를 정한다 — LOCK 이면 암호 문서, SCAN 이면 스캔본 실패, 그 밖은 읽음."""

    def __init__(self):
        self.calls = []

    def process(self, db, doc_id, file_path):
        self.calls.append(doc_id)
        data = Path(file_path).read_bytes()
        if b"LOCK" in data:
            db.update_document(doc_id, status="failed", error=LOCKED)
        elif b"SCAN" in data:
            db.update_document(doc_id, status="failed", error=SCAN)
        else:
            db.update_document(doc_id, status="reviewed", masked_text="본문", ai_review="")

    def reprocess(self, db, doc_id):
        pass

    def extract_text(self, file_path):
        return ""

    def analyze(self, db, doc_id):
        pass


def _app(tmp_path):
    proc = FlakyProcessor()
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=proc, drafter=FakeDrafter(),
                     password="boot-pass-1")
    return app, proc


def _upload(c, name, data):
    r = c.post("/library/upload", data={"doc_type": "auto"}, files=[("files", (name, io.BytesIO(data), "application/octet-stream"))],
               follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]


def test_classify_matches_audit_labels():
    assert read_failure.reason_label(LOCKED) == "암호 문서"
    assert read_failure.reason_label("RuntimeError: PDF 형식이 아닙니다 (0바이트) — 빈 파일") == "빈 파일"
    assert read_failure.reason_label("HWP 형식이 아닙니다 (12바이트)") == "형식 불일치"
    assert read_failure.reason_label("PDF 가 아니라 웹 페이지가 저장돼 있습니다 (900바이트)") == "형식 불일치"
    assert read_failure.reason_label(SCAN) == "스캔 판독 없음"
    assert read_failure.reason_label("", "empty") == "글 없는 문서"
    assert read_failure.reason_label("KeyError: x") == "기타 판독 실패"
    import importlib.util
    spec = importlib.util.spec_from_file_location("audit175", Path(__file__).resolve().parents[1] / "scripts" / "175_pipeline_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.reason(LOCKED) == "암호 문서" and mod.reason("", "empty") == "글 없는 문서"      # 점검과 화면이 같은 이름


def test_actions_per_reason(tmp_path):
    app, _ = _app(tmp_path)
    db = app.state.db

    def acts(err, name="a.hwpx"):
        did = db.add_document(filename=name, stored_path=str(tmp_path / name))
        db.update_document(did, status="failed", error=err)
        return [a["do"] for a in read_failure.info(db, db.get_document(did))["actions"]]

    assert acts(LOCKED) == ["replace", "discard"]
    assert acts(SCAN, "s.pdf") == ["ocr", "replace", "discard"]
    assert acts("HWPX 형식이 아닙니다 (0바이트) — 빈 파일") == ["replace", "discard"]
    assert acts("HWP 형식이 아닙니다 (12바이트)", "a.hwp") == ["replace", "resave", "discard"]
    assert acts("HWP 형식이 아닙니다 (12바이트)", "a.pdf") == ["replace", "discard"]
    assert acts("KeyError: x", "a.hwp") == ["retry", "resave", "replace", "discard"]
    assert acts("같은 내용의 문서가 이미 있습니다 — #1 계획서") == ["twin", "discard"]
    did = db.add_document(filename="x.pdf", stored_path="dgx://링크/x.pdf")
    db.update_document(did, status="failed", error="KeyError: x")
    assert [a["do"] for a in read_failure.info(db, db.get_document(did))["actions"]] == ["retry"]   # 보관소 문서는 바꾸거나 지우지 않는다
    assert read_failure.info(db, {"id": 1, "status": "reviewed"}) is None


def test_owner_sees_reason_and_replaces_locked_file(tmp_path):
    """암호 문서 — 목록·문서 화면·알림에 「읽지 못함 · 암호 문서」, 암호를 푼 파일로 바꾸면 같은 문서 번호로 다시 읽는다."""
    app, proc = _app(tmp_path)
    db = app.state.db
    c = TestClient(app)
    assert _login(c, "zzaimy", "boot-pass-1")
    _upload(c, "계획서.hwpx", b"PK LOCK")
    did = max(proc.calls)
    assert db.get_document(did)["status"] == "failed"
    page = c.get("/?type=all").text
    assert "읽지 못함 · 암호 문서" in page and f'/doc/{did}#readFix">암호를 푼 파일로 바꾸기' in page
    assert "읽지 못한 문서 1" in page and "읽지 못함 · 암호 문서 — 암호를 푼 파일로 바꾸기" in page     # 종 알림
    doc_page = c.get(f"/doc/{did}").text
    assert 'id="readFix"' in doc_page and f'action="/doc/{did}/replace"' in doc_page and "처리 실패:" not in doc_page
    r = c.post(f"/doc/{did}/replace", files={"file": ("계획서(암호 해제).hwpx", b"PK fine", "x")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/doc/{did}"
    d = db.get_document(did)
    assert d["status"] == "reviewed" and Path(d["stored_path"]).read_bytes() == b"PK fine"
    assert [f["path"] for f in db.list_files(doc_id=did)] == [d["stored_path"]]       # 옛 원본 장부 줄은 남지 않는다
    assert c.post(f"/doc/{did}/replace", files={"file": ("a.hwpx", b"PK", "x")}).status_code == 409   # 읽은 문서는 그대로


def test_replace_rejects_unknown_format_with_readable_message(tmp_path):
    app, proc = _app(tmp_path)
    c = TestClient(app)
    assert _login(c, "zzaimy", "boot-pass-1")
    _upload(c, "a.hwpx", b"PK LOCK")
    did = max(proc.calls)
    r = c.post(f"/doc/{did}/replace", files={"file": ("a.exe", b"MZ", "x")}, follow_redirects=False)
    assert "fix_err=" in r.headers["location"]
    assert "올릴 수 없는 형식입니다" in c.get(r.headers["location"]).text


def test_reread_and_discard_by_owner(tmp_path):
    app, proc = _app(tmp_path)
    db = app.state.db
    c = TestClient(app)
    assert _login(c, "zzaimy", "boot-pass-1")
    _upload(c, "a.hwpx", b"PK LOCK")
    did = max(proc.calls)
    Path(db.get_document(did)["stored_path"]).write_bytes(b"PK ok now")
    assert c.post(f"/doc/{did}/reread", follow_redirects=False).status_code == 303
    assert db.get_document(did)["status"] == "reviewed"
    _upload(c, "b.hwpx", b"PK LOCK")
    did2 = max(proc.calls)
    r = c.post(f"/doc/{did2}/discard", follow_redirects=False)                # 읽지 못한 문서는 올린 사람도 지운다
    assert r.status_code == 303 and "ok=" in r.headers["location"] and db.get_document(did2) is None
    assert c.post(f"/doc/{did}/discard").status_code == 409                    # 읽은 문서 지우기는 관리자만(기존 규칙)


def test_other_user_cannot_fix_my_failed_doc(tmp_path):
    app, proc = _app(tmp_path)
    app.state.accounts["lee"] = app.state.new_account("lee", "pass-12345678", "staff")
    app.state.accounts["lee"]["dept"] = "산학협력단"
    c = TestClient(app)
    assert _login(c, "zzaimy", "boot-pass-1")
    c.post("/library/upload", data={"doc_type": "auto", "dept": "공통"}, files=[("files", ("a.hwpx", io.BytesIO(b"PK LOCK"), "x"))])
    did = max(proc.calls)
    c2 = TestClient(app)
    assert _login(c2, "lee", "pass-12345678")
    assert c2.post(f"/doc/{did}/discard").status_code in (403, 404)


def test_scan_queue_reads_when_ready_and_gives_up(tmp_path, monkeypatch):
    """스캔본 — 글자 읽기 맡기기는 대기열에, 판독이 닿으면 읽는다. 끝내 못 읽으면 횟수를 다 쓰고 멈춘다. 실패하면 저절로 대기열에도."""
    monkeypatch.setenv("ZZAIMY_READ_QUEUE", "0")                              # 앱 안 작업 줄 대신 시험이 직접 돌린다
    app, proc = _app(tmp_path)
    db = app.state.db
    c = TestClient(app)
    assert _login(c, "zzaimy", "boot-pass-1")
    _upload(c, "스캔.pdf", b"%PDF SCAN")
    did = max(proc.calls)
    assert c.post(f"/doc/{did}/ocr", follow_redirects=False).status_code == 303
    info = read_failure.info(db, db.get_document(did))
    assert info["queued"] and "ocr" not in [a["do"] for a in info["actions"]] and "글자 읽기 대기 중" in info["hint"]
    assert read_failure.drain(db, proc, ready=lambda: False) == []            # 판독이 없으면 기다린다
    for _ in range(read_failure.MAX_TRIES):
        read_failure.drain(db, proc, ready=lambda: True)
    assert read_failure.queued(db)[str(did)]["gave_up"]
    assert "번 맡겼지만" in read_failure.info(db, db.get_document(did))["hint"]
    Path(db.get_document(did)["stored_path"]).write_bytes(b"%PDF readable")
    assert read_failure.queue_ocr(db, did, by="zzaimy")                        # 포기한 것도 다시 맡길 수 있다
    assert read_failure.drain(db, proc, ready=lambda: True) == [did]
    assert db.get_document(did)["status"] == "reviewed" and str(did) not in read_failure.queued(db)
    did2 = db.add_document(filename="b.pdf", stored_path=str(tmp_path / "b.pdf"))
    read_failure.after_failure(db, did2, SCAN)
    assert read_failure.queued(db)[str(did2)]["by"] == "자동"
    read_failure.after_failure(db, did2 + 1, LOCKED)
    assert str(did2 + 1) not in read_failure.queued(db)


def test_pipeline_marks_encrypted_hwpx_instead_of_garbage(tmp_path):
    from zzaimy.app.pipeline import DocumentProcessor
    p = tmp_path / "locked.hwpx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", bytes(range(256)) * 4)
    try:
        DocumentProcessor()._parse(p)
        raise AssertionError("깨진 바이트를 본문으로 읽었다")
    except RuntimeError as e:
        assert read_failure.classify(str(e)) == "locked"


def test_pipeline_resaves_unreadable_hwp_with_webhwp(tmp_path, monkeypatch):
    """우리 읽기 도구가 모두 못 읽은 한글은 기안기로 다시 저장한 사본으로 한 번 더 읽는다."""
    from zzaimy.app.pipeline import DocumentProcessor
    bad = tmp_path / "깨짐.hwpx"
    bad.write_bytes(b"PK\x03\x04 broken zip")
    good = tmp_path / "good.hwpx"

    def resave(path):
        with zipfile.ZipFile(good, "w") as z:
            z.writestr("mimetype", "application/hwp+zip")
            z.writestr("Contents/section0.xml", '<hs:sec xmlns:hs="h" xmlns:hp="p"><hp:p><hp:run><hp:t>다시 저장한 본문</hp:t></hp:run></hp:p></hs:sec>')
        return good

    monkeypatch.setattr(read_failure, "resave_hwp", resave)
    proc = DocumentProcessor()
    assert "다시 저장한 본문" in proc._parse(bad) and "기안기로 다시 저장해 읽음" in proc._last_parse_note
    monkeypatch.setattr(read_failure, "resave_hwp", lambda path: (_ for _ in ()).throw(RuntimeError("기안기 서버에 닿지 않습니다")))
    try:
        proc._parse(bad)
        raise AssertionError("못 읽은 파일이 통과했다")
    except Exception as e:
        assert "기안기" not in str(e)                                         # 처음 오류 그대로


def _failed_rows(db, rows):
    archive.ensure(db)
    with db._conn() as conn:
        for rel, why in rows:
            conn.execute("INSERT INTO archive_files (rel, size, mtime, ext, analysis) VALUES (?, 5, 7, ?, ?)",
                         (rel, Path(rel).suffix.lstrip("."), f"처리 실패: {why}"))
    archive_cleanup.put(db, [r for r, _ in rows], "failed", by="scripts/217")


def test_archive_failed_list_and_retry(tmp_path):
    """원본 보관소 정리 카드 — 「처리 실패」 목록(사유·찾기)에서 고른 원본을 정리함에서 빼고 다시 읽기 장부에. 스캔은 글자 읽기로."""
    app, _ = _app(tmp_path)
    db = app.state.db
    _failed_rows(db, [("링크/가/계획서.hwp", "HWP 형식이 아닙니다 (9바이트)"), ("링크/나/스캔.pdf", SCAN), ("링크/다/보고.pdf", "KeyError: x")])
    c = TestClient(app)
    assert _login(c, "zzaimy", "boot-pass-1")
    assert c.get("/archive/cleanup/failed").status_code == 403
    assert _login(c, "zzdev", "devpass")
    d = c.get("/archive/cleanup/failed").json()
    assert d["total"] == 3 and d["by_reason"] == {"형식 불일치": 1, "스캔 판독 없음": 1, "기타 판독 실패": 1}
    assert {r["name"]: r["reason"] for r in d["rows"]}["스캔.pdf"] == "스캔 판독 없음"
    assert c.get("/archive/cleanup/failed?q=스캔").json()["total"] == 1
    r = c.post("/archive/cleanup/retry", json={"rels": ["링크/나/스캔.pdf", "링크/다/보고.pdf", "링크/없음.pdf"], "mode": "auto"}).json()
    assert r["retried"] == 2 and r["retry_pending"] == 2
    assert {k: v["mode"] for k, v in read_failure.archive_pending(db).items()} == {"링크/나/스캔.pdf": "ocr", "링크/다/보고.pdf": "retry"}
    assert c.get("/archive/cleanup/failed").json()["total"] == 1
    assert (tmp_path / ".archive-retry").exists()                              # 동기화가 다음 1분에 돌게
    page = c.get("/archive").text
    assert 'id="acFail"' in page and 'data-fdo="retry"' in page


def test_run_archive_retry_reads_or_returns_to_cleanup(tmp_path):
    app, proc = _app(tmp_path)
    db = app.state.db
    _failed_rows(db, [("링크/가/계획서.hwp", "KeyError"), ("링크/나/암호.hwp", "KeyError"), ("링크/다/스캔.pdf", SCAN)])
    read_failure.archive_retry(db, ["링크/가/계획서.hwp", "링크/나/암호.hwp", "링크/다/스캔.pdf"], by="zzdev")

    def fetch(row):
        p = tmp_path / ("got-" + Path(row["rel"]).name)
        p.write_bytes(b"LOCK" if "암호" in row["rel"] else b"fine")
        return p

    got = read_failure.run_archive_retry(db, proc, fetch=fetch, ready=lambda: False, data_dir=tmp_path)
    assert got == {"read": 1, "failed": 1, "waiting": 1}
    with db._conn() as conn:
        did = conn.execute("SELECT doc_id FROM archive_files WHERE rel = '링크/가/계획서.hwp'").fetchone()[0]
        bad = conn.execute("SELECT doc_id, analysis FROM archive_files WHERE rel = '링크/나/암호.hwp'").fetchone()
    assert db.get_document(did)["stored_path"] == "dgx://링크/가/계획서.hwp" and db.get_document(did)["status"] == "reviewed"
    assert bad[0] is None and "암호" in bad[1]
    assert set(read_failure.archive_pending(db)) == {"링크/다/스캔.pdf"}           # 스캔은 판독이 닿을 때까지 장부에
    fails = [json.loads(x) for x in (tmp_path / "parse_failures.jsonl").read_text(encoding="utf-8").splitlines()]
    assert fails[-1]["rel"] == "링크/나/암호.hwp" and "암호" in fails[-1]["error"]
    assert json.loads((tmp_path / "origins.jsonl").read_text(encoding="utf-8").splitlines()[-1])["origin"] == "링크/가/계획서.hwp"
    assert db.failed_documents(limit=50) == []                                   # 다시 실패한 원본은 문서로 남기지 않는다


def test_worker_drains_queue_in_background(tmp_path, monkeypatch):
    """앱 안 작업 줄 — 대기열에 넣으면 떠서 읽고, 할 일이 없으면 끝난다."""
    import time
    monkeypatch.setenv("ZZAIMY_READ_QUEUE_S", "0.05")
    monkeypatch.setattr(read_failure, "ocr_ready", lambda: True)
    app, proc = _app(tmp_path)
    db = app.state.db
    p = tmp_path / "s.pdf"
    p.write_bytes(b"%PDF ok")
    did = db.add_document(filename="s.pdf", stored_path=str(p))
    db.update_document(did, status="failed", error=SCAN)
    read_failure.queue_ocr(db, did, by="zzaimy")
    for _ in range(100):
        if db.get_document(did)["status"] == "reviewed" and not read_failure.queued(db):
            break
        time.sleep(0.05)
    assert db.get_document(did)["status"] == "reviewed" and read_failure.queued(db) == {}
