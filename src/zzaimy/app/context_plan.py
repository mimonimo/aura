"""절을 쓰기 전에 Writer(27B)가 무엇이 필요한지 생각하고 어디서 찾을지 정한다 — 컨텍스트 구성 리즈닝(교수님 방향, 2026-10-01).

코드가 정한 검색어·개수로 재료를 모으던 것을 바꾼다(10/11: 과거 문서에 학과·교원 현황이 있는데 고정 검색으로 못 찾아 「확인 필요」투성이).
역할 나눔:
- Writer: 절의 작성 지침·담당자 요구·쓸 수 있는 자료 목록을 보고 필요한 정보와 찾을 곳·검색어를 정한다(계획).
- 코드: 그 계획대로 도구를 실행한다 — 프로젝트 문서 검색, 문서함(사업 문서, 그래프로 좁힌 검색), 공고·지침 기준. 열람 범위는 검색 단계에서
  그대로 적용된다(절대 규칙 4). 찾은 근거를 「무엇을 찾으려 했고 무엇이 나왔나」로 묶어 절 쓰기 재료로 넘긴다.
- 기록: 계획·검색·결과를 context_plan_episodes.jsonl 에 남긴다 — 리즈닝 학습 재료(데이터셋 생성은 아스트라).

특정 사업·문서 이름을 코드에 두지 않는다. 판단은 Writer 가, 코드는 도구와 안전장치만.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

MAX_NEEDS = 6
PER_NEED = 3
MAX_SNIPPETS = 14
SNIPPET_CHARS = 600

PROMPT = """당신은 행정 문서 작성 에이전트다. 아래 절을 쓰기 전에, 이 절을 쓰는 데 필요한 정보가 무엇인지 생각하고 어디서 찾을지 정한다.

[문서] {doc_title}
[쓸 절] {heading}
[이 절의 작성 지침·현재 글]
{guide}

[담당자 요구·대화에서 밝힌 방향]
{intent}

[찾을 수 있는 곳]
- project: 이 프로젝트에 올린 문서 — {project_docs}
- criteria: 공고·기본계획·지침(평가 기준) — {criteria_docs}
- library: 문서함의 과거 사업 문서 전체(지난 계획서·실적보고서·평가 결과, 사업·연차별로 좁혀 찾는다). 우리 기관의 현황·실적·조직·인프라는 주로 여기 있다

할 일:
1. 이 절이 요구하는 항목을 짚고, 각 항목을 쓰려면 어떤 사실·수치·사례가 필요한지 생각한다.
2. 필요한 정보마다 찾을 곳(project·criteria·library)과 검색어(명사 위주 2~6낱말)를 정한다. 최대 {max_needs}개.
3. 자료로 찾을 수 없고 담당자만 아는 값(내부 결정·계획 수치)은 ask 로 둔다.

JSON 하나만 답한다:
{{"thinking": "이 절이 요구하는 것과 필요한 근거(두세 문장)",
  "needs": [{{"what": "찾을 정보", "source": "project|criteria|library", "query": "검색어"}}],
  "ask": [{{"name": "담당자에게 물을 값", "why": "자료로 찾기 어려운 까닭"}}]}}"""


def _doc_list(docs: list[dict], title_of, limit: int = 12) -> str:
    names = [title_of(d.get("filename") or "") for d in docs if d]
    names = [n for n in names if n][:limit]
    return ", ".join(f"「{n[:40]}」" for n in names) or "(없음)"


def plan(client, *, doc_title: str, heading: str, guide: str, intent: str, project_docs: str, criteria_docs: str) -> dict:
    """Writer 의 찾기 계획 — {thinking, needs:[{what,source,query}], ask:[{name,why}]}. 실패하면 needs 가 빈 계획."""
    empty = {"thinking": "", "needs": [], "ask": [], "ok": False}
    if client is None:
        return empty
    prompt = PROMPT.format(doc_title=doc_title[:80], heading=heading[:80], guide=(guide or "(없음)")[:2500], intent=(intent or "(없음)")[:1500],
                           project_docs=project_docs, criteria_docs=criteria_docs, max_needs=MAX_NEEDS)
    try:
        resp = client.client.chat.completions.create(
            model=client.model, temperature=0.0, max_tokens=900, extra_body=getattr(client, "_extra", {}),
            messages=[{"role": "user", "content": prompt}])
        raw = resp.choices[0].message.content or ""
        m = re.search(r"\{.*\}", raw, re.S)
        got = json.loads(m.group(0)) if m else {}
    except Exception:
        return empty
    needs = []
    for n in got.get("needs") or []:
        if not isinstance(n, dict):
            continue
        src = str(n.get("source") or "").strip().lower()
        query = re.sub(r"\s+", " ", str(n.get("query") or "")).strip()
        if src in ("project", "criteria", "library") and len(query) >= 2:
            needs.append({"what": str(n.get("what") or query)[:80], "source": src, "query": query[:80]})
    asks = [{"name": str(a.get("name"))[:60], "why": str(a.get("why") or "")[:120]}
            for a in (got.get("ask") or []) if isinstance(a, dict) and a.get("name")]
    return {"thinking": str(got.get("thinking") or "")[:600], "needs": needs[:MAX_NEEDS], "ask": asks[:6], "ok": bool(needs)}


def execute(plan_: dict, *, search_project, search_criteria, search_library) -> list[dict]:
    """계획대로 찾는다 — 도구는 호출부가 넘긴다(권한·범위가 들어 있는 함수). [{what, source, query, hits:[{title, doc_id, chunk_id, text}]}]"""
    tools = {"project": search_project, "criteria": search_criteria, "library": search_library}
    seen: set = set()
    total = 0
    out = []
    # 담당자에게 물으려던 값도 묻기 전에 과거 문서에서 먼저 찾아본다 — 학과 교원 수·재학생 수 같은 현황은 지난 계획서·보고서에 있는 경우가 많다(10/11)
    asks = [{"what": a["name"], "source": "library", "query": a["name"][:60], "from_ask": True} for a in plan_.get("ask") or []]
    for n in (plan_.get("needs") or []) + asks:
        hits = []
        try:
            raw = tools[n["source"]](n["query"]) or []
        except Exception:
            raw = []
        for h in raw:
            key = h.get("chunk_id") or (h.get("doc_id"), (h.get("text") or "")[:60])
            if key in seen or total >= MAX_SNIPPETS:
                continue
            seen.add(key)
            hits.append(dict(h, text=(h.get("text") or "")[:SNIPPET_CHARS]))
            total += 1
            if len(hits) >= PER_NEED:
                break
        out.append(dict(n, hits=hits))
    return out


def render(plan_: dict, found: list[dict]) -> str:
    """절 쓰기 재료 — 무엇을 찾으려 했고 무엇이 나왔나. 못 찾은 것도 그대로 적는다(모델이 지어내지 않게)."""
    if not found:
        return ""
    lines = ["[에이전트가 이 절을 위해 찾은 근거 — 출처는 《 》 안의 문서 제목만 쓴다. 못 찾은 정보는 지어내지 말고 비워 두고 묻는다]"]
    if plan_.get("thinking"):
        lines.append(f"(계획) {plan_['thinking']}")
    where = {"project": "프로젝트 문서", "criteria": "공고·지침", "library": "문서함 과거 사업 문서"}
    for n in found:
        if n.get("from_ask") and not n["hits"]:
            continue                                     # 못 찾은 물을 값은 아래 한 줄로
        lines.append(f"◇ {n['what']} — {where.get(n['source'], n['source'])}, 검색 「{n['query']}」"
                     + (" (담당자에게 묻기 전에 찾아봄 — 찾았으면 이 값을 출처와 함께 쓰고 묻지 않는다)" if n.get("from_ask") else ""))
        if not n["hits"]:
            lines.append("  (찾지 못함)")
        for h in n["hits"]:
            note = f" · {h['note']}" if h.get("note") else ""
            lines.append(f"  《{h.get('title') or '문서'}》{note}\n  {h['text']}")
    left = [n["what"] for n in found if n.get("from_ask") and not n["hits"]]
    if left:
        lines.append("[과거 문서에서도 찾지 못한 값 — 비우고 묻는다] " + ", ".join(left))
    return "\n".join(lines)


def record(data_dir: Path, rec: dict) -> None:
    p = Path(data_dir) / "context_plan_episodes.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **rec}, ensure_ascii=False) + "\n")
