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
    page = dev.get("/dev/train?tab=collect").text
    assert "대화 메시지" in page and "업무 도우미 기록" in page
    r = dev.post("/dev/collect/make", data={"keys": ["chat_messages"]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dev/train?tab=collect"
    for _ in range(100):
        if "/dev/collect/file/collect-" in (page := dev.get("/dev/train?tab=collect").text):
            break
        time.sleep(0.1)
    name = page.split("/dev/collect/file/")[1].split('"')[0]
    got = dev.get(f"/dev/collect/file/{name}")
    assert got.status_code == 200 and got.content[:2] == b"PK"
    assert dev.get("/dev/collect/file/..%2Fplatform.db").status_code == 404


def test_resolve_period_quick_ranges():
    t = "2026-10-18"
    assert collect.resolve_period("all", "2026-01-01", "2026-02-01", t) == ("", "")
    assert collect.resolve_period("", "", "", t) == ("", "")                      # 비우면 전체
    assert collect.resolve_period("today", "", "", t) == (t, t)
    assert collect.resolve_period("7d", "", "", t) == ("2026-10-12", t)
    assert collect.resolve_period("30d", "", "", t) == ("2026-09-19", t)
    assert collect.resolve_period("month", "", "", t) == ("2026-10-01", t)
    assert collect.resolve_period("custom", "2026-10-15", "2026-10-02", t) == ("2026-10-02", "2026-10-15")   # 거꾸로 적어도
    assert collect.resolve_period("", "2026-10-02", "", t) == ("2026-10-02", "")


def test_page_groups_empty_rows_and_period_chips(tmp_path):
    _seed(tmp_path)
    (tmp_path / "platform" / "accounts.json").write_text(json.dumps({"zzdev": {"pw": "dev-pass", "role": "dev"}}))
    app = create_app(db_path=tmp_path / "platform" / "platform.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), responder=FakeResponder(), password="boot-pass-1")
    c = TestClient(app)
    c.post("/login", data={"username": "zzdev", "pw": "dev-pass"}, follow_redirects=False)
    page = c.get("/dev/train?tab=collect").text
    for label in ("전체", "오늘", "최근 7일", "최근 30일", "이번 달", "직접 지정"):
        assert f'>{label}</label>' in page
    assert 'name="period" value="all" checked' in page
    assert 'type="date" name="since" value="20' in page and 'type="date" name="until" value="20' in page   # 빈 날짜 칸 없음
    cat = collect.catalog(app.state.db, tmp_path / "platform")
    n_empty = sum(1 for x in cat if not x["n"])
    assert f"비어 있는 항목 {n_empty}개 보기" in page
    assert page.index("업무 도우미 기록") < page.index("절 작성 기록")           # 자료 있는 항목이 위
    assert page.count('class="col-pick"') == len(cat) - n_empty
    assert "<th class=\"hide-sm\">있는 곳</th>" not in page and "platform_helper_episodes.jsonl" in page
    # 빠른 기간 — 이번 달(오늘 기준)로도 만들어진다
    r = c.post("/dev/collect/make", data={"keys": ["helper"], "period": "month"}, follow_redirects=False)
    assert r.status_code == 303 and "err" not in r.headers["location"]
    for _ in range(100):
        if "/dev/collect/file/collect-" in c.get("/dev/train?tab=collect").text:
            break
        time.sleep(0.1)


def test_collect_is_a_tab_of_train_page(tmp_path):
    """수집 자료는 데이터·학습의 탭(10-27) — 옛 주소는 그 탭으로 넘기고, 사이드바에는 데이터·학습만 켜진다."""
    import re
    from zzaimy.app import dev_nav
    _seed(tmp_path)
    (tmp_path / "platform" / "accounts.json").write_text(json.dumps({"zzdev": {"pw": "dev-pass", "role": "dev"}}))
    app = create_app(db_path=tmp_path / "platform" / "platform.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), responder=FakeResponder(), password="boot-pass-1")
    c = TestClient(app)
    c.post("/login", data={"username": "zzdev", "pw": "dev-pass"}, follow_redirects=False)
    r = c.get("/dev/collect", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dev/train?tab=collect"
    r = c.post("/dev/collect/make", data={}, follow_redirects=False)            # 고른 항목 없음 — 탭으로 돌아가 까닭을 보인다
    assert r.status_code == 303 and r.headers["location"].startswith("/dev/train?tab=collect&err=")
    page = c.get(r.headers["location"]).text
    assert "항목을 하나 이상 고르세요" in page and 'id="collectForm"' in page
    assert "/dev/collect\"" not in page and 'href="/dev/collect"' not in page
    assert '<a href="/dev/train?tab=collect" data-train-tab="collect" class="on" aria-current="page">수집 자료</a>' in page
    assert "문답 검수" not in page                                               # 다른 탭 본문은 안 그린다
    first = c.get("/dev/train").text
    assert 'data-train-tab="data" class="on"' in first and 'id="collectForm"' not in first
    assert "/dev/collect" not in dev_nav.NAMES and "수집 자료" not in dev_nav.NAMES.values()
    on = re.findall(r'<a[^>]*href="([^"]+)"[^>]*class="active"[^>]*aria-current="page"', page)
    assert on == ["/dev/train"]                                                  # 탭에서도 사이드바는 데이터·학습
