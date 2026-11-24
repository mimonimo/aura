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
    assert "4월 / 학생 역량강화: 기록 2개, 참여 인원 97" in obs
    assert "5월 / 산학협력: 기록 1개, 참여 인원 61" in obs
    assert "합계 · 참여 인원: 158" in obs
    assert any("42+55=97" in e for e in ev) and any("97+61=158" in e for e in ev)
    obs2, _ = fl.aggregate(db, 7, ["분야"], ["참여 인원"], filt={"분야": "산학"})
    assert "산학협력: 기록 1개, 참여 인원 61" in obs2 and "학생 역량강화" not in obs2


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
    assert dropped == 0 and kept[0]["참여 인원"] == "학생 33명" and kept[0]["활동"] == "AI 경진대회" and "unverified" not in kept[0]   # 10차: 앞부분으로 줄여 살린다
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


def test_aggregate_skips_values_with_other_unit():
    db = FakeDB([])
    recs = [{"날짜": "2026. 7. 8.", "참여 인원": "학생 38명", "quote": "q"},
            {"날짜": "2026. 7. 22.", "참여 인원": "2개사", "quote": "q"},
            {"날짜": "2026. 7. 9.", "참여 인원": "41명", "quote": "q"}]
    fl.save(db, 8, {"items": ["날짜", "참여 인원"], "live": ["k"], "docs": {"k": {"doc_id": 1, "title": "t", "items": ["날짜", "참여 인원"], "records": recs}}})
    obs, ev = fl.aggregate(db, 8, ["날짜:월"], ["참여 인원"])
    assert "참여 인원 79명" in obs and "2개사" in obs and any("38+41=79명" in x for x in ev)


def test_multiword_item_value_needs_its_label_nearby():
    doc = ("참여 인원은 학생 61명이며, 만족도 조사 결과 4.4점이었다.\n이번 활동으로 성과지표 「산업체 협약」 실적 2건을(를) 거두었다.\n"
           "다른 활동: 성과지표 「교육 이수 인원」 실적 38명을 거두었다.")
    recs = [{"교육 이수 인원": "61명", "참여 인원": "학생 61명", "quote": "참여 인원은 학생 61명이며"},
            {"교육 이수 인원": "38명", "참여 인원": "", "quote": "성과지표 「교육 이수 인원」 실적 38명을 거두었다."}]
    kept, _ = fl.validate(recs, ["교육 이수 인원", "참여 인원"], doc)
    assert kept[0]["교육 이수 인원"] == "" and kept[0]["참여 인원"] == "학생 61명" and kept[0]["unverified"] == ["교육 이수 인원"]
    assert kept[1]["교육 이수 인원"] == "38명"


def test_distinct_counts_unnamed_records_by_document():
    db = FakeDB([])
    recs = [{"분야": "학생", "활동": "캠프", "quote": "q"}, {"분야": "학생", "활동": "", "quote": "q"}]
    data = {"items": ["분야", "활동"], "live": ["a", "b"], "docs": {
        "a": {"doc_id": 1, "title": "캠프 결과", "items": ["분야", "활동"], "records": recs},
        "b": {"doc_id": 2, "title": "특강 결과", "items": ["분야", "활동"], "records": [{"분야": "학생", "활동": "", "quote": "q"}]}}}
    fl.save(db, 9, data)
    obs, _ = fl.aggregate(db, 9, ["분야"], [], distinct="활동")
    assert "활동 2개" in obs                       # 캠프(이름) + 특강 결과(이름 없는 문서) — 캠프 문서의 빈 기록은 다시 세지 않는다


def test_value_outside_quote_joins_its_line_to_quote_for_number_gate():
    # 11-22 5차: 값은 본문에서 확인됐지만 인용이 다른 문장이라 표에 넣을 때 근거 없음으로 막히던 것
    doc = ("파이썬 기초 집중 캠프(1기) 결과 보고\n2026. 4. 9. 파이썬 기초 집중 캠프(1기)을(를) 운영하였다.\n"
           "참여 인원은 학생 42명이며, 만족도 조사 결과 5점 만점에 4.5점이었다.\n")
    recs = [{"날짜": "2026. 4. 9.", "참여 인원": "학생 42명", "quote": "2026. 4. 9. 파이썬 기초 집중 캠프(1기)을(를) 운영하였다."}]
    kept, dropped = fl.validate(recs, ["날짜", "참여 인원"], doc)
    assert dropped == 0 and "42명" in kept[0]["quote"]
    md = "| 월 | 주요 활동 | 참여 인원(명) |\n| --- | --- | --- |\n| 4월 | 파이썬 기초 집중 캠프(1기) | 학생 42명 |"
    _, bad = mask_unsupported(md, ["《캠프》 " + kept[0]["quote"]], context=True)
    assert "42명" not in bad and "42" not in bad


def test_later_partial_collect_keeps_earlier_items_and_documents():
    # 11-22 5차: 뒤 요청이 일부 문서·적은 항목으로 다시 모으자 「분야」 와 다른 문서 기록이 집계에서 사라졌다
    db, cli = FakeDB(DOCS), FakeClient(ANS)
    fl.collect(db, 7, DOCS, ITEMS, cli, title_of=_title, parallel=1)
    cli.calls.clear()
    fl.collect(db, 7, DOCS[:2], ["날짜", "참여 인원"], cli, title_of=_title, parallel=1)
    assert cli.calls == []                                      # 요청 항목을 모두 담아 읽은 결과가 있으면 다시 읽지 않는다
    obs, _ = fl.aggregate(db, 7, ["분야"], ["참여 인원"])
    assert "산학협력: 기록 1개, 참여 인원 61" in obs and "학생 역량강화: 기록 2개, 참여 인원 97" in obs
    fl.collect(db, 7, DOCS[2:3], ["날짜", "비목"], cli, title_of=_title, parallel=1)
    assert cli.calls == ["5월 세미나"]                           # 새 항목이면 그 문서만, 앞서 모은 항목까지 합쳐 다시
    items = fl.load(db, 7)["items"]
    assert "분야" in items and "비목" in items
    obs2, _ = fl.aggregate(db, 7, ["분야"], ["참여 인원"])
    assert "산학협력: 기록 1개, 참여 인원 61" in obs2


def test_same_value_from_same_line_summed_once_and_filter_drop_noted():
    # 11-22 6차: 지표마다 나뉜 기록이 같은 참여 인원을 되풀이해 7월 38→76. filter {활동: *} 로 캠프 42명이 빠짐
    docs = [{"id": 1, "filename": "부트캠프.docx", "masked_text": "하계 부트캠프 결과 보고\n일시 2026. 7. 8.\n참여 인원은 학생 38명이며 만족도 4.7점이었다.\n성과지표 「교육 이수 인원」 실적 38명.\n성과지표 「취업 연계」 실적 6명."},
            {"id": 2, "filename": "캠프.docx", "masked_text": "일시 2026. 7. 20.\n참여 인원은 학생 42명이다."}]
    ans = {"부트캠프": [{"날짜": "2026. 7. 8.", "활동": "부트캠프", "참여 인원": "학생 38명", "실적": "38명", "quote": "성과지표 「교육 이수 인원」 실적 38명."},
                    {"날짜": "2026. 7. 8.", "활동": "부트캠프", "참여 인원": "학생 38명", "실적": "6명", "quote": "성과지표 「취업 연계」 실적 6명."}],
           "캠프": [{"날짜": "2026. 7. 20.", "참여 인원": "학생 42명", "quote": "참여 인원은 학생 42명이다."}]}
    db = FakeDB(docs)
    fl.collect(db, 9, docs, ["날짜", "활동", "참여 인원", "실적"], FakeClient(ans), title_of=_title, parallel=1)
    obs, _ = fl.aggregate(db, 9, ["날짜:월"], ["참여 인원"])
    assert "참여 인원 80명" in obs and "한 번만 더했다" in obs
    obs2, _ = fl.aggregate(db, 9, ["날짜:월"], ["참여 인원"], filt={"활동": "*"})
    assert "참여 인원 38명" in obs2 and "filter 로 뺀 기록" in obs2 and "42명" in obs2


def test_dominant_unit_is_per_group():
    # 11-22 6차: 성과지표로 묶으면 지표마다 단위가 다른데 전체 주된 단위(명)로 걸러 협약 4건·교과목 2개가 0
    docs = [{"id": i, "filename": f"d{i}.docx", "masked_text": t} for i, t in enumerate(
        ["교육 이수 인원 실적 42명", "교육 이수 인원 실적 47명", "교육 이수 인원 실적 38명", "산업체 협약 실적 4건", "교과목 개발 실적 2개"], 1)]
    ans = {f"d{i}": [{"성과지표": k, "실적": v, "quote": f"{k} 실적 {v}"}] for i, (k, v) in enumerate(
        [("교육 이수 인원", "42명"), ("교육 이수 인원", "47명"), ("교육 이수 인원", "38명"), ("산업체 협약", "4건"), ("교과목 개발", "2개")], 1)}
    db = FakeDB(docs)
    fl.collect(db, 11, docs, ["성과지표", "실적"], FakeClient(ans), title_of=_title, parallel=1)
    obs, _ = fl.aggregate(db, 11, ["성과지표"], ["실적"])
    assert "산업체 협약: 기록 1개, 실적 4건" in obs and "교과목 개발: 기록 1개, 실적 2개" in obs and "실적 127명" in obs
    assert "전체 합계는 내지 않았다" in obs


def test_unnamed_report_not_counted_when_its_activity_is_named_elsewhere():
    # 11-22 6차: 결과 보고(활동 이름 못 뽑음)와 집행 내역 줄(같은 활동 이름)이 둘로 세어져 산학협력 6→8
    docs = [{"id": 1, "filename": "세미나 결과 보고.docx", "masked_text": "일시 2026. 6. 3.\n분야 산학협력\n참여 인원은 학생 61명이다."},
            {"id": 2, "filename": "집행 내역.xlsx", "masked_text": "2026-06-03 | 산학협력 | 세미나 | 3000000\n2026-07-01 | 산학협력 | 매칭 데이 | 2000000"}]
    ans = {"세미나 결과 보고": [{"분야": "산학협력", "참여 인원": "학생 61명", "quote": "참여 인원은 학생 61명이다."}],
           "집행 내역": [{"분야": "산학협력", "활동": "세미나", "금액": "3000000", "quote": "2026-06-03 | 산학협력 | 세미나 | 3000000"},
                      {"분야": "산학협력", "활동": "매칭 데이", "금액": "2000000", "quote": "2026-07-01 | 산학협력 | 매칭 데이 | 2000000"}]}
    db = FakeDB(docs)
    fl.collect(db, 12, docs, ["분야", "활동", "참여 인원", "금액"], FakeClient(ans), title_of=_title, parallel=1)
    obs, ev = fl.aggregate(db, 12, ["분야"], [], distinct=["활동"])
    assert "활동 2개" in obs and any("2개(=2건)" in e for e in ev)


def test_same_value_dedup_when_document_has_no_line_breaks():
    # 11-22 7차: 줄 없이 이어진 본문이라 인용이 여러 문장 — 기록마다 인용이 달라 7월 38→76 이 그대로였다
    t = ("2026. 7. 8. 하계 부트캠프을(를) 운영하였다. 참여 인원은 학생 38명이며, 만족도 4.7점이었다. "
         "이번 활동으로 성과지표 「교육 이수 인원」 실적 38명을(를) 거두었다. 이번 활동으로 성과지표 「취업 연계」 실적 6명을(를) 거두었다.")
    docs = [{"id": 1, "filename": "부트캠프.docx", "masked_text": t}]
    ans = {"부트캠프": [{"날짜": "2026. 7. 8.", "참여 인원": "학생 38명", "실적": "38명",
                     "quote": "2026. 7. 8. 하계 부트캠프을(를) 운영하였다. 참여 인원은 학생 38명이며, 만족도 4.7점이었다. 이번 활동으로 성과지표 「교육 이수 인원」 실적 38명을(를) 거두었다."},
                    {"참여 인원": "학생 38명", "실적": "6명", "quote": "이번 활동으로 성과지표 「취업 연계」 실적 6명을(를) 거두었다."}]}
    db = FakeDB(docs)
    fl.collect(db, 13, docs, ["날짜", "참여 인원", "실적"], FakeClient(ans), title_of=_title, parallel=1)
    obs, _ = fl.aggregate(db, 13, [], ["참여 인원"])
    assert "참여 인원 38명" in obs


def test_long_table_times_out_then_splits_with_header():
    """11-22 8차: 36행 집행 내역을 여러 항목으로 뽑다 시간 초과 → 문서 전체 「읽지 못함」. 긴 조각은 반으로 나눠(머리줄 함께) 다시 뽑는다."""
    rows = [f"2026-{3 + i // 4:02d}-1{i % 4} | 비목{i % 3} | 행사{i} | {1000000 + i * 10000}" for i in range(40)]
    text = "집행일 | 비목 | 적요 | 금액(원)\n" + "\n".join(rows)
    seen = []

    class APITimeoutError(Exception):
        pass

    class _C:
        def create(self, model, messages, max_tokens, **kw):
            part = messages[-1]["content"].split("<<<\n", 1)[1].rsplit("\n>>>", 1)[0]
            seen.append(part)
            if len(part) > 900:
                raise APITimeoutError("Request timed out.")
            recs = [{"집행일": ln.split(" | ")[0], "비목": ln.split(" | ")[1], "금액": ln.split(" | ")[3], "quote": ln}
                    for ln in part.splitlines()[1:]]
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
                                                            message=SimpleNamespace(content=json.dumps({"records": recs}, ensure_ascii=False)))])

    class _M:
        def list(self):
            raise RuntimeError("서버 없음")

    c = SimpleNamespace(model="fake", _extra={}, client=SimpleNamespace(chat=SimpleNamespace(completions=_C()), models=_M(), base_url="",
                                                                        with_options=lambda **k: None))
    c.client.with_options = lambda **k: c.client
    old = fl.MIN_SPLIT
    fl.MIN_SPLIT = 300
    try:
        out = fl._extract(c, "예산 집행 내역", text, ["집행일", "비목", "금액"], 100000)
    finally:
        fl.MIN_SPLIT = old
    assert len(out) == 40
    assert all(p.startswith("집행일 | 비목") for p in seen)          # 나눈 조각마다 머리줄
    assert len({r["금액"] for r in out}) == 40


def test_truncated_json_splits():
    """출력이 길이 한도로 잘린 답(finish_reason=length)도 나눠 다시 뽑는다."""
    text = "\n".join(f"행{i} | {i}명" for i in range(30))

    class _C:
        def create(self, model, messages, max_tokens, **kw):
            part = messages[-1]["content"].split("<<<\n", 1)[1].rsplit("\n>>>", 1)[0]
            lines = part.splitlines()
            if len(lines) > 10:
                return SimpleNamespace(choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content='{"records": [{"활동": "행0"'))])
            recs = [{"활동": ln.split(" | ")[0], "quote": ln} for ln in lines]
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps({"records": recs}, ensure_ascii=False)))])

    c = SimpleNamespace(model="fake", _extra={}, client=SimpleNamespace(chat=SimpleNamespace(completions=_C()), models=SimpleNamespace(list=lambda: (_ for _ in ()).throw(RuntimeError())), base_url=""))
    c.client.with_options = lambda **k: c.client
    old = fl.MIN_SPLIT
    fl.MIN_SPLIT = 10
    try:
        out = fl._extract(c, "목록", text, ["활동"], 100000)
    finally:
        fl.MIN_SPLIT = old
    assert sorted(r["활동"] for r in out) == sorted(f"행{i}" for i in range(30))


def test_month_label_shows_year_only_when_mixed():
    docs = [{"id": 1, "filename": "a.docx", "masked_text": "일시 2025. 12. 3.\n참여 인원은 학생 10명이다."},
            {"id": 2, "filename": "b.docx", "masked_text": "일시 2026. 1. 9.\n참여 인원은 학생 20명이다."}]
    ans = {"a": [{"날짜": "2025. 12. 3.", "참여 인원": "10명", "quote": "참여 인원은 학생 10명이다."}],
           "b": [{"날짜": "2026. 1. 9.", "참여 인원": "20명", "quote": "참여 인원은 학생 20명이다."}]}
    db = FakeDB(docs)
    fl.collect(db, 7, docs, ["날짜", "참여 인원"], FakeClient(ans), title_of=_title, parallel=1)
    obs, _ = fl.aggregate(db, 7, ["날짜:월"], ["참여 인원"])
    assert "2025년 12월:" in obs and "2026년 1월:" in obs and obs.index("2025년 12월") < obs.index("2026년 1월")


def test_inherit_single_doc_value_for_filter_and_group():
    """11-22 9차: 한 결과 보고가 활동 든 기록·인원 든 기록으로 나뉘면 filter {활동} 의 인원이 0 — 문서 안 값이 하나면 물려준다."""
    docs = [{"id": 1, "filename": "캠프.docx", "masked_text": "파이썬 캠프를 운영하였다.\n참여 인원은 학생 42명이다."},
            {"id": 2, "filename": "집행.xlsx", "masked_text": "a | 행사A | 100\nb | 행사B | 200"}]
    ans = {"캠프": [{"활동": "파이썬 캠프", "quote": "파이썬 캠프를 운영하였다."},
                  {"참여 인원": "42명", "quote": "참여 인원은 학생 42명이다."}],
           "집행": [{"활동": "행사A", "금액": "100", "quote": "a | 행사A | 100"},
                  {"활동": "행사B", "금액": "200", "quote": "b | 행사B | 200"}]}
    db = FakeDB(docs)
    fl.collect(db, 7, docs, ["활동", "참여 인원", "금액"], FakeClient(ans), title_of=_title, parallel=1)
    obs, _ = fl.aggregate(db, 7, ["활동"], ["참여 인원"], filt={"활동": "파이썬 캠프"})
    assert "파이썬 캠프: 기록 2개, 참여 인원 42명" in obs
    obs2, _ = fl.aggregate(db, 7, ["활동"], ["금액"])
    assert "행사A: 기록 1개, 금액 100" in obs2 and "행사B: 기록 1개, 금액 200" in obs2


def test_numeric_group_names_hint():
    docs = [{"id": 1, "filename": "a.docx", "masked_text": "실적 42명을 거두었다."},
            {"id": 2, "filename": "b.docx", "masked_text": "실적 3건을 거두었다."}]
    ans = {"a": [{"성과지표 실적": "42명", "quote": "실적 42명을 거두었다."}], "b": [{"성과지표 실적": "3건", "quote": "실적 3건을 거두었다."}]}
    db = FakeDB(docs)
    fl.collect(db, 7, docs, ["성과지표 실적"], FakeClient(ans), title_of=_title, parallel=1)
    obs, _ = fl.aggregate(db, 7, ["성과지표 실적"], [])
    assert "묶음 이름이 모두 수다" in obs


def test_value_with_extra_trailing_word_is_trimmed():
    """11-22 10차: 「캠프(1기) 운영」 처럼 문서에 없는 낱말을 덧붙인 활동 이름은 문서에 그대로 있는 앞부분으로 줄여 살린다."""
    text = "2026. 4. 9. 파이썬 기초 집중 캠프(1기)을(를) 운영하였다. 참여 인원은 학생 42명이다."
    kept, _ = fl.validate([{"활동": "파이썬 기초 집중 캠프(1기) 운영", "참여 인원": "학생 42명", "quote": text}], ["활동", "참여 인원"], text)
    assert kept[0]["활동"] == "파이썬 기초 집중 캠프(1기)" and "unverified" not in kept[0]
    kept, _ = fl.validate([{"활동": "전혀 다른 행사", "참여 인원": "학생 42명", "quote": text}], ["활동", "참여 인원"], text)
    assert kept[0]["활동"] == ""


def test_empty_answer_on_table_doc_retries_with_hint():
    """11-22 10차: 집행 내역 표에서 빈 답 → 저장돼 다시 읽지 않음. 표 꼴 문서의 빈 답은 표라고 알려 한 번 더 읽는다."""
    text = "집행일 | 비목 | 금액\n2026-03-18 | 인건비 | 4000000\n2026-04-09 | 장학금 | 4700000\n2026-05-14 | 인건비 | 5600000"
    calls = []

    class _C:
        def create(self, model, messages, max_tokens, **kw):
            u = messages[-1]["content"]
            calls.append("[알림]" in u)
            recs = [] if "[알림]" not in u else [{"비목": "인건비", "금액": "4000000", "quote": "2026-03-18 | 인건비 | 4000000"}]
            return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps({"records": recs}, ensure_ascii=False)))])

    c = SimpleNamespace(model="fake", _extra={}, client=SimpleNamespace(chat=SimpleNamespace(completions=_C()), models=SimpleNamespace(list=lambda: (_ for _ in ()).throw(RuntimeError())), base_url=""))
    c.client.with_options = lambda **k: c.client
    docs = [{"id": 9, "filename": "집행.xlsx", "masked_text": text}]
    db = FakeDB(docs)
    obs = fl.collect(db, 7, docs, ["비목", "금액", "참여 인원"], c, title_of=_title, parallel=1)
    assert calls == [False, True] and "기록 1건" in obs


def test_group_name_that_is_an_unsummed_item_gets_hint():
    """11-22 11차: 「산업체 협약」 묶음의 기록 수(3)를 협약 실적으로 씀 — 묶음 이름이 더하지 않은 항목이면 sum 에 넣으라고 알린다."""
    docs = [{"id": 1, "filename": "a.docx", "masked_text": "성과지표 「산업체 협약」 실적 4건을 거두었다."}]
    ans = {"a": [{"성과지표": "산업체 협약", "산업체 협약": "4건", "quote": "성과지표 「산업체 협약」 실적 4건을 거두었다."}]}
    db = FakeDB(docs)
    fl.collect(db, 7, docs, ["성과지표", "산업체 협약"], FakeClient(ans), title_of=_title, parallel=1)
    obs, _ = fl.aggregate(db, 7, ["성과지표"], [])
    assert "「산업체 협약」 은(는) 모은 항목이기도 하다" in obs
    obs2, _ = fl.aggregate(db, 7, ["성과지표"], ["산업체 협약"])
    assert "모은 항목이기도" not in obs2 and "산업체 협약 4건" in obs2


def test_for_table_groups_by_item_matching_row_labels():
    # 11-23 17차: 분야별 표 합계를 손으로 더해 막히자 「확인 필요」 로 채웠다 — 행 이름이 어느 항목 값인지 맞춰 묶어 보인다
    db = FakeDB([])
    its = ["분야", "활동", "참여 인원"]
    recs = [{"분야": "산학협력", "활동": "세미나", "참여 인원": "61명", "quote": "q1"},
            {"분야": "산학협력", "활동": "매칭 데이", "참여 인원": "52명", "quote": "q2"},
            {"분야": "학생 역량강화", "활동": "캠프", "참여 인원": "42명", "quote": "q3"},
            {"분야": "학생 역량강화", "활동": "특강", "참여 인원": "55명", "quote": "q4"}]
    fl.save(db, 11, {"items": its, "live": ["k"], "docs": {"k": {"doc_id": 1, "title": "t", "items": its, "records": recs}}})
    out = fl.for_table(db, 11, ["산학협력", "학생 역량강화", "합계"], ["활동 수", "참여 인원(명)"])
    assert "group_by: [분야]" in out and "sum: [참여 인원]" in out and "distinct: [활동]" in out
    assert "113명" in out and "97명" in out
    assert any("61+52=113명" in x for x in fl.load(db, 11)["aggregates"])
    # 행 이름이 어느 항목 값도 아니면 무엇을 모을지 알린다
    miss = fl.for_table(db, 11, ["교육 이수 인원(명)", "취업 연계(명)"], ["성과지표", "실적"])
    assert "collect_facts" in miss and "실적" in miss


def test_for_table_month_rows_group_by_date_month():
    db = FakeDB([])
    its = ["날짜", "참여 인원"]
    recs = [{"날짜": "2026. 4. 9.", "참여 인원": "42명", "quote": "q1"},
            {"날짜": "2026. 4. 23.", "참여 인원": "2명", "quote": "q2"},
            {"날짜": "2026. 5. 7.", "참여 인원": "55명", "quote": "q3"}]
    fl.save(db, 12, {"items": its, "live": ["k"], "docs": {"k": {"doc_id": 1, "title": "t", "items": its, "records": recs}}})
    out = fl.for_table(db, 12, ["4월", "5월"], ["참여 인원(명)"], "월")
    assert "group_by: [날짜:월]" in out and "44명" in out and "55명" in out


def test_for_table_dry_only_reports_unmatched_rows():
    db = FakeDB([])
    its = ["분야", "참여 인원"]
    recs = [{"분야": "산학협력", "참여 인원": "61명", "quote": "q"}, {"분야": "학생 역량강화", "참여 인원": "42명", "quote": "q"}]
    fl.save(db, 13, {"items": its, "live": ["k"], "docs": {"k": {"doc_id": 1, "title": "t", "items": its, "records": recs}}})
    assert fl.for_table(db, 13, ["산학협력", "학생 역량강화"], ["참여 인원"], "분야", dry=True) == ""
    assert "aggregates" not in fl.load(db, 13)
    assert "collect_facts {items: [성과지표, 실적]}" in fl.for_table(db, 13, ["교육 이수 인원(명)", "취업 연계(명)"], ["실적"], "성과지표", dry=True)


def test_aggregate_without_sum_adds_number_items():
    # 11-23 18차: group_by [성과] 만으로 불러 지표마다 빈 줄만 받았다 — 수 값 항목(실적)을 더해 보인다
    db = FakeDB([])
    its = ["활동", "성과", "실적"]
    recs = [{"활동": "캠프", "성과": "교육 이수 인원", "실적": "42명", "quote": "a"},
            {"활동": "특강", "성과": "교육 이수 인원", "실적": "49명", "quote": "b"},
            {"활동": "특강", "성과": "취업 연계", "실적": "11명", "quote": "c"}]
    fl.save(db, 14, {"items": its, "live": ["k"], "docs": {"k": {"doc_id": 1, "title": "t", "items": its, "records": recs}}})
    obs, ev = fl.aggregate(db, 14, ["성과"], [], count=False)
    assert "실적 91명" in obs and "sum 을 주지 않아" in obs and any("42+49=91명" in x for x in ev)


def test_for_table_hides_groups_not_in_rows():
    db = FakeDB([])
    its = ["분야", "참여 인원"]
    recs = [{"분야": "산학협력", "참여 인원": "61명", "quote": "q"}, {"분야": "산학협력", "참여 인원": "52명", "quote": "q"},
            {"분야": "학생 역량강화", "참여 인원": "42명", "quote": "q"}, {"참여 인원": "7명", "quote": "q"}]
    fl.save(db, 15, {"items": its, "live": ["k"], "docs": {"k": {"doc_id": 1, "title": "t", "items": its, "records": recs}}})
    out = fl.for_table(db, 15, ["산학협력", "학생 역량강화"], ["참여 인원"], "분야")
    assert "113명" in out and "(빈 값)" not in out


def test_misplaced_flags_other_group_totals_only():
    # 11-23 18차: 분야 합계 183 을 월별 표 4월 행에, 전체 합계 482 를 교육 이수 인원 실적에 적었다
    db = FakeDB([])
    its = ["날짜", "분야", "참여 인원"]
    recs = [{"날짜": "2026. 4. 9.", "분야": "산학협력", "참여 인원": "61명", "quote": "a"},
            {"날짜": "2026. 5. 7.", "분야": "산학협력", "참여 인원": "52명", "quote": "b"},
            {"날짜": "2026. 4. 20.", "분야": "학생 역량강화", "참여 인원": "42명", "quote": "c"}]
    fl.save(db, 16, {"items": its, "live": ["k"], "docs": {"k": {"doc_id": 1, "title": "t", "items": its, "records": recs}}})
    fl.aggregate(db, 16, ["분야"], ["참여 인원"], count=False)
    fl.aggregate(db, 16, ["날짜:월"], ["참여 인원"], count=False)
    bad = fl.misplaced(db, 16, [["4월", "산학협력", "113"], ["교육 이수 인원(명)", "300", "155"]])
    assert len(bad) == 2 and "산학협력" in bad[0] and "합계" in bad[1]
    # 그 묶음의 행이거나 문서에 적힌 값이면 묻지 않는다
    assert fl.misplaced(db, 16, [["산학협력", "113"], ["4월", "103"], ["4월", "61"], ["합계", "155"]]) == []
    assert fl.misplaced(db, 16, [["총 참여 인원", "155"]]) == []


def test_quarter_grouping_and_for_table_quarter_rows():
    # 분기별 보고 표(묶음 B) — 날짜를 분기로 묶는다
    db = FakeDB([])
    its = ["운영일", "참석자"]
    recs = [{"운영일": "2026년 4월 7일", "참석자": "36명", "quote": "a"}, {"운영일": "2026년 5월 26일", "참석자": "28명", "quote": "b"},
            {"운영일": "2026년 7월 14일", "참석자": "52명", "quote": "c"}]
    fl.save(db, 17, {"items": its, "live": ["k"], "docs": {"k": {"doc_id": 1, "title": "t", "items": its, "records": recs}}})
    obs, ev = fl.aggregate(db, 17, ["운영일:분기"], ["참석자"], count=False)
    assert "2분기: 참석자 64명" in obs and "3분기: 참석자 52명" in obs
    out = fl.for_table(db, 17, ["2분기", "3분기", "4분기"], ["참석자(명)"], "분기")
    assert "group_by: [운영일:분기]" in out and "64명" in out


def test_misplaced_ignores_counts_small_numbers_and_user_values():
    # 11-23 19차: 목표 10(담당자 값)·실적 9(기록 수와 같은 수)가 막혀 성과지표 표가 비었다
    db = FakeDB([])
    its = ["비목", "금액", "분야", "참여 인원"]
    recs = [{"비목": "인건비", "금액": "4000000", "quote": "a"}, {"비목": "인건비", "금액": "3000000", "quote": "b"},
            {"분야": "산학협력", "참여 인원": "61명", "quote": "c"}, {"분야": "산학협력", "참여 인원": "52명", "quote": "d"}]
    fl.save(db, 18, {"items": its, "live": ["k"], "docs": {"k": {"doc_id": 1, "title": "t", "items": its, "records": recs}}})
    fl.aggregate(db, 18, ["비목"], ["금액"], count=True)
    fl.aggregate(db, 18, ["분야"], ["참여 인원"], count=True)
    assert fl.misplaced(db, 18, [["산업체 협약(건)", "10", "2"]]) == []          # 기록 수(2)·작은 수
    assert fl.misplaced(db, 18, [["교육 이수 인원(명)", "300", "113"]], said="목표는 교육 이수 인원 300명") != []
    assert fl.misplaced(db, 18, [["교육 이수 인원(명)", "113"]], said="교육 이수 인원 113명으로 적어") == []


def test_split_row_value_only_on_spaced_slash_matching_columns():
    from zzaimy.app.webhwp_agent import split_row_value
    assert split_row_value("300 / 209 / 69.7", 4) == ["300", "209", "69.7"]
    assert split_row_value("300 / 209", 4) is None                  # 열 수가 다르면 나누지 않는다
    assert split_row_value("2026/04/07 행사", 3) is None
    assert split_row_value("목표 300 / 실적 209 / 달성률 69.7%", ["성과지표", "목표", "실적", "달성률(%)"]) == ["300", "209", "69.7%"]


def test_date_grouping_uses_date_under_other_item_name():
    # 11-23 묶음 B: 날짜가 기록마다 「날짜」·「분기」 항목으로 갈렸다 — 분기로 묶을 때 같은 기록의 날짜 값을 쓴다
    db = FakeDB([])
    its = ["날짜", "분기", "참석자"]
    recs = [{"날짜": "2026년 4월 7일", "참석자": "36명", "quote": "a"}, {"분기": "2026년 5월 26일", "참석자": "28명", "quote": "b"},
            {"날짜": "2026년 7월 14일", "참석자": "52명", "quote": "c"}]
    fl.save(db, 19, {"items": its, "live": ["k"], "docs": {"k": {"doc_id": 1, "title": "t", "items": its, "records": recs}}})
    obs, _ = fl.aggregate(db, 19, ["날짜:분기"], ["참석자"], count=False)
    assert "2분기: 참석자 64명" in obs and "날짜 모름" not in obs


def test_distinct_does_not_count_multi_group_table_doc_as_activity():
    # 11-23 묶음 B: 지출 내역 엑셀의 이름 빈 기록이 과제마다 활동 하나로 세어졌다
    db = FakeDB([])
    its = ["과제", "활동", "금액"]
    docs = {"a": {"doc_id": 1, "title": "강좌 A 결과서", "items": its, "records": [{"과제": "강좌", "활동": "강좌 A", "quote": "a"}]},
            "b": {"doc_id": 2, "title": "상담 B 결과서", "items": its, "records": [{"과제": "상담", "활동": "상담 B", "quote": "b"}]},
            "c": {"doc_id": 3, "title": "강좌 C 결과서", "items": its, "records": [{"과제": "강좌", "quote": "c"}]},
            "x": {"doc_id": 4, "title": "지출 내역", "items": its, "records": [{"과제": "강좌", "금액": "100", "quote": "x1"},
                                                                         {"과제": "상담", "금액": "200", "quote": "x2"}]}}
    fl.save(db, 20, {"items": its, "live": list(docs), "docs": docs})
    obs, _ = fl.aggregate(db, 20, ["과제"], [], count=False, distinct=["활동"])
    assert "강좌: 활동 2개" in obs and "상담: 활동 1개" in obs        # 이름 빈 결과서 C 는 하나로, 지출 내역은 세지 않음


def test_aggregate_points_at_numbers_after_group_names_in_quotes():
    docs = [{"id": 11, "filename": "협약 1.docx", "masked_text": "가상테크 외 3개사와 협약하였다. 이번 활동으로 성과지표 「산업체 협약」 실적 4건을 거두었다."},
            {"id": 12, "filename": "협약 2.docx", "masked_text": "가상로보틱스 외 2개사와 협약하였다. 이번 활동으로 성과지표 「산업체 협약」 실적 3건을 거두었다."}]
    ans = {"협약 1": [{"성과": "산업체 협약", "quote": "이번 활동으로 성과지표 「산업체 협약」 실적 4건을 거두었다."}],
           "협약 2": [{"성과": "산업체 협약", "quote": "이번 활동으로 성과지표 「산업체 협약」 실적 3건을 거두었다."}]}
    db = FakeDB(docs)
    fl.collect(db, 8, docs, ["성과"], FakeClient(ans), title_of=_title, parallel=1)
    obs, _ = fl.aggregate(db, 8, ["성과"], [])
    assert "묶음 이름 바로 뒤의 수" in obs and "4건" in obs and '"성과 실적"' in obs


def test_group_name_number_hint_also_when_summed_item_is_zero_for_that_group():
    # sum 에 인원을 주었어도 협약 묶음의 인원이 0 이면 인용 속 「산업체 협약」 실적 수를 가리킨다(11-23 녹화: 협약 실적을 기록 수 3 으로 씀)
    docs = [{"id": 11, "filename": "협약 1.docx", "masked_text": "가상테크 외 3개사와 협약하였다. 이번 활동으로 성과지표 「산업체 협약」 실적 4건을 거두었다."},
            {"id": 12, "filename": "협약 2.docx", "masked_text": "가상로보틱스 외 2개사와 협약하였다. 이번 활동으로 성과지표 「산업체 협약」 실적 3건을 거두었다."},
            {"id": 13, "filename": "캠프.docx", "masked_text": "캠프에 학생 42명이 참여하였다. 성과지표 「교육 이수 인원」 에 반영한다."}]
    ans = {"협약 1": [{"성과": "산업체 협약", "quote": "이번 활동으로 성과지표 「산업체 협약」 실적 4건을 거두었다."}],
           "협약 2": [{"성과": "산업체 협약", "quote": "이번 활동으로 성과지표 「산업체 협약」 실적 3건을 거두었다."}],
           "캠프": [{"성과": "교육 이수 인원", "참여 인원": "학생 42명", "quote": "캠프에 학생 42명이 참여하였다."}]}
    db = FakeDB(docs)
    fl.collect(db, 8, docs, ["성과", "참여 인원"], FakeClient(ans), title_of=_title, parallel=1)
    obs, _ = fl.aggregate(db, 8, ["성과"], ["참여 인원"])
    assert "묶음 이름 바로 뒤의 수" in obs and "4건" in obs


def test_bracketed_unit_in_item_is_not_a_label_word():
    # 「집행액(천원)」 으로 지출 내역 표(머리 「지출액(원)」)를 읽으면 행에는 「천원」 이 없다 — 금액이 지워지면 안 된다(11-23 녹화)
    doc = "지출일자 | 세목 | 지출액(원)\n2026.04.07 | 홍보비 | 3150000\n2026.04.21 | 강사료 | 2600000"
    recs = [{"세목": "홍보비", "집행액(천원)": "3150000", "quote": "2026.04.07 | 홍보비 | 3150000"}]
    kept, dropped = fl.validate(recs, ["세목", "집행액(천원)"], doc)
    assert dropped == 0 and kept[0]["집행액(천원)"] == "3150000"
    # 괄호 밖 낱말은 그대로 본다 — 다른 활동 인원을 「교육 이수 인원(명)」 으로 뽑은 값은 지운다
    doc2 = "세미나 참여 61명. 교육 이수 인원 24명."
    kept2, _ = fl.validate([{"교육 이수 인원(명)": "61명", "quote": "세미나 참여 61명."}], ["교육 이수 인원(명)"], doc2)
    assert not kept2 or kept2[0]["교육 이수 인원(명)"] == ""


def test_aggregate_filter_can_exclude_and_match_globs_and_documents():
    # 「운영위원회 회의 참석자는 빼고 다시 계산해 줘」 — 빼는 조건이 없어 되풀이하다 못 고치던 것(11-23 녹화)
    docs = [{"id": 1, "filename": "강좌.docx", "masked_text": "2026년 4월 7일 생활 속 디지털 기초 강좌. 참석자 36명."},
            {"id": 2, "filename": "회의.docx", "masked_text": "2026년 4월 2일 사업 운영위원회 1차 회의. 참석 운영위원 5명."}]
    ans = {"강좌": [{"날짜": "2026년 4월 7일", "활동": "생활 속 디지털 기초 강좌", "참석자": "36명", "quote": "참석자 36명."}],
           "회의": [{"날짜": "2026년 4월 2일", "활동": "사업 운영위원회 1차 회의", "참석자": "5명", "quote": "참석 운영위원 5명."}]}
    db = FakeDB(docs)
    fl.collect(db, 9, docs, ["날짜", "활동", "참석자"], FakeClient(ans), title_of=_title, parallel=1)
    obs, ev = fl.aggregate(db, 9, ["날짜:분기"], ["참석자"], filt={"활동": "!운영위원회"})
    assert "36명" in obs and "41명" not in obs and "뺀 기록 1건" in obs
    obs2, _ = fl.aggregate(db, 9, ["날짜:분기"], ["참석자"], filt={"활동": "*운영위원회*"})
    assert "5명" in obs2 and "36명" not in obs2
    obs3, _ = fl.aggregate(db, 9, ["날짜:분기"], ["참석자"], documents=["!회의"])
    assert "36명" in obs3 and "41명" not in obs3


def test_final_answer_shows_plain_words_not_tool_names():
    from zzaimy.app.doc_agent import plain_tool_names
    out = plain_tool_names("aggregate_facts 도구가 회의를 빼지 못했다. aggregate_facts 결과로 인정되지 않았다. hwp_undo 로 되돌림. my_var 은 그대로",
                           ["aggregate_facts", "hwp_undo"])
    assert "aggregate_facts" not in out and "hwp_undo" not in out and "실적 집계 도구가" in out and "my_var" in out


def test_period_item_is_collected_as_date_and_missing_dates_are_warned():
    # items 「분기」 로 모으면 문서의 날짜를 못 뽑아 분기 합계가 줄었다(11-23 재녹화 10) — 날짜로 모으고, 날짜 모름 값은 알린다
    docs = [{"id": 1, "filename": "강좌.docx", "masked_text": "2026년 4월 7일 디지털 강좌. 참석자 36명."},
            {"id": 2, "filename": "모임.docx", "masked_text": "협의회 모임. 참석자 19명."}]
    ans = {"강좌": [{"날짜": "2026년 4월 7일", "참석자": "36명", "quote": "참석자 36명."}],
           "모임": [{"참석자": "19명", "quote": "참석자 19명."}]}
    db = FakeDB(docs)
    obs = fl.collect(db, 11, docs, ["분기", "참석자"], FakeClient(ans), title_of=_title, parallel=1)
    assert "「날짜」 로 모았다" in obs and "날짜:분기" in obs
    agg, _ = fl.aggregate(db, 11, ["날짜:분기"], ["참석자"])
    assert "2분기" in agg or "2026-Q2" in agg
    assert "날짜를 읽지 못한 기록" in agg and "19" in agg
    assert fl.quarter_of("3분기") == "3분기" and fl.quarter_of("2026. 5. 12.") == "2026-Q2"


def test_for_table_says_plainly_when_row_names_appear_in_quotes():
    # 11-23 4차 녹화 01: 핵심지표 행 이름이 결과서 인용에 나오는데 「적혀 있으면」 조건만 보고 담당자에게 물었다
    docs = [{"id": 1, "filename": "강좌.docx", "masked_text": "수강생 25명이 수료하였다. 이 결과는 핵심지표 「강좌 수료자」 달성치에 25명으로 반영한다."},
            {"id": 2, "filename": "상담.docx", "masked_text": "상담 44건. 이 결과는 핵심지표 「학습 상담」 달성치에 44건으로 반영한다."}]
    ans = {"강좌": [{"활동": "강좌", "quote": "이 결과는 핵심지표 「강좌 수료자」 달성치에 25명으로 반영한다."}],
           "상담": [{"활동": "상담", "quote": "이 결과는 핵심지표 「학습 상담」 달성치에 44건으로 반영한다."}]}
    db = FakeDB(docs)
    fl.collect(db, 12, docs, ["활동"], FakeClient(ans), title_of=_title, parallel=1)
    out = fl.for_table(db, 12, ["강좌 수료자(명)", "학습 상담(건)"], ["목표치", "달성치"], "핵심지표", dry=True)
    assert "인용에" in out and "「강좌 수료자」 1개 문서" in out and "collect_facts {items: [핵심지표, 목표치, 달성치]}" in out


def test_base_ratio_matches_units_when_item_declares_thousand_won():
    # 11-23 24차: 「집행액(천원)」 에 원 단위 금액이 모이고 base 120000(천원)을 주자 집행률 43000% — 단위를 맞춘 43.0% 를 보인다
    docs = [{"id": 1, "filename": "지출.xlsx", "masked_text": "지출일자 | 세목 | 지출액(원)\n2026.04.07 | 홍보비 | 31500000\n2026.04.21 | 강사료 | 20100000"}]
    ans = {"지출": [{"세목": "홍보비", "집행액(천원)": "31500000", "quote": "2026.04.07 | 홍보비 | 31500000"},
                   {"세목": "강사료", "집행액(천원)": "20100000", "quote": "2026.04.21 | 강사료 | 20100000"}]}
    db = FakeDB(docs)
    fl.collect(db, 13, docs, ["세목", "집행액(천원)"], FakeClient(ans), title_of=_title, parallel=1)
    obs, ev = fl.aggregate(db, 13, ["세목"], ["집행액(천원)"], base=120000)
    assert "43.0%" in obs and "51,600" in obs and "43000" not in obs
    assert any("단위 맞춤" in x for x in ev)


def test_group_name_number_hint_when_summed_value_differs_from_quoted_result():
    # 24차 A 짧게: 성과지표로 묶고 참여 인원을 더해 취업 연계 52 로 씀 — 인용의 「취업 연계」 실적 6명과 다르면 가리킨다
    docs = [{"id": 1, "filename": "부트캠프.docx", "masked_text": "참여 인원은 학생 38명. 이번 활동으로 성과지표 「취업 연계」 실적 6명을 거두었다."},
            {"id": 2, "filename": "특강.docx", "masked_text": "참여 인원은 학생 49명. 이번 활동으로 성과지표 「취업 연계」 실적 15명을 거두었다."}]
    ans = {"부트캠프": [{"성과": "취업 연계", "참여 인원": "학생 38명", "quote": "참여 인원은 학생 38명. 이번 활동으로 성과지표 「취업 연계」 실적 6명을 거두었다."}],
           "특강": [{"성과": "취업 연계", "참여 인원": "학생 49명", "quote": "참여 인원은 학생 49명. 이번 활동으로 성과지표 「취업 연계」 실적 15명을 거두었다."}]}
    db = FakeDB(docs)
    fl.collect(db, 14, docs, ["성과", "참여 인원"], FakeClient(ans), title_of=_title, parallel=1)
    obs, _ = fl.aggregate(db, 14, ["성과"], ["참여 인원"])
    assert "묶음 이름 바로 뒤의 수" in obs and "6명" in obs and "15명" in obs


def test_aggregate_warns_number_item_as_group_key_and_too_many_groups():
    # 25차 B: 참석 인원·달성치를 묶음 기준으로 줌 / 25차 A: 기준 셋을 한꺼번에 줘 58묶음 — 맨 앞 줄에 알린다
    db = FakeDB([])
    its = ["분기", "활동", "참석 인원"]
    docs = {f"d{i}": {"doc_id": i, "title": f"행사 {i} 결과서", "items": its,
                      "records": [{"분기": f"{i % 4 + 1}분기", "활동": f"행사 {i}", "참석 인원": f"{10 + i}명", "quote": f"q{i}"}]}
            for i in range(20)}
    fl.save(db, 30, {"items": its, "live": list(docs), "docs": docs})
    obs, _ = fl.aggregate(db, 30, ["분기", "활동", "참석 인원"], [])
    lines = obs.split("\n")
    assert "「참석 인원」 은(는) 수 값 항목" in lines[1] and "「분기」" not in lines[1]
    assert "잘게 나뉘었다" in lines[2]
    obs2, _ = fl.aggregate(db, 30, ["분기"], ["참석 인원"])
    assert "주의" not in obs2


def test_aggregate_says_record_count_is_not_activity_count():
    # 25차 B 짧게: 「평생교육 네트워크: 기록 12개 《문서 5개》」 를 운영 12회로 씀 — distinct 로 센 활동 수를 쓰라고 알린다
    db = FakeDB([])
    its = ["과제", "활동", "금액"]
    docs = {"a": {"doc_id": 1, "title": "강좌 A 결과서", "items": its,
                  "records": [{"과제": "강좌", "활동": "강좌 A", "금액": "100", "quote": "a1"}, {"과제": "강좌", "활동": "강좌 A", "금액": "200", "quote": "a2"}]},
            "b": {"doc_id": 2, "title": "강좌 B 결과서", "items": its, "records": [{"과제": "강좌", "활동": "강좌 B", "quote": "b"}]}}
    fl.save(db, 31, {"items": its, "live": list(docs), "docs": docs})
    obs, _ = fl.aggregate(db, 31, ["과제"], [], count=True)
    assert "기록 3개·문서 2개" in obs and 'distinct: ["활동"]' in obs
    obs2, _ = fl.aggregate(db, 31, ["과제"], [], count=True, distinct=["활동"])
    assert "뽑은 줄 수" not in obs2


class _ShyClient(FakeClient):
    """알림 없이 부르면 빈 답 — 26차 B 한마당 결과서처럼 수가 분명한 문서를 27B 가 빈 답으로 낸 꼴."""

    def __init__(self, by_title):
        super().__init__(by_title)
        outer = self

        class _C:
            def create(self, model, messages, max_tokens, **kw):
                user = messages[-1]["content"]
                title = user.split("《", 1)[1].split("》", 1)[0]
                outer.calls.append(title)
                recs = outer.by_title.get(title, []) if "[알림]" in user else []
                return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps({"records": recs}, ensure_ascii=False)))])

        self.client.chat = SimpleNamespace(completions=_C())


def test_collect_rereads_numeric_doc_that_came_back_empty():
    docs = [{"id": 41, "filename": "한마당.docx", "masked_text": "2026년 12월 1일에 한마당을 진행하였다.\n참석자는 64명이었다."},
            {"id": 42, "filename": "안내.docx", "masked_text": "작성 안내: 표를 채운다."}]
    ans = {"한마당": [{"날짜": "2026년 12월 1일", "참석자": "64명", "quote": "참석자는 64명이었다."}]}
    db, c = FakeDB(docs), _ShyClient(ans)
    obs = fl.collect(db, 40, docs, ["날짜", "참석자"], c, title_of=_title, parallel=1)
    assert "참석자=64명" in obs
    assert c.calls.count("한마당") == 2 and c.calls.count("안내") == 1     # 수가 없는 문서는 다시 읽지 않는다


def test_fill_labeled_takes_empty_item_from_two_cell_label_row():
    # 26차 A: 개요 표의 「분야 | 산학협력」 을 27B 가 비워 뽑아 성과 공유회 70명이 (빈 값) 으로 빠짐
    text = "2026. 12. 17. 성과 공유회를 운영하였다.\n참여 인원은 학생 70명이다.\n사업명 | 2026 가상 사업\n분야 | 산학협력\n일시 | 2026. 12. 17."
    kept = [{"활동": "성과 공유회", "참여 인원": "학생 70명", "quote": "참여 인원은 학생 70명이다."}]
    out = fl.fill_labeled(kept, ["날짜", "분야", "활동", "참여 인원"], text)
    assert out[0]["분야"] == "산학협력" and out[0]["활동"] == "성과 공유회"
    # 표 머리줄(값이 다른 항목 이름)·두 번 나온 이름은 쓰지 않는다
    out2 = fl.fill_labeled([{"금액": "100"}], ["비목", "금액", "분야"], "비목 | 금액\n분야 | 가\n분야 | 나")
    assert "비목" not in out2[0] and "분야" not in out2[0]


def test_collect_fills_item_from_label_row():
    docs = [{"id": 51, "filename": "공유회.docx", "masked_text": "참여 인원은 학생 70명이다.\n분야 | 산학협력"}]
    ans = {"공유회": [{"참여 인원": "학생 70명", "quote": "참여 인원은 학생 70명이다."}]}
    db = FakeDB(docs)
    fl.collect(db, 50, docs, ["분야", "참여 인원"], FakeClient(ans), title_of=_title, parallel=1)
    obs, _ = fl.aggregate(db, 50, ["분야"], ["참여 인원"])
    assert "산학협력: 기록 1개, 참여 인원 70명" in obs


def test_aggregate_warns_when_empty_group_holds_values():
    # 28차 B: group_by [핵심지표 반영] 에서 (빈 값) 수료자 103명을 두고 「강좌 수료자」 묶음 68명을 달성치로 씀 — 합계 줄을 쓰라고 알린다
    db = FakeDB([])
    its = ["활동", "수료자", "핵심지표 반영"]
    docs = {"a": {"doc_id": 1, "title": "강좌 A", "items": its, "records": [{"활동": "강좌 A", "수료자": "38명", "핵심지표 반영": "강좌 수료자", "quote": "a"}]},
            "b": {"doc_id": 2, "title": "강좌 B", "items": its, "records": [{"활동": "강좌 B", "수료자": "47명", "quote": "b"}]}}
    fl.save(db, 32, {"items": its, "live": list(docs), "docs": docs})
    obs, _ = fl.aggregate(db, 32, ["핵심지표 반영"], ["수료자"])
    lines = obs.split("\n")
    assert "빈 기록에도 「수료자」 47" in lines[1] and "「강좌 수료자」 은 38" in lines[1] and "합계 · 수료자: 85명" in obs
    obs2, _ = fl.aggregate(db, 32, ["활동"], ["수료자"])
    assert "빈 기록에도" not in obs2


def test_unpicked_mentions_lists_other_docs_with_item_numbers():
    # 28차 B 한번에: 제목에 「상담」 이 든 문서만 골라 학습 상담 82건 — 고르지 않은 결과서의 「학습 상담 27건」 을 알린다
    docs = [{"id": 1, "filename": "상담 주간.docx", "masked_text": "학습 상담 44건을 진행하였다."},
            {"id": 2, "filename": "워크숍.docx", "masked_text": "기간 중 학습 상담 27건을 진행하였다."},
            {"id": 3, "filename": "강좌.docx", "masked_text": "수강생 30명이 수료하였다."}]
    out = fl.unpicked_mentions(["활동명", "학습 상담 건수"], docs[:1], docs)
    assert "《워크숍.docx》 「학습 상담」 27건" in out and "강좌" not in out
    assert fl.unpicked_mentions(["학습 상담 건수"], docs, docs) == ""


def test_count_hint_picks_activity_even_with_digits_in_names():
    # 29차 B: 「사업 운영위원회 1차 회의」 의 1 때문에 활동이 빠지고 값 없는 「운영 횟수」 를 distinct 로 권함 — 활동을 권하고, 빈 sum 을 알린다
    db = FakeDB([])
    its = ["과제", "활동", "운영 횟수", "참석자"]
    docs = {"a": {"doc_id": 1, "title": "운영위 1차", "items": its, "records": [{"과제": "네트워크", "활동": "사업 운영위원회 1차 회의", "참석자": "5명", "quote": "a"}]},
            "b": {"doc_id": 2, "title": "협의회", "items": its, "records": [{"과제": "네트워크", "활동": "협의회 정례 모임", "참석자": "19명", "quote": "b1"},
                                                                          {"과제": "네트워크", "활동": "협의회 정례 모임", "참석자": "", "quote": "b2"}]}}
    fl.save(db, 33, {"items": its, "live": list(docs), "docs": docs})
    obs, _ = fl.aggregate(db, 33, ["과제"], ["운영 횟수", "참석자"], count=True)
    assert 'distinct: ["활동"]' in obs and "「운영 횟수」 값이 든 기록이 하나도 없다" in obs and 'distinct: ["운영 횟수"]' not in obs


def test_aggregate_warns_target_values_in_sum():
    # 30차 B 짧게: 작성 안내의 목표치 250명이 기록으로 뽑혀 수료자 합 421 — 그 문서를 빼는 인자를 보인다
    db = FakeDB([])
    its = ["지표", "수료자"]
    docs = {"g": {"doc_id": 1, "title": "결과보고서 작성 안내", "items": its,
                  "records": [{"지표": "강좌 수료자", "수료자": "250명", "quote": "2026년 핵심지표 목표치: 강좌 수료자 250명"}]},
            "a": {"doc_id": 2, "title": "강좌 A 결과서", "items": its,
                  "records": [{"지표": "강좌 수료자", "수료자": "38명", "quote": "핵심지표 「강좌 수료자」 달성치에 38명으로 반영한다."}]}}
    fl.save(db, 34, {"items": its, "live": list(docs), "docs": docs})
    obs, _ = fl.aggregate(db, 34, ["지표"], ["수료자"])
    assert "목표치는 실적이 아니다" in obs and 'documents: ["!결과보고서 작성 안내"]' in obs
    obs2, _ = fl.aggregate(db, 34, ["지표"], ["수료자"], documents=["!결과보고서 작성 안내"])
    assert "목표치는" not in obs2 and "수료자 38명" in obs2


def test_quoted_number_hint_skips_records_already_summed():
    # 30차 B 한번에: 수료자는 모였는데 다른 sum 항목이 비었다고 강좌 수료자 인용만 보이고 「지역 기관 연계」 인용이 잘림
    db = FakeDB([])
    its = ["지표", "수료자", "연계 기관"]
    recs = {"a": ("강좌 A", {"지표": "강좌 수료자", "수료자": "38명", "quote": "핵심지표 「강좌 수료자」 달성치에 38명으로 반영"}),
            "b": ("강좌 B", {"지표": "강좌 수료자", "수료자": "47명", "quote": "핵심지표 「강좌 수료자」 달성치에 47명으로 반영"}),
            "c": ("협약 1", {"지표": "지역 기관 연계", "quote": "핵심지표 「지역 기관 연계」 달성치에 3곳으로 반영"}),
            "d": ("협약 2", {"지표": "지역 기관 연계", "quote": "핵심지표 「지역 기관 연계」 달성치에 2곳으로 반영"})}
    docs = {k: {"doc_id": i, "title": t, "items": its, "records": [r]} for i, (k, (t, r)) in enumerate(recs.items(), 1)}
    fl.save(db, 35, {"items": its, "live": list(docs), "docs": docs})
    obs, _ = fl.aggregate(db, 35, ["지표"], ["수료자", "연계 기관"])
    assert "「지역 기관 연계」 3곳" in obs and "「강좌 수료자」 38명" not in obs


def test_base_unfounded_flags_scaled_base():
    # 30차 B 한번에: 1억 2천만 원을 12,000,000 으로 옮겨 base 로 줌
    texts = ["총 사업비는 1억 2천만 원이다.", "지출액 3150000"]
    out = fl.base_unfounded(12000000, texts)
    assert "12,000,000 이 근거" in out and "120,000,000원" in out
    assert fl.base_unfounded(120000000, texts) == "" and fl.base_unfounded(250, texts) == ""
    assert fl.base_unfounded("3,150,000", texts) == ""


def test_unpicked_mentions_separates_target_lines():
    # 31차 B 짧게: 작성 안내의 목표치와 협약 결과서의 실적 3곳이 한 목록에 섞여 모델이 목록째 넘김 — 목표 줄은 뺀다
    docs = [{"id": 1, "filename": "한마당.docx", "masked_text": "기관 연계 1곳"},
            {"id": 2, "filename": "작성 안내.txt", "masked_text": "2026년 핵심지표 목표치: 기관 연계 8곳."},
            {"id": 3, "filename": "협약.docx", "masked_text": "지역 기관 3곳과 협약.\\n핵심지표 「지역 기관 연계」 달성치에 3곳으로 반영한다."}]
    out = fl.unpicked_mentions(["기관 연계"], docs[:1], docs)
    assert "《협약.docx》 「기관 연계」 3곳" in out and "8곳" not in out and "목표로 적힌 수는 뺐다: 《작성 안내.txt》" in out


def test_fill_aliases_learns_label_name_across_docs():
    # 33차 B 짧게: 과제를 「분야」 로 모았는데 문서는 「추진 과제 | 지역 연계 강좌」 — 두 문서에서 분야 값이 그 칸 값과 같으면 빈 문서도 채운다
    a = ({"records": [{"분야": "지역 연계 강좌", "참석 인원": "41명"}]}, "추진 과제 | 지역 연계 강좌\n장소 | 도서관")
    b = ({"records": [{"분야": "평생교육 네트워크", "참석 인원": "64명"}]}, "추진 과제 | 평생교육 네트워크")
    c = ({"records": [{"참석 인원": "52명"}]}, "추진 과제 | 지역 연계 강좌\n장소 | 복지관")
    notes = fl.fill_aliases([a, b, c], ["분야", "참석 인원"])
    assert c[0]["records"][0]["분야"] == "지역 연계 강좌" and notes and "「추진 과제」" in notes[0]
    d = ({"records": [{"참석 인원": "10명"}]}, "장소 | 강당")
    assert fl.fill_aliases([a, d], ["분야"]) == [] and "분야" not in d[0]["records"][0]
