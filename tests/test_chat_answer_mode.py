"""대화 입력줄의 답변 방식 — 「일반」(모델 지식, 모델이 인터넷 확인이 필요하다고 표시하면 「인터넷에서 찾아보기」 선택지)과
「RAG」(계정의 RAG 범위, 고른 RAG 로 좁히기만). 계정마다 서버에 기억한다. 관리자 검색 실험은 입력줄에 없고 /dev/quality 쪽 도구로."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import answer_pref, grant_search, web_search
from zzaimy.app.main import create_app


class ScopeResponder:
    def __init__(self):
        self.seen = []

    def answer(self, db, question, scope=None, **kw):
        self.seen.append({"q": question, "scope": scope, "graph_mode": grant_search.GRAPH_MODE.get()})
        return "합성 답변"


def _wait(cond):
    for _ in range(60):
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def staff(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_DOC_AGENT", "0")
    (tmp_path / "accounts.json").write_text(json.dumps({
        "kim": {"pw": "kim-pass", "role": "staff", "dept": "기획처"},
        "stu": {"pw": "stu-pass", "role": "student"},
        "zzdev": {"pw": "dev-pass", "role": "dev"},
    }))
    r = ScopeResponder()
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), responder=r, password="boot-pass-1")

    def login(uid, pw):
        c = TestClient(app)
        assert c.post("/login", data={"username": uid, "pw": pw}, follow_redirects=False).status_code == 303
        return c
    return app, r, login


def test_toggle_replaces_admin_selector_for_everyone(staff):
    app, r, login = staff
    for who in (("kim", "kim-pass"), ("zzdev", "dev-pass")):
        page = login(*who).get("/chat").text
        assert 'name="retrieval"' not in page and "검색: 온톨로지" not in page and "[실험" not in page
        seg = page.split('class="chat-mode-seg"', 1)[1].split("</div>", 1)[0]
        assert ">일반</button>" in seg and ">RAG</button>" in seg
        assert 'id="answerMode" data-mode="rag"' in page             # 처음은 RAG
    stu = login("stu", "stu-pass").get("/chat").text
    assert 'id="answerMode" data-mode="rag" hidden' in stu and 'id="ragPick"' not in stu   # 학생은 학사 RAG 하나 — 토글 없음


def test_mode_choice_is_remembered_per_account_on_server(staff):
    app, r, login = staff
    kim = login("kim", "kim-pass")
    assert kim.post("/api/chat/answer-mode", data={"mode": "model"}).json()["mode"] == "model"
    page = login("kim", "kim-pass").get("/chat").text                 # 새 로그인(다른 기기)에서도
    assert 'id="answerMode" data-mode="model"' in page and 'id="webField" value="model"' in page
    assert 'data-mode="rag"' in login("zzdev", "dev-pass").get("/chat").text     # 다른 계정은 그대로
    # 프로젝트 안 대화는 RAG 가 기본
    pid = app.state.db.create_project("grant", "합성 사업", owner="kim")
    assert 'id="answerMode" data-mode="rag"' in kim.get(f"/chat?project={pid}").text


def test_general_mode_skips_retrieval_and_offer_chip_runs_web_search(staff, monkeypatch):
    app, r, login = staff
    calls = []
    monkeypatch.setattr(web_search, "answer_from_model",
                        lambda q: calls.append(("model", q)) or {"answer": "아는 만큼의 답", "offer_web": True})
    monkeypatch.setattr(web_search, "answer_with_web", lambda q: calls.append(("web", q)) or {
        "answer": "웹 답 [1]", "sources": [{"n": 1, "title": "공식 자료", "url": "https://example.com/"}]})
    kim = login("kim", "kim-pass")
    res = kim.post("/chat/send", data={"question": "올해 최저임금은?", "web": "model"}, follow_redirects=False)
    sid = int(res.headers["location"].rsplit("/", 1)[1])
    db = app.state.db
    assert _wait(lambda: db.list_chats(sid)[-1]["role"] == "assistant")
    assert r.seen == [] and calls == [("model", "올해 최저임금은?")]        # 문서 검색을 돌지 않는다
    assert db.list_chats(sid)[-1]["content"].endswith("인터넷에서 찾아볼까요?")
    page = kim.get(f"/chat/{sid}").text
    chip = page.split('aria-label="다음 작업 제안"', 1)[1].split("</form>", 1)[0]
    assert 'name="web" value="1"' in chip and "인터넷에서 찾아보기" in chip and 'value="올해 최저임금은?"' in chip
    kim.post("/chat/send", data={"session_id": sid, "question": "올해 최저임금은?", "web": "1"})
    assert _wait(lambda: len(calls) == 2)
    assert calls[-1] == ("web", "올해 최저임금은?") and r.seen == []
    assert db.list_chats(sid)[-2]["mode"] == "web"
    assert ">일반</span>" in kim.get(f"/chat/{sid}").text and ">인터넷 검색</span>" in kim.get(f"/chat/{sid}").text


def test_general_answer_without_offer_has_no_chip(staff, monkeypatch):
    app, r, login = staff
    monkeypatch.setattr(web_search, "answer_from_model", lambda q: {"answer": "공문서 두문은 기관명·수신으로 시작합니다", "offer_web": False})
    kim = login("kim", "kim-pass")
    sid = int(kim.post("/chat/send", data={"question": "공문 두문 구성", "web": "model"}, follow_redirects=False)
              .headers["location"].rsplit("/", 1)[1])
    assert _wait(lambda: app.state.db.list_chats(sid)[-1]["role"] == "assistant")
    assert "인터넷에서 찾아보기" not in kim.get(f"/chat/{sid}").text


def test_rag_mode_uses_account_scope_and_narrows_only(staff):
    app, r, login = staff
    kim = login("kim", "kim-pass")
    page = kim.get("/chat").text
    menu = page.split('id="ragPickMenu"', 1)[1].split("</div>", 1)[0]
    assert 'value="공통" checked' in menu and 'value="기획처" checked' in menu    # 기본 = 고를 수 있는 것 전부
    kim.post("/chat/send", data={"question": "사업 목표 알려줘"})
    assert _wait(lambda: len(r.seen) == 1)
    assert set(r.seen[0]["scope"]["grant_depts"]) == {"기획처", "공통"} and r.seen[0]["graph_mode"] == ""
    # 공통만 고르기 — 범위 밖 부서를 섞어 보내도 넓어지지 않는다
    got = kim.post("/api/chat/answer-mode", data={"rags_pick": "1", "rags": ["공통", "산학협력단"]}).json()
    assert got["picked"] == ["공통"] and got["label"] == "공통"
    res = kim.post("/chat/send", data={"question": "공통 규정만", "rags_pick": "1", "rags": ["공통"]}, follow_redirects=False)
    assert _wait(lambda: len(r.seen) == 2)
    assert r.seen[1]["scope"]["grant_depts"] == ["공통"] and r.seen[1]["scope"]["dept"] == "공통"
    sid = int(res.headers["location"].rsplit("/", 1)[1])
    assert ">RAG · 공통</span>" in kim.get(f"/chat/{sid}").text
    # 범위 밖 것만 보내면 저장은 기본(전부)으로 — 범위 밖이 저장되지 않는다
    got = kim.post("/api/chat/answer-mode", data={"rags_pick": "1", "rags": ["산학협력단"]}).json()
    assert got["label"] == "" and set(got["picked"]) == {"공통", "기획처"}
    assert json.loads(app.state.db.get_setting("chat_mode_pref:kim"))["rags"] == []


def test_narrow_is_intersection_and_fails_closed():
    scope = {"space": "dept:기획처", "role": "staff", "user": "kim", "dept": "기획처", "grant": True, "grant_depts": ["기획처", "공통"]}
    choices = answer_pref.rag_choices(scope)
    assert choices == ["공통", "기획처"]
    assert answer_pref.narrow(scope, [], choices) is scope                         # 기본 = 그대로
    assert answer_pref.narrow(scope, ["공통", "기획처"], choices) is scope
    one = answer_pref.narrow(scope, ["기획처", "산학협력단"], choices)
    assert one["grant_depts"] == ["기획처"] and one["dept"] == "기획처"           # 범위 밖은 버린다
    shut = answer_pref.narrow(scope, ["산학협력단"], choices)
    assert shut["grant"] is False and shut["grant_depts"] == [] and shut["dept"] == "공통"   # 넓히지 않고 닫는다
    dev = {"space": "dev", "role": "dev", "grant": True, "grant_depts": None}
    assert answer_pref.rag_choices(dev, ["기획처", "산학협력단"]) == ["공통", "기획처", "산학협력단"]
    assert answer_pref.narrow(dev, ["산학협력단"], answer_pref.rag_choices(dev, ["기획처", "산학협력단"]))["grant_depts"] == ["산학협력단"]
    assert answer_pref.rag_choices({"levels": ("student",), "grant": False, "grant_depts": []}) == []


def test_model_offer_flag_is_read_from_model_output():
    assert web_search._split_offer("답입니다.\n[인터넷 확인: 필요]") == ("답입니다.", True)
    assert web_search._split_offer("답입니다.\n**[인터넷 확인: 불필요]**") == ("답입니다.", False)
    assert web_search._split_offer("표시 없는 답") == ("표시 없는 답", False)


def test_retrieval_compare_runs_three_modes_for_dev_only(staff, monkeypatch):
    app, r, login = staff
    seen = []
    monkeypatch.setattr(grant_search, "search", lambda db, q, k=6, user=None, depts=None, **kw:
                        seen.append(grant_search.GRAPH_MODE.get()) or {"hits": [{"doc_id": 1, "filename": "a.pdf", "path": ["a.pdf"]}], "steps": ["s"]})
    out = login("zzdev", "dev-pass").post("/dev/quality/retrieval-compare", data={"question": "취업률 목표"}).json()
    assert [m["mode"] for m in out["modes"]] == ["fulltext", "graph", "graph_read"] == seen
    assert out["modes"][0]["hits"][0]["filename"] == "a.pdf"
    assert login("kim", "kim-pass").post("/dev/quality/retrieval-compare", data={"question": "x"},
                                         follow_redirects=False).status_code in (303, 403, 404)
    page = login("zzdev", "dev-pass").get("/dev/quality").text           # 화면 — 「사업 문서 검색(GraphRAG)」 카드 안의 비교 폼
    card = page.split('id="grant-search"', 1)[1].split('id="model-compare"', 1)[0]
    assert 'id="rcForm"' in card and 'name="question"' in card and 'name="k"' in card and 'min="1" max="20"' in card
    assert "fetch('/dev/quality/retrieval-compare'" in page and 'href="/doc/${' in page
    assert login("zzdev", "dev-pass").post("/dev/quality/retrieval-compare", data={"question": "x", "k": "99"}).json()["k"] == 20


def test_answer_uses_the_senders_pick_stored_on_the_message(staff):
    """답은 이 질문을 보낸 사람이 그때 고른 RAG 로 — 계정의 저장값이 나중에 바뀌어도 그 질문의 선택이 남는다(chat_msg_rags:<메시지>)."""
    app, r, login = staff
    kim = login("kim", "kim-pass")
    res = kim.post("/chat/send", data={"question": "공통만", "rags_pick": "1", "rags": ["공통"]}, follow_redirects=False)
    sid = int(res.headers["location"].rsplit("/", 1)[1])
    assert _wait(lambda: len(r.seen) == 1)
    db = app.state.db
    mid = [m for m in db.list_chats(sid) if m["role"] == "user"][-1]["id"]
    assert answer_pref.message_pick(db, mid) == {"by": "kim", "rags": ["공통"]}
    assert r.seen[0]["scope"]["grant_depts"] == ["공통"]
    # 다른 사람(공유 프로젝트의 동료)이 보낸 질문으로 적혀 있으면 그 사람의 선택·범위로 — 주인의 저장값(전부)을 쓰지 않는다
    kim.post("/api/chat/answer-mode", data={"rags_pick": "1", "rags": ["공통", "기획처"]})
    db.set_setting(f"chat_msg_rags:{mid}", json.dumps({"by": "zzdev", "rags": ["기획처"]}))
    kim.post(f"/chat/{sid}/retry", follow_redirects=False)
    assert _wait(lambda: len(r.seen) == 2)
    assert r.seen[1]["scope"]["grant_depts"] == ["기획처"]


def test_shared_pick_is_within_both_owner_and_sender_scope():
    owner = {"space": "dept:기획처", "role": "staff", "user": "kim", "dept": "기획처", "grant": True, "grant_depts": ["기획처", "공통"]}
    sender = {"space": "dept:산학협력단", "role": "staff", "user": "park", "dept": "산학협력단", "grant": True, "grant_depts": ["산학협력단", "공통"]}
    both = answer_pref.narrow_shared(owner, sender, [])                       # 보낸 사람 기본(전부) — 주인 범위와 겹치는 공통만
    assert both["grant_depts"] == ["공통"] and both["grant"] is True
    shut = answer_pref.narrow_shared(owner, sender, ["산학협력단"])            # 주인 범위 밖 — 넓히지 않고 닫는다
    assert shut["grant"] is False and shut["grant_depts"] == []
    sneaky = answer_pref.narrow_shared(owner, sender, ["기획처"])              # 보낸 사람 범위 밖 값 — 닫는다
    assert sneaky["grant"] is False
    dev = {"space": "dev", "role": "dev", "grant": True, "grant_depts": None}
    assert answer_pref.narrow_shared(dev, sender, [], ["기획처", "산학협력단"])["grant_depts"] == ["공통", "산학협력단"]
    assert answer_pref.narrow_shared(owner, owner, []) is owner              # 같은 사람·기본 = 그대로
    assert answer_pref.narrow_shared(owner, None, [])["grant"] is False      # 보낸 사람 범위를 모르면 닫는다
