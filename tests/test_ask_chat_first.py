"""묻기는 요청에 맞춰, 입력은 채팅이 먼저, 다른 사업 문서의 값은 채우지 않음 — 가상 사업·가상 값만 쓴다.

- 묻는 글은 값마다 처리할 길(자료 값·출처, 찾은 지난 값, 「확인 필요」)을 한 줄씩 적는다.
- 사실 값은 묻기 전에 지난 자료(library)부터 찾게 한 번 안내하고, 처리 안의 수치는 이번 검색 결과에 있는 것만 내보인다.
- 채팅 답(「이름: 값」·「확인 필요로 둬」·「자료 값으로」)은 칸 없이 그대로 기억하고, 「필요한 값 입력」 칸은 접어 둔다.
- 프로젝트에 올라 있어도 다른 사업의 문서에서는 값을 채우지 않는다(같은 사업은 받고, 다른 연도면 알린다)."""
from __future__ import annotations

import json

from test_resolve_asks import PROJECT_DOC, _app, _hit, _obs, _run, _step
from zzaimy.app import asks
from zzaimy.app.db import Database

MINE = "2027 가상 융합 교육 혁신 사업"
OTHER_NOTICE = "2026년 가상 지역 선도대학 육성사업 공고"


def _project_session(db, name=MINE):
    pid = db.create_project("교육", name)
    return db.create_chat_session("가상 대화", project_id=pid)


# ── B. 묻는 글의 모양 ──────────────────────────────────────────────────────
def test_ask_message_says_how_each_item_can_be_handled():
    items = [{"name": "가상 기준값", "kind": "사실"}, {"name": "가상 목표치", "kind": "결정"},
             {"name": "가상 참여 인원", "kind": "사실", "how": "지난 가상 보고서의 31명을 쓸지"},
             {"name": "가상 마감일", "kind": "사실"}]
    sug = {"가상 마감일": {"value": "2099. 1. 2.", "title": "가상 안내문", "note": "2098년 자료 · 올해 값인지 확인"}}
    msg = asks.ask_message(items, sug, "")
    lines = msg.split("\n")
    assert lines[0] and lines[-1].startswith("채팅으로 「이름: 값」")
    assert "- 가상 기준값: 자료에 없음 · 알려 주시거나 「확인 필요」 로 둠" in lines
    assert "- 가상 목표치: 정해서 알려 주시거나 「확인 필요」 로 둠" in lines
    assert "- 가상 참여 인원: 지난 가상 보고서의 31명을 쓸지" in lines
    assert any(l.startswith("- 가상 마감일: 자료 값 「2099. 1. 2.」(《가상 안내문》 · 2098년 자료") for l in lines)
    assert "—" not in msg                                         # 화면 글에 긴 줄표를 쓰지 않는다
    assert asks.ask_message(items[:1], {}, "머리말 한 줄").split("\n")[0] == "머리말 한 줄"


def test_fact_ask_first_searches_past_documents_and_how_numbers_must_be_seen(tmp_path, monkeypatch):
    past = _hit("가상 지난 실적 보고서", "가상 참여 인원 31명", doc_id=55)
    app, client = _app(tmp_path, monkeypatch, [], library_hits=[past])
    db = app.state.db
    sid = db.create_chat_session("가상", owner="zzaimy")
    ask = lambda how: _step("ask_user", items=[{"name": "가상 참여 인원", "kind": "사실", "how": how},
                                               {"name": "가상 운영비", "kind": "사실", "how": "지난 자료의 999만 원을 쓸지"}], message="")
    _run(client, monkeypatch, sid, [
        ask("지난 보고서 값을 쓸지"),                                       # 지난 자료를 찾기 전 — 한 번 돌려보낸다
        _step("search", source="library", query="가상 참여 인원"),
        ask("《가상 지난 실적 보고서》의 31명을 쓸지")], "가상 계획서에 필요한 값 정리해 줘")
    obs, _ep = _obs(tmp_path, "ask_user")
    assert "source=library" in obs[0] and obs[1] == "물었다."
    last = db.list_chats(sid)[-1]["content"]
    assert "- 가상 참여 인원: 《가상 지난 실적 보고서》의 31명을 쓸지" in last
    assert "999" not in last and "- 가상 운영비: 자료에 없음" in last                # 결과에 없는 수치는 내보이지 않는다
    assert {a["name"] for a in asks.pending(db, sid)} == {"가상 참여 인원", "가상 운영비"}
    assert all("how" not in a for a in asks.pending(db, sid))


def test_decision_ask_is_not_sent_to_search_first(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch, [])
    db = app.state.db
    sid = db.create_chat_session("가상", owner="zzaimy")
    _run(client, monkeypatch, sid, [_step("ask_user", items=[{"name": "가상 목표치", "kind": "결정"}], message="")], "가상 목표를 정하자")
    obs, _ep = _obs(tmp_path, "ask_user")
    assert obs == ["물었다."] and "- 가상 목표치: 정해서 알려 주시거나" in db.list_chats(sid)[-1]["content"]


# ── C. 채팅 답·접힌 입력 칸 ────────────────────────────────────────────────
def test_chat_reply_is_remembered_without_the_form(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch, [])
    db = app.state.db
    sid = db.create_chat_session("가상", owner="zzaimy")
    asks.set_pending(db, sid, [{"name": "가상 기준값", "hint": ""}, {"name": "가상 목표치", "hint": ""},
                               {"name": "가상 마감일", "hint": ""}, {"name": "가상 남은 값", "hint": ""}, {"name": "가상 끝 값", "hint": ""}])
    db.set_setting(f"chat_facts:{sid}:suggested", json.dumps({"가상 마감일": {"value": "2099. 3. 4.", "title": "가상 안내문"}}, ensure_ascii=False))
    # 「이름: 값」 꼴 채팅 답 — 칸 없이, 모델 해석 없이 그대로 기억한다
    _run(client, monkeypatch, sid, [_step("finish", message="받았습니다")],
         "가상 기준값: 120명\n가상 목표치: 확인 필요\n가상 마감일: 자료 값")
    sess = db.get_chat_session(sid)
    got = asks.facts(db, sess)
    assert got["가상 기준값"] == "120명" and got["가상 목표치"] == "확인 필요" and got["가상 마감일"] == "2099. 3. 4."
    assert [a["name"] for a in asks.pending(db, sid)] == ["가상 남은 값", "가상 끝 값"] and asks.suggestions(db, sess) == {}
    _o, ep = _obs(tmp_path, "finish")
    assert "remember_values" not in [s.get("action") for s in ep["steps"]]
    # 말로 한 답은 모델이 값만 떼어 remember_values 로 — 「확인 필요로 둬」 도 「확인 필요」 로 남는다
    _run(client, monkeypatch, sid, [_step("remember_values", values={"가상 남은 값": "확인 필요로 둬", "가상 끝 값": "77건"}),
                                    _step("finish", message="받았습니다")],
         "가상 남은 값은 확인 필요로 둬 주시고 가상 끝 값은 77건이에요")
    got = asks.facts(db, sess)
    assert got["가상 남은 값"] == "확인 필요" and got["가상 끝 값"] == "77건" and asks.pending(db, sid) == []


def test_accept_without_a_suggestion_is_not_remembered(tmp_path):
    db = Database(tmp_path / "t.db")
    sess = {"id": 1}
    assert asks.norm_answers(db, sess, {"가상 값": "자료 값으로", "가상 둘": "그대로", "가상 셋": "31명"}) == {"가상 셋": "31명"}


def test_value_form_is_folded_with_an_open_button(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch, [])
    db = app.state.db
    sid = db.create_chat_session("가상", owner="zzaimy")
    db.add_chat(sid, "user", "가상 요청")
    db.add_chat(sid, "assistant", "물을 값이 있습니다.")
    asks.set_pending(db, sid, [{"name": "가상 기준값", "hint": ""}])
    page = client.get(f"/chat/{sid}").text
    head = page.split('id="chatValueForm"', 1)[0].rsplit("<form", 1)[1]
    assert "is-folded" in head
    form = page.split('id="chatValueForm"', 1)[1].split("</form>", 1)[0]
    assert 'data-form-toggle aria-expanded="false">입력 칸 열기</button>' in form
    assert 'name="field_가상 기준값"' in form                                        # 접혀 있어도 칸은 그대로 있다(열면 쓴다)


# ── D. 다른 사업 문서의 값 ─────────────────────────────────────────────────
def _resolve(db, sid, title, value="2099. 5. 6."):
    asks.set_pending(db, sid, [{"name": "가상 마감일", "hint": ""}])
    hits = [{"title": title, "text": f"접수 마감 {value} 18:00", "doc_id": PROJECT_DOC, "chunk_id": 1}]
    return asks.resolve_from_evidence(db, db.get_chat_session(sid), "가상 마감일", value, title, hits, allowed_docs={PROJECT_DOC})


def test_value_from_a_different_program_document_is_refused(tmp_path):
    db = Database(tmp_path / "t.db")
    sid = _project_session(db)
    why = _resolve(db, sid, OTHER_NOTICE)
    assert "다른 사업" in why and "가상 지역 선도대학 육성사업" in why and "참고 자료" in why
    assert asks.suggestions(db, db.get_chat_session(sid)) == {}


def test_value_from_the_same_program_is_kept_and_other_year_is_noted(tmp_path):
    db = Database(tmp_path / "t.db")
    sid = _project_session(db)
    assert _resolve(db, sid, "2027 가상 융합 교육 혁신 사업 공고") == ""
    assert "note" not in asks.suggestions(db, db.get_chat_session(sid))["가상 마감일"]
    sid2 = _project_session(db)
    assert _resolve(db, sid2, "2026년 가상 융합 교육 혁신 사업 안내") == ""
    assert asks.suggestions(db, db.get_chat_session(sid2))["가상 마감일"]["note"].startswith("2026년 자료")


def test_unknown_programs_are_not_judged(tmp_path):
    db = Database(tmp_path / "t.db")
    assert _resolve(db, _project_session(db), "가상 모집 안내") == ""                 # 출처가 사업을 부르지 않는다
    assert _resolve(db, db.create_chat_session("프로젝트 없음"), OTHER_NOTICE) == ""    # 견줄 프로젝트가 없다


def test_agent_resolve_refuses_and_state_marks_reference_only(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch, [_hit(OTHER_NOTICE, "접수 마감 2026. 8. 31. 18:00")])
    db = app.state.db
    sid = _project_session(db)
    asks.set_pending(db, sid, [{"name": "가상 마감일", "hint": "", "kind": "사실"}])
    _run(client, monkeypatch, sid, [
        _step("search", source="project", query="가상 마감일 접수"),
        _step("resolve_from_evidence", name="가상 마감일", value="2026. 8. 31. 18:00", source_title=OTHER_NOTICE),
        _step("finish", message="끝")], "빈 값 채워 줘")
    obs, _ep = _obs(tmp_path, "resolve_from_evidence")
    assert "다른 사업" in obs[0]
    assert asks.suggestions(db, db.get_chat_session(sid)) == {}
    assert not any("자료에서 찾음" in m["content"] for m in db.list_chats(sid))


def test_search_run_gate_constant_present():
    import inspect
    from zzaimy.app import main
    src = inspect.getsource(main)
    assert "SEARCH_RUN_MAX" in src and "찾기는 충분하다" in src
