"""외부 검색 모드 — 검색 결과 파싱, 쪽 글 추출, 27B 답에 출처 번호."""

import httpx

from zzaimy.app import web_search

DDG = '''<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fb200&amp;rut=1">NVIDIA B200 <b>사양</b></a>
<a class="result__snippet" href="#">B200 은 Blackwell 아키텍처…</a></div>
<div class="result"><a class="result__a" href="https://example.org/h200">H200 소개</a><a class="result__snippet" href="#">H200 은 141GB HBM3e</a></div>'''
PAGE = "<html><head><style>x{}</style><script>var a=1;</script></head><body><nav>메뉴</nav><h1>B200</h1><p>B200 은 192GB HBM3e 메모리와 8TB/s 대역폭을 갖춘 가속기다.</p><p>짧음</p></body></html>"


class _Msg:
    def __init__(self, c): self.content = c


class _Resp:
    def __init__(self, c): self.choices = [type("C", (), {"message": _Msg(c)})()]


class FakeClient:
    model = "writer"
    _extra = {}
    def __init__(self):
        self.prompts = []
        outer = self
        class _Comp:
            def create(self_inner, **kw):
                outer.prompts.append(kw["messages"][0]["content"]); return _Resp("B200 은 192GB HBM3e 메모리를 갖춘다[1]. H200 은 141GB 다[2].")
        self.client = type("K", (), {"chat": type("Ch", (), {"completions": _Comp()})()})()


def test_search_fetch_and_answer_with_sources(monkeypatch):
    monkeypatch.setattr(web_search, "_safe_url", lambda url: True)          # 시험에서는 DNS 를 안 본다
    monkeypatch.setattr(web_search, "screen_question", lambda q: False)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "html.duckduckgo.com":
            assert req.method == "POST" and "b200" in req.content.decode().lower()
            return httpx.Response(200, text=DDG)
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html; charset=utf-8"})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    hits = web_search.search("B200 H200 차이", http=http)
    assert [h["url"] for h in hits] == ["https://example.org/b200", "https://example.org/h200"] and hits[0]["title"] == "NVIDIA B200 사양"
    text = web_search.fetch_text("https://example.org/b200", http=http)
    assert "192GB HBM3e" in text and "var a" not in text and "메뉴" not in text and "짧음" not in text
    fc = FakeClient()
    res = web_search.answer_with_web("B200 과 H200 차이", client=fc, http=http)
    assert res["searched"] == 2 and [s["n"] for s in res["sources"]] == [1, 2]
    assert "[웹 자료]" in fc.prompts[0] and "[1] NVIDIA B200 사양 (https://example.org/b200)" in fc.prompts[0]
    out = web_search.render(res)
    assert "[1]" in out and "출처(외부 검색):" in out and "[2] H200 소개 — https://example.org/h200" in out
    assert "찾지 못했습니다" in web_search.render({"answer": "", "sources": [], "searched": 0})



def test_private_targets_and_sensitive_questions_are_blocked(monkeypatch):
    """사설망·루프백 주소는 받지 않고, 개인정보가 든 질문은 밖으로 보내지 않는다(아스트라 검토 C-130)."""
    import pytest
    for bad in ("http://127.0.0.1/x", "http://10.0.0.5/", "http://192.168.16.226/login", "http://169.254.169.254/latest", "http://localhost/", "ftp://example.org/"):
        assert web_search._safe_url(bad) is False
    assert web_search._safe_url("http://8.8.8.8/") is True
    # 리다이렉트 목적지가 사설망이면 따라가지 않는다
    calls = []
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.host == "8.8.8.8":
            return httpx.Response(302, headers={"location": "http://10.0.0.5/secret"})
        return httpx.Response(200, text="<p>비밀 자료 비밀 자료 비밀 자료 비밀 자료</p>", headers={"content-type": "text/html"})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert web_search.fetch_text("http://8.8.8.8/", http=http) == "" and calls == ["http://8.8.8.8/"]
    monkeypatch.setattr(web_search, "screen_question", lambda q: True)
    with pytest.raises(web_search.Blocked):
        web_search.answer_with_web("주민번호 900101-1234567 인 사람의 …", client=FakeClient(), http=http)
    # 검사기는 접수 문서 마스킹과 같은 것(access_guard.scrub) — 가려지는 글자가 있으면 보내지 않는다
    monkeypatch.undo()
    from zzaimy.app import access_guard
    monkeypatch.setattr(access_guard, "scrub", lambda text: text.replace("900101-1234567", "[주민번호]"))
    assert web_search.screen_question("주민번호 900101-1234567 확인해 줘") is True
    assert web_search.screen_question("B200 과 H200 의 차이") is False



def test_model_knowledge_mode_has_no_network_and_flags_no_sources():
    fc = FakeClient()
    res = web_search.answer_from_model("B200 과 H200 의 차이", client=fc)
    assert res["mode"] == "model" and res["sources"] == [] and "학습한 지식만으로" in fc.prompts[0]
    out = web_search.render_model(res)
    assert out.startswith("(모델 지식 기반 답변 — 출처 없음") and "B200" in out
