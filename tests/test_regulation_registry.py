from zzaimy.app import regulation_registry as rr


def test_groups_versions_and_picks_latest_revision_as_current():
    rows = [
        {"id": 1, "filename": "영남이공대학교 근로장학제도 운영 규정 · 2012년 2월 23일.hwp", "text": "근로장학제도 운영 규정 학과장회 통과일자 : 2012년 2월 23일 제1조"},
        {"id": 2, "filename": "4-01 영남이공대학교 근로장학제도 운영 규정.hwp", "text": "학과장회 통과일자 : 2016년 3월 17일 제1조(목적) <개정 2020.3.1>"},
        {"id": 3, "filename": "취업책임교원제 운영 규정(2013-11-28).hwp", "text": "통과일자 : 2013년 11월 28일"},
    ]
    regs = rr.build(rows)
    g = next(x for x in regs if "근로장학" in x["name"])
    assert len(g["versions"]) == 2 and g["current"]["id"] == 2 and g["current"]["latest"] == "2020-03-01" and g["code"] == "4-01"
    assert [v["status"] for v in g["versions"]] == ["현행", "구판"]
    assert next(x for x in regs if "취업책임" in x["name"])["current"]["passed"] == "2013-11-28"


def test_dates_from_compact_header():
    d = rr.dates_of("5-24-1 학생등록 규정 학과장회 통과일자 년월일 : 2025 10 30 제 장 총칙")
    assert d["passed"] == "2025-10-30"


def test_regulations_page_renders(tmp_path):
    from fastapi.testclient import TestClient
    from zzaimy.app.main import create_app
    from test_app import FakeDrafter, FakeProcessor
    app = create_app(db_path=tmp_path / "r.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter(), password="test-only-password")
    c = TestClient(app)
    c.post("/login", data={"username": "zzaimy", "pw": "test-only-password"}, follow_redirects=False)
    r = c.get("/regulations")
    assert r.status_code == 200 and "규정 목록" in r.text


def test_prefer_current_drops_or_labels_old_versions(monkeypatch):
    from zzaimy.app import responder, regulation_registry as rr
    cur = {"id": 2, "latest": "2020-03-01"}
    monkeypatch.setattr(rr, "superseded", lambda db, i: cur if i == 1 else None)
    both = responder.prefer_current(None, [{"doc_id": 1, "reg_title": "근로장학"}, {"doc_id": 2, "reg_title": "근로장학"}])
    assert [h["doc_id"] for h in both] == [2]
    only_old = responder.prefer_current(None, [{"doc_id": 1, "reg_title": "근로장학"}])
    assert "구판" in only_old[0]["reg_title"] and "2020-03-01" in only_old[0]["reg_title"]


def test_future_dates_ignored():
    assert rr.dates_of("학과장회 통과일자 : 2099년 1월 1일 <개정 2020.3.1>")["latest"] == "2020-03-01"


def test_only_admin_shared_rules_decide_versions(tmp_path):
    import json
    from zzaimy.app.db import Database
    db = Database(tmp_path / "p.db")
    (tmp_path / "accounts.json").write_text(json.dumps({"boss": {"role": "dev"}, "kim": {"role": "staff"}}))
    with db._conn() as c:
        for i, (own, txt) in enumerate([("boss", "통과일자 : 2016년 3월 17일"), ("kim", "통과일자 : 2025년 1월 1일")], 1):
            c.execute("INSERT INTO documents (id, filename, stored_path, created_at, owner, doc_type, masked_text, status) VALUES (?, ?, 'x', '2026-01-01', ?, 'regulation', ?, 'done')",
                      (i, "근로장학 규정.hwp", own, txt))
    assert rr.superseded(db, 1) is None          # 담당자가 올린 늦은 날짜 판은 현행을 차지하지 못한다


def test_bundle_role_needs_head_word():
    import re, textwrap
    src = open("src/zzaimy/app/main.py", encoding="utf-8").read()
    i, j = src.index("    def _bundle_role("), src.index("    def _intake_bundle(")
    from zzaimy.app.project_documents import CRITERIA_KINDS
    ns = {"re": re, "_CRITERIA_KINDS": CRITERIA_KINDS, "_BASE_PLAN": re.compile(r"기본\s*계획|추진\s*계획|시행\s*계획|운영\s*계획")}
    exec(textwrap.dedent(src[i:j]), ns)
    role = ns["_bundle_role"]
    assert role("메모_학생 모집·충원 및 학습지속 지원체계.docx") == "intake"
    assert role("자료_사업추진 계획 총괄표.xlsx") == "intake"
    assert role("2026 지원사업 공고문.pdf") == "criteria"
    assert role("부트캠프 사업 공고(교육부 공고 제2025-247호).hwpx") == "criteria"
    assert role("지표정의서 및 평가편람(ver5).hwpx") == "criteria"
