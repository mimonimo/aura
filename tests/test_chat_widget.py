"""화면을 떠나지 않는 에이전트 — 떠 있는 창, 글 선택 질문, 답변 다시 받기, 문서 훑어보기.

데이터는 전부 합성이며 LLM 은 가짜 응답기로 대체한다.
"""

from fastapi.testclient import TestClient

from zzaimy.app.main import create_app
from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder


def _client(tmp_path):
    app = create_app(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder(),
    )
    return TestClient(app)


def test_ask_creates_session_and_answers(tmp_path):
    c = _client(tmp_path)
    r = c.post("/chat/ask", data={"question": "휴학 처리 기준을 알려주세요."})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    sid = body["session_id"]

    msgs = c.get(f"/chat/{sid}/messages").json()
    assert msgs["waiting"] is False
    roles = [m["role"] for m in msgs["messages"]]
    assert roles == ["user", "assistant"]
    assert "합성 답변" in msgs["messages"][1]["content"]


def test_ask_quotes_selected_text(tmp_path):
    c = _client(tmp_path)
    sid = c.post("/chat/ask", data={
        "question": "이 조항이 무슨 뜻입니까?",
        "context": "제19조(휴학) 학생은 질병으로 수학할 수 없을 때 휴학할 수 있다.",
    }).json()["session_id"]
    first = c.get(f"/chat/{sid}/messages").json()["messages"][0]["content"]
    assert "제19조(휴학)" in first          # 고른 글이 질문에 함께 실린다
    assert first.startswith("「")           # 인용은 따옴표로 감싸 보인다
    assert "이 조항이 무슨 뜻입니까?" in first


def test_ask_continues_same_session(tmp_path):
    c = _client(tmp_path)
    sid = c.post("/chat/ask", data={"question": "첫 질문입니다."}).json()["session_id"]
    again = c.post("/chat/ask", data={"question": "이어서 묻습니다.", "session_id": str(sid)})
    assert again.json()["session_id"] == sid   # 새 대화가 생기지 않는다
    msgs = c.get(f"/chat/{sid}/messages").json()["messages"]
    assert len(msgs) == 4                      # 질문·답변 두 쌍


def test_ask_rejects_blank_question(tmp_path):
    c = _client(tmp_path)
    assert c.post("/chat/ask", data={"question": "   "}).json()["ok"] is False


def test_retry_replaces_last_answer(tmp_path):
    c = _client(tmp_path)
    sid = c.post("/chat/ask", data={"question": "지원 자격을 알려주세요."}).json()["session_id"]
    before = c.get(f"/chat/{sid}/messages").json()["messages"]
    assert len(before) == 2

    r = c.post(f"/chat/{sid}/retry")
    assert r.json()["ok"] is True
    after = c.get(f"/chat/{sid}/messages").json()["messages"]
    assert len(after) == 2                          # 답변이 늘지 않고 갈린다
    assert after[1]["id"] != before[1]["id"]        # 이전 답변은 지워졌다
    assert after[0]["content"] == before[0]["content"]


def test_peek_returns_document_text(tmp_path):
    c = _client(tmp_path)
    c.post("/upload", files={"file": ("합성규정.pdf", b"%PDF fake", "application/pdf")})
    r = c.get("/chat/peek/1")
    assert r.status_code == 200
    assert r.json()["title"] == "합성규정.pdf"


def test_peek_starts_at_cited_passage(tmp_path):
    """출처를 누르면 그 대목이 든 조각부터 보인다 — 띄어쓰기가 달라도 찾는다."""
    from zzaimy.app.db import Database
    c = _client(tmp_path)
    c.post("/upload", files={"file": ("합성계획서.pdf", b"%PDF fake", "application/pdf")})
    Database(tmp_path / "t.db").replace_doc_chunks(1, [{"content": f"앞부분 문단 {i} " + "가" * 600} for i in range(6)]
                                                   + [{"content": "3. 추진 체계 — 사업단장 아래 운영위원회를 둔다."}])
    whole = c.get("/chat/peek/1").json()
    assert whole["from_start"] and "운영위원회" not in whole["text"]
    got = c.get("/chat/peek/1", params={"q": "추진체계 — 사업단장 아래 운영위원회"}).json()
    assert not got["from_start"] and got["text"].startswith("… 3. 추진 체계")
    assert c.get("/chat/peek/1", params={"q": "없는 대목입니다 정말로"}).json()["from_start"]


def test_peek_missing_document(tmp_path):
    assert _client(tmp_path).get("/chat/peek/9999").status_code == 404


def test_widget_present_off_chat_and_absent_on_chat(tmp_path):
    c = _client(tmp_path)
    assert "에이전트에게 묻기" in c.get("/inbox").text     # 라이브러리에는 떠 있다
    assert "직접 묻기" in c.get("/inbox").text             # 글 선택 질문
    assert "에이전트에게 묻기" not in c.get("/chat").text  # 채팅 화면에는 겹치지 않는다


def test_chat_page_offers_retry_after_answer(tmp_path):
    c = _client(tmp_path)
    c.post("/chat/send", data={"question": "휴학 기준을 알려주세요."})
    page = c.get("/chat/1").text
    assert "다시 시도" in page
    assert "작업 패널" in page


# ---- 생각하는 모델 대응 (한도를 생각에 다 써 답이 비어 오는 경우) ----

class _Msg:
    def __init__(self, content, reasoning=""):
        self.content = content
        self.reasoning = reasoning


class _Choice:
    def __init__(self, content, reasoning="", finish_reason="stop"):
        self.message = _Msg(content, reasoning)
        self.finish_reason = finish_reason


class _Resp:
    def __init__(self, content, reasoning="", finish_reason="stop"):
        self.choices = [_Choice(content, reasoning, finish_reason)]
        self.usage = None


class _FakeCompletions:
    """첫 호출은 생각만 하고 답이 비어 돌아오고, 한도가 넓어지면 답을 준다."""

    def __init__(self):
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        if int(kw.get("max_tokens") or 0) < 1000:
            return _Resp("", reasoning="한참 생각합니다.", finish_reason="length")
        return _Resp("정산보고서에는 집행 내역과 증빙이 들어갑니다.")


class _FakeModels:
    def list(self):
        class D:
            data = [type("M", (), {"id": "생각모델"})()]
        return D()


class _FakeClient:
    def __init__(self):
        self.chat = type("C", (), {})()
        self.chat.completions = _FakeCompletions()
        self.models = _FakeModels()


def test_widens_budget_when_model_only_thinks(monkeypatch):
    from zzaimy.generate import client as cl

    fake = _FakeClient()
    monkeypatch.setattr(cl, "OpenAI", lambda **kw: fake)
    c = cl.VllmClient(base_url="http://localhost:1/v1", model="생각모델")

    r = c.client.chat.completions.create(model="생각모델", max_tokens=120,
                                         messages=[{"role": "user", "content": "질문"}])
    assert r.choices[0].message.content                      # 빈 답으로 끝나지 않는다
    assert len(fake.chat.completions.calls) == 2             # 한 번만 다시 받는다
    assert fake.chat.completions.calls[1]["max_tokens"] >= cl._THINKING_FLOOR


def test_keeps_answer_when_model_answers_first_try(monkeypatch):
    from zzaimy.generate import client as cl

    fake = _FakeClient()
    monkeypatch.setattr(cl, "OpenAI", lambda **kw: fake)
    c = cl.VllmClient(base_url="http://localhost:1/v1", model="생각모델")
    c.client.chat.completions.create(model="생각모델", max_tokens=2000,
                                     messages=[{"role": "user", "content": "질문"}])
    assert len(fake.chat.completions.calls) == 1             # 쓸데없이 다시 부르지 않는다


# ---- 외부 참조 — 토큰화 후 외부 처리, 답은 교내 모델이 만든다 ----

def test_chat_no_longer_offers_legacy_external_api(tmp_path):
    page = _client(tmp_path).get("/chat").text
    assert 'id="extBtn"' in page
    assert 'name="external"' not in page


def test_legacy_external_flag_does_not_trigger_external_reference(tmp_path, monkeypatch):
    # 제거된 external 필드는 외부 호출을 활성화하지 않는다.
    c = _client(tmp_path)
    c.post("/chat/send", data={"question": "지원 자격을 알려주세요.", "external": "1"})
    msgs = c.get("/chat/1/messages").json()["messages"]
    texts = [m["content"] for m in msgs]
    assert not any("외부 참조를 쓰지 못했습니다" in t for t in texts)
    assert any("합성 답변" in t for t in texts)      # 답변 자체는 계속된다


# ---- 화면 맥락을 알고 할 일을 내어 준다 (몰래 실행하지 않는다) ----

def test_ask_offers_actions_for_the_current_document(tmp_path):
    c = _client(tmp_path)
    c.post("/upload", files={"file": ("규정.pdf", b"%PDF fake", "application/pdf")})
    body = c.post("/chat/ask", data={"question": "이 문서 정체 읽어줘", "page": "/doc/1"}).json()
    assert body["done"]                                  # 시킨 일은 바로 한다
    assert any("사업 정보" in line for line in body["done"])


def test_ask_without_page_offers_no_actions(tmp_path):
    body = _client(tmp_path).post("/chat/ask", data={"question": "안녕하세요"}).json()
    assert body.get("actions") == []


# ---- 명령하면 에이전트가 직접 한다 ----

def test_agent_runs_identity_when_told(tmp_path):
    from zzaimy.app.db import Database

    c = _client(tmp_path)
    c.post("/upload", files={"file": ("공고.pdf", b"%PDF fake", "application/pdf")})
    db = Database(tmp_path / "t.db")
    db.replace_doc_chunks(1, [{"kind": "text", "page_no": 1,
                               "content": "2026년 통영시 학자금 지원 공고입니다. 통영시장이 공고합니다."}])
    body = c.post("/chat/ask", data={"question": "이 문서 정체 읽어줘", "page": "/doc/1"}).json()
    assert body["ok"] is True
    assert body["done"]                      # 시키면 바로 한다
    assert not any(a["auto"] for a in body["actions"])   # 한 일은 단추로 남기지 않는다


def test_agent_starts_a_project_from_an_announcement(tmp_path):
    from zzaimy.app.db import Database

    c = _client(tmp_path)
    c.post("/upload", files={"file": ("지원사업공고.pdf", b"%PDF fake", "application/pdf")})
    db = Database(tmp_path / "t.db")
    db.set_doc_identity(1, {"program": "통영시 학자금 지원", "organizer": "통영시장"})
    body = c.post("/chat/ask", data={"question": "이 공고로 사업 준비해줘", "page": "/doc/1"}).json()
    assert any("프로젝트" in line for line in body["done"])
    projects = db.list_all_projects()
    assert projects, "프로젝트가 만들어져야 한다"
    assert db.get_document(1)["project_id"] == projects[0]["id"]


def test_agent_leaves_outward_actions_as_buttons(tmp_path):
    body = _client(tmp_path).post("/chat/ask", data={"question": "외부 전송 켜줘"}).json()
    labels = [a["label"] for a in body["actions"]]
    assert "외부 구독 연결" in labels          # 상태 확인 화면만 연결하며 자동 실행하지 않는다
    assert body["done"] == []


def test_ollama_server_gets_reasoning_effort_none(monkeypatch):
    """Ollama 는 vLLM 식 생각 끄기 인자를 무시한다 — reasoning_effort=none 을 붙여 보낸다."""
    from zzaimy.generate import client as cl

    seen = {}

    class _Comp:
        def create(self, **kw):
            seen.update(kw)
            raise RuntimeError("stop")

    class _Fake:
        base_url = "http://ollama.example:11434/v1/"

        def __init__(self, **kw):
            self.chat = type("C", (), {"completions": _Comp()})()

    monkeypatch.setattr(cl, "OpenAI", _Fake)
    monkeypatch.setattr(cl, "is_ollama", lambda url: True)
    c = cl.VllmClient(model="m")
    try:
        c.client.chat.completions.create(model="m", messages=[],
                                         extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    except RuntimeError:
        pass
    assert seen["extra_body"] == {"reasoning_effort": "none"}


def test_vllm_call_without_extra_gets_thinking_off(monkeypatch):
    """내부 vLLM 호출이 생각 끄기 인자를 빠뜨려도 클라이언트가 붙인다 — 호출부가 정한 값은 그대로."""
    from zzaimy.generate import client as cl

    seen = []

    class _Comp:
        def create(self, **kw):
            seen.append(kw)
            raise RuntimeError("stop")

    class _Fake:
        base_url = "http://vllm.example:8000/v1/"

        def __init__(self, **kw):
            self.chat = type("C", (), {"completions": _Comp()})()

    monkeypatch.setattr(cl, "OpenAI", _Fake)
    monkeypatch.setattr(cl, "is_ollama", lambda url: False)
    c = cl.VllmClient(model="m")
    for extra in (None, {"chat_template_kwargs": {"enable_thinking": True}}):
        try:
            c.client.chat.completions.create(model="m", messages=[], **({"extra_body": extra} if extra else {}))
        except RuntimeError:
            pass
    assert seen[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert seen[1]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}


def test_helper_chats_stay_out_of_sidebar(tmp_path, monkeypatch):
    """떠 있는 창의 업무 도우미 대화는 「업무 상담」 — 왼쪽 사이드바·대화 검색에는 안 나오고 창의 기록(channel=helper)에만."""
    monkeypatch.setenv("ZZAIMY_PLATFORM_HELPER", "1")
    c = _client(tmp_path)
    helper = c.post("/chat/ask", data={"question": "학생 RAG 등록 어떻게 해?", "page": "/dev/rag"}).json()["session_id"]
    monkeypatch.setenv("ZZAIMY_PLATFORM_HELPER", "0")
    work = c.post("/chat/ask", data={"question": "휴학 처리 기준을 알려주세요."}).json()["session_id"]
    side = {s["id"] for s in c.get("/api/chat/sessions").json()["sessions"]}
    mine = {s["id"] for s in c.get("/api/chat/sessions?channel=helper").json()["sessions"]}
    assert work in side and helper not in side
    assert helper in mine and work not in mine
    page = c.get("/chat").text
    assert f'/chat/{helper}"' not in page                         # 사이드바 최근 대화에 없다


def test_find_document_is_one_query_and_picks_clear_winner(tmp_path):
    """질문 속 문서 짚기는 문서 수와 상관없이 한 번에 찾는다 — 문서마다 따로 묻지 않는다(수만 건에서 창이 멈추던 것)."""
    from zzaimy.app.db import Database
    c = _client(tmp_path)
    db = Database(tmp_path / "t.db")
    a = db.add_document("LINC3 성과보고서 2024.hwp", "x")
    db.add_document("다른 공고.hwp", "x")
    calls = []
    orig = Database.get_doc_identity
    Database.get_doc_identity = lambda self, i: calls.append(i) or orig(self, i)
    try:
        sid = c.post("/chat/ask", data={"question": "LINC3 성과보고서 요약", "page": "/"}).json()["session_id"]
    finally:
        Database.get_doc_identity = orig
    assert sid and len(calls) < 5


def test_helper_chat_continues_on_project_page(tmp_path, monkeypatch):
    """업무 상담 대화는 프로젝트에 묶지 않는다 — 프로젝트 화면에서 기록을 골라 이어 써도 409 로 막지 않고, 새로 만들어도 프로젝트가 없다."""
    monkeypatch.setenv("ZZAIMY_PLATFORM_HELPER", "1")
    c = _client(tmp_path)
    c.post("/projects", data={"sector": "grant", "name": "시험 프로젝트"}, follow_redirects=False)
    from zzaimy.app.db import Database
    proj = Database(tmp_path / "t.db").list_projects("grant")[0]["id"]
    sid = c.post("/chat/ask", data={"question": "문서 어떻게 올려?", "page": "/"}).json()["session_id"]
    msgs = c.get(f"/chat/{sid}/messages").json()
    assert msgs["channel"] == "helper" and msgs["project_id"] is None
    import time
    for _ in range(50):
        if not c.get(f"/chat/{sid}/messages").json()["waiting"]:
            break
        time.sleep(0.1)
    r = c.post("/chat/ask", data={"question": "그럼 폴더는?", "page": f"/project/{proj}", "session_id": str(sid)})
    assert r.status_code == 200 and r.json().get("session_id") == sid
    new = c.post("/chat/ask", data={"question": "학생 공개는?", "page": f"/project/{proj}"}).json()["session_id"]
    assert c.get(f"/chat/{new}/messages").json()["project_id"] is None


def test_answer_feedback_and_run_are_recorded(tmp_path):
    """베타 수집 — 답마다 실행 기록(answer_runs)이 남고, 대화 주인이 도움됨·아쉬움(까닭·의견)을 남기고 거둘 수 있다."""
    from zzaimy.app.db import Database
    c = _client(tmp_path)
    db = Database(tmp_path / "t.db")
    sid = db.create_chat_session("평가 시험", owner="zzaimy")
    db.add_chat(sid, "user", "질문")
    mid = db.add_chat(sid, "assistant", "답")
    r = c.post(f"/chat/{sid}/messages/{mid}/feedback", data={"verdict": "bad", "reasons": ["숫자·사실이 틀림", "아무거나"], "note": "300명이 맞음"})
    assert r.json()["ok"] and db.answer_feedback_for(sid, "zzaimy")[mid] == {"verdict": "bad", "reasons": ["숫자·사실이 틀림"], "note": "300명이 맞음"}
    page = c.get(f"/chat/{sid}").text
    assert 'data-feedback="bad"' in page and 'aria-pressed="true"' in page
    c.post(f"/chat/{sid}/messages/{mid}/feedback", data={"verdict": ""})
    assert db.answer_feedback_for(sid, "zzaimy") == {}
    qmid = [m["id"] for m in db.list_chats(sid) if m["role"] == "user"][0]
    assert c.post(f"/chat/{sid}/messages/{qmid}/feedback", data={"verdict": "good"}).status_code == 404     # 질문에는 평가 없음
