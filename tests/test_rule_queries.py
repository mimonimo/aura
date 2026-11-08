"""규정 검색어 만들기·합치기(11-06) — 묶음 질문에 조항 하나만 남지 않게, 셈한 결과는 셈이 맞을 때만 근거 있는 값."""

from types import SimpleNamespace

from zzaimy.app import responder
from zzaimy.verify.numbers import flag_unsupported


def test_rule_queries_from_model(monkeypatch):
    monkeypatch.setenv("ZZAIMY_RULE_QUERIES", "1")
    seen = {}

    class Fake:
        model = "m"
        _extra = {}

        def __init__(self, role=None):
            self.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self.create)))

        def create(self, **kw):
            seen["msgs"] = kw["messages"]
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content="- 교원 정년\n2. 명예퇴직수당 산정표\n1970년 7월생 500만원\n교원 정년"))])
    import zzaimy.generate.client as gc
    monkeypatch.setattr(gc, "VllmClient", Fake)
    qs = responder.rule_queries("명예퇴직금은 얼마야", [{"role": "user", "content": "나는 교수야"}])
    assert qs == ["교원 정년", "명예퇴직수당 산정표"]             # 개인 값 줄은 빼고, 같은 말은 한 번
    assert "나는 교수야" in seen["msgs"][-1]["content"]


def test_merge_rule_hits_interleaves_and_drops_weak():
    a = [{"doc_id": 1, "heading": "제3조", "content": "명예퇴직 자격", "weak_evidence": True}]
    b = [[{"doc_id": 2, "heading": "제5조", "content": "교원 정년 65세"}], [{"doc_id": 1, "heading": "별표", "content": "산정표"}]]
    out = responder.merge_rule_hits(a, b)
    assert [h["heading"] for h in out] == ["제5조", "별표"]


def test_merge_prefers_strong_copy_of_same_article():
    first = [{"doc_id": 9, "heading": "정관", "content": "명퇴", "weak_evidence": True}]
    more = [[{"doc_id": 5, "heading": "제5조", "content": "교원 정년 만 65세", "weak_evidence": True}],
            [{"doc_id": 5, "heading": "제5조", "content": "교원 정년 만 65세"}]]
    assert [h["heading"] for h in responder.merge_rule_hits(first, more)] == ["제5조"]


def test_correct_calculation_result_is_supported():
    ev = ["월보수액 × 75% × 잔여월수. 월급은 500만원이라고 가정"]
    text, flagged = flag_unsupported("500만 원 × 75% × 12개월 = 4,500만 원", ev)
    assert flagged == [] or flagged == ["12"]
    assert "4,500만 원" in text and "4,500만(근거" not in text
    text, flagged = flag_unsupported("500만 원 × 75% × 12개월 = 5,000만 원", ev)
    assert "5,000만" in flagged                                   # 틀린 셈은 표시
    text, flagged = flag_unsupported("800만 원 × 75% × 12개월 = 7,200만 원", ev)
    assert "800만" in flagged                                      # 근거 없는 항은 셈이 맞아도 표시
    assert flag_unsupported("2034년 3월 ~ 2035년 3월 = 12개월", ev)[1] == []


def test_calculation_check_is_bounded():
    import time
    t = time.time()
    flag_unsupported(("1" * 3000 + " × ") * 3 + "= 5", ["1"])
    flag_unsupported("1만 " * 300 + "× 2 = 3", ["1"])
    assert time.time() - t < 2


def test_wrong_date_addition_is_flagged():
    t, f = flag_unsupported("2014년 3월 + 20년 = 2015년 3월이므로", ["20년 이상"])
    assert "2015년 3월(근거 확인 필요)" in t and f == ["2015년 3월"]
    assert flag_unsupported("2014년 3월 + 20년 = 2034년 3월, 1970년 7월 + 65세 = 2035년 7월", ["20년 65세"])[1] == []


def test_wrong_calculation_is_recomputed():
    from zzaimy.verify.numbers import fix_calculations
    t = "명예퇴직수당은 6,750만 원입니다.\n* 500만 원 × 75% × 12개월 = 6,750만 원."
    out, fixed = fix_calculations(t)
    assert "6,750만" not in out and out.count("4,500만 원") == 2 and fixed == ["6,750만 → 4,500만"]
    out, fixed = fix_calculations("800만 원 × 75% × 18개월 = 1억 원")
    assert "1억 800만 원" in out
    assert fix_calculations("500만 원 × 75% × 12개월 = 4,500만 원")[1] == []


def test_critique_text_is_not_written_as_body():
    from zzaimy.app.webhwp_agent import check_body
    leak = ("2) 사업 목표 가)에서 '기초 AI 리터러시'라는 추상적 표현만 있고, [근거 7]에 명시된 항목이 누락됨.\n"
            "- (1) 구체성 — [본문] 3) 주요 추진 과제 가)에서 방법만 있고 대상이 누락됨.\n[본문]")
    assert check_body(leak, None, 262144) and "점검" in check_body(leak, None, 262144)
    ok = "1) 1학년 대상 AI 기초 교과 운영\n가) 신입생 전체 대상 정규 교과로 운영함\n나) 자료에 없는 학점은 확인 필요"
    assert check_body(ok, None, 262144) is None


def test_reused_computed_amount_is_supported():
    ev = ["월보수 × 75% × 잔여월수, 월급은 500만 원이라고 가정, 60월"]
    t = "500만 원 × 75% × 60 = 2억 2,500만 원\n합계: 2억 2,500만 원 + 2,250만 원 = 2억 4,750만 원"
    _, f = flag_unsupported(t, ev)
    assert "2억" not in f and "2,500만" not in f


def test_date_lines_are_not_recomputed_as_amounts():
    from zzaimy.verify.numbers import fix_calculations
    t = "2014년 3월 + 20년 = 2034년 3월\n1970년 7월 + 65년 = 2035년 7월\n2034년 3월 ~ 2035년 8월 = 18개월\n500만 원 × 75% × 18개월 = 6,750만 원"
    out, fixed = fix_calculations(t)
    assert out == t and fixed == []
    _, f = flag_unsupported(t, ["월급은 500만 원이라고 가정, 월보수액×75%×잔여월수, 20년 이상, 만 65세"])
    assert "500만" not in f and "6,750만" not in f
