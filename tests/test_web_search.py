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


def test_search_fetch_and_answer_with_sources():
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
