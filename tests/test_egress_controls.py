"""외부 전송 통제 — 외부 LLM 연결의 가림 관문, 용도별 외부 연결 차단, 드라이브 업로드 전 열람 검사, 외부 검색 관문."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import httpx
import pytest

PHONE = "010-1234-5678"
MAIL = "hong@ync.ac.kr"


def _resp(text: str):
    return NS(choices=[NS(message=NS(content=text), finish_reason="stop")], usage=None)


def test_guard_masks_outgoing_messages_and_restores_answer():
    from zzaimy.generate import outbound

    seen = {}

    def create(**kw):
        seen.update(kw)
        body = kw["messages"][1]["content"]
        tok = next(t for t in body.split() if t.startswith("[[KR_PHONE") or t.startswith("[[PHONE"))
        return _resp(f"담당 연락처는 {tok.rstrip(',')} 입니다")

    guarded = outbound.guard(create)
    out = guarded(model="m", messages=[{"role": "system", "content": "지침"},
                                       {"role": "user", "content": f"담당자 연락처 {PHONE}, 메일 {MAIL}"}])
    sent = json.dumps(seen["messages"], ensure_ascii=False)
    assert PHONE not in sent and MAIL not in sent and "[[" in sent
    assert PHONE in out.choices[0].message.content                       # 답은 교내에서 원값으로 되돌린다


def test_guard_blocks_images_and_missing_masker(monkeypatch):
    from zzaimy.generate import outbound

    called = []
    guarded = outbound.guard(lambda **kw: called.append(kw) or _resp("x"))
    with pytest.raises(outbound.OutboundBlocked):
        guarded(model="m", messages=[{"role": "user", "content": [
            {"type": "text", "text": "이 쪽을 읽어"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}])

    def broken():
        raise ImportError("presidio 없음")

    monkeypatch.setattr(outbound, "_masker", None)
    monkeypatch.setattr(outbound, "_get_masker", broken)
    with pytest.raises(outbound.OutboundBlocked):
        guarded(model="m", messages=[{"role": "user", "content": "아무 글"}])
    assert called == []                                                   # 원문이 나가지 않았다


def test_stream_restores_tokens_split_across_chunks():
    from zzaimy.generate import outbound

    vault = outbound.Vault()
    vault.tok2val["[[KR_PHONE_1]]"] = PHONE

    def chunk(text, finish=None):
        return NS(choices=[NS(index=0, delta=NS(content=text), finish_reason=finish)], usage=None)

    pieces = ["번호는 [[KR_", "PHONE_1]] 이고 ", "끝[", "", "]"]
    out = []
    for c in outbound.restore_stream([chunk(p) for p in pieces[:-1]] + [chunk(pieces[-1], "stop")], vault):
        out.append(c.choices[0].delta.content or "")
    assert "".join(out) == f"번호는 {PHONE} 이고 끝[]"


class _FakeCompletions:
    def __init__(self, log):
        self.log = log

    def create(self, **kw):
        self.log.append(kw)
        return _resp("답")


class _FakeOpenAI:
    log: list = []

    def __init__(self, base_url="", api_key="", **kw):
        self.base_url = base_url
        self.chat = NS(completions=_FakeCompletions(_FakeOpenAI.log))
        self.models = NS(list=lambda: NS(data=[NS(id="m")]))

    def with_options(self, **kw):
        return _FakeOpenAI(self.base_url)

    copy = with_options


def _cfg(external: bool) -> dict:
    return {"base_url": "https://partner.example/v1" if external else "http://in:8000/v1", "model": "m",
            "vision_model": "", "api_key": "k", "configured": True, "source": "t",
            "kind": "partner" if external else "vllm", "external": external, "connection_id": "c1"}


def test_vllm_client_on_external_connection_sends_only_masked_text(monkeypatch):
    from zzaimy.generate import client as gen, model_config

    _FakeOpenAI.log = []
    monkeypatch.setattr(gen, "OpenAI", _FakeOpenAI)
    monkeypatch.setattr(model_config, "current", lambda role="": _cfg(True))
    c = gen.VllmClient(role="answer")
    assert c.external is True
    c.client.chat.completions.create(model=c.model, messages=[{"role": "user", "content": f"연락처 {PHONE}"}])
    c.client.with_options(timeout=5).chat.completions.create(model=c.model, messages=[{"role": "user", "content": f"메일 {MAIL}"}])
    sent = json.dumps(_FakeOpenAI.log, ensure_ascii=False)
    assert len(_FakeOpenAI.log) == 2 and PHONE not in sent and MAIL not in sent

    _FakeOpenAI.log = []
    monkeypatch.setattr(model_config, "current", lambda role="": _cfg(False))
    c = gen.VllmClient(role="answer")                                     # 교내 서버는 원문 그대로(내부 활용, ADR-0044)
    assert c.external is False
    c.client.chat.completions.create(model=c.model, messages=[{"role": "user", "content": f"연락처 {PHONE}"}])
    assert PHONE in json.dumps(_FakeOpenAI.log, ensure_ascii=False)


def test_token_count_is_not_sent_to_external_connection(monkeypatch):
    from zzaimy.app import llm_budget

    def no_network(*a, **k):
        raise AssertionError("외부 연결에 메시지를 세러 보냈다")

    monkeypatch.setattr(llm_budget.urllib.request, "urlopen", no_network)
    fake = NS(external=True, model="m", client=NS(base_url="https://partner.example/v1", api_key="k"))
    assert llm_budget._server_count(fake, [{"role": "user", "content": PHONE}]) is None


def test_intake_review_and_vision_never_fall_back_to_external_default(tmp_path, monkeypatch):
    """문서 작업 기본 연결이 외부 서버여도 반입 검토·이미지 판독은 교내 경로로 간다."""
    from zzaimy.generate import llm_connections as lc, model_config

    monkeypatch.delenv("ZZAIMY_ROLE_CONN", raising=False)
    monkeypatch.setenv("VLLM_BASE_URL", "http://in:8000/v1")
    lc.configure(tmp_path / "c.json")
    ext = lc.add("외부 기관", "partner", "https://llm.partner.example/v1", "m-x", "k-1234")
    lc.activate(ext["id"], ack_external=True)
    assert lc.role_conn("answer")["id"] == ext["id"]
    for role in ("review", "vision"):
        assert lc.role_conn(role) is None
        cfg = model_config.current(role)
        assert cfg["external"] is False and cfg["base_url"] == "http://in:8000/v1"
    monkeypatch.setenv("ZZAIMY_ROLE_CONN", f"review={ext['id']}")       # 일괄 작업 덮어쓰기로도 외부로 못 보낸다
    assert lc.role_conn("review") is None


def test_web_search_can_be_turned_off_and_fails_closed(monkeypatch):
    from zzaimy.app import access_guard, web_search

    monkeypatch.setenv("ZZAIMY_WEB_SEARCH", "off")
    with pytest.raises(web_search.Blocked):
        web_search.answer_with_web("B200 과 H200 의 차이", client=object(),
                                   http=httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("나갔다"))))
    monkeypatch.delenv("ZZAIMY_WEB_SEARCH")
    monkeypatch.setattr(access_guard, "masker_ready", lambda: False)
    assert web_search.screen_question("B200 과 H200 의 차이") is True      # 검사기가 없으면 보내지 않는다


def test_google_cse_key_goes_in_header_not_url(monkeypatch):
    from zzaimy.app import web_search

    monkeypatch.setenv("ZZAIMY_GOOGLE_CSE_KEY", "secret-key-123")
    monkeypatch.setenv("ZZAIMY_GOOGLE_CSE_CX", "cx-1")
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"items": [{"title": "t", "link": "https://example.org/a", "snippet": "s"}]})

    out = web_search.search_google("질문", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert out and "secret-key-123" not in str(seen[0].url)
    assert seen[0].headers.get("x-goog-api-key") == "secret-key-123"


def test_unshare_failure_is_reported(caplog):
    from zzaimy.ingest import gdrive_files

    gdrive_files_headers = gdrive_files._headers
    try:
        gdrive_files._headers = lambda email, http: {}
        http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        assert gdrive_files.unshare("a@x", "f1", "p1", http=http) is False
        assert "공개 권한을 닫지 못했습니다" in caplog.text
        ok = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(204)))
        assert gdrive_files.unshare("a@x", "f1", "p1", http=ok) is True
    finally:
        gdrive_files._headers = gdrive_files_headers


def test_google_view_copy_of_hidden_document_is_refused(tmp_path):
    """구글 열람본 만들기(드라이브 업로드)도 문서 경로와 같은 열람 검사를 거친다."""
    from tests.test_doc_visibility import _client
    from zzaimy.app.db import Database

    (tmp_path / "accounts.json").write_text(json.dumps({
        "kim": {"pw": "kim-pass", "role": "staff", "dept": "학생처"},
        "lee": {"pw": "lee-pass", "role": "staff", "dept": "산학협력단"},
    }))
    db = Database(tmp_path / "t.db")
    src = tmp_path / "x.txt"; src.write_text("학생처 접수 서류")
    did = db.add_document("학생처 서류.txt", str(src), doc_type="auto", owner="kim", dept="학생처", access_level="dept")
    db.update_document(did, status="reviewed", masked_text="학생처 접수 서류")
    lee = _client(tmp_path, "lee", "lee-pass")
    assert lee.get(f"/api/doc/{did}/google").status_code == 404
    kim = _client(tmp_path, "kim", "kim-pass")
    assert kim.get(f"/api/doc/{did}/google").status_code == 400           # 볼 수 있는 사람은 계정 연결 안내까지 간다
