"""scripts/188 — 시험지 고르기·판정·요약 표(긴 측정이 끝에서 깨지지 않게)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("s188", Path(__file__).resolve().parent.parent / "scripts/188_graphrag_vs_fulltext.py")
s188 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s188)


def _items():
    sec = [{"id": f"sec{i}", "set": "section", "type": "section", "q": f"q{i}", "doc_id": i, "twins": {i}, "prog_docs": {i, 99},
            "gold_text": "취업률 목표 달성"} for i in range(4)]
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
    assert len(s188.blind_pack(rows, 7)) == len(items)
