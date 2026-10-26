"""프로젝트 공유 규칙(ADR-0075) — 「나만 보기」 프로젝트의 한글 작업 문서는 담당자·관리자만(같은 부서도 문서함·찾기·주소로 못 엶),
공유를 켜면 공유 범위대로(읽기만=보기, 함께 작업=고치기). 공유받은 다른 부서 사람은 담당자 대화(비공개 제외)를 읽되 근거는 자기 열람
범위 안의 것만 열고 범위 밖은 「볼 수 없는 자료」 로 가린다. 출처 문서 주소(/doc/{id})는 원래 열람 검사 그대로."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_accounts import _app, _login
from tests.test_webhwp import _hwpx
from zzaimy.app import webhwp
from zzaimy.ingest import gdocs, gdrive

PW = "share-pass-123"


@pytest.fixture(autouse=True)
def _no_google(monkeypatch):
    monkeypatch.setattr(gdrive, "list_accounts", lambda: [])
    monkeypatch.setattr(gdocs, "has_docs_scope", lambda email: True)
    monkeypatch.setattr(webhwp, "status", lambda *a, **k: {"ok": True, "version": "t", "error": ""})


def _world(tmp_path):
    app = _app(tmp_path)
    for uid, role, dept, name in (("lead", "staff", "기획처", "박책임"), ("mem", "staff", "기획처", "김기획"),
                                  ("san", "staff", "산학협력단", "정산학"), ("boss", "dev", "", "관리자")):
        a = app.state.new_account(uid, PW, role, name)
        a["dept"], a["name"] = dept, name
        app.state.accounts[uid] = a
    app.state.save_accounts()
    db = app.state.db
    pid = db.create_project("grant", "규칙시험사업", owner="lead")
    base = Path(app.state.data_dir)
    work = webhwp.add(db, base, "기획처", "작업 계획서.hwpx", _hwpx("계획"), owner="lead", folder="작업/규칙시험사업")
    sid = db.create_chat_session("개요 초안", project_id=pid, owner="lead")
    linked = webhwp.add(db, base, "기획처", "대화 작업본.hwpx", _hwpx("대화"), owner="lead", folder="공문")
    db.set_setting(f"chat_hwp_files:{sid}", json.dumps([linked]))
    plain = webhwp.add(db, base, "기획처", "부서 공문.hwpx", _hwpx("공문"), owner="lead", folder="공문")
    cl = {}
    for uid in ("lead", "mem", "san", "boss"):
        c = TestClient(app)
        assert _login(c, uid, PW)
        cl[uid] = c
    return app, db, pid, sid, {"work": work, "linked": linked, "plain": plain}, cl


def _share(c, pid, items):
    r = c.post(f"/project/{pid}/shares", data={"mode": "shared" if items else "private",
                                                "to": [t for t, _ in items], "level": [lv for _, lv in items]}, follow_redirects=False)
    assert r.status_code == 303


def test_private_project_work_docs_open_only_for_owner_and_admin(tmp_path):
    app, db, pid, sid, f, cl = _world(tmp_path)
    _share(cl["lead"], pid, [])                                   # 나만 보기
    mem = cl["mem"]
    for fid in (f["work"], f["linked"]):
        assert cl["lead"].get(f"/hwp/{fid}/file").status_code == 200
        assert cl["boss"].get(f"/hwp/{fid}/file").status_code == 200
        assert mem.get(f"/hwp/{fid}/file").status_code == 404          # 같은 부서라도 못 연다
        assert mem.post(f"/hwp/{fid}/save", content=_hwpx("x")).status_code == 404
    assert mem.get(f"/hwp/{f['plain']}/file").status_code == 200      # 작업 문서가 아닌 부서 문서는 부서 폴더 규칙 그대로
    # 문서함 목록·찾기에서도 안 보인다 — 작업 폴더째 숨고 부서 문서 수도 빼고 센다
    home = mem.get("/hwp", params={"dept": "기획처"}).text
    assert "작업" not in [x["name"] for x in mem.get("/api/hwp/browse", params={"dept": "기획처"}).json()["here"]["folders"]]
    folder = mem.get("/api/hwp/browse", params={"dept": "기획처", "folder": "공문"}).json()["here"]["files"]
    assert [x["name"] for x in folder] == ["부서 공문.hwpx"]
    count = {x["dept"]: x["count"] for x in mem.get("/api/hwp/browse").json()["depts"]}
    assert count["기획처"] == 1 and "작업 계획서" not in home
    assert mem.get("/api/finder/search", params={"q": "작업"}).json()["dept"] == []
    lead_folder = cl["lead"].get("/api/hwp/browse", params={"dept": "기획처", "folder": "공문"}).json()["here"]["files"]
    assert {x["name"] for x in lead_folder} == {"부서 공문.hwpx", "대화 작업본.hwpx"}


def test_shared_project_work_docs_follow_share_level(tmp_path):
    app, db, pid, sid, f, cl = _world(tmp_path)
    mem, san = cl["mem"], cl["san"]
    _share(cl["lead"], pid, [("user:mem", "view"), ("user:san", "edit")])
    for fid in (f["work"], f["linked"]):
        assert mem.get(f"/hwp/{fid}/file").status_code == 200
        assert mem.post(f"/hwp/{fid}/save", content=_hwpx("x")).status_code == 403   # 읽기만 — 같은 부서여도 고치지 못함
        assert san.get(f"/hwp/{fid}/file").status_code == 200
        assert san.post(f"/hwp/{fid}/save", content=_hwpx("고침")).status_code == 200
    # 대화를 비공개로 돌리면 그 대화에 이은 파일은 대화를 쓴 사람만(폴더로 이어진 작업 문서는 공유대로)
    db.set_chat_private(sid, True)
    assert mem.get(f"/hwp/{f['linked']}/file").status_code == 404 and san.get(f"/hwp/{f['linked']}/file").status_code == 404
    assert cl["lead"].get(f"/hwp/{f['linked']}/file").status_code == 200
    assert mem.get(f"/hwp/{f['work']}/file").status_code == 200


def test_other_department_reads_owner_chat_with_evidence_limited_to_own_scope(tmp_path):
    app, db, pid, sid, f, cl = _world(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    (inbox / "a.txt").write_text("a")
    open_doc = db.add_document("공통 운영 지침서.pdf", str(inbox / "a.txt"), owner="lead", dept="공통", access_level="public")
    closed = db.add_document("기획처 내부 예산 검토안.pdf", str(inbox / "a.txt"), owner="lead", dept="기획처", access_level="dept")
    db.add_chat(sid, "user", f"[첨부#{closed}] 기획처 내부 예산 검토안.pdf\n개요 써 줘")
    db.add_chat(sid, "assistant", "공통 운영 지침서와 기획처 내부 예산 검토안을 보면 개요는 이렇습니다.")
    app.state.chat.chat_topics.record(sid, [
        {"doc_id": open_doc, "title": "공통 운영 지침서", "heading": "제1조", "snippet": "공통 조각 글"},
        {"doc_id": closed, "title": "기획처 내부 예산 검토안", "heading": "예산", "snippet": "비밀 조각 글"}])
    hidden = db.create_chat_session("비밀 메모", project_id=pid, owner="lead")
    db.set_chat_private(hidden, True)
    _share(cl["lead"], pid, [("user:san", "view")])
    san = cl["san"]
    page = san.get(f"/project/{pid}").text
    assert f'href="/chat/{sid}/read"' in page and f"/chat/{hidden}" not in page
    assert "다른 사람 대화" not in page and "열리지 않습니다" not in page
    t = san.get(f"/chat/{sid}/read").text
    # 질문·답 글은 모두 보인다
    assert "개요 써 줘" in t and "와 기획처 내부 예산 검토안을 보면 개요는 이렇습니다" in t
    # 근거 — 볼 수 있는 것만 링크, 범위 밖은 이름을 가리고 열지 않는다(조각도 안 실림)
    assert f'href="/doc/{open_doc}' in t and f'href="/doc/{closed}' not in t
    assert "볼 수 없는 자료" in t and "첨부 · 볼 수 없는 자료" in t
    assert "비밀 조각 글" not in t and 'title="기획처 내부 예산 검토안"' not in t
    # 출처 문서 주소로 직접 가도 원래 열람 검사 그대로 — 새지 않는다
    assert san.get(f"/doc/{closed}").status_code in (403, 404)
    assert san.get(f"/doc/{open_doc}").status_code == 200
    assert san.get(f"/chat/{hidden}/read").status_code == 404
    # 같은 부서 담당자 본인은 그대로 다 연다
    own = cl["lead"].get(f"/chat/{sid}/read").text
    assert f'href="/doc/{closed}' in own
    # 공유를 거두면 대화도 닫힌다
    _share(cl["lead"], pid, [])
    assert san.get(f"/chat/{sid}/read").status_code == 404


def test_shared_viewer_head_shows_level_chip(tmp_path):
    app, db, pid, sid, f, cl = _world(tmp_path)
    _share(cl["lead"], pid, [("user:mem", "edit"), ("user:san", "view")])
    assert '<span class="pill project-share-chip"' in cl["mem"].get(f"/project/{pid}").text
    assert ">함께 작업</span>" in cl["mem"].get(f"/project/{pid}").text
    t = cl["san"].get(f"/project/{pid}").text
    assert ">읽기만</span>" in t and "님의 프로젝트입니다" not in t
    assert "project-share-chip" not in cl["lead"].get(f"/project/{pid}").text


def test_department_share_does_not_reach_other_departments(tmp_path):
    """부서 공유는 고른 부서 소속만 — 다른 부서(아래 팀 포함)로 저절로 가지 않는다."""
    app, db, pid, sid, f, cl = _world(tmp_path)
    _share(cl["lead"], pid, [("dept:기획처", "view")])
    assert cl["mem"].get(f"/project/{pid}").status_code == 200
    assert cl["san"].get(f"/project/{pid}").status_code == 404
    assert cl["san"].get(f"/hwp/{f['work']}/file").status_code == 404
