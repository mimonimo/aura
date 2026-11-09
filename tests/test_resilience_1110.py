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


def _bump(path, ns=11_000_000):
    st = path.stat(); os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + ns))


def _login_ok(client, uid, pw):
    return client.post("/login", data={"username": uid, "pw": pw}, follow_redirects=False).headers.get("location") == "/"


def test_revocation_in_memory_survives_reload_from_disk(tmp_path):
    """메모리에서 막은 계정(파일 쓰기 전)이 파일 다시 읽기로 되살아나지 않는다 — 파일 쪽은 다른 계정만 바뀜."""
    app = _app(tmp_path)
    path = tmp_path / "accounts.json"
    _add_account_on_disk(path, "rv1", "pw-rv1-xx")
    client = TestClient(app)
    assert _login_ok(client, "rv1", "pw-rv1-xx")
    app.state.accounts["rv1"] = {**app.state.accounts["rv1"], "active": False}   # 앱이 막음(저장 전)
    acc = json.loads(path.read_text()); acc["zzaimy"]["name"] = "밖에서 고침"
    path.write_text(json.dumps(acc, ensure_ascii=False)); _bump(path)
    TestClient(app).get("/login")                       # 다시 읽기
    assert app.state.accounts["rv1"]["active"] is False
    assert json.loads(path.read_text())["rv1"]["active"] is False   # 파일에도 회수가 남는다
    assert app.state.accounts["zzaimy"]["name"] == "밖에서 고침"
    assert not _login_ok(TestClient(app), "rv1", "pw-rv1-xx")


def test_merge_never_revives_disabled_or_deleted_and_role_takes_lower(tmp_path):
    app = _app(tmp_path)
    path = tmp_path / "accounts.json"
    for u in ("a1", "a2", "a3"):
        _add_account_on_disk(path, u, "pw-" + u + "-xx")
    TestClient(app).get("/login")                       # 셋 다 맞춘 상태(base)
    # 밖: a1 막음, a2 지움, a3 학생으로 낮춤
    acc = json.loads(path.read_text())
    acc["a1"]["active"] = False
    del acc["a2"]
    acc["a3"]["role"] = "student"
    path.write_text(json.dumps(acc, ensure_ascii=False)); _bump(path)
    # 앱(아직 다시 읽기 전): a1·a2 이름 고치고 a3 를 관리자로 올림 → 저장
    st = app.state.accounts
    st["a1"] = {**st["a1"], "name": "메모리"}
    st["a2"] = {**st["a2"], "name": "메모리"}
    st["a3"] = {**st["a3"], "role": "dev"}
    app.state.save_accounts()
    disk = json.loads(path.read_text())
    assert disk["a1"]["active"] is False and disk["a1"]["name"] == "메모리"
    assert "a2" not in disk and "a2" not in st
    assert disk["a3"]["role"] == "student"
    assert not _login_ok(TestClient(app), "a1", "pw-a1-xx") and not _login_ok(TestClient(app), "a2", "pw-a2-xx")


def test_unreadable_file_keeps_memory_revocation(tmp_path):
    app = _app(tmp_path)
    path = tmp_path / "accounts.json"
    _add_account_on_disk(path, "rv2", "pw-rv2-xx")
    TestClient(app).get("/login")
    app.state.accounts["rv2"] = {**app.state.accounts["rv2"], "active": False}
    path.write_text('{"rv2": {"active": true')       # 반쯤 쓰인 파일
    _bump(path)
    TestClient(app).get("/login")
    assert app.state.accounts["rv2"]["active"] is False
    assert not _login_ok(TestClient(app), "rv2", "pw-rv2-xx")
