"""모델 비교 결과 화면(/dev/models/compare) — 관리자만, 덜 찬 결과도 그린다, 문서 내려받기는 실행의 docs/ 안만,
개발 현황 Writer 줄의 바로가기는 결과가 있을 때만."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import model_compare
from zzaimy.app.main import create_app

PW = {"zzdev": "devpass", "plan": "plan-pass", "stu": "stu-pass"}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    base = tmp_path / "model_compare"
    base.mkdir()
    monkeypatch.setenv("ZZAIMY_MODEL_COMPARE_DIR", str(base))
    (tmp_path / "accounts.json").write_text(json.dumps({
        "zzdev": {"pw": "devpass", "role": "dev"}, "zzaimy": {"pw": "boot-pass-1", "role": "staff"},
        "plan": {"pw": "plan-pass", "role": "staff", "dept": "기획처"}, "stu": {"pw": "stu-pass", "role": "student"}}))
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), password="boot-pass-1")
    return app, base, tmp_path


def _client(app, uid) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": uid, "pw": PW[uid]}, follow_redirects=False)
    assert r.status_code == 303, uid
    return c


def _partial_run(base, run_id="run-1"):
    d = base / run_id
    (d / "docs" / "open").mkdir(parents=True)
    (d / "docs" / "open" / "기안문.hwpx").write_bytes(b"PK-hwpx")
    (d / "summary.json").write_text(json.dumps({
        "run_id": run_id, "created": "2026-10-23T10:00",
        "arms": [{"key": "open", "label": "Qwen 27B", "model": "q27", "kind": "open"},
                 {"key": "lora", "label": "writer-lora", "model": "w", "kind": "sft-lora"}],
        "blocks": [{"key": "writer", "title": "절 작성",
                    "pairwise": [{"a": "lora", "b": "open", "win": 6, "tie": 2, "loss": 2, "ci_low": 0.41, "ci_high": 0.83, "n": 10}],
                    "per_dimension": {"instruction": {"open": 3.1, "lora": 3.9}, "style": {"open": 2.5}},
                    "metrics": {"ttft_s": {"open": 0.8, "lora": 0.9}}}],     # per_type 없음 — 진행 중
        "notes": ["판정 모델은 같은 27B"]}, ensure_ascii=False))
    (d / "samples.json").write_text(json.dumps([
        {"id": "s1", "type": "기안문", "input": "<script>alert(1)</script> 입력",
         "outputs": {"open": "오픈 출력", "lora": "어댑터 출력"},
         "scores": {"open": {"instruction": 3}, "lora": {"instruction": 4}}, "judge_reason": "어댑터가 형식을 지킴"}],
        ensure_ascii=False))
    (d / "docs.json").write_text(json.dumps([{"arm": "open", "title": "기안문 초안", "file": "open/기안문.hwpx",
                                              "created": "2026-10-23"}], ensure_ascii=False))
    return d


@pytest.mark.parametrize("uid,code", [("zzdev", 200), ("plan", 403), ("stu", 403)])
def test_admin_only(world, uid, code):
    app, base, _ = world
    _partial_run(base)
    c = _client(app, uid)
    assert c.get("/dev/models/compare").status_code == code
    assert c.get("/dev/models/compare/run-1").status_code == code
    assert c.get("/dev/models/compare/run-1/doc", params={"f": "open/기안문.hwpx"}).status_code == code


def test_renders_partial_run(world):
    app, base, _ = world
    _partial_run(base)
    page = _client(app, "zzdev").get("/dev/models/compare").text
    assert "run-1" in page and "오픈 27B" in page and "SFT 어댑터" in page
    assert "절 작성" in page and "이김 6" in page and "0.41~0.83" in page
    assert "지시 충실" in page and "문체" in page and "첫 글자까지" in page
    assert "진행 중" in page                                   # 서류 갈래별 표가 아직 없다
    assert "<script>alert(1)</script>" not in page              # 표본 글은 JSON 안에서도 이스케이프
    assert "기안문 초안" in page and "/dev/models/compare/run-1/doc?f=" in page
    assert "기안기로 열기" not in page


def test_samples_only_run_and_run_list(world):
    app, base, _ = world
    _partial_run(base, "run-1")
    d = base / "run-2"
    d.mkdir()
    (d / "samples.json").write_text(json.dumps([{"id": "a", "type": "공문", "input": "x", "outputs": {"open": "y"}}]))
    c = _client(app, "zzdev")
    page = c.get("/dev/models/compare/run-2").text
    assert page.count("진행 중") >= 2 and 'href="/dev/models/compare/run-1"' in page
    assert c.get("/dev/models/compare/nope").status_code == 404
    assert c.get("/dev/models/compare/..%2F..").status_code == 404


def test_doc_download_stays_inside_docs(world):
    app, base, tmp = world
    d = _partial_run(base)
    (tmp / "secret.hwpx").write_bytes(b"secret")
    (d / "summary.hwpx").write_bytes(b"outside docs")
    c = _client(app, "zzdev")
    ok = c.get("/dev/models/compare/run-1/doc", params={"f": "open/기안문.hwpx"})
    assert ok.status_code == 200 and ok.content == b"PK-hwpx"
    for bad in ("../summary.hwpx", "../../../secret.hwpx", str(tmp / "secret.hwpx"), "open/../../summary.hwpx",
                "../summary.json", "open", ""):
        assert c.get("/dev/models/compare/run-1/doc", params={"f": bad}).status_code == 404, bad
    assert model_compare.safe_doc(d, "../../secret.hwpx") is None


def test_dev_link_only_when_runs_exist(world):
    app, base, _ = world
    c = _client(app, "zzdev")
    assert 'href="/dev/models/compare"' not in c.get("/dev").text
    _partial_run(base)
    page = c.get("/dev").text
    assert 'href="/dev/models/compare"' in page and "모델 비교 결과" in page


def test_labels_and_percent_keys_from_summary():
    from zzaimy.app import model_compare as mc
    b = mc._block({"key": "x", "metrics": {"invented_any": {"o": 0.041}, "chrf": {"o": 27.9}}}, ["o"],
                  {"invented_any": "질문에 없는 숫자가 든 답 비율", "chrf": "정답 겹침(chrF)"}, {"invented_any"})
    rows = {r["name"]: r for r in b["other"]}
    assert rows["질문에 없는 숫자가 든 답 비율"]["pct"] is True and rows["정답 겹침(chrF)"]["pct"] is False
