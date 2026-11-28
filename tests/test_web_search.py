"""인터넷 검색 — 검색 결과 파싱, 쪽 글 추출, 27B 답에 출처 번호."""

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
    assert "[1]" in out and "출처(인터넷 검색):" in out and "[2] H200 소개 (https://example.org/h200)" in out
    assert "찾지 못했습니다" in web_search.render({"answer": "", "sources": [], "searched": 0})



def test_private_targets_and_sensitive_questions_are_blocked(monkeypatch):
    """사설망·루프백 주소는 받지 않고, 개인정보가 든 질문은 밖으로 보내지 않는다."""
    import pytest
    for bad in ("http://127.0.0.1/x", "http://10.0.0.5/", "http://172.16.5.10/login", "http://169.254.169.254/latest", "http://localhost/", "ftp://example.org/"):
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
    assert out.startswith("B200") and "모델이 아는 내용으로 답했습니다" in out



def test_search_provider_switches_with_keys(monkeypatch):
    monkeypatch.delenv("ZZAIMY_WEB_SEARCH", raising=False)
    for k in ("ZZAIMY_GOOGLE_CSE_KEY", "ZZAIMY_GOOGLE_CSE_CX", "ZZAIMY_NAVER_CLIENT_ID", "ZZAIMY_NAVER_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert web_search.provider() == "duckduckgo"
    monkeypatch.setenv("ZZAIMY_NAVER_CLIENT_ID", "id"); monkeypatch.setenv("ZZAIMY_NAVER_CLIENT_SECRET", "sec")
    assert web_search.provider() == "naver"
    monkeypatch.setenv("ZZAIMY_GOOGLE_CSE_KEY", "key"); monkeypatch.setenv("ZZAIMY_GOOGLE_CSE_CX", "cx")
    assert web_search.provider() == "google"                      # 둘 다 있으면 구글
    seen = []
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.host == "www.googleapis.com":
            assert req.url.params["cx"] == "cx" and req.url.params["q"] == "B200"
            return httpx.Response(200, json={"items": [{"title": "NVIDIA B200", "link": "https://example.org/b", "snippet": "설명"}]})
        return httpx.Response(200, json={"items": [{"title": "<b>B200</b> 소개", "link": "https://example.org/n", "description": "네이버 &amp; 설명"}]})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert web_search.search("B200", http=http) == [{"title": "NVIDIA B200", "url": "https://example.org/b", "snippet": "설명"}]
    monkeypatch.setenv("ZZAIMY_WEB_SEARCH", "naver")
    assert web_search.search("B200", http=http) == [{"title": "B200 소개", "url": "https://example.org/n", "snippet": "네이버 & 설명"}]


def test_search_query_only_uses_user_words():
    from types import SimpleNamespace
    from zzaimy.app import web_search as ws

    def fake(text):
        msg = SimpleNamespace(content=text)
        return SimpleNamespace(client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=msg)])))), model="m", _extra={})
    hist = {"recent": [{"role": "user", "content": "우리대학 직원 정년퇴임 연령은?"},
                       {"role": "assistant", "content": "직원인사 규정 제35조 기밀 내용 61세"}]}
    # 답에서 온 낱말(제35조·61세)이 섞이면 질문 그대로
    assert ws.search_query("교원은?", fake("직원인사 규정 제35조 61세"), hist) == "교원은?"
    # 담당자가 쓴 낱말만이면 그 검색어
    assert ws.search_query("교원은?", fake("우리대학 교원 정년퇴임 연령"), hist) == "우리대학 교원 정년퇴임 연령"


def test_model_answer_streams_partial_without_offer_line(monkeypatch):
    """11-25: 일반 모드 답이 17초 동안 아무것도 없다가 한꺼번에 왔다 — 조각 응답으로 받아 쓰는 대로 넘기고, 끝 표시 줄은 넘기지 않는다."""
    import time as _t
    from types import SimpleNamespace as NS
    pieces = ["구구단은 ", "for 문 두 개로 ", "씁니다.\n", "[인터넷 확인: 불", "필요]"]

    class Comp:
        def create(self, **kw):
            assert kw.get("stream") is True
            def gen():
                for p in pieces:
                    yield NS(choices=[NS(delta=NS(content=p))])
            return gen()
    fc = NS(model="m", _extra={}, client=NS(chat=NS(completions=Comp())))
    ticks = iter(range(0, 1000))
    monkeypatch.setattr(_t, "monotonic", lambda: next(ticks) * 1.0)
    seen = []
    res = web_search.answer_from_model("구구단 코드", client=fc, on_partial=seen.append)
    assert res["answer"] == "구구단은 for 문 두 개로 씁니다." and res["offer_web"] is False
    assert len(seen) >= 2 and seen[0].startswith("구구단은") and not any("인터넷" in s for s in seen)


def test_table_values_and_snippets_reach_the_model_and_second_query_runs(monkeypatch):
    """표 칸의 수치(짧은 줄)는 남기고, 검색 요약도 함께 넣고, 못 찾았다고 하면 결과에 보인 낱말로 한 번 더 찾는다(11-28)."""
    monkeypatch.setattr(web_search, "_safe_url", lambda url: True)
    monkeypatch.setattr(web_search, "screen_question", lambda q: False)
    monkeypatch.setattr(web_search, "provider", lambda: "naver")
    menu = "".join(f"<p>메뉴 항목 {i} 안내 소개 페이지 바로가기 링크</p>" for i in range(300))
    pages = {
        "https://a.example/1": f"<html><body>{menu}<table><tr><td>구분</td><td>재학생</td></tr><tr><td>2025</td><td>8,123명</td></tr></table></body></html>",
        "https://b.example/2": "<html><body><p>이 대학은 대구에 있으며 여러 학과를 두고 있다. 학생 수는 이 글에 없다.</p></body></html>",
        "https://c.example/3": "<html><body><p>대학알리미 공시: 재학생 수 8,123명(2025년 4월 1일 기준) 입니다.</p></body></html>",
    }
    queries = []

    def fake_search(q, k=6, http=None):
        queries.append(q)
        if len(queries) == 1:
            return [{"title": "학교 소개", "url": "https://b.example/2", "snippet": "대학알리미 에서 학생 수 확인"}]
        return [{"title": "대학알리미 공시", "url": "https://c.example/3", "snippet": ""}]
    monkeypatch.setattr(web_search, "search", fake_search)
    http = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, text=pages.get(str(req.url), ""), headers={"content-type": "text/html"})))
    text = web_search.fetch_text("https://a.example/1", max_chars=1500, http=http, focus="재학생 수")
    assert "8,123명" in text and len(text) <= 1500

    answers = iter(["자료에서 확인하지 못했다.", "대학알리미 재학생 수", "재학생은 8,123명이다[2]."])
    prompts = []

    class _Comp:
        def create(self, **kw):
            prompts.append(kw["messages"][-1]["content"]); return _Resp(next(answers))

    class C:
        model = "writer"; _extra = {}
        client = type("K", (), {"chat": type("Ch", (), {"completions": _Comp()})()})()
    res = web_search.answer_with_web("학교 재학생 수", client=C(), http=http)
    assert queries == ["학교 재학생 수", "대학알리미 재학생 수"]
    assert res["answer"].startswith("재학생은 8,123명") and [s["n"] for s in res["sources"]] == [1, 2]
    assert "[검색 요약] 대학알리미 에서 학생 수 확인" in prompts[0]
