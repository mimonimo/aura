"""수집 자료 — 목록(건수·기간)과 가린 사본 묶음(가명·나눔·평가 질문 heldout). 관리자만."""
from __future__ import annotations

import json
import time
import zipfile

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from tests.test_chat_widget import FakeResponder
from zzaimy.app import collect
from zzaimy.app.db import Database
from zzaimy.app.main import create_app


def _seed(tmp_path):
    db = Database(tmp_path / "platform" / "platform.db")
    sid = db.create_chat_session(title="합성 대화", owner="kim")
    db.add_chat(sid, "user", "연락처 010-1234-5678 로 휴학 기간을 알려 줘")
    mid = db.add_chat(sid, "assistant", "휴학은 통산 3년입니다.")
    db.set_answer_feedback(mid, sid, "kim", "bad", ["숫자·사실이 틀림"], "확인 필요")
    sid2 = db.create_chat_session(title="평가 질문", owner="lee")
    db.add_chat(sid2, "user", "평가 세트에 있는 합성 질문입니다")
    (tmp_path / "platform" / "platform_helper_episodes.jsonl").write_text(
        json.dumps({"at": "2026-10-17T10:00:00", "session": sid, "request": "학생 RAG 등록", "role": "dev", "steps": []}, ensure_ascii=False) + "\n")
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "set.json").write_text(json.dumps([{"question": "평가 세트에 있는 합성 질문입니다"}], ensure_ascii=False))
    return db


def test_catalog_and_masked_bundle(tmp_path):
    db = _seed(tmp_path)
    d = tmp_path / "platform"
    cat = {c["key"]: c for c in collect.catalog(db, d)}
    assert cat["chat_messages"]["n"] == 3 and cat["answer_feedback"]["n"] == 1 and cat["helper"]["n"] == 1
    assert cat["drafting"]["n"] == 0
    p = collect.build(db, d, ["chat_messages", "answer_feedback", "helper"], "", "", by="zzdev")
    with zipfile.ZipFile(p) as zf:
        man = json.loads(zf.read("manifest.json"))
        msgs = [json.loads(x) for x in zf.read("chat_messages.jsonl").decode().splitlines()]
        fb = [json.loads(x) for x in zf.read("answer_feedback.jsonl").decode().splitlines()]
    text = json.dumps(msgs, ensure_ascii=False)
    assert "010-1234-5678" not in text                                   # 글은 가린다
    assert "kim" not in text and msgs[0]["owner"].startswith("u_")      # 사람은 가명
    assert fb[0]["owner"] == msgs[0]["owner"]                           # 같은 사람 같은 가명
    assert all(m["session_id"].startswith("s_") for m in msgs)
    assert [m["split"] for m in msgs if "평가 세트" in m["content"]] == ["heldout"]
    assert man["sources"]["chat_messages"]["rows"] == 3 and man["sources"]["helper"]["rows"] == 1
    assert not (d / ".collect_salt").read_text() in json.dumps(man)     # 소금은 묶음에 없다
    # 기간 거르기
    p2 = collect.build(db, d, ["helper"], "2026-10-18", "2026-10-19T99")
    with zipfile.ZipFile(p2) as zf:
        assert zf.read("helper.jsonl") == b""


def test_page_is_dev_only_and_makes_bundle(tmp_path):
    _seed(tmp_path)
    (tmp_path / "platform" / "accounts.json").write_text(json.dumps({"kim": {"pw": "kim-pass", "role": "staff", "dept": "학생처"},
                                                                    "zzdev": {"pw": "dev-pass", "role": "dev"}}))
    app = create_app(db_path=tmp_path / "platform" / "platform.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), responder=FakeResponder(), password="boot-pass-1")
    def login(u, pw):
        c = TestClient(app)
        assert c.post("/login", data={"username": u, "pw": pw}, follow_redirects=False).status_code == 303
        return c
    assert login("kim", "kim-pass").get("/dev/collect").status_code == 403
    dev = login("zzdev", "dev-pass")
    page = dev.get("/dev/collect").text
    assert "대화 메시지" in page and "업무 도우미 걸음" in page
    r = dev.post("/dev/collect/make", data={"keys": ["chat_messages"]}, follow_redirects=False)
    assert r.status_code == 303
    for _ in range(100):
        if "/dev/collect/file/collect-" in (page := dev.get("/dev/collect").text):
            break
        time.sleep(0.1)
    name = page.split("/dev/collect/file/")[1].split('"')[0]
    got = dev.get(f"/dev/collect/file/{name}")
    assert got.status_code == 200 and got.content[:2] == b"PK"
    assert dev.get("/dev/collect/file/..%2Fplatform.db").status_code == 404
