"""수집 자료 묶음 — 뒤처리가 기댈 수 있는 고정 스키마(JSONL)와 manifest, 화면 「구조·표본 보기」."""
from __future__ import annotations

import json
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
    db.set_answer_feedback(mid, sid, "kim", "bad", ["숫자·사실이 틀림"], "메일 kim@example.com 으로 확인")
    sid2 = db.create_chat_session(title="평가 질문", owner="lee")
    db.add_chat(sid2, "user", "평가 세트에 있는 합성 질문입니다")
    # 기록 파일은 줄마다 키가 다르다 — 묶음에서는 같은 키·같은 차례가 되어야 한다
    lines = [{"at": "2026-10-17T10:00:00", "session": sid, "request": "학생 RAG 등록", "role": "dev", "steps": []},
             {"at": "2026-10-18T09:00:00", "session": sid2, "request": "전화 010-9876-5432 바꿔 줘", "role": "staff",
              "page": "/team", "finished": True}]
    (tmp_path / "platform" / "platform_helper_episodes.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "set.json").write_text(json.dumps([{"question": "평가 세트에 있는 합성 질문입니다"}], ensure_ascii=False))
    return db


def _bundle(path):
    with zipfile.ZipFile(path) as zf:
        man = json.loads(zf.read("manifest.json"))
        files = {n: [json.loads(x) for x in zf.read(n).decode("utf-8").splitlines()] for n in zf.namelist() if n.endswith(".jsonl")}
    return man, files


def test_manifest_describes_every_item(tmp_path):
    db = _seed(tmp_path)
    d = tmp_path / "platform"
    man, files = _bundle(collect.build(db, d, ["chat_messages", "answer_feedback", "helper"], "", "", by="zzdev"))
    assert man["schema_version"] == collect.SCHEMA_VERSION
    assert set(files) == {"chat_messages.jsonl", "answer_feedback.jsonl", "helper.jsonl"}       # 항목마다 한 파일
    for key in ("masking", "pseudonymization", "split_rule", "format"):
        assert man[key], key
    assert "9" in man["split_rule"]["ratio"] and "heldout" in man["split_rule"]["heldout"]
    assert "salt" in man["pseudonymization"] and man["made_by"].startswith("u_")
    for key, info in man["sources"].items():
        assert info["file"] == f"{key}.jsonl" and info["name"] and isinstance(info["rows"], int)
        assert info["rows"] == len(files[info["file"]])
        assert info["period"]["first"] <= info["period"]["last"]
        names = [f["name"] for f in info["fields"]]
        assert names[-1] == "split" and len(names) == len(set(names))
        for f in info["fields"]:
            assert set(f) == {"name", "type", "meaning", "treatment"} and f["type"]
        assert sum(info["split"].values()) == info["rows"]
    fields = {f["name"]: f for f in man["sources"]["chat_messages"]["fields"]}
    assert fields["owner"]["treatment"].startswith("가명") and fields["session_id"]["treatment"].startswith("대화 가명")
    assert fields["content"]["treatment"] == "개인정보 가림" and fields["content"]["meaning"]
    assert fields["id"]["type"] == "int"
    assert not (d / ".collect_salt").read_text() in json.dumps(man)                # 소금은 묶음에 없다


def test_jsonl_rows_share_one_stable_schema(tmp_path):
    db = _seed(tmp_path)
    man, files = _bundle(collect.build(db, tmp_path / "platform", ["chat_messages", "helper"], "", ""))
    for key, info in man["sources"].items():
        names = [f["name"] for f in info["fields"]]
        for row in files[info["file"]]:
            assert list(row) == names                                               # 모든 줄이 같은 키, 같은 차례
    helper = files["helper.jsonl"]
    assert helper[0]["page"] is None and helper[1]["steps"] is None                 # 없는 값은 null
    types = {f["name"]: f["type"] for f in man["sources"]["helper"]["fields"]}
    assert types["page"] == "str|null"
    text = json.dumps(files, ensure_ascii=False)
    assert "010-1234-5678" not in text and "010-9876-5432" not in text and "kim" not in text
    assert [r["split"] for r in files["chat_messages.jsonl"] if "평가 세트" in r["content"]] == ["heldout"]


def test_period_and_counts_follow_the_chosen_range(tmp_path):
    db = _seed(tmp_path)
    man, files = _bundle(collect.build(db, tmp_path / "platform", ["helper"], "2026-10-18", "2026-10-18T99"))
    info = man["sources"]["helper"]
    assert info["rows"] == 1 and info["period"]["first"].startswith("2026-10-18")
    assert man["period"] == ["2026-10-18", "2026-10-18"]
    # 묶음 목록(화면)도 새 manifest 를 읽는다
    assert [(m["rows"], m["period"]) for m in collect.made(tmp_path / "platform")] == [(1, "2026-10-18 ~ 2026-10-18")]


def test_peek_shows_fields_and_masked_first_three(tmp_path):
    db = _seed(tmp_path)
    for i in range(4):
        db.add_chat(db.create_chat_session(title=f"추가 {i}", owner="park"), "user", f"추가 질문 {i} park@example.com")
    got = collect.peek(db, tmp_path / "platform", "chat_messages")
    assert len(got["sample"]) == collect.SAMPLE_N
    names = [f["name"] for f in got["fields"]]
    assert names[-1] == "split" and "content" in names and "owner" in names
    for r in got["sample"]:
        assert list(r) == names
        assert r["owner"].startswith("u_") and r["session_id"].startswith("s_")
    text = json.dumps(got, ensure_ascii=False)
    assert "010-1234-5678" not in text and "kim" not in text
    # 표본은 묶음과 같은 변환 — 같은 사람은 같은 가명
    man, files = _bundle(collect.build(db, tmp_path / "platform", ["chat_messages"], "", ""))
    assert got["sample"][0]["owner"] == files["chat_messages.jsonl"][0]["owner"]
    assert [f["name"] for f in man["sources"]["chat_messages"]["fields"]] == names


def test_peek_route_is_dev_only(tmp_path):
    _seed(tmp_path)
    (tmp_path / "platform" / "accounts.json").write_text(json.dumps({"kim": {"pw": "kim-pass", "role": "staff", "dept": "학생처"},
                                                                    "zzdev": {"pw": "dev-pass", "role": "dev"}}))
    app = create_app(db_path=tmp_path / "platform" / "platform.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), responder=FakeResponder(), password="boot-pass-1")

    def login(u, pw):
        c = TestClient(app)
        assert c.post("/login", data={"username": u, "pw": pw}, follow_redirects=False).status_code == 303
        return c
    assert login("kim", "kim-pass").get("/dev/collect/peek/chat_messages").status_code == 403
    dev = login("zzdev", "dev-pass")
    r = dev.get("/dev/collect/peek/helper")
    assert r.status_code == 200 and r.json()["fields"][-1]["name"] == "split"
    assert dev.get("/dev/collect/peek/nope").status_code == 404
    page = dev.get("/dev/train?tab=collect").text
    assert 'data-peek="chat_messages"' in page and "구조·표본 보기" in page and "manifest.json" in page


def test_webhwp_edits_source_reads_epoch_times_and_past_marks(tmp_path):
    """기안기 칸 고친 기록(시각이 숫자)도 수집 항목 — 날짜 범위로 고르고 시각은 날짜 글로. 옛 독스 경로 항목은 「지난 기록」."""
    import time
    from zzaimy.app import collect
    from zzaimy.app import webhwp_coedit as ce
    from zzaimy.app.db import Database
    db = Database(tmp_path / "t.db")
    ce.ensure(db)
    with db._conn() as c:
        c.execute("INSERT INTO webhwp_field_edits (fid, field, version, text, author, at) VALUES (1, '칸:요약', 1, '첫 글', 'kim', ?)", (time.time(),))
    src = next(s for s in collect.SOURCES if s.key == "webhwp_edits")
    rows = list(collect._db_rows(db, src, "2000-01-01", "9999"))
    assert len(rows) == 1 and rows[0]["at"][:2] == "20" and rows[0]["text"] == "첫 글"
    assert not list(collect._db_rows(db, src, "2000-01-01", "2000-01-02"))
    assert {s.key for s in collect.SOURCES if s.past} >= {"drafting", "gdocs_audit"}
