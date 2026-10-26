"""플랫폼 업무 도우미(떠 있는 창) — 안내 자료가 화면과 맞는지, 플랫폼 질문이 안내 걸음으로 가고 [실행] 제안이 뜨는지,
누르면 화면과 같은 일이 되고 권한 없는 계정은 막히는지, 문서 질문·작성은 에이전트 대화로 넘어가는지.

가짜 Writer 가 정해진 JSON 걸음을 낸다. 데이터는 합성이다."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from fastapi.testclient import TestClient

from test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app import dev_nav, platform_guide, platform_ops
from zzaimy.app.main import create_app


# ---- 안내 자료가 화면과 어긋나지 않는다 ----

def test_every_menu_page_has_a_guide_entry():
    menu = [href for _, links in dev_nav.GROUPS for href, _ in links] + ["/chat", "/?type=all"]
    for href in menu:
        assert href in platform_guide.BY_PATH, f"{href} 화면의 안내 항목이 없다 — platform_guide.PAGES 에 넣는다"
        assert platform_guide.page_for(href.split("?")[0] if href != "/?type=all" else "/").path == href


def test_guide_buttons_and_anchors_exist_in_templates():
    for p in platform_guide.PAGES:
        src = (platform_guide.TEMPLATES / p.template).read_text(encoding="utf-8")
        for b in p.buttons:
            assert b in src, f"{p.path} 안내의 단추 「{b}」 가 {p.template} 에 없다 — 화면이 바뀌었으면 안내도 고친다"
        for t in p.tasks:
            if t.anchor:
                assert f'id="{t.anchor}"' in src, f"{p.path}#{t.anchor} 자리가 {p.template} 에 없다"
            if t.op:
                assert t.op in platform_ops.OPS
        for t in p.tasks:
            if t.mark:
                where = platform_guide.TEMPLATES / (t.mark_in or p.template)
                assert t.mark in where.read_text(encoding="utf-8"), f"{p.path} 「{t.what}」 의 요소 {t.mark} 가 {where.name} 에 없다"
    for op in platform_ops.OPS.values():
        path, _, anchor = op.screen.partition("#")
        page = platform_guide.BY_PATH[path]
        src = (platform_guide.TEMPLATES / page.template).read_text(encoding="utf-8")
        if anchor:
            assert f'id="{anchor}"' in src
        if op.mark:
            assert op.mark in (platform_guide.TEMPLATES / (op.mark_in or page.template)).read_text(encoding="utf-8")
            assert op.target["selector"] == f"[{op.mark}]"


def test_lookup_puts_current_page_first_and_explains_terms():
    got = platform_guide.lookup("학생 RAG 등록 어떻게 해?", current="/dev/rag")
    assert got.startswith("■ RAG 공간")
    assert "학생 RAG:" in got and "/dev/rag#student-regs" in got and "「학생 공개」" in got
    assert "op=regs.audience" in got and '[data-rr="student"]' in got        # 가리킬 요소도 함께
    staff = platform_guide.lookup("학생 RAG 등록", current="/dev/rag", role="staff")
    assert "지금 계정으로는 열 수 없음" in staff


# ---- 에이전트 걸음 ----

def _step(action, **args):
    return json.dumps({"thought": "t", "action": action, "args": args}, ensure_ascii=False)


class _Agent:
    """걸음을 차례로 내는 가짜 Writer — 받은 메시지를 남긴다."""

    def __init__(self, steps):
        self.steps, self.model, self._extra, self.seen = list(steps), "fake", {}, []
        outer = self

        class _C:
            def create(self, **kw):
                outer.seen.append(kw["messages"])
                content = outer.steps.pop(0) if outer.steps else _step("finish", message="끝")
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=_C()))


def _use_agent(monkeypatch, steps):
    from zzaimy.generate import client as _gc

    fake = _Agent(steps)
    monkeypatch.setattr(_gc, "VllmClient", lambda *a, **k: fake)
    return fake


def _seed(db):
    reg = db.add_document("학사 운영 규정.hwp", "x", doc_type="regulation")
    other = db.add_document("내부 심사 규정.hwp", "x", doc_type="regulation")
    for d in (reg, other):
        db.update_document(d, status="reviewed")
    return reg, other


STEPS = lambda reg: [  # noqa: E731
    _step("platform_help", query="학생 RAG 등록"),
    _step("platform_lookup", what="registered", query="학사"),
    _step("propose_action", op="regs.audience", args={"documents": [str(reg)], "audience": "student"}),
    _step("finish", message="학생 RAG 는 학생 계정이 찾는 규정 묶음입니다. RAG 공간(/dev/rag#student-regs)의 「등록된 기준 문서」 에서 골라 「학생 공개」 를 누르면 됩니다. "
                            "아래 [실행] 을 누르면 학사 운영 규정을 학생 공개로 바꿉니다."),
]


def test_platform_question_explains_and_offers_run_button(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_PLATFORM_HELPER", "1")
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    c, db = TestClient(app), app.state.db
    reg, other = _seed(db)
    fake = _use_agent(monkeypatch, STEPS(reg))

    sid = c.post("/chat/ask", data={"question": "학생 RAG 등록 어떻게 해?", "page": "/dev/rag"}).json()["session_id"]

    first = "\n".join(m["content"] for m in fake.seen[0])
    assert "지금 보는 화면: RAG 공간 (/dev/rag)" in first           # 위젯이 보낸 화면이 상태에 들어간다
    system = fake.seen[0][0]["content"]
    assert system.startswith("당신은 대학 행정 업무 플랫폼 AURA 의 플랫폼 업무 도우미다")    # 에이전트 대화와 다른 지시·도구
    assert "- platform_help:" in system and "- search:" not in system and "- write_section:" not in system
    ep = [json.loads(x) for x in (tmp_path / "platform_helper_episodes.jsonl").read_text(encoding="utf-8").splitlines()][-1]
    acts = [s["action"] for s in ep["steps"]]
    assert acts == ["platform_help", "platform_lookup", "propose_action", "finish"]   # 사업 문서 search 없이
    assert "학생 RAG:" in ep["steps"][0]["observation"]
    assert "(%d) 학사 운영 규정.hwp" % reg in ep["steps"][1]["observation"]

    got = c.get(f"/chat/{sid}/messages").json()
    assert "/dev/rag#student-regs" in got["messages"][-1]["content"]
    [prop] = got["proposals"]
    assert "학사 운영 규정" in prop["plan"] and "학생 공개" in prop["plan"] and prop["screen"] == "/dev/rag#student-regs"
    assert prop["kind"] == "run" and prop["target"] == {"path": "/dev/rag", "anchor": "student-regs", "selector": '[data-rr="student"]'}
    assert db.get_document(reg).get("audience") != "student"       # 제안만 — 누르기 전에는 바꾸지 않는다

    r = c.post(f"/chat/{sid}/proposals/{prop['id']}/run").json()
    assert r["ok"] and "1건" in r["message"] and "교직원만 → 학생 공개" in r["message"]
    assert db.get_document(reg)["audience"] == "student"
    assert db.get_document(other).get("audience") != "student"
    assert c.get(f"/chat/{sid}/messages").json()["proposals"] == []
    assert not (tmp_path / "doc_agent_episodes.jsonl").exists()        # 에이전트 대화는 돌지 않았다
    assert "실행했습니다" in c.get(f"/chat/{sid}/messages").json()["messages"][-1]["content"]
    again = c.post(f"/chat/{sid}/proposals/{prop['id']}/run").json()
    assert again["ok"] is False                                     # 두 번 실행하지 않는다
    log = [json.loads(x) for x in (tmp_path / "platform_actions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[-1]["op"] == "regs.audience" and log[-1]["ok"] and log[-1]["args"]["ids"] == [reg]


def test_new_request_expires_old_proposals(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_PLATFORM_HELPER", "1")
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    c, db = TestClient(app), app.state.db
    reg, _ = _seed(db)
    _use_agent(monkeypatch, STEPS(reg) + [_step("finish", message="다른 답")])
    sid = c.post("/chat/ask", data={"question": "학생 RAG 등록 어떻게 해?", "page": "/dev/rag"}).json()["session_id"]
    pid = c.get(f"/chat/{sid}/messages").json()["proposals"][0]["id"]
    c.post("/chat/ask", data={"question": "고마워", "session_id": str(sid), "page": "/dev/rag"})
    assert c.get(f"/chat/{sid}/messages").json()["proposals"] == []
    assert c.post(f"/chat/{sid}/proposals/{pid}/run").json()["ok"] is False
    assert db.get_document(reg).get("audience") != "student"


def _auth_app(tmp_path):
    (tmp_path / "accounts.json").write_text(json.dumps({
        "zzaimy": {"pw": "staffpass", "role": "staff"},
        "zzdev": {"pw": "devpass", "role": "dev"},
    }))
    return create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                      drafter=FakeDrafter(), responder=FakeResponder(), password="boot-pass-1")


def _login(c, uid, pw):
    assert c.post("/login", data={"username": uid, "pw": pw}, follow_redirects=False).status_code == 303


def test_staff_account_cannot_propose_or_run_admin_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_PLATFORM_HELPER", "1")
    app = _auth_app(tmp_path)
    db = app.state.db
    reg, _ = _seed(db)
    _use_agent(monkeypatch, [
        _step("platform_lookup", what="registered"),
        _step("propose_action", op="regs.audience", args={"documents": [str(reg)], "audience": "student"}),
        _step("finish", message="학생 공개 지정은 관리자가 RAG 공간에서 합니다. 관리자에게 요청해 주세요."),
    ])
    staff = TestClient(app)
    _login(staff, "zzaimy", "staffpass")
    sid = staff.post("/chat/ask", data={"question": "학사 운영 규정 학생 공개로 해 줘", "page": "/chat"}).json()["session_id"]
    ep = [json.loads(x) for x in (tmp_path / "platform_helper_episodes.jsonl").read_text(encoding="utf-8").splitlines()][-1]
    look, prop = ep["steps"][0], ep["steps"][1]
    assert "관리자 계정만" in look["observation"] and "학사 운영 규정" not in look["observation"]   # 목록(문서 이름)을 보이지 않는다
    assert "관리자 계정만" in prop["observation"]
    assert staff.get(f"/chat/{sid}/messages").json()["proposals"] == []
    assert db.get_document(reg).get("audience") != "student"

    # 관리자 대화에 올라온 제안도 남의 계정은 누를 수 없고, 교직원 계정은 자기 대화에 심어진 제안도 실행하지 못한다
    platform_ops._save(db, sid, [{"id": "abc123", "op": "regs.audience", "label": "x", "args": {"ids": [reg], "audience": "student"},
                                  "plan": "p", "screen": "/dev/rag#student-regs", "status": "pending"}])
    r = staff.post(f"/chat/{sid}/proposals/abc123/run")
    assert r.status_code == 403
    assert db.get_document(reg).get("audience") != "student"
    dev = TestClient(app)
    _login(dev, "zzdev", "devpass")
    assert dev.post(f"/chat/{sid}/proposals/abc123/run").status_code == 404      # 남의 대화
    assert db.get_document(reg).get("audience") != "student"


def test_admin_can_register_candidate_and_create_dept(tmp_path):
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    ctx = platform_ops.Ctx(db=db, data_dir=tmp_path, user="zzdev", role="dev")
    plan, norm = platform_ops.OPS["dept_rag.create"].check(ctx, {"name": "산학협력단"})
    assert "산학협력단" in plan
    msg, changed = platform_ops.OPS["dept_rag.create"].run(ctx, norm)
    from zzaimy.app import rag_spaces
    assert any(r["dept"] == "산학협력단" for r in rag_spaces.config(tmp_path)["dept_rags"])
    try:
        platform_ops.OPS["dept_rag.create"].check(ctx, {"name": "산학협력단"})
        raise AssertionError("이미 있는 부서를 다시 만들면 안 된다")
    except platform_ops.OpRefused as e:
        assert "이미" in str(e)
    staff = platform_ops.Ctx(db=db, data_dir=tmp_path, user="zzaimy", role="staff")
    try:
        platform_ops.propose(staff, 1, "dept_rag.create", {"name": "기획처"})
        raise AssertionError("교직원은 부서를 만들 수 없다")
    except platform_ops.OpRefused as e:
        assert "관리자" in str(e)


def test_widget_sends_page_and_draws_run_buttons(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    html = TestClient(app).get("/inbox").text
    assert "/static/aura-helper.js" in html and "/static/aura-helper.css" in html
    js = (platform_guide.TEMPLATES.parent / "static" / "aura-helper.js").read_text(encoding="utf-8")
    assert 'fd.append("page", location.pathname)' in js
    assert re.search(r'"/proposals/" \+ encodeURIComponent\(p\.id\) \+ "/run"', js)


def _helper_app(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_PLATFORM_HELPER", "1")
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    return app, TestClient(app), app.state.db


def test_document_question_is_handed_to_agent_chat(tmp_path, monkeypatch):
    app, c, db = _helper_app(tmp_path, monkeypatch)
    _use_agent(monkeypatch, [
        _step("propose_action", op="chat.handoff", args={"question": "올해 지원사업 공고의 신청 자격을 알려 줘"}),
        _step("finish", message="문서 내용 질문이라 에이전트 대화에서 이어 하겠습니다. 아래 [실행] 을 누르세요."),
    ])
    sid = c.post("/chat/ask", data={"question": "올해 지원사업 공고의 신청 자격이 뭐야?", "page": "/dev/rag"}).json()["session_id"]
    [prop] = c.get(f"/chat/{sid}/messages").json()["proposals"]
    assert prop["label"] == "에이전트 대화에서 이어 하기"
    n_sessions = len(db.list_chat_sessions()) if hasattr(db, "list_chat_sessions") else None
    r = c.post(f"/chat/{sid}/proposals/{prop['id']}/run").json()
    assert r["ok"] and re.fullmatch(r"/chat/\d+", r["go"])
    new_sid = int(r["go"].rsplit("/", 1)[1])
    assert new_sid != sid
    msgs = c.get(r["go"] + "/messages").json()["messages"]
    assert msgs[0]["role"] == "user" and "신청 자격" in msgs[0]["content"]
    assert msgs[-1]["role"] == "assistant" and "합성 답변" in msgs[-1]["content"]      # 기존 답변 경로가 답했다
    if n_sessions is not None:
        assert len(db.list_chat_sessions()) == n_sessions + 1


def test_simple_document_question_is_answered_in_helper(tmp_path, monkeypatch):
    """간단한 문서 질문은 도우미가 열람 범위 안에서 찾아 바로 답한다 — 넘기기 제안 없이."""
    from zzaimy.app.regulations import split_regulation

    app, c, db = _helper_app(tmp_path, monkeypatch)
    reg = db.add_document("합성 학사 규정.hwp", "x", doc_type="regulation")
    db.update_document(reg, status="reviewed")
    db.add_regulation_chunks(reg, "합성 학사 규정", split_regulation("제1조(목적) 합성. 제7조(휴학 기간) 휴학은 통산 3년을 넘지 못한다."))
    fake = _use_agent(monkeypatch, [
        _step("doc_search", query="휴학 기간"),
        _step("finish", message="휴학은 통산 3년을 넘지 못합니다(「합성 학사 규정」 제7조)."),
    ])
    sid = c.post("/chat/ask", data={"question": "휴학은 최대 몇 년까지야?", "page": "/"}).json()["session_id"]
    got = c.get(f"/chat/{sid}/messages").json()
    assert "통산 3년" in got["messages"][-1]["content"] and not got["proposals"]
    observed = "\n".join(str(m.get("content")) for m in fake.seen[-1])
    assert "합성 학사 규정" in observed and "통산 3년" in observed       # 도구 결과에 규정 글이 왔다
    assert "doc_search" in str(fake.seen[0][0]["content"])               # 도구 목록에 있다


def test_writing_request_starts_project_and_sends_first_request(tmp_path, monkeypatch):
    app, c, db = _helper_app(tmp_path, monkeypatch)
    _use_agent(monkeypatch, [
        _step("finish", message="어느 사업의 어떤 문서인지 알려 주세요. 업무 영역도 알려 주시면 프로젝트를 만들어 드립니다."),
        _step("propose_action", op="project.start",
              args={"sector": "국고사업", "name": "2027 현장실습 지원사업 계획서", "due_date": "2026-11-30",
                    "first_request": "2027 현장실습 지원사업 계획서 초안을 쓰려고 합니다. 공고와 작성 서식을 확인하고 쓸 절 차례를 정리해 주세요."}),
        _step("finish", message="프로젝트를 만들고 첫 요청을 보낼 준비를 했습니다. [실행] 을 누르면 그 대화로 옮겨 갑니다."),
    ])
    sid = c.post("/chat/ask", data={"question": "계획서 쓰고 싶어", "page": "/inbox"}).json()["session_id"]
    assert c.get(f"/chat/{sid}/messages").json()["proposals"] == []          # 모르는 것은 먼저 묻는다
    c.post("/chat/ask", data={"question": "국고사업이고 2027 현장실습 지원사업 계획서야, 마감은 11월 30일", "session_id": str(sid), "page": "/inbox"})
    [prop] = c.get(f"/chat/{sid}/messages").json()["proposals"]
    assert "2027 현장실습 지원사업 계획서" in prop["plan"] and "국고사업" in prop["plan"]
    assert prop["target"]["selector"] == '[data-modal-open="#projectModal"]'
    assert db.list_all_projects() == []                                       # 누르기 전에는 만들지 않는다
    r = c.post(f"/chat/{sid}/proposals/{prop['id']}/run").json()
    assert r["ok"] and r["go"].startswith("/chat/")
    [proj] = db.list_all_projects()
    assert proj["name"] == "2027 현장실습 지원사업 계획서" and proj["sector"] == "grant" and proj["due_date"] == "2026-11-30"
    new_sid = int(r["go"].rsplit("/", 1)[1])
    assert db.get_chat_session(new_sid)["project_id"] == proj["id"]
    msgs = c.get(r["go"] + "/messages").json()["messages"]
    assert msgs[0]["content"].startswith("2027 현장실습 지원사업 계획서 초안을 쓰려고 합니다")
    assert msgs[-1]["role"] == "assistant"                                   # 첫 요청의 답을 에이전트 대화가 만들었다
    assert "프로젝트" in r["message"] and f"/project/{proj['id']}" in r["message"]


def test_open_page_points_at_element_and_respects_role(tmp_path, monkeypatch):
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    sid = db.create_chat_session("도우미", owner="zzaimy")
    dev = platform_ops.Ctx(db=db, data_dir=tmp_path, user="zzaimy", role="dev")
    item = platform_ops.propose_open(dev, sid, "/?type=all", "문서 올리기 창을 엽니다", '[data-modal-open="#libUploadModal"]')
    assert item["kind"] == "go" and item["target"]["selector"] == '[data-modal-open="#libUploadModal"]'
    bad = platform_ops.propose_open(dev, sid, "/dev/rag#student-regs", "학생 공개", "[onclick=x]")
    assert bad["target"]["selector"] == "" and bad["screen"] == "/dev/rag#student-regs"     # 안내 자료에 없는 요소는 버린다
    staff = platform_ops.Ctx(db=db, data_dir=tmp_path, user="zzaimy", role="staff")
    for page in ("/dev/rag", "/somewhere"):
        try:
            platform_ops.propose_open(staff, sid, page, "x")
            raise AssertionError("열 수 없는 화면을 열게 하면 안 된다")
        except platform_ops.OpRefused:
            pass
    try:
        platform_ops.run_proposal(dev, sid, item["id"])
        raise AssertionError("화면 열기 제안은 실행할 것이 없다")
    except platform_ops.OpRefused:
        pass


def test_upload_status_lists_only_own_documents(tmp_path):
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    mine = db.add_document("내 계획서.hwp", "x", doc_type="grant", owner="kim")
    db.add_document("남의 비밀 문서.hwp", "x", doc_type="grant", owner="lee")
    got = platform_ops.lookup(platform_ops.Ctx(db=db, data_dir=tmp_path, user="kim", role="staff"), "my_uploads")
    assert f"({mine}) 내 계획서.hwp" in got and "처리 중" in got and "남의 비밀" not in got


# ---- 떠 있는 창: 화면 조작(AURA 커서)·멈춤·추천 질문·기록 ----

def test_open_page_now_carries_text_only_for_guide_element(tmp_path, monkeypatch):
    """「라이브러리에서 ○○ 사업 찾아 줘」 — 도우미가 now·text 를 넣으면 화면이 바로 그 칸에 말을 넣어 찾는다(제안의 act)."""
    app, c = _app(tmp_path, monkeypatch)
    _use_agent(monkeypatch, [
        _step("open_page", page="/?type=all", element='[id="libFind"]', text="  혁신  지원 사업 ", now=True, why="사업 찾기"),
        _step("finish", message="라이브러리에서 찾습니다."),
    ])
    sid = c.post("/chat/ask", data={"question": "라이브러리에서 혁신지원 사업 찾아 줘", "page": "/hwp"}).json()["session_id"]
    p = c.get(f"/chat/{sid}/messages").json()["proposals"][0]
    assert p["kind"] == "go" and p["target"]["selector"] == '[id="libFind"]'
    assert p["act"] == {"now": True, "text": "혁신 지원 사업"}
    db = app.state.db
    ctx = platform_ops.Ctx(db=db, data_dir=tmp_path, user="zzaimy", role="dev")
    bare = platform_ops.propose_open(ctx, sid, "/hwp", "문서함", "", now=True, text="무시")
    assert bare["act"] == {"now": True, "text": ""}               # 가리킬 요소가 없으면 넣을 말도 없다


def test_guide_lists_cursor_targets_for_common_requests():
    lib = platform_guide.render(platform_guide.BY_PATH["/?type=all"])
    hwp = platform_guide.render(platform_guide.BY_PATH["/hwp"])
    assert '[id="libFind"]' in lib and '[data-aura="hwp-new"]' in hwp


def test_stop_drops_late_answer_and_frees_session(tmp_path, monkeypatch):
    """「멈춤」 — 도는 요청의 남은 걸음·답·제안은 버리고 대화는 바로 다음 질문을 받는다."""
    app, c = _app(tmp_path, monkeypatch)
    h = app.state.chat
    db = app.state.db
    sid = db.create_chat_session("도우미", owner="zzaimy", channel="helper")
    db.add_chat(sid, "user", "문서함 열어 줘")
    run_id = h._helper_runs[sid] = object()
    h._chat_running.add(sid)
    assert c.post(f"/chat/{sid}/stop").json() == {"ok": True, "stopped": True}
    assert db.list_chats(sid)[-1]["content"] == "멈췄습니다." and sid not in h._chat_running
    fake = _use_agent(monkeypatch, [_step("open_page", page="/hwp", now=True, why="x"), _step("finish", message="늦은 답")])
    h._helper_task(sid, "문서함 열어 줘", "/", run_id)           # 멈춘 뒤에 도는 옛 요청
    assert [m["content"] for m in db.list_chats(sid)][-1] == "멈췄습니다."
    assert c.get(f"/chat/{sid}/messages").json()["proposals"] == []
    assert not fake.seen or len(fake.seen) == 1                  # 첫 걸음 뒤 바로 끊긴다
    assert c.post(f"/chat/{sid}/stop").json()["stopped"] is False


def test_helper_suggest_uses_current_page_tasks(tmp_path, monkeypatch):
    app, c = _app(tmp_path, monkeypatch)
    got = c.get("/api/helper/suggest", params={"page": "/hwp"}).json()
    assert got["page"] == "한글 문서함" and got["items"][0] == "새 한글 문서 만들기" and len(got["items"]) <= 3
    assert c.get("/api/helper/suggest", params={"page": "/nowhere"}).json()["items"] == []


def test_helper_history_has_last_activity_time(tmp_path, monkeypatch):
    app, c = _app(tmp_path, monkeypatch)
    db = app.state.db
    sid = db.create_chat_session("도우미 기록", owner="zzaimy", channel="helper")
    db.add_chat(sid, "user", "질문")
    row = next(r for r in c.get("/api/chat/sessions?channel=helper").json()["sessions"] if r["id"] == sid)
    assert row["last_at"] == db.list_chats(sid)[-1]["created_at"]


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_PLATFORM_HELPER", "1")
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    return app, TestClient(app)
