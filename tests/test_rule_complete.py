"""규정 답 완결성(11-10 대화 183: 「정년 만 65세」 만 남고 「학기의 말일」 이 빠짐)과 출처 링크(날짜 꼬리 이름·조항 링크)."""
from __future__ import annotations

from zzaimy.app import citations, rule_complete as rc
from zzaimy.app.responder import EV_BUDGET, pick_evidence

ART = "제 5 조(교원정년) 교원의 정년은 만 65세로 하고, 정년으로 인한 퇴직의 시기는 정년이 되는 날이 속하는 학기의 말일로 한다."
CTX = ("[이미 찾은 근거]\n《영남이공대학교 교원인사 규정 · 2023년05월25일》 · 제5조(교원정년)\n" + ART
       + "\n\n《영남이공대학교 교직원 명예퇴직수당 지급 규정》 · 제3조(지급대상)\n제3조(지급대상) 20년 이상 근속한 교원으로서 정년퇴직일 전에 자진하여 퇴직하는 자.")


def test_missing_time_clause_of_cited_article_is_found():
    short = "교원의 정년은 만 65세입니다(《영남이공대학교 교원인사 규정》 제5조). 1970년 7월생이면 2035년 7월이 정년입니다."
    gaps = rc.missing(short, CTX)
    assert [g["key"] for g in gaps] == ["제5조"] and "말일" in gaps[0]["terms"]
    full = ("정년퇴직일은 2035년 8월 31일입니다. 《영남이공대학교 교원인사 규정》 제5조에 따라 정년은 만 65세이고, "
            "퇴직 시기는 정년이 되는 날이 속하는 학기의 말일입니다.")
    assert rc.missing(full, CTX) == []
    assert rc.missing("명예퇴직은 20년 이상 근속해야 합니다.", CTX) == []          # 조항을 짚지 않은 답은 따지지 않는다


def test_proviso_numbers_are_needed_terms():
    t = "제10조(휴직기간) 휴직기간은 1년 이내로 한다. 다만, 육아휴직은 3년 이내로 한다."
    assert "3년" in rc.needed_terms(t)
    assert rc.needed_terms("제2조(목적) 이 규정은 교원의 복무를 정한다.") == []


def test_supplement_prompt_and_quote_carry_article_text():
    gaps = rc.missing("정년은 만 65세입니다(《교원인사 규정》 제5조).", CTX)
    assert "학기의 말일" in rc.supplement_prompt(gaps) and "말일" in rc.quote_lines(gaps)


def test_evidence_is_not_cut_at_eight_and_rule_articles_kept_whole():
    """에이전트가 셋째 검색에서 찾은 조항도 건넨다 — 예전에는 앞 8개만 건네 「교원 정년」 결과가 빠졌다."""
    ev = [{"title": f"규정{i}", "text": "가" * 300} for i in range(8)] + [{"title": "교원인사 규정", "text": ART + "나" * 1500, "rule": True}]
    got = pick_evidence(ev)
    assert got[-1]["title"] == "교원인사 규정"
    big = [{"title": f"r{i}", "text": "가" * 1200} for i in range(40)]
    assert sum(min(len(e["text"]), 1200) for e in pick_evidence(big)) <= EV_BUDGET


def test_cite_links_match_title_without_date_tail_and_link_article():
    src = [{"doc_id": 45916, "title": "영남이공대학교 교원인사 규정 · 2023년05월25일", "heading": "제5조(교원정년)"},
           {"doc_id": 7, "title": "2-1 영남이공대학교 학칙(2026.02.27.)", "heading": ""}]
    html = citations.linkify("<p>《영남이공대학교 교원인사 규정》 제5조와 《2-1 영남이공대학교 학칙》 제8조를 보면</p>", src)
    assert 'href="/doc/45916#q=' in html and 'href="/doc/45916#q=%EC%A0%9C5%EC%A1%B0"' in html
    assert 'href="/doc/7#q=%EC%A0%9C8%EC%A1%B0"' in html
    assert html.count("<a ") == 4 and "<a class=\"cite\" href=\"/doc/7" in html
