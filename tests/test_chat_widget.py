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


# ---- 지식 그래프 근거 문장 (본문 전체에서 찾는다) ----

def test_graph_evidence_quotes_from_full_text(tmp_path):
    from zzaimy.app.db import Database

    c = _client(tmp_path)
    db = Database(tmp_path / "t.db")
    c.post("/upload", files={"file": ("인용문서.pdf", b"%PDF fake", "application/pdf")})
    c.post("/upload", files={"file": ("피인용문서.pdf", b"%PDF fake", "application/pdf")})

    class _C:
        def __init__(self, heading, content):
            self.heading, self.content = heading, content

    # 근거 표현은 앞부분이 아니라 한참 뒤에 둔다 — 미리보기 구간 밖에서도 찾아야 한다
    filler = "앞부분 문장입니다. " * 300
    db.add_regulation_chunks(1, "인용 규정", [
        _C("제1조", filler),
        _C("제9조", "이 절차는 영남이공대학교 학칙 제19조에 따른다."),
    ])
    db.add_regulation_chunks(2, "영남이공대학교 학칙", [_C("제19조", "휴학에 관한 조문입니다.")])

    r = c.get("/graph/evidence", params={"kind": "cites", "s": "d1", "t": "d2"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["term"] == "영남이공대학교 학칙"
    assert body["quotes"] and "제19조에 따른다" in body["quotes"][0]["text"]
    assert body["quotes"][0]["heading"] == "제9조"


def test_graph_evidence_reports_when_term_absent(tmp_path):
    from zzaimy.app.db import Database

    c = _client(tmp_path)
    db = Database(tmp_path / "t.db")
    c.post("/upload", files={"file": ("문서.pdf", b"%PDF fake", "application/pdf")})

    class _C:
        def __init__(self, heading, content):
            self.heading, self.content = heading, content

    db.add_regulation_chunks(1, "어떤 규정", [_C("제1조", "관련 없는 본문입니다.")])
    body = c.get("/graph/evidence", params={"s": "d1", "term": "없는 이름"}).json()
    assert body["ok"] is False and body["n"] == 0    # 없으면 없다고 말한다


def test_graph_evidence_rejects_non_document_source(tmp_path):
    body = _client(tmp_path).get("/graph/evidence", params={"s": "e42", "term": "가"}).json()
    assert body["ok"] is False


# ---- 외부 참조 — 토큰화 후 외부 처리, 답은 교내 모델이 만든다 ----

def test_chat_offers_external_reference(tmp_path):
    page = _client(tmp_path).get("/chat").text
    assert 'id="extBtn"' in page
    assert 'name="external"' in page


def test_external_reference_reports_when_unavailable(tmp_path, monkeypatch):
    # 외부 전송이 꺼져 있으면 그 사실을 남기고 교내 자료만으로 답한다
    c = _client(tmp_path)
    c.post("/chat/send", data={"question": "지원 자격을 알려주세요.", "external": "1"})
    msgs = c.get("/chat/1/messages").json()["messages"]
    texts = [m["content"] for m in msgs]
    assert any("외부 참조를 쓰지 못했습니다" in t for t in texts)
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
    assert "외부 전송 켜기" in labels          # 밖으로 나가는 일은 확인을 받는다
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
