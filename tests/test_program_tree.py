"""사업 목록 대·중·소 분류 — 장부의 지원기관·체계 갈래로 가르고, 이름은 장부 정식 이름으로. 장부에 없으면 확인 필요로."""
import importlib.util
from pathlib import Path

from zzaimy.app import program_tree as pt

LEDGER = {"categories": {"특수목적": {"label": "특수목적·별도 공모형"}},
          "programs": [{"name": "가나 선도전문대학 육성사업(GN+)", "terms": ["GN+", "가나 선도전문대학"], "category": "특수목적", "ministry": "교육부"},
                       {"name": "청해진대학 사업", "terms": [], "category": "타부처", "ministry": "고용노동부"},
                       {"name": "다라 위탁사업", "terms": [], "category": "타부처", "ministry": "(재)대구테크노파크"},
                       {"name": "마바 교육", "terms": [], "category": "타부처", "ministry": "대구 남구청"}]}


def test_tree_levels_and_names():
    items = [{"key": "a", "label": "가나 선도전문대학", "n": 9}, {"key": "b", "label": "청해진 대학 사업", "n": 3},
             {"key": "c", "label": "다라 위탁사업", "n": 2}, {"key": "d", "label": "마바 교육", "n": 1}, {"key": "e", "label": "수익 비수익 사업", "n": 1}]
    tree = {t["label"]: t for t in pt.build(items, LEDGER)}
    edu = tree[pt.L1_EDU]["groups"][0]
    assert edu["label"] == "특수목적·별도 공모형" and edu["progs"][0]["label"] == "가나 선도전문대학 육성사업(GN+)"
    assert tree[pt.L1_GOV]["groups"][0]["label"] == "고용노동부"
    assert tree[pt.L1_ORG]["groups"][0]["label"] == "대구테크노파크"
    assert tree[pt.L1_LOCAL]["groups"][0]["label"] == "대구 남구청"
    assert tree[pt.L1_UNKNOWN]["unknown"] and tree[pt.L1_UNKNOWN]["groups"][0]["progs"][0]["key"] == "e"
    assert list(tree) == [x for x in pt.ORDER if x in tree]


def test_ledger_consolidate_variants():
    spec = importlib.util.spec_from_file_location("l186", Path(__file__).parents[1] / "scripts" / "186_school_program_ledger.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    sch = lambda *ys: {"years": list(ys)}
    entries = [{"name": "청해진대학 사업", "ministry": "고용노동부", "school": sch(2023, 2024, 2025)},
               {"name": "신청해진 대학", "ministry": "고용노동부", "school": sch(2026)},
               {"name": "재학생 맞춤형 고용서비스 사업", "ministry": "고용노동부", "school": sch(2023)},
               {"name": "대학 재학생 맞춤형 고용서비스 사업", "ministry": "고용노동부", "school": sch(2024)},
               {"name": "고교 재학생 맞춤형 고용서비스 사업", "ministry": "고용노동부", "school": sch(2024)},
               {"name": "가나 장학금", "ministry": "교육부", "school": sch(2025)},
               {"name": "가나 AI 장학금", "ministry": "교육부", "school": sch(2026)}]
    gone = m.consolidate(entries)
    assert gone == [("신청해진 대학", "청해진대학 사업")]          # 여럿의 변형·영문 덧말은 묶지 않는다
    assert entries[0]["school"]["years"] == [2023, 2024, 2025, 2026] and "신청해진 대학" in entries[0]["terms"]
