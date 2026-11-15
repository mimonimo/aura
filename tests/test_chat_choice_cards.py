"""대화창 선택 카드·미리 보기(app/chat_preview) — 가상 값만 쓴다.

- 도구 꼴: 질문 1~4개, 질문마다 선택지 2~4개, 미리 보기 갈래(font·text·table·image·page·change)만 받는다.
- 「먼저 작업, 필요하면 바꾸기 후보」: 기준(작업 문서·기관 서식)이 있으면 모양 선택을 먼저 묻는 호출은 막고, 담당자가 고르겠다고 했거나 결정이면 묻는다.
- 선택 카드는 답 아래에 그리고, 고르면 「머리: 2번 이름」 이 다음 요청으로 들어가며 고른 것은 상태·작업 메모에 남는다. 답한 카드는 고른 값으로 접힌다.
- 반영 미리 보기는 도구 결과(쓴 글·넣은 값·서식 인자)에서만 만든다.
- 화면 규칙: details 없음, 긴 줄표 없음."""
from __future__ import annotations

import json

from test_resolve_asks import _app, _obs, _run, _step
from zzaimy.app import chat_memory, chat_preview as cp

TABLE_OPT = lambda label, orient, fill: {"label": label, "description": "항목을 행으로" if orient == "행" else "항목을 열로",
                                          "preview": {"type": "table", "head": [["구분", "내용"]],
                                                      "rows": [["가상 과정", "가상 운영"], ["가상 둘", "가상 셋"], ["넷", "다섯"], ["여섯", "일곱"], ["여덟", "아홉"]],
                                                      "style": {"head_fill": fill, "head_align": "가운데", "outer": "0.4 mm", "bogus": 1},
                                                      "layout": {"orient": orient, "total": True, "place": "이 절 안", "head_rows": 2}}}


def _card(**over):
    q = {"header": "표 배치", "question": "표를 어떻게 넣을까요?", "options": [TABLE_OPT("행 배치", "행", "#E5E5E5"), TABLE_OPT("열 배치", "열", "연파랑")]}
    q.update(over)
    return [q]


# ── 도구 꼴 ────────────────────────────────────────────────────────────────
def test_card_shape_limits_and_preview_kinds():
    card, why = cp.norm_card(_card())
    assert why == "" and card["mode"] == "ask"
    q = card["questions"][0]
    assert q["kind"] == "모양" and q["allow_other"] is True and q["multi"] is False
    pv = q["options"][0]["preview"]
    assert len(pv["rows"]) == cp.ROWS_SHOWN and pv["more"] == 1                    # 나머지는 「외 N행」
    assert pv["style"] == {"head_fill": "#E5E5E5", "outer": 0.4, "head_align": "가운데"}   # hwp_style 인자 이름만
    assert q["options"][1]["preview"]["style"]["head_fill"] == "#DCE6F2"
    assert pv["layout"] == {"orient": "행", "total": True, "place": "이 절 안", "head_rows": 2}
    assert cp.norm_card(_card(options=[TABLE_OPT("하나", "행", "#E5E5E5")]))[0] is None   # 선택지 2개 미만
    assert cp.norm_card(_card() * 5)[0] is None                                        # 질문 5개
    assert cp.norm_preview({"type": "script", "src": "x"}) is None
    assert cp.norm_preview({"type": "image", "src": "https://바깥/그림.png"}) is None   # 같은 사이트 안 주소만
    assert cp.norm_preview({"type": "font", "font": "가상체'; color:red", "pt": 11})["font"] == "가상체 colorred"
    dec, _ = cp.norm_card([{"header": "문서 틀", "question": "어느 틀로 쓸까요?", "options": [{"label": "가상 틀 하나"}, {"label": "가상 틀 둘"}]}])
    assert dec["questions"][0]["kind"] == "결정"


def test_table_looks_follow_hwp_style_args_and_can_be_swapped():
    looks = cp.table_looks()
    assert 2 <= len(looks) <= 4 and looks[0]["style"]["head_fill"] == "#E5E5E5"
    assert all(set(x["style"]) <= set(cp.STYLE_KEYS) for x in looks)
    cp.set_table_looks(lambda kind: [{"label": "가상 갈래 모양", "style": {"head_fill": "#123456", "zebra": True}}])
    try:
        assert cp.table_looks("예산") == [{"label": "가상 갈래 모양", "description": "", "style": {"head_fill": "#123456"}}]
    finally:
        cp.set_table_looks(None)
    assert cp.table_looks()[0]["label"] == looks[0]["label"]


def test_gate_first_blocks_shape_questions_when_a_basis_exists():
    card, _ = cp.norm_card(_card())
    assert "먼저 반영" in cp.gate_first(card, has_basis=True, wants_choice=False)
    assert cp.gate_first(card, has_basis=True, wants_choice=True) == ""
    assert cp.gate_first(card, has_basis=False, wants_choice=False) == ""
    dec, _ = cp.norm_card([{"header": "문서 틀", "question": "어느 틀?", "options": [{"label": "가"}, {"label": "나"}]}])
    assert cp.gate_first(dec, has_basis=True, wants_choice=False) == ""


def test_numbers_not_in_evidence_become_unconfirmed():
    card, _ = cp.norm_card([{"header": "표 배치", "question": "표?", "options": [
        {"label": "가", "preview": {"type": "table", "head": [["구분", "인원"]], "rows": [["가상 과정", "31명"], ["가상 둘", "999명"]]}},
        {"label": "나", "preview": {"type": "text", "text": "가상 참여 31명\n가상 예산 777원"}}]}])
    assert cp.scrub_numbers(card, "가상 결과 참여 31명") == 2
    opts = card["questions"][0]["options"]
    assert opts[0]["preview"]["rows"] == [["가상 과정", "31명"], ["가상 둘", "확인 필요"]]
    assert opts[1]["preview"]["text"] == "가상 참여 31명\n확인 필요"


# ── 반영 미리 보기 ─────────────────────────────────────────────────────────
def test_done_previews_come_from_tool_results_only():
    steps = [
        {"action": "hwp_write", "args": {"field": "가상 절", "text": "1) 가상 첫 줄\n<표> 가상 일정\n| 구분 | 일정 |\n|---|---|\n| 가 | 3월 |\n| 나 | 4월 |"},
         "observation": "「가상 절」 에 썼다"},
        {"action": "hwp_fill", "args": {"values": {"가상 칸": "가상 값"}}, "observation": "넣은 것 없음 — 칸을 찾지 못함"},
        {"action": "hwp_style", "args": {"tables": [2], "head_fill": "연파랑"}, "observation": "표 서식을 입혔다"},
        {"action": "hwp_text_style", "args": {"scope": "all", "font": "가상체", "pt": 11}, "observation": "바꿨다"},
        {"action": "hwp_replace", "args": {"find": "가상 옛 글", "replace": "가상 새 글"}, "observation": "1곳 바꿨다"},
    ]
    from zzaimy.app import doc_agent
    got = cp.from_steps(steps, doc_agent._REFUSED)
    whats = [x["what"] for x in got]
    assert "채운 칸" not in whats                                                    # 거절된 걸음은 보이지 않는다
    assert len(got) == cp.DONE_MAX and whats[-1] == "바꾼 글"
    full = {x["what"]: x["preview"] for x in cp.from_steps(steps[:2], doc_agent._REFUSED)}
    assert full["「가상 절」 글"]["text"] == "1) 가상 첫 줄"
    assert full["「가상 절」 표"]["head"] == [["구분", "일정"]] and full["「가상 절」 표"]["title"] == "가상 일정"
    style = next(x for x in got if x["what"].startswith("표 서식"))
    assert style["preview"]["style"]["head_fill"] == "#DCE6F2" and style["changed"] == ["head_fill"]


# ── 에이전트 흐름 ──────────────────────────────────────────────────────────
def _ask_choice(**extra):
    return _step("ask_user", message="가상 질문 머리말", choices=_card(), **extra)


def test_agent_choice_card_renders_and_answer_flows_into_next_request(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch, [])
    db = app.state.db
    sid = db.create_chat_session("가상", owner="zzaimy")
    page = _run(client, monkeypatch, sid, [_ask_choice()], "가상 표 배치 후보를 보여 줘")   # 담당자가 고르겠다고 했다
    assert "data-choice-card" in page and 'name="pick_0"' in page and 'name="other_0"' in page
    assert "외 1행" in page and "항목을 행으로" in page and "--pv-head: #E5E5E5" in page
    assert "<details" not in page and "—" not in page.split('id="chatScroll"', 1)[1].split("</main>", 1)[0].split("<script", 1)[0]
    mid = next(int(m["id"]) for m in reversed(db.list_chats(sid)) if m["role"] == "assistant")
    assert cp.card_of(db, mid)["questions"][0]["header"] == "표 배치"
    # 고르면 「머리: 2번 이름」 이 다음 요청으로 — 에이전트가 이어서 반영한다
    from zzaimy.generate import client as _gc
    from test_resolve_asks import _AgentFake
    seen: list[str] = []
    fake = _AgentFake([_step("finish", message="가상 반영")])
    real = fake.client.chat.completions.create
    fake.client.chat.completions.create = lambda **kw: (seen.append(json.dumps(kw.get("messages"), ensure_ascii=False)), real(**kw))[1]
    monkeypatch.setattr(_gc, "VllmClient", lambda *a, **k: fake)
    r = client.post("/chat/send", data={"session_id": str(sid), "choice_mid": str(mid), "question": "고른 것", "pick_0": "2"}, follow_redirects=False)
    assert r.status_code == 303
    users = [m["content"] for m in db.list_chats(sid) if m["role"] == "user"]
    assert users[-1] == "[고른 것] 표 배치: 2번 열 배치(항목을 열로)"
    assert seen and "담당자가 고른 것" in seen[0] and "hwp_style" in seen[0] and "#DCE6F2" in seen[0]
    chosen = cp.chosen(db, sid)
    assert chosen[0]["picked"] == ["열 배치"]
    page = client.get(f"/chat/{sid}").text
    assert "cc-choice is-answered" in page and "2번 열 배치" in page            # 답한 카드는 고른 값으로 접힌다
    # 작업 메모 「확정된 것」 에도 코드가 남긴다
    memo = chat_memory.keep_choices("지금 목표:\n가상 목표", db, sid)
    assert "확정된 것:" in memo and "표 배치: 열 배치" in memo


def test_agent_shape_choice_first_is_refused_when_a_doc_exists_and_repeat_is_refused(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch, [])
    db = app.state.db
    sid = db.create_chat_session("가상", owner="zzaimy")
    from zzaimy.app import house_profile
    monkeypatch.setattr(house_profile, "current", lambda: {"roles": {"body": {"font": "가상체"}}})   # 기관 서식이 있다
    _run(client, monkeypatch, sid, [_ask_choice(), _step("finish", message="먼저 반영함")], "가상 표를 넣어 줘")
    obs, _ep = _obs(tmp_path, "ask_user")
    assert "먼저 반영" in obs[0] and "alternatives" in obs[0]
    # 이미 고른 질문은 다시 묻지 않는다
    db.set_setting(f"chat_choices:{sid}", json.dumps([{"header": "표 배치", "question": "표?", "picked": ["행 배치"], "options": []}], ensure_ascii=False))
    _run(client, monkeypatch, sid, [_ask_choice(), _step("finish", message="그대로 반영")], "가상 표 후보 골라 보자")
    obs, _ep = _obs(tmp_path, "ask_user")
    assert "이미 고른 것" in obs[0] and "행 배치" in obs[0]


def test_finish_alternatives_become_a_change_card(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch, [])
    db = app.state.db
    sid = db.create_chat_session("가상", owner="zzaimy")
    page = _run(client, monkeypatch, sid, [
        _step("answer_question", question="가상"),
    ], "가상 질문")
    # 쓰기가 없으면 바꾸기 후보는 달지 않는다(반영한 것이 없으니)
    _run(client, monkeypatch, sid, [_step("finish", message="가상 끝", alternatives=_card())], "가상 끝내기")
    mid = next(int(m["id"]) for m in reversed(db.list_chats(sid)) if m["role"] == "assistant")
    assert cp.card_of(db, mid) == {}
    # 꼴이 틀린 후보는 끝내기 전에 돌려준다
    _run(client, monkeypatch, sid, [_step("finish", message="가상 끝", alternatives=[{"header": "하나뿐", "question": "?", "options": [{"label": "가"}]}]),
                                    _step("finish", message="가상 끝")], "가상 끝내기 둘")
    obs, _ep = _obs(tmp_path, "finish")
    assert "alternatives 꼴이 맞지 않다" in obs[0]


def test_change_card_renders_with_change_wording(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch, [])
    db = app.state.db
    sid = db.create_chat_session("가상", owner="zzaimy")
    db.add_chat(sid, "user", "가상 요청")
    mid = db.add_chat(sid, "assistant", "가상 표를 넣었습니다.")
    card, _ = cp.norm_card(_card(), "change")
    cp.save_card(db, int(mid), card)
    cp.save_done(db, int(mid), [{"what": "「가상 절」 표", "preview": cp.norm_preview({"type": "table", "head": [["구분"]], "rows": [["가"]]})}])
    page = client.get(f"/chat/{sid}").text
    assert "다른 모양으로 바꾸기" in page and "이대로 바꾸기" in page and "반영한 것" in page and "「가상 절」 표" in page
    # 아무것도 고르지 않고 보내면 대화에 넣지 않는다
    n = len(db.list_chats(sid))
    client.post("/chat/send", data={"session_id": str(sid), "choice_mid": str(mid), "question": "고른 것"}, follow_redirects=False)
    assert len(db.list_chats(sid)) == n
    # 다른 대화의 카드 번호로는 고를 수 없다
    other = db.create_chat_session("다른 가상", owner="zzaimy")
    client.post("/chat/send", data={"session_id": str(other), "choice_mid": str(mid), "question": "고른 것", "pick_0": "1"}, follow_redirects=False)
    assert cp.chosen(db, other) == [] and not db.list_chats(other)
