"""실사용 대비(11-10): 계정 파일 외부 변경 반영·덮어쓰기 방지, 처리 중 문서 겹쳐 돌리지 않기, 모델 호출 동시 개수 문, 답 실패 안내."""
import importlib
import json
import os
import threading
import time

from fastapi.testclient import TestClient

from zzaimy.app import passwords
from zzaimy.app.main import create_app
from test_app import FakeDrafter, FakeProcessor


def _app(tmp_path):
    return create_app(db_path=tmp_path / "r.db", inbox_dir=tmp_path / "inbox",
                      processor=FakeProcessor(), drafter=FakeDrafter(), password="test-only-password")


def _add_account_on_disk(path, uid, pw):
    acc = json.loads(path.read_text())
    h, s = passwords.hash_pw(pw)
    acc[uid] = {"role": "staff", "name": "시험", "dept": "기획처", "active": True, "pw_hash": h, "salt": s}
    path.write_text(json.dumps(acc, ensure_ascii=False))
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000))   # 같은 순간 쓰기여도 바뀐 것으로 보이게


def test_account_added_outside_app_can_log_in_without_restart(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    path = tmp_path / "accounts.json"
    _add_account_on_disk(path, "tf_member", "pw-for-tf-1")
    r = client.post("/login", data={"username": "tf_member", "pw": "pw-for-tf-1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "tf_member" in app.state.accounts


def test_app_save_keeps_account_added_outside(tmp_path):
    app = _app(tmp_path)
    path = tmp_path / "accounts.json"
    _add_account_on_disk(path, "outside_one", "pw-x-123")
    app.state.accounts["zzaimy"]["name"] = "담당자(고침)"          # 메모리만 고친 채 저장 — 밖에서 더한 계정이 사라지면 안 된다
    app.state.save_accounts()
    disk = json.loads(path.read_text())
    assert "outside_one" in disk and disk["zzaimy"]["name"] == "담당자(고침)"


def test_requeue_skips_documents_being_processed(tmp_path):
    app = _app(tmp_path)
    from zzaimy.app.pipeline import ACTIVE_DOCS
    f = tmp_path / "a.txt"
    f.write_text("본문")
    db = app.state.db if hasattr(app.state, "db") else None
    from zzaimy.app.db import Database
    db = db or Database(tmp_path / "r.db")
    d1 = db.add_document("a.txt", str(f))
    d2 = db.add_document("b.txt", str(f))
    db.update_document(d1, status="processing")
    db.update_document(d2, status="processing")
    ACTIVE_DOCS.add(d1)
    try:
        got = app.state.requeue_stalled_docs(min_age_min=0)
    finally:
        ACTIVE_DOCS.discard(d1)
    assert d1 not in got and d2 in got


def test_llm_gate_limits_concurrency_and_counts_waiting(monkeypatch):
    monkeypatch.setenv("ZZAIMY_LLM_SLOTS", "1")
    from zzaimy.generate import llm_gate
    gate = importlib.reload(llm_gate)
    hold = threading.Event()
    seen = []

    def first():
        with gate.slot():
            hold.wait(2)

    def second():
        with gate.slot():
            seen.append(gate.running())

    t1 = threading.Thread(target=first); t1.start()
    time.sleep(0.05)
    t2 = threading.Thread(target=second); t2.start()
    time.sleep(0.1)
    assert gate.waiting() == 1 and gate.running() == 1
    hold.set(); t1.join(); t2.join()
    assert seen == [1] and gate.waiting() == 0 and gate.running() == 0
    monkeypatch.delenv("ZZAIMY_LLM_SLOTS")
    importlib.reload(llm_gate)


def test_broken_or_partial_account_file_keeps_current_table(tmp_path):
    app = _app(tmp_path)
    path = tmp_path / "accounts.json"
    before = dict(app.state.accounts)
    for bad in ('{"zzaimy": {"role": "staff"', "[]", "{}", '{"x": {"role": "dev"}}'):   # 반쯤 쓰임·꼴 틀림·비밀번호 없는 줄
        path.write_text(bad)
        st = path.stat(); os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000))
        TestClient(app).get("/login")
        assert set(app.state.accounts) == set(before)
        assert "x" not in app.state.accounts


def test_deactivated_outside_is_rejected_and_app_save_does_not_revive(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    path = tmp_path / "accounts.json"
    _add_account_on_disk(path, "member2", "pw-member-2")
    r = client.post("/login", data={"username": "member2", "pw": "pw-member-2"}, follow_redirects=False)
    assert r.headers["location"] == "/"
    acc = json.loads(path.read_text()); acc["member2"]["active"] = False
    path.write_text(json.dumps(acc, ensure_ascii=False))
    st = path.stat(); os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 9_000_000))
    # 앱이 다른 계정을 고쳐 저장해도(아직 다시 읽기 전) 밖에서 막은 계정을 되살리지 않는다
    app.state.accounts["zzaimy"]["name"] = "담당자2"
    app.state.save_accounts()
    disk = json.loads(path.read_text())
    assert disk["member2"]["active"] is False and disk["zzaimy"]["name"] == "담당자2"
    assert client.get("/", follow_redirects=False).status_code in (303, 307, 401)   # 그 세션은 바로 무효


def test_account_table_never_empty_during_reload(tmp_path):
    app = _app(tmp_path)
    path = tmp_path / "accounts.json"
    seen_empty = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            if not app.state.accounts.get("zzaimy"):
                seen_empty.append(1)

    t = threading.Thread(target=reader); t.start()
    try:
        for i in range(30):
            _add_account_on_disk(path, f"m{i}", "pw-1234-x")
            TestClient(app).get("/login")
    finally:
        stop.set(); t.join()
    assert not seen_empty and "m29" in app.state.accounts
