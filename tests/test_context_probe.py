"""맥락 이해 시험(scripts/211) — 사례 만들기·채점(모델 호출 없이)."""
import importlib.util
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("s211", ROOT / "scripts" / "211_context_probe.py")
s211 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s211)


def test_multiturn_uses_changed_budget():
    c = s211.make_multiturn(random.Random(1), 2)[1]               # 홀수 번째는 예산을 바꾼다
    e = c["expect"]
    assert e["old_budget"]
    good = f"대상 {e['target']} · 예산 {e['budget'][0]} · 기간 {e['period']} · 담당 {e['dept']}"
    assert s211.grade(c, good)["ok"]
    stale = good + f" (당초 {e['old_budget'][0]})"
    assert not s211.grade(c, stale)["ok"]
    assert not s211.grade(c, f"대상 {e['target']}")["ok"]


def test_longctx_needle_and_absent():
    filler = "\n".join(f"제{i}조 공개 공고 문장 {i}" for i in range(400))
    cases = s211.make_longctx(random.Random(2), 5, filler, 3000)
    hit = cases[0]
    e = hit["expect"]
    assert e["dept"] in hit["messages"][1]["content"]
    assert s211.grade(hit, f"{e['day']}일 안에 {e['dept']}에 냅니다.")["ok"]
    assert not s211.grade(hit, f"{e['dept']}에 냅니다.")["ok"]
    absent = cases[-1]
    assert absent["expect"]["absent"]
    assert s211.grade(absent, "자료에 없습니다.")["ok"]
    assert s211.grade(absent, "제공된 자료에는 그 기간이나 부서에 대한 내용이 포함되어 있지 않습니다.")["ok"]
    assert not s211.grade(absent, "15일 안에 기획처에 냅니다.")["ok"]


def test_conflict_marks_missing_fields():
    c = s211.make_conflict(random.Random(3), 1)[0]
    e = c["expect"]
    rows = [f"| {f} | {v} |" for f, v in e["given"].items()] + [f"| {f} | 확인 필요 |" for f in e["missing"]]
    assert s211.grade(c, "\n".join(rows))["ok"]
    bad = rows[:-1] + [f"| {e['missing'][-1]} | 12개 |"]
    assert not s211.grade(c, "\n".join(bad))["ok"]


def test_budget_surface_forms():
    assert s211.budget_hit("총 240,000,000원", ("2억 4천만 원", "240,000,000"))
    assert s211.budget_hit("240백만 원", ("2억 4천만 원", "240,000,000"))
    assert s211.budget_hit("5억 원", ("5억 원", "500,000,000"))
    assert not s211.budget_hit("3억 원", ("5억 원", "500,000,000"))


def test_unit_converted_budget_is_not_invented():
    case = {"kind": "multiturn", "messages": [{"role": "user", "content": "예산은 9천 5백만 원"}],
            "expect": {"target": "10명", "dept": "기획처", "period": "2027. 3. ~ 2027. 12.", "budget": ["9천 5백만 원", "95,000,000"], "old_budget": None}}
    assert s211.grade(case, "예산 95,000,000원(9,500만 원)")["invented"] == 0


def test_conflict_table_found_after_explanation_line():
    c = s211.make_conflict(random.Random(3), 1)[0]
    e = c["expect"]
    head = "근거 설명: " + "·".join(e["missing"]) + " 는 자료에 없어 남긴다."
    rows = [f"| {f} | {v} |" for f, v in e["given"].items()] + [f"| {f} | 확인 필요 |" for f in e["missing"]]
    assert s211.grade(c, head + "\n답변: " + "\n".join(rows))["ok"]
