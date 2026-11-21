"""실적 모으기·집계(app/fact_ledger) — 인용 검증, 저장 재사용, 달·분야별 집계, 집계 합계의 수치 관문 통과."""
from __future__ import annotations

import json
from types import SimpleNamespace

from zzaimy.app import fact_ledger as fl
from zzaimy.verify.numbers import mask_unsupported


class FakeDB:
    def __init__(self, docs):
        self.settings = {}
        self.docs = {d["id"]: d for d in docs}

    def get_setting(self, k, default=""):
        return self.settings.get(k, default)

    def set_setting(self, k, v):
        self.settings[k] = v

    def get_document(self, i):
        return self.docs.get(i)


class FakeClient:
    """문서 제목마다 정해 둔 기록 JSON 을 돌려준다(서버 없음 → 문맥 한도 기본값)."""

    def __init__(self, by_title):
        self.by_title, self.calls, self.model, self._extra = by_title, [], "fake", {}
        outer = self

        class _C:
            def create(self, model, messages, max_tokens, **kw):
                user = messages[-1]["content"]
                title = user.split("《", 1)[1].split("》", 1)[0]
                outer.calls.append(title)
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"records": outer.by_title.get(title, [])}, ensure_ascii=False)))])

        class _M:
            def list(self):
                raise RuntimeError("서버 없음")

        self.client = SimpleNamespace(chat=SimpleNamespace(completions=_C()), models=_M(), base_url="")


DOCS = [
    {"id": 1, "filename": "4월 캠프.docx", "masked_text": "일시 2026. 4. 9.\n분야 학생 역량강화\n파이썬 캠프를 운영하였다. 참여 인원은 학생 42명이다."},
    {"id": 2, "filename": "4월 특강.docx", "masked_text": "일시 2026. 4. 28.\n분야 학생 역량강화\nAI 특강 참여 인원은 학생 55명이다."},
    {"id": 3, "filename": "5월 세미나.docx", "masked_text": "일시 2026. 5. 25.\n분야 산학협력\n세미나 참여 인원은 학생 61명이다."},
    {"id": 4, "filename": "회의록.docx", "masked_text": "안건: 분기 점검"},
]
ANS = {
    "4월 캠프": [{"날짜": "2026. 4. 9.", "분야": "학생 역량강화", "참여 인원": "42명", "quote": "참여 인원은 학생 42명이다."},
               {"날짜": "2026. 4. 9.", "분야": "학생 역량강화", "참여 인원": "99명", "quote": "참여 인원은 학생 99명이다."}],   # 지어낸 인용
    "4월 특강": [{"날짜": "2026. 4. 28.", "분야": "학생 역량강화", "참여 인원": "55명", "quote": "AI 특강 참여 인원은 학생 55명이다."}],
    "5월 세미나": [{"날짜": "2026. 5. 25.", "분야": "산학협력", "참여 인원": "61명", "quote": "세미나 참여 인원은 학생 61명이다."}],
}
ITEMS = ["날짜", "분야", "참여 인원"]


def _title(d):
    return d["filename"].rsplit(".", 1)[0]


def test_validate_drops_quotes_not_in_document_and_values_not_in_quote():
    recs = [{"참여 인원": "42명", "quote": "참여 인원은 학생 42명이다."},
            {"참여 인원": "43명", "quote": "참여 인원은 학생 42명이다."},          # 문서에 없는 값
            {"참여 인원": "42명", "quote": "없는 글"}]
    kept, dropped = fl.validate(recs, ["참여 인원"], DOCS[0]["masked_text"])
    assert len(kept) == 1 and dropped == 2


def test_collect_reads_every_document_and_reuses_saved_results():
    db, cli = FakeDB(DOCS), FakeClient(ANS)
    obs = fl.collect(db, 7, DOCS, ITEMS, cli, title_of=_title, parallel=1)
    assert "문서 4/4개" in obs and "기록 3건" in obs and "《회의록》" in obs
    assert sorted(cli.calls) == ["4월 캠프", "4월 특강", "5월 세미나", "회의록"]
    cli.calls.clear()
    fl.collect(db, 7, DOCS, ITEMS, cli, title_of=_title, parallel=1)
    assert cli.calls == []                                      # 같은 문서·항목은 다시 부르지 않는다
    changed = [dict(DOCS[0], masked_text=DOCS[0]["masked_text"] + " 추가")] + DOCS[1:]
    fl.collect(db, 7, changed, ITEMS, cli, title_of=_title, parallel=1)
    assert cli.calls == ["4월 캠프"]                             # 본문이 바뀐 문서만 다시


def test_aggregate_by_month_and_field_with_derivations():
    db = FakeDB(DOCS)
    fl.collect(db, 7, DOCS, ITEMS, FakeClient(ANS), title_of=_title, parallel=1)
    obs, ev = fl.aggregate(db, 7, ["날짜:월", "분야"], ["참여 인원"])
    assert "2026-04 / 학생 역량강화: 2건, 참여 인원 97" in obs
    assert "2026-05 / 산학협력: 1건, 참여 인원 61" in obs
    assert "합계 · 참여 인원: 158" in obs
    assert any("42+55=97" in e for e in ev) and any("97+61=158" in e for e in ev)
    obs2, _ = fl.aggregate(db, 7, ["분야"], ["참여 인원"], filt={"분야": "산학"})
    assert "산학협력: 1건, 참여 인원 61" in obs2 and "학생 역량강화" not in obs2


def test_aggregated_totals_pass_the_number_gate():
    db = FakeDB(DOCS)
    fl.collect(db, 7, DOCS, ITEMS, FakeClient(ANS), title_of=_title, parallel=1)
    _, ev = fl.aggregate(db, 7, ["날짜:월"], ["참여 인원"])
    draft = "4월 참여 인원은 97명, 5월은 61명으로 모두 158명이다."
    _, bad = mask_unsupported(draft, ev)
    assert not bad
    _, bad2 = mask_unsupported("모두 160명이다.", ev)
    assert bad2                                                  # 집계에 없는 합계는 여전히 막힌다
    _, bad3 = mask_unsupported(draft, [fl.evidence_text(db, 7)])  # 다음 요청 — 저장된 집계가 근거
    assert not bad3


def test_number_reads_korean_units():
    assert fl.number("3,400,000원") == 3400000
    assert fl.number("340만 원") == 3400000
    assert fl.number("1억 2,000만원") == 120000000
    assert fl.number("4.3점") == fl.number("4.3")
    assert fl.number("없음") is None
    assert fl.month_of("2026. 4. 9.") == "2026-04" and fl.month_of("2026-11-03") == "2026-11" and fl.month_of("7월 8일") == "7월"


def test_aggregate_rejects_unknown_items():
    db = FakeDB(DOCS)
    fl.collect(db, 7, DOCS, ITEMS, FakeClient(ANS), title_of=_title, parallel=1)
    obs, ev = fl.aggregate(db, 7, ["부서"], ["금액"])
    assert "모은 항목에 없는 이름" in obs and not ev


def test_validate_keeps_record_when_one_value_is_paraphrased():
    # 11-20 시험: 활동 「AI 경진대회 운영」(문서는 「AI 경진대회을(를) 운영하였다」) 한 값 때문에 참여 인원·실적까지 버렸다
    doc = "분야 | 학생 역량강화\n일시 | 2026. 10. 29.\n2026. 10. 29. AI 경진대회을(를) 운영하였다.\n참여 인원은 학생 33명이며, 만족도는 4.6점이었다."
    recs = [{"날짜": "2026. 10. 29.", "분야": "학생 역량강화", "활동": "AI 경진대회 운영", "참여 인원": "학생 33명",
             "quote": "2026. 10. 29. AI 경진대회을(를) 운영하였다. 참여 인원은 학생 33명이며"}]
    kept, dropped = fl.validate(recs, ["날짜", "분야", "활동", "참여 인원"], doc)
    assert dropped == 0 and kept[0]["참여 인원"] == "학생 33명" and kept[0]["활동"] == "" and kept[0]["unverified"] == ["활동"]
    # 인용에 든 값이 하나도 없으면 버린다
    kept2, dropped2 = fl.validate([{"활동": "없는 활동", "quote": "참여 인원은 학생 33명이며"}], ["활동"], doc)
    assert kept2 == [] and dropped2 == 1


def test_aggregate_distinct_share_base_and_nonempty_filter():
    db = FakeDB([])
    data = {"items": ["분야", "활동", "참여 인원", "금액"], "live": ["k1"], "docs": {"k1": {"doc_id": 1, "title": "운영 결과", "items": ["분야", "활동", "참여 인원", "금액"], "records": [
        {"분야": "산학협력", "활동": "세미나", "참여 인원": "61명", "금액": "", "quote": "q"},
        {"분야": "산학협력", "활동": "세미나", "참여 인원": "", "금액": "3,000,000", "quote": "q"},
        {"분야": "산학협력", "활동": "협약", "참여 인원": "", "금액": "1,000,000", "quote": "q"},
        {"분야": "학생 역량강화", "활동": "캠프", "참여 인원": "42명", "금액": "4,000,000", "quote": "q"}]}}}
    fl.save(db, 7, data)
    obs, ev = fl.aggregate(db, 7, ["분야"], ["금액"], distinct="활동", share=True, base="10000000")
    assert "활동 2개" in obs and "50.0%" in obs and "80.0%" in obs        # 산학협력 4,000,000/8,000,000, 합계/기준 8,000,000/10,000,000
    assert any("서로 다른 활동: 2개" in x for x in ev) and any("=80.0%" in x for x in ev)
    obs2, _ = fl.aggregate(db, 7, ["분야"], ["참여 인원"], filt={"참여 인원": "*"})
    assert "기록 2건" in obs2 and "참여 인원: 103" in obs2
    obs3, _ = fl.aggregate(db, 7, ["분야"], ["금액"], distinct=["활동"])          # 모델이 목록으로 줘도
    assert "활동 2개" in obs3 and "없는 이름" not in obs3


def test_quote_with_small_copy_error_or_ellipsis_kept():
    from zzaimy.app import fact_ledger as fl
    doc = ("사업명 | 가상 사업\n분야 | 학생 역량강화\n일시 | 2026. 9. 10.\n\n2026. 9. 10. 파이썬 캠프(2기)을(를) 운영하였다.\n\n"
           "참여 인원은 학생 47명이며, 만족도는 4.4점이었다.\n\n기간 | 2026. 10. 7. (1일)\n출장자 | 담당 교원 2명\n\n박람회 참관 목적으로 출장하였다.")
    items = ["날짜", "활동", "참여 인원"]
    recs = [{"날짜": "2026. 9. 10.", "활동": "파이썬 캠프(2기)", "참여 인원": "학생 47명",
             "quote": "2026. 9. 10. 파이썬 캠프(2기)를(를) 운영하였다. 참여 인원은 학생 47명이며, 만족도는 4.4점이었다."},
            {"날짜": "2026. 10. 7.", "활동": "박람회 참관", "참여 인원": "담당 교원 2명",
             "quote": "기간 | 2026. 10. 7. (1일) ... 출장자 | 담당 교원 2명 ... 박람회 참관 목적으로 출장하였다."},
            {"날짜": "2026. 9. 10.", "활동": "없는 활동", "참여 인원": "학생 47명",
             "quote": "전혀 다른 글이 여기에 길게 적혀 있다고 해 보자 학생 47명"}]
    kept, dropped = fl.validate(recs, items, doc)
    assert [k["참여 인원"] for k in kept] == ["학생 47명", "담당 교원 2명"] and dropped == 1
