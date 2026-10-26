"""프로젝트 공유를 여러 계정으로 끝까지 — 다른 부서 함께 작업·읽기만이 프로젝트 한글 작업 문서까지 닿는지, 거두면 바로 막히는지,
공동 편집(이름표·칸 잠금), 다른 부서의 담당자 대화 읽기, 공유가 풀린 내 대화의 읽기 화면, 학생 차단(ADR-0066·project_access)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from tests.test_webhwp import _hwpx
from zzaimy.app import project_access as pa
from zzaimy.app import webhwp
from zzaimy.ingest import gdocs, gdrive

PW = "share-pass-123"


@pytest.fixture(autouse=True)
def _no_google(monkeypatch):
    monkeypatch.setattr(gdrive, "list_accounts", lambda: [])
    monkeypatch.setattr(gdocs, "has_docs_scope", lambda email: True)
    monkeypatch.setattr(webhwp, "status", lambda *a, **k: {"ok": True, "version": "t", "error": ""})


def _setup(tmp_path):
    app = _app(tmp_path)
    for uid, role, dept, grade, name in (("lead", "staff", "기획처", "lead", "박책임"), ("mem", "staff", "기획처", "member", "김기획"),
                                         ("san", "staff", "산학협력단", "member", "정산학"), ("san2", "staff", "산학협력단", "member", "오산학"),
                                         ("stu", "student", "", None, "한학생")):
        a = app.state.new_account(uid, PW, role, name)
        a["dept"], a["name"] = dept, name
        if grade:
            a["grade"] = grade
        app.state.accounts[uid] = a
    app.state.save_accounts()
    db = app.state.db
    pid = db.create_project("grant", "공유시험사업", owner="lead")
    base = Path(app.state.data_dir)
    fid = webhwp.add(db, base, "기획처", "계획서 작업본.hwpx", _hwpx("계획"), owner="lead", folder="작업/공유시험사업")
    cl = {}
    for uid in ("lead", "mem", "san", "san2", "stu"):
        c = TestClient(app)
        assert _login(c, uid, PW)
        cl[uid] = c
    return app, db, pid, fid, cl


def _share(c, pid, items):
    r = c.post(f"/project/{pid}/shares", data={"mode": "shared" if items else "private",
                                                "to": [t for t, _ in items], "level": [lv for _, lv in items]}, follow_redirects=False)
    assert r.status_code == 303


def test_other_department_share_reaches_project_hwp_work_docs(tmp_path):
    app, db, pid, fid, cl = _setup(tmp_path)
    san, san2 = cl["san"], cl["san2"]
    # 공유 전 — 다른 부서는 프로젝트도 작업 문서도 못 연다
    assert san.get(f"/project/{pid}").status_code == 404
    assert san.get(f"/hwp/{fid}/file").status_code == 404
    _share(cl["lead"], pid, [("user:san", "edit"), ("user:san2", "view")])
    # 함께 작업 — 목록에 보이고 열고 저장·칸 잠금까지
    assert "계획서 작업본" in san.get(f"/project/{pid}").text
    assert san.get(f"/hwp/{fid}/file").status_code == 200
    assert san.post(f"/hwp/{fid}/save", content=_hwpx("고침")).status_code == 200
    assert san.post(f"/hwp/{fid}/lock").json().get("ok") is True
    san.post(f"/hwp/{fid}/unlock")
    # 읽기만 — 보이고 열리지만 고치지 못함(저장·칸 쓰기·잠금 거절, 편집 화면은 보기 전용과 프로젝트로 돌아가기)
    assert "계획서 작업본" in san2.get(f"/project/{pid}").text
    assert san2.get(f"/hwp/{fid}/file").status_code == 200
    assert san2.post(f"/hwp/{fid}/save", content=_hwpx("몰래")).status_code == 403
    assert san2.post(f"/hwp/{fid}/ops", json={"client": "san2:t", "ops": []}).status_code == 403
    assert san2.post(f"/hwp/{fid}/lock").json() == {"ok": False, "by": "", "readonly": True}
    page = san2.get(f"/webhwpctrl/aura-edit?id={fid}").text
    assert "readonly = true" in page and f'href="/project/{pid}"' in page and "\\uc77d\\uae30\\ub9cc \\uacf5\\uc720 \\u2014" in page
    assert san2.get(f"/hwp/{fid}/coedit").json()["readonly"] is True
    # 거두면 바로 — 목록에서 사라지고 주소로 열어도 404
    _share(cl["lead"], pid, [])
    for c in (san, san2):
        assert c.get(f"/project/{pid}").status_code == 404
        assert c.get(f"/hwp/{fid}/file").status_code == 404
        assert c.post(f"/hwp/{fid}/save", content=_hwpx("x")).status_code == 404


def test_work_doc_made_by_other_department_member_opens_for_owner_and_not_for_same_name_project(tmp_path):
    app, db, pid, fid, cl = _setup(tmp_path)
    _share(cl["lead"], pid, [("user:san", "edit")])
    # 산학협력단 사람이 프로젝트에 복사해 넣은 작업본(산학협력단/작업/<프로젝트>) — 담당자도 프로젝트 화면에서 보고 연다
    r = cl["san"].post(f"/hwp/{fid}/copy", data={"project": str(pid), "name": "산학 작업본"}, headers={"accept": "application/json"})
    assert r.status_code == 200, r.text
    new = r.json()["id"]
    assert webhwp.dept_of_path(Path(app.state.data_dir), webhwp.get(db, new)["path"]) == "산학협력단"
    assert "산학 작업본" in cl["lead"].get(f"/project/{pid}").text
    assert cl["lead"].get(f"/hwp/{new}/file").status_code == 200
    # 같은 이름의 다른 프로젝트(산학협력단 사람 것) 폴더에 둔 파일은 이 프로젝트 사람에게 새지 않는다 — 만든 사람이 이 프로젝트에 쓸 수 없으면 제외
    db.create_project("grant", "공유시험사업", owner="san2")
    other = webhwp.add(db, Path(app.state.data_dir), "산학협력단", "남의 것.hwpx", _hwpx("x"), owner="san2", folder="작업/공유시험사업")
    assert cl["lead"].get(f"/hwp/{other}/file").status_code == 404
    assert "남의 것" not in cl["lead"].get(f"/project/{pid}").text


def test_two_accounts_coedit_same_document_see_each_other_and_field_lock(tmp_path):
    app, db, pid, fid, cl = _setup(tmp_path)
    _share(cl["lead"], pid, [("user:san", "edit")])
    lead, san = cl["lead"], cl["san"]
    lead.post(f"/hwp/{fid}/presence", json={"client": "lead:w1", "field": "", "page": 1})
    got = san.post(f"/hwp/{fid}/presence", json={"client": "san:w1", "field": "", "page": 1}).json()
    assert {p["name"] for p in got["people"]} >= {"박책임", "정산학"}            # 이름표 — 계정 아이디가 아닌 이름
    assert lead.post(f"/hwp/{fid}/lease", json={"action": "acquire", "field": "개요", "client": "lead:w1"}).json()["ok"] is True
    r = san.post(f"/hwp/{fid}/lease", json={"action": "acquire", "field": "개요", "client": "san:w1"}).json()
    assert r["ok"] is False and r.get("by_name") == "박책임"                      # 남이 쓰는 칸은 잠김 — 누가 쓰는지 이름으로


def test_other_department_reads_public_owner_chats_and_names_not_ids(tmp_path):
    app, db, pid, fid, cl = _setup(tmp_path)
    s = db.create_chat_session("개요 초안", project_id=pid, owner="lead")
    db.add_chat(s, "user", "개요 써 줘")
    p = db.create_chat_session("비밀", project_id=pid, owner="lead")
    db.set_chat_private(p, True)
    _share(cl["lead"], pid, [("user:san2", "view"), ("user:mem", "view")])
    # 다른 부서 읽기만 — 담당자의 공개 대화는 읽는다(근거는 자기 범위 안의 것만, tests/test_project_share_rules). 비공개는 빼고
    t = cl["san2"].get(f"/project/{pid}").text
    assert f'href="/chat/{s}/read"' in t and "다른 사람 대화" not in t and "박책임 · 읽기 전용" in t
    assert cl["san2"].get(f"/chat/{s}/read").status_code == 200
    assert cl["san2"].get(f"/chat/{p}/read").status_code == 404
    # 같은 부서 읽기만 — 읽기 전용 줄에 이름
    t = cl["mem"].get(f"/project/{pid}").text
    assert "박책임 · 읽기 전용" in t and "다른 사람 대화" not in t
    assert cl["mem"].get(f"/chat/{p}/read").status_code == 404
    # 공유 창·안내 문구는 계정 아이디 대신 이름
    assert "오산학(읽기만)" in pa.describe(pa.shares(db, db.get_project(pid)), app.state.accounts)
    assert 'title="박책임 님이 공유 · 대화·작업 문서 보기만">읽기만</span>' in t


def test_own_chat_in_revoked_project_turns_read_only(tmp_path):
    app, db, pid, fid, cl = _setup(tmp_path)
    _share(cl["lead"], pid, [("user:mem", "edit")])
    r = cl["mem"].post("/chat/send", data={"question": "성과 지표", "project_id": str(pid)}, follow_redirects=False)
    sid = int(r.headers["location"].rsplit("/", 1)[1])
    assert cl["mem"].get(f"/chat/{sid}", follow_redirects=False).status_code == 200
    _share(cl["lead"], pid, [])
    r = cl["mem"].get(f"/chat/{sid}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/chat/{sid}/read"
    t = cl["mem"].get(f"/chat/{sid}/read").text
    assert "프로젝트 공유가 풀려 이 대화는 읽기만 합니다" in t and f'href="/project/{pid}"' not in t
    assert cl["mem"].post("/chat/send", data={"question": "더", "session_id": str(sid)}).status_code == 403


def test_student_sees_no_project_or_document_box(tmp_path):
    app, db, pid, fid, cl = _setup(tmp_path)
    _share(cl["lead"], pid, [("dept:기획처", "edit")])
    stu = cl["stu"]
    assert stu.get(f"/project/{pid}").status_code == 403
    assert stu.get("/hwp").status_code == 403 and stu.get(f"/hwp/{fid}/file").status_code == 403
    assert "공유시험사업" not in stu.get("/chat").text
