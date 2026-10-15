"""scripts/188 — 시험지 고르기·판정·요약 표(긴 측정이 끝에서 깨지지 않게)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("s188", Path(__file__).resolve().parent.parent / "scripts/188_graphrag_vs_fulltext.py")
s188 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s188)


def _items():
    sec = [{"id": f"sec{i}", "set": "section", "type": "section", "q": f"q{i}", "doc_id": i, "twins": {i}, "prog_docs": {i, 99},
            "gold_text": "취업률 목표 70% 달성, 80명"} for i in range(4)]
    rel = [{"id": f"rel{i}", "set": "relation", "type": t, "q": f"r{i}", "gold": [10 + i, 20 + i], "any": t == "lineage"}
           for i, t in enumerate(["plan_report", "plan_report", "lineage", "evaluates"])]
    return sec + rel


def test_pick_answer_items_alternates_relation_types():
    got = s188.pick_answer_items(_items(), 2, 3)
    assert [x["id"] for x in got] == ["sec0", "sec1", "rel0", "rel2", "rel3"]


def test_judge_and_summary_table():
    items = _items()
    assert s188.judge_search(items[0], [5, 0])["rank"] == 2
    assert s188.judge_search(items[4], [10, 20])["ok"] and not s188.judge_search(items[4], [10])["ok"]
    assert s188.judge_search(items[6], [12])["ok"]                       # 앞뒤 단계는 하나만 들어도
    rows = []
    for it in items:
        r = {"id": it["id"], "set": it["set"], "type": it["type"], "q": it["q"], "search": {}, "answers": {}}
        for m, got in (("fulltext", [99]), ("graph", [it.get("doc_id", 0), *it.get("gold", [])])):
            r["search"][m] = {"got": got, "sec": 1.0, "error": "", **s188.judge_search(it, got)}
            r["answers"][m] = {"answer": "취업률 목표 70%", "error": "", "wall_sec": 30.0, "timing": {"search_sec": 3.0},
                               "evidence_docs": got, "empty": False, "no_evidence": False, "unsupported_numbers": ["70"],
                               "evidence_has_gold": True}
        rows.append(r)
    summ = {m: s188.summarize(rows, m) for m in ("fulltext", "graph")}
    assert summ["graph"]["search"]["section"]["doc_at_k"] == 1.0 and summ["fulltext"]["search"]["section"]["doc_at_k"] == 0.0
    assert summ["graph"]["search"]["relation"]["ok"] == 1.0
    assert summ["graph"]["answer"]["with_unsupported_numbers"] == len(items)
    md = s188.table(summ)
    assert "A 풀텍스트 RAG" in md and "100.0%" in md
    assert s188.unsupported_numbers("2023년 계획", "2023년 목표 75%, 2024. 3. 1.", "목표 75 % 2024.03.01") == []
    for r, it in zip(rows, items):
        for a in r["answers"].values():
            a["quality"] = s188.quality(it, a)
    summ = {m: s188.summarize(rows, m) for m in ("fulltext", "graph")}
    assert summ["graph"]["answer"]["numbers_total"] == len(items) and summ["graph"]["answer"]["fact_recall_mean"] == 0.5
    assert summ["graph"]["answer"]["relation_both_sides"]["n"] == 4
    assert s188._wilson(0, 0) is None and s188._wilson(5, 10)[0] < 0.5 < s188._wilson(5, 10)[1]
    assert s188.gold_facts("목표 70% 2023년 1,200명 70", "2023년") == ["70", "1200"]


def test_think_compare_summary_pairs_and_blind_keys():
    """--think-compare — 변형별 요약·짝 비교(켬 기준)·눈가림 열쇠가 끔/켬으로 붙는다."""
    rows = []
    scores = [(1, 2), (2, 2), (0, 1), (2, 1)]
    for i, (off, on) in enumerate(scores):
        r = {"id": f"sec{i}", "set": "section", "type": "section", "q": f"q{i}", "search": {}, "answers": {}}
        for v, sc, sec in (("think_off", off, 20.0), ("think_on", on, 90.0)):
            r["answers"][v] = {"answer": "취업률 목표 70%", "error": "", "wall_sec": sec + 5, "empty": False,
                               "timing": {"generate_sec": sec, "completion_tokens": int(sec * 12), "think": v == "think_on"},
                               "unsupported_numbers": [], "quality": {"num_total": 1, "num_unsupported": 0, "fact_recall": 0.5,
                                                                      "fact_hits": 1, "n_facts": 2},
                               "judge": {"score": sc}}
        rows.append(r)
    t = s188.summarize_think(rows)
    assert t["think_off"]["generate_sec_mean"] == 20.0 and t["think_on"]["completion_tokens_mean"] == 1080
    assert t["think_on"]["think_on_calls"] == 4 and t["think_off"]["think_on_calls"] == 0
    assert t["pairs"] == {"n": 4, "better": 2, "worse": 1, "same": 1, "mean_diff": 0.25, "sign_test_p": 1.0}
    assert "짝 비교" in s188.think_table(t)

    class DB:
        pass
    blind, key = s188.blind_pack(DB(), rows, {r["id"]: {"set": "section", "gold_text": "정답"} for r in rows}, 7, keys=s188.THINK)
    assert len(blind) == 4 and all(set(k.values()) == set(s188.THINK) for k in key.values())
    assert all(set(b["answers"]) == {"가", "나"} for b in blind)


def test_run_answer_think_sets_env_only_for_that_call(monkeypatch):
    import os
    from zzaimy.app import responder
    seen = []

    class AR:
        def answer(self, db, q, scope=None):
            seen.append(os.environ.get("ZZAIMY_ANSWER_THINK"))
            return "답"
    monkeypatch.setattr(responder, "AgentResponder", AR)
    monkeypatch.delenv("ZZAIMY_ANSWER_THINK", raising=False)
    it = {"q": "q", "set": "relation", "type": "lineage", "gold": [1], "any": True}
    s188.run_answer(None, it, "graph_read", think=True)
    s188.run_answer(None, it, "graph_read", think=False)
    assert seen == ["1", "0"] and "ZZAIMY_ANSWER_THINK" not in os.environ


def test_named_question_points_to_one_document():
    """같은 제목의 절을 가진 문서가 여럿이면 갈래 낱말 자리에 문서 이름을 넣는다 — 원래 질문은 그대로."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("s173", Path(__file__).resolve().parents[1] / "scripts/173_rag_probe.py")
    s173 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s173)
    q = "별빛 사업 2025년 계획서의 「과제 배경 및 목표」 내용을 알려줘"
    assert s173.named_question(q, "과제 배경 및 목표", "단위과제(2-1) 과제계획서.hwpx") == \
        "별빛 사업 2025년 「단위과제(2-1) 과제계획서」의 「과제 배경 및 목표」 내용을 알려줘"
    assert s173.named_question(q, "과제 배경 및 목표", "") == q
