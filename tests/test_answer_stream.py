"""답 생성은 조각 응답으로 받아 쓰는 대로 넘긴다 — 진행 상태의 「쓰는 중인 답」, 다 쓰면 최종 답과 같은 글."""
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import llm_budget
from zzaimy.app.main import create_app
from zzaimy.app.responder import AgentResponder as Responder


def _chunk(text=None, finish=None, usage=None):
    return NS(choices=[NS(delta=NS(content=text), finish_reason=finish)] if text is not None or finish else [], usage=usage)


def test_stream_collects_text_and_reports_partials(monkeypatch):
    seen = []
    monkeypatch.setattr(llm_budget, "create_fitted",
                        lambda *a, **kw: iter([_chunk("안녕"), _chunk("하세요"), _chunk("", "stop"), _chunk(usage=None)]))
    monkeypatch.setattr("time.monotonic", iter(range(0, 100, 1)).__next__)
    client = NS(model="m", connection_id=None, _extra={})
    text, finish = Responder._stream(client, [], 100, [], lambda m: None, seen.append)
    assert text == "안녕하세요" and finish == "stop"
    assert seen and seen[-1].startswith("안녕")


def test_status_carries_partial_answer_while_writing(tmp_path):
    observed = []

    class R:
        def answer(self, db, question, on_progress=None, on_partial=None, **kw):
            on_progress("답변 작성 중")
            on_partial("부분 답 010-12")                  # 쓰는 중인 마지막 낱말(덜 쓰인 번호)은 보이지 않는다
            observed.append(client.get("/chat/1/status").json())
            return "부분 답 끝"

    app = create_app(db_path=tmp_path / "c.db", inbox_dir=tmp_path / "in", processor=FakeProcessor(), drafter=FakeDrafter(), responder=R())
    client = TestClient(app)
    client.post("/chat/send", data={"question": "질문"})
    assert observed[0]["progress"]["partial"] == "부분 답"
    assert client.get("/chat/1/status").json()["progress"] is None          # 끝나면 진행 상태(쓰는 중인 답)는 사라진다


def test_partial_answer_is_masked_and_holds_back_last_word(tmp_path, monkeypatch):
    """마스킹을 켠 경우 — 쓰는 중인 답도 가림을 거치고, 완성되지 않은 마지막 낱말은 보이지 않는다."""
    from zzaimy.app import privacy_policy
    observed = []

    class R:
        def answer(self, db, question, on_progress=None, on_partial=None, **kw):
            on_partial("연락처 010-1234-5678 로 문의")
            observed.append(client.get("/chat/1/status").json()["progress"]["partial"])
            on_partial("연락처 010-12")
            observed.append(client.get("/chat/1/status").json()["progress"]["partial"])
            return "끝"

    app = create_app(db_path=tmp_path / "c.db", inbox_dir=tmp_path / "in", processor=FakeProcessor(), drafter=FakeDrafter(), responder=R())
    privacy_policy.save(app.state.db, True, list(privacy_policy.ENTITIES), "t")
    client = TestClient(app)
    client.post("/chat/send", data={"question": "질문"})
    assert "010-1234-5678" not in observed[0] and observed[0].startswith("연락처")
    assert observed[1] == "연락처"
