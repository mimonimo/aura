"""10-27 보안 점검 — 프로젝트 공유(「읽기만」·「함께 작업」, ADR-0075)와 업무 도우미 커서(platform_ops) 빈틈을 막은 시험.

공유: 작업 문서 권한은 만들 때 적은 프로젝트(meta project)로 정하고, 이름이 같은 다른 프로젝트·프로젝트 이름 바꾸기·「작업/」 폴더
이름 바꾸기로 새지 않는다. 비공개 대화에 이은 작업본은 「작업/」 폴더에 있어도 쓴 사람만. 「읽기만」 은 대화에 잇기·드라이브 최종본
올리기 같은 쓰기 길도 막힌다. 빠른 찾기도 못 여는 작업 문서 이름을 띄우지 않는다.
도우미: 화면 열기 주소는 우리 화면 경로만(열린 리다이렉트 막기), 마지막 단추 허락은 시키는 꼴의 말에만, 결과 보고는 대화 주인만,
이어 하기 차례(auraQueue)는 같은 사람·서버의 지금 제안으로만."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_project_share_rules import _share, _world
from tests.test_webhwp import _hwpx
from zzaimy.app import platform_guide, platform_ops, webhwp
from zzaimy.ingest import gdocs, gdrive


@pytest.fixture(autouse=True)
def _no_google(monkeypatch):
    monkeypatch.setattr(gdrive, "list_accounts", lambda: [])
    monkeypatch.setattr(gdocs, "has_docs_scope", lambda email: True)
    monkeypatch.setattr(webhwp, "status", lambda *a, **k: {"ok": True, "version": "t", "error": ""})


# ---- 공유 ----

def test_private_chat_work_copy_in_work_folder_stays_with_writer(tmp_path):
    """에이전트가 비공개 대화에서 「작업/<프로젝트>」 에 만든 작업본 — 폴더로도 이어졌지만 비공개 대화의 것이라 공유받은 사람은 못 연다."""
    app, db, pid, sid, f, cl = _world(tmp_path)
    base = Path(app.state.data_dir)
    agent = webhwp.add(db, base, "기획처", "비공개 초안.hwpx", _hwpx("비밀"), owner="lead", folder="작업/규칙시험사업",
                       source="대화-%d" % sid, project=pid)
    _share(cl["lead"], pid, [("user:mem", "edit"), ("user:san", "view")])
    assert cl["mem"].get(f"/hwp/{agent}/file").status_code == 200
    db.set_chat_private(sid, True)
    for who in ("mem", "san"):
        assert cl[who].get(f"/hwp/{agent}/file").status_code == 404
    assert cl["lead"].get(f"/hwp/{agent}/file").status_code == 200
    assert cl["mem"].get(f"/hwp/{f['work']}/file").status_code == 200            # 대화에 잇지 않은 폴더 작업 문서는 공유대로


def test_same_name_project_does_not_widen_access(tmp_path):
    """같은 이름 프로젝트 둘 — 둘 다에 「함께 작업」 인 사람이 둔 옛 파일은 두 프로젝트 모두에서 열 수 있어야 연다."""
    app, db, pid, sid, f, cl = _world(tmp_path)
    base = Path(app.state.data_dir)
    other = db.create_project("grant", "규칙시험사업", owner="san")              # 다른 부서 사람의 같은 이름 프로젝트
    _share(cl["lead"], pid, [("user:mem", "edit")])                               # 담당자 것: mem 만
    _share(cl["san"], other, [("user:mem", "edit"), ("user:boss", "view"), ("dept:기획처", "view")])
    legacy = webhwp.add(db, base, "기획처", "옛 작업본.hwpx", _hwpx("옛"), owner="mem", folder="작업/규칙시험사업")
    assert cl["mem"].get(f"/hwp/{legacy}/file").status_code == 200
    assert cl["san"].get(f"/hwp/{legacy}/file").status_code == 404                 # san 프로젝트로만 맞춰 넓어지지 않는다
    # 만들 때 프로젝트를 적은 새 파일은 그 프로젝트 하나로만 정한다
    stamped = webhwp.add(db, base, "기획처", "새 작업본.hwpx", _hwpx("새"), owner="mem", folder="작업/규칙시험사업", project=pid)
    assert cl["san"].get(f"/hwp/{stamped}/file").status_code == 404
    assert cl["lead"].get(f"/hwp/{stamped}/file").status_code == 200


def test_project_rename_keeps_private_work_docs_private(tmp_path):
    app, db, pid, sid, f, cl = _world(tmp_path)
    _share(cl["lead"], pid, [])                                                   # 나만 보기
    mem = cl["mem"]
    assert mem.get(f"/hwp/{f['work']}/file").status_code == 404
    r = cl["lead"].post(f"/projects/{pid}/rename", data={"name": "새 이름 사업"}, follow_redirects=False)
    assert r.status_code in (200, 303)
    assert db.get_project(pid)["name"] == "새 이름 사업"
    assert mem.get(f"/hwp/{f['work']}/file").status_code == 404                     # 폴더 이름과 어긋나도 프로젝트 규칙 그대로
    assert "작업 계획서" not in json.dumps(mem.get("/api/hwp/browse", params={"dept": "기획처", "folder": "작업/규칙시험사업"}).json(),
                                         ensure_ascii=False)
    assert cl["lead"].get(f"/hwp/{f['work']}/file").status_code == 200


def test_folder_rename_cannot_pull_work_docs_out_of_project(tmp_path):
    app, db, pid, sid, f, cl = _world(tmp_path)
    _share(cl["lead"], pid, [])
    mem = cl["mem"]
    for folder in ("작업/규칙시험사업", "작업"):
        r = mem.post("/hwp/folder-rename", data={"dept": "기획처", "folder": folder, "name": "풀림"})
        assert r.status_code == 403
    assert mem.get(f"/hwp/{f['work']}/file").status_code == 404
    # 고칠 수 있는 담당자는 그대로 바꾼다(그 뒤에도 권한은 프로젝트대로)
    assert cl["lead"].post("/hwp/folder-rename", data={"dept": "기획처", "folder": "작업/규칙시험사업", "name": "옮김"}).status_code == 200


def test_revoked_maker_file_stays_under_project_rule(tmp_path):
    """다른 부서 「함께 작업」 사람이 자기 부서 「작업/<프로젝트>」 에 둔 옛 파일 — 공유가 풀려도 그 부서 폴더 규칙으로 풀리지 않는다."""
    app, db, pid, sid, f, cl = _world(tmp_path)
    from tests.test_accounts import _login
    from fastapi.testclient import TestClient
    a = app.state.new_account("san2", "share-pass-123", "staff", "산학둘")
    a["dept"] = "산학협력단"
    app.state.accounts["san2"] = a
    app.state.save_accounts()
    san2 = TestClient(app)
    assert _login(san2, "san2", "share-pass-123")
    base = Path(app.state.data_dir)
    _share(cl["lead"], pid, [("user:san", "edit")])
    made = webhwp.add(db, base, "산학협력단", "산학 작업본.hwpx", _hwpx("산학"), owner="san", folder="작업/규칙시험사업")
    assert cl["san"].get(f"/hwp/{made}/file").status_code == 200
    assert san2.get(f"/hwp/{made}/file").status_code == 404                       # 같은 부서라도 공유 밖
    _share(cl["lead"], pid, [])                                                   # 공유를 거둔다
    assert cl["san"].get(f"/hwp/{made}/file").status_code == 404
    assert san2.get(f"/hwp/{made}/file").status_code == 404
    # 담당자 쪽 공유로 남의 부서 파일이 열리지도 않는다(그 부서 폴더 규칙도 맞아야) — 공유가 풀린 뒤에는 관리자만
    assert cl["lead"].get(f"/hwp/{made}/file").status_code == 404
    assert cl["boss"].get(f"/hwp/{made}/file").status_code == 200


def test_view_only_cannot_bind_to_chat_or_push_drive_final(tmp_path):
    app, db, pid, sid, f, cl = _world(tmp_path)
    _share(cl["lead"], pid, [("user:mem", "view"), ("user:san", "edit")])
    mem = cl["mem"]
    own = db.create_chat_session("내 대화", owner="mem")
    r = mem.post(f"/api/chat/{own}/hwp/bind", json={"id": f["work"]})
    assert r.status_code == 403
    from zzaimy.app import webhwp_agent
    assert f["work"] not in webhwp_agent.files_of(db, own)
    assert mem.post(f"/hwp/{f['work']}/to-drive").status_code == 403
    _share(cl["lead"], pid, [("user:mem", "edit")])
    assert mem.post(f"/api/chat/{own}/hwp/bind", json={"id": f["work"]}).status_code == 200      # 함께 작업은 된다


def test_quick_search_hides_private_work_doc_names(tmp_path):
    app, db, pid, sid, f, cl = _world(tmp_path)
    _share(cl["lead"], pid, [])
    names = [x["name"] for x in cl["mem"].get("/api/quick-search", params={"q": "작업"}).json()["hwp"]]
    assert "작업 계획서.hwpx" not in names and "대화 작업본.hwpx" not in [x["name"] for x in
                                                                      cl["mem"].get("/api/quick-search", params={"q": "대화"}).json()["hwp"]]
    assert "작업 계획서.hwpx" in [x["name"] for x in cl["lead"].get("/api/quick-search", params={"q": "작업"}).json()["hwp"]]


# ---- 업무 도우미 ----

def _ctx(tmp_path, said=""):
    from zzaimy.app.db import Database
    db = Database(tmp_path / "h.db")
    sid = db.create_chat_session("도우미", owner="kim", channel="helper")
    return platform_ops.Ctx(db=db, data_dir=tmp_path, user="kim", role="dev", said=said), sid


@pytest.mark.parametrize("page", ["//evil.example", "///evil.example", "//evil.example/hwp", "/\\evil.example", "/\\/evil.example",
                                  "javascript:alert(1)", "https://evil.example/", "/%2F%2Fevil.example", "/hwp/../../evil",
                                  "/./hwp", " //evil.example", "/hwp\n//evil", "\t/hwp"])
def test_open_page_refuses_outside_or_odd_paths(tmp_path, page):
    ctx, sid = _ctx(tmp_path)
    with pytest.raises(platform_ops.OpRefused):
        platform_ops.propose_open(ctx, sid, page, "열기", now=True)
    assert platform_ops.pending(ctx.db, sid) == []


def test_open_page_keeps_our_paths(tmp_path):
    ctx, sid = _ctx(tmp_path)
    for page, screen in (("/", "/?type=all"), ("/hwp", "/hwp"), ("/hwp/", "/hwp"), ("/project/3#notes", "/project/3#notes"), ("/settings", "/settings")):
        item = platform_ops.propose_open(ctx, sid, page, "열기")
        assert item["screen"] == screen and item["target"]["path"].startswith("/") and not item["target"]["path"].startswith("//")
        platform_ops.expire(ctx.db, sid)


def test_commanded_gaps_closed():
    make = ("만들어", "만들고", "생성해", "추가해")
    save = ("저장해", "저장하고", "저장까지", "공유해", "공유하고")
    # 시킨 말
    assert platform_ops.commanded("새 프로젝트 만들어 줘", make)
    assert platform_ops.commanded("2027 현장실습 프로젝트 만들어주세요.", make)
    assert platform_ops.commanded("성명 고치고 저장해", save)
    assert platform_ops.commanded("저장해 줘. 그리고 닫아", save)
    assert platform_ops.commanded("홍길동 넣고 저장하고 알려 줘", save)
    # 시킨 것이 아닌 말 — 이름을 꾸미는 꼴·묻는 꼴·막는 말이 뒤에 떨어져 붙은 꼴
    assert not platform_ops.commanded("어제 만들어 둔 프로젝트 열어 줘", make)
    assert not platform_ops.commanded("만들어진 프로젝트 목록 보여 줘", make)
    assert not platform_ops.commanded("저장해도 돼?", save)
    assert not platform_ops.commanded("이거 저장해야 하나요", save)
    assert not platform_ops.commanded("저장해선 안 돼, 넣기만", save)
    assert not platform_ops.commanded("공유해 주지는 마", save)
    assert not platform_ops.commanded("저장해 줘 아 아니다 저장하지 마", save)
    assert not platform_ops.commanded("공유해 버리면 안 되니까 칸만 채워", save)


def test_proposal_result_only_from_chat_owner(tmp_path):
    app, db, pid, sid, f, cl = _world(tmp_path)
    helper = db.create_chat_session("도우미", owner="lead", channel="helper")
    ctx = platform_ops.Ctx(db=db, data_dir=Path(app.state.data_dir), user="lead", role="staff")
    item = platform_ops.propose_open(ctx, helper, "/settings", "성명", now=True,
                                     steps=[{"element": '[data-aura="settings-name"]', "text": "홍길동"}])
    body = {"results": [{"step": 0, "result": "typed"}]}
    for who in ("mem", "san", "boss"):
        assert cl[who].post(f"/chat/{helper}/proposals/{item['id']}/result", json=body).status_code == 404
    assert cl["lead"].post(f"/chat/{helper}/proposals/{item['id']}/result", json=body).json()["message"]


def test_cursor_queue_is_bound_to_user_and_server_proposals():
    js = (platform_guide.TEMPLATES.parent / "static" / "aura-helper.js").read_text(encoding="utf-8")
    html = (platform_guide.TEMPLATES / "_aura_helper.html").read_text(encoding="utf-8")
    assert 'data-who="{{ request.state.user' in html
    # 이어 하기: 같은 사람·같은 화면(또는 커서가 누른 단추로 옮긴 화면)만, 단계·허락은 서버의 지금 제안에서 다시 만든다
    assert "v.who !== who" in js and '"/chat/" + encodeURIComponent(s0) + "/messages"' in js and "segOf(p, old.mode)" in js
    # 화면 이동은 우리 주소만
    assert "function localUrl(u)" in js and "location.href = seg.screen" in js and "screen: localUrl(p.screen)" in js
    assert "location.href = d.go" not in js and "go.href = p.screen" not in js
