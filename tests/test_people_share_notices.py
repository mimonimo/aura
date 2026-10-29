"""사람 고르기 공유 쓰임 — 프로젝트 공유 창의 사람 고르기 표식, /api/people?purpose=share 범위·개인정보 없음,
사업 관리 신청·승인·반려의 종(업무 알림), 부서 사람 화면의 찾기·등급 묶음, 사람 고르기의 글자 바꾸기. 사람은 모두 가짜 값."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_program_admin_flow import OTHER, filled  # noqa: F401  (고정물)
from tests.test_program_sheet import world  # noqa: F401  (고정물)
from tests.test_staff_grades import _add, _client, _no_google  # noqa: F401  (구글 끄기 고정물)
from zzaimy.app import notifications

STATIC = Path(__file__).resolve().parents[1] / "src" / "zzaimy" / "app" / "static"
QUOTED = 'Kim "Q" O\'Neil'


@pytest.fixture()
def share_world(filled):
    app, tmp_path, dev, *_ = filled
    _add(app, "plain", dept="기획처", grade="member")
    _add(app, "gone", dept="교무처", grade="lead")
    app.state.accounts["gone"]["active"] = False
    _add(app, "stu", role="student", dept="기획처")
    a = app.state.accounts["boss2"]
    a.update({"name": QUOTED, "email": "fake@example.com", "phone": "010-0000-0000"})
    app.state.save_accounts()
    pid = app.state.db.create_project("grant", "공유 시험", owner="lead1")
    return app, tmp_path, dev, pid


def test_people_share_scope_and_no_private_fields(share_world):
    app, _, dev, _ = share_world
    plain = _client(app, "plain")                                       # 사업 관리 권한 없는 일반 직원도 공유는 한다
    assert plain.get("/api/people").status_code == 403                  # 기존 사업 관리 쓰임은 그대로 막힘
    r = plain.get("/api/people", params={"purpose": "share", "limit": 50})
    assert r.status_code == 200
    d = r.json()
    got = {p["user"] for p in d["people"]}
    assert {"boss", "lead1", "plain", "boss2", "clerk1"} <= got          # 다른 부서 교직원도
    assert "gone" not in got and "stu" not in got                        # 사용 중이 아닌 계정·학생은 빼고
    assert all(set(p) == {"user", "name", "dept"} for p in d["people"])  # 아이디·이름·부서만
    assert "fake@example.com" not in r.text and "010-0000-0000" not in r.text
    assert d["my_dept"] == "기획처"
    q = plain.get("/api/people", params={"purpose": "share", "q": "O'Neil"}).json()["people"]
    assert [p["user"] for p in q] == ["boss2"] and q[0]["name"] == QUOTED   # 이름은 원문 그대로(화면이 바꿔 넣는다)
    depts = plain.get("/api/people", params={"purpose": "share", "kind": "dept", "q": "기획"}).json()["depts"]
    assert depts and depts[0]["user"] == "dept:기획처" and depts[0]["kind"] == "dept"
    assert _client(app, "stu").get("/api/people", params={"purpose": "share"}).status_code == 403
    # 기존 사업 관리 쓰임 — 등급 칸은 그대로
    assert set(dev.get("/api/people", params={"q": "clerk"}).json()["people"][0]) == {"user", "name", "dept", "grade", "grade_label"}


def test_project_share_modal_uses_people_picker(share_world):
    app, _, _, pid = share_world
    lead = _client(app, "lead1")
    page = lead.get(f"/project/{pid}").text
    assert "/static/people-picker.js" in page and "/static/people-picker.css" in page
    modal = page.split('id="shareModal"')[1].split("</form>")[0]
    assert "data-people-picker" in modal and "data-pass" in modal and 'data-purpose="share"' in modal and 'data-kind="dept"' in modal
    assert 'data-owner="lead1"' in modal and "share-owner-level" in modal and "소유" in modal
    assert "data-share-choices" not in page                              # 사람 목록을 화면에 싣지 않는다
    # 저장은 전과 같이 — 사람·부서 줄마다 권한
    r = lead.post(f"/project/{pid}/shares", data={"mode": "shared", "to": ["user:boss2", "dept:기획처"], "level": ["edit", "view"]},
                  follow_redirects=False)
    assert r.status_code == 303
    modal = lead.get(f"/project/{pid}").text.split('id="shareModal"')[1].split("</form>")[0]
    assert 'data-to="user:boss2"' in modal and 'data-to="dept:기획처"' in modal
    assert 'value="edit" selected' in modal
    assert QUOTED not in modal and "Kim &#34;Q&#34; O&#39;Neil" in modal  # 따옴표 든 이름도 바꿔 넣는다


def test_program_request_notices_in_bell(share_world):
    app, _, dev, _ = share_world
    boss = _client(app, "boss")
    boss.post("/programs/admin/request", data={"key": OTHER, "kind": "assign", "role": "heads", "reason": "공동 운영"})
    d = dev.get("/api/notifications/notices").json()
    assert d["count"] == 1
    n = d["items"][0]
    assert n["text"] == "사업 관리 신청: 마바사 지원사업 지정 신청" and n["href"] == "/programs/admin?view=requests"
    dev.post("/programs/admin/request/decide", data={"id": "1", "decision": "no", "note": "부서 협의 먼저"})
    b = boss.get("/api/notifications/notices").json()
    assert b["count"] == 1 and "반려" in b["items"][0]["text"] and "부서 협의 먼저" in b["items"][0]["text"]
    boss.post("/programs/admin/request", data={"key": OTHER, "kind": "assign", "role": "heads", "reason": "다시"})
    dev.post("/programs/admin/request/decide", data={"id": "2", "decision": "ok"})
    b = boss.get("/api/notifications/notices").json()
    assert b["count"] == 2 and "승인" in b["items"][0]["text"]          # 최근 것부터
    boss.post("/api/notifications/notices/read", data={"id": str(b["items"][0]["id"])})
    assert boss.get("/api/notifications/notices").json()["count"] == 1
    boss.post("/api/notifications/notices/read")
    assert boss.get("/api/notifications/notices").json()["count"] == 0
    assert "data-notice-list" in dev.get("/programs/admin").text          # 종 안 자리


def test_notify_keeps_app_links_only(tmp_path):
    from tests.test_accounts import _app
    db = _app(tmp_path).state.db
    notifications.notify(db, ["a", "a", ""], "하나", "https://example.com/x")
    notifications.notify(db, ["a"], "둘", "//evil")
    items = notifications.unseen_notices(db, "a")
    assert [n["text"] for n in items] == ["하나", "둘"] and all(n["href"] == "" for n in items)
    for i in range(notifications.NOTICE_KEEP + 5):
        notifications.notify(db, ["a"], f"n{i}", "/x")
    assert len(json.loads(db.get_setting("notices:a", "[]"))) == notifications.NOTICE_KEEP


def test_team_members_sorted_by_grade(share_world):
    """조직 관리의 구성원 — 자료(JSON)는 등급 높은 차례, 이름은 원문 그대로(화면이 textContent 로 넣는다)."""
    app, _, dev, _ = share_world
    rows = dev.get("/api/org/members", params={"dept": "기획처"}).json()["rows"]
    rank = {"head": 3, "lead": 2, "clerk": 2, "member": 1}
    prim = [r for r in rows if r["primary"]]
    assert [rank.get(r["grade"], 0) for r in prim] == sorted((rank.get(r["grade"], 0) for r in prim), reverse=True)
    src = (STATIC / "org-chart.js").read_text(encoding="utf-8")
    assert "innerHTML" not in src and "textContent" in src


def test_people_picker_escapes_quotes():
    src = (STATIC / "people-picker.js").read_text(encoding="utf-8")
    assert "&quot;" in src and "&#39;" in src and "d.textContent = t" not in src
    if not shutil.which("node"):
        pytest.skip("node 없음")
    body = src.split("function esc(t) {")[1].split("\n  }")[0]
    js = "function esc(t) {" + body + "\n}\nprocess.stdout.write(esc(%s));" % json.dumps('<a title="x" onclick=\'y\'>&')
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20).stdout
    assert out == "&lt;a title=&quot;x&quot; onclick=&#39;y&#39;&gt;&amp;"
