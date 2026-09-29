"""외부 검색 모드 — 문서 작업이 아닌 일반 질문(장비 사양 비교 같은 것)을 27B 가 웹 검색 결과로 답한다(사용자 지시 2026-09-29).

원칙:
- 담당자가 그 메시지에 '외부 검색' 을 켰을 때만 쓴다. 밖으로 나가는 것은 질문 글뿐이다 — 문서·첨부·교내 자료는 보내지 않는다.
- 검색은 키 없는 DuckDuckGo HTML 결과를 쓰고, 상위 쪽 몇 개를 받아 글만 남긴다. 답은 교내 27B 가 그 자료만 근거로 만들고
  문장마다 [n] 출처 번호를 단다. 자료에 없는 것은 모른다고 한다(브리프 규칙 1: 수치는 인출, 생성하지 않는다).
- 모델은 계획한 4종 그대로(Writer 27B). 외부 모델 참조(egress)와는 다른 길이다.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, unquote, urlparse

SEARCH_URL = "https://html.duckduckgo.com/html/"
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ZZAIMY/1.0"}
_RESULT = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET = re.compile(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', re.S)
_TAG = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def _real_url(href: str) -> str:
    """DuckDuckGo 는 결과를 /l/?uddg=<url> 로 감싼다."""
    if "uddg=" in href:
        q = parse_qs(urlparse(href).query)
        if q.get("uddg"):
            return unquote(q["uddg"][0])
    return href if href.startswith("http") else "https:" + href if href.startswith("//") else href


def search(query: str, k: int = 5, http=None) -> list[dict]:
    """[{title, url, snippet}] — 결과가 없거나 막히면 빈 목록."""
    import httpx

    http = http or httpx.Client(timeout=20, headers=_UA, follow_redirects=True)
    r = http.post(SEARCH_URL, data={"q": query, "kl": "kr-kr"}, headers=_UA)
    if r.status_code != 200:
        return []
    links = _RESULT.findall(r.text)
    snips = [_text(s) for s in _SNIPPET.findall(r.text)]
    out = []
    for i, (href, title) in enumerate(links[:k]):
        url = _real_url(html.unescape(href))
        if not url.startswith("http"):
            continue
        out.append({"title": _text(title)[:120], "url": url, "snippet": snips[i][:300] if i < len(snips) else ""})
    return out


def fetch_text(url: str, max_chars: int = 4000, http=None) -> str:
    """쪽의 글만(스크립트·스타일·태그 제거). 못 받으면 빈 글."""
    import httpx

    http = http or httpx.Client(timeout=20, headers=_UA, follow_redirects=True)
    try:
        r = http.get(url, headers=_UA)
    except Exception:
        return ""
    if r.status_code != 200 or "text/html" not in (r.headers.get("content-type") or "text/html"):
        return ""
    body = re.sub(r"(?is)<(script|style|nav|footer|header|noscript)[^>]*>.*?</\1>", " ", r.text)
    body = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", body)
    text = html.unescape(_TAG.sub(" ", body))
    lines = [" ".join(ln.split()) for ln in text.split("\n")]
    text = "\n".join(ln for ln in lines if len(ln) > 20)
    return text[:max_chars]


_PROMPT = """당신은 대학 행정 담당자를 돕는 에이전트다. 아래 [웹 자료]만 근거로 질문에 답한다.
- 사실·수치는 자료에 있는 것만 쓰고, 문장 끝에 근거 번호를 [n] 로 단다. 자료에 없으면 "자료에서 확인하지 못했다"고 쓴다.
- 자료끼리 다르면 둘 다 적고 어느 쪽이 최신인지 표시한다. 광고·추측은 뺀다.
- 한국어로, 담백한 서술형으로. 마지막에 "출처" 줄은 쓰지 않는다(화면이 붙인다).

[질문]
{question}

[웹 자료]
{sources}
"""


def answer_with_web(question: str, client=None, k: int = 4, http=None, max_tokens: int = 1500) -> dict:
    """웹 검색 → 상위 쪽 글 → 27B 답. 돌려주는 것: {answer, sources:[{n,title,url}], searched: n}."""
    results = search(question, k=k, http=http)
    sources = []
    for i, s in enumerate(results, 1):
        body = fetch_text(s["url"], http=http) or s.get("snippet", "")
        if body:
            sources.append({"n": i, "title": s["title"], "url": s["url"], "text": body})
    if not sources:
        return {"answer": "", "sources": [], "searched": len(results)}
    if client is None:
        from zzaimy.generate.client import VllmClient

        client = VllmClient()
    packed = "\n\n".join(f"[{s['n']}] {s['title']} ({s['url']})\n{s['text'][:3500]}" for s in sources)
    prompt = _PROMPT.format(question=question.strip(), sources=packed)
    resp = client.client.chat.completions.create(
        model=client.model, messages=[{"role": "user", "content": prompt}], temperature=0.2, max_tokens=max_tokens,
        extra_body=getattr(client, "_extra", None) or {})
    answer = (resp.choices[0].message.content or "").strip()
    return {"answer": answer, "sources": [{"n": s["n"], "title": s["title"], "url": s["url"]} for s in sources], "searched": len(results)}


def render(result: dict) -> str:
    """채팅에 남길 글 — 답 + 출처 목록."""
    if not result.get("answer"):
        return "외부 검색에서 쓸 만한 자료를 찾지 못했습니다. 질문을 더 구체적으로 적어 주세요."
    lines = [result["answer"], "", "출처(외부 검색):"]
    lines += [f"[{s['n']}] {s['title']} — {s['url']}" for s in result["sources"]]
    return "\n".join(lines)
