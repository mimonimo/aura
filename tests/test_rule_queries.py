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
