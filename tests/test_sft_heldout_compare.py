"""학습셋 보류분 견주기(scripts/210) — 기계 지표·쌍대 판정 합치기·보고(모델 호출 없이)."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("s210", ROOT / "scripts" / "210_sft_heldout_compare.py")
s210 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s210)


def test_overlap_metrics():
    assert s210.chrf("사업단이 계획을 수립함", "사업단이 계획을 수립함") == 100.0
    assert s210.chrf("", "가나다") == 0.0
    assert 0 < s210.chrf("사업단이 계획을 세움", "사업단이 계획을 수립함") < 100
    assert s210.rouge_l("가 나 다 라", "가 다 라") > s210.rouge_l("라 다 가", "가 다 라")
    assert s210.rouge_l("가 나", "다 라") == 0.0


def test_invented_numbers_only_counts_values_missing_from_prompt():
    msgs = [{"role": "user", "content": "예산 1,200 백만원, 2027년 3월 시작"}]
    assert s210.invented_numbers("예산 1200 백만원으로 2027년 시작, 참여 45명", msgs) == ["45"]
    assert s210.invented_numbers("1. 목적\n2. 내용", msgs) == []          # 줄머리 번호는 세지 않는다
    assert s210.invented_numbers("## 3.1 추진 체계\n### 3.2 일정\n- 202X년 운영", msgs) == []   # 절 번호·연도 자리표
    assert s210.invented_numbers("목표 3.5% 달성", msgs) == ["3.5"]


def test_format_and_endings():
    t = "근거 설명: 규정에 따름\n답변: ## 계획\n| 단계 | 내용 |\n|---|---|\n| 1 | 가 |\n- 사업단이 검토함\n- 결과를 보고한다."
    f = s210.format_marks(t)
    assert f["table"] and f["gold_frame"] and f["bullet_ratio"] > 0
    e = s210.endings(t)
    assert e.get("명사형", 0) >= 1 and e.get("서술형", 0) >= 1


def test_pairwise_combines_both_orders():
    v = s210.parse_verdict('잡말 {"지시 충실": "A", "형식": "B", "근거 충실": "같음", "문체": "A", "완결성": "A", "종합": "A", "까닭": "x"}')
    assert v["종합"] == "A" and v["근거 충실"] == "같음"
    assert s210.parse_verdict("판정 없음") is None
    win_a = {"종합": "A"}
    win_b = {"종합": "B"}
    # 후보(cand)가 두 순서 모두 이김
    assert s210.combine([(win_a, "cand"), (win_b, "base")], "cand", "종합") == "win"
    # 두 순서 모두 A 자리를 고름 = 모델로는 엇갈림 → 비김
    assert s210.combine([(win_a, "cand"), (win_a, "base")], "cand", "종합") == "tie"
    assert s210.combine([(win_b, "cand"), (win_a, "base")], "cand", "종합") == "loss"
    s = s210.wlt_summary(["win", "tie", "loss", "win"])
    assert (s["win"], s["tie"], s["loss"], s["score"]) == (2, 1, 1, 0.625)
    lo, hi = s["ci95"]
    assert 0 <= lo <= 0.625 <= hi <= 1


def test_report_from_cached_runs(tmp_path):
    data = tmp_path / "test.jsonl"
    idx = tmp_path / "test-index.jsonl"
    rows = [{"prompt": [{"role": "system", "content": "s"}, {"role": "user", "content": f"질문 {i} 예산 300"}],
             "completion": [{"role": "assistant", "content": "근거 설명: 원문\n답변: 예산 300 으로 운영함"}],
             "chat_template_kwargs": {"enable_thinking": False}} for i in range(3)]
    data.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    idx.write_text("\n".join(json.dumps({"job": "j" * 12, "track": "t", "turn": i + 1}) for i in range(3)), encoding="utf-8")
    items = s210.load_items(data, idx)
    assert [s210.group_of(it) for it in items] == ["t·첫 턴", "t·이어지는 턴", "t·이어지는 턴"]
    out = tmp_path / "run"
    out.mkdir()
    for m, text in (("base", "예산 999 로 운영한다"), ("cand", "근거 설명: 원문\n답변: 예산 300 으로 운영함")):
        (out / f"gen-{m}.jsonl").write_text("\n".join(json.dumps(
            {"key": it["id"], "model": m, "text": text, "finish": "stop", "usage": {"completion_tokens": 10}, "sec": 1.0},
            ensure_ascii=False) for it in items), encoding="utf-8")
    js = []
    for it in items:
        js.append({"key": f"{it['id']}|base", "item": it["id"], "a_is": "base", "verdict": {"종합": "B", **{k: "B" for k in s210.CRITERIA}}})
        js.append({"key": f"{it['id']}|cand", "item": it["id"], "a_is": "cand", "verdict": {"종합": "A", **{k: "A" for k in s210.CRITERIA}}})
    (out / "judge-base__cand.jsonl").write_text("\n".join(json.dumps(j, ensure_ascii=False) for j in js), encoding="utf-8")
    a = NS(models=["base", "cand"], pairs=[("base", "cand")], judge_model="j", temperature=0.2, max_tokens=100, data=str(data))
    rep = s210.phase_report(items, a, out)
    p = rep["pairs"]["cand 대 base"]
    assert p["groups"]["전체"]["종합"]["win"] == 3 and p["groups"]["전체"]["종합"]["score"] == 1.0
    g = rep["mech"]["전체"]
    assert g["base"]["invented_any"] == 1.0 and g["cand"]["invented_any"] == 0.0
    assert g["cand"]["gold_frame"] == 1.0 and g["cand"]["chrf"] == 100.0
    assert p["position_bias_A_share"] == 0.5 and p["flip_rate"] == 0.0
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "질문 0" not in md and "예산 999" not in md          # 원문은 보고에 넣지 않는다


def test_models_with_own_address_and_default_pairs():
    names, urls, served = s210.parse_models("a, b, c=http://other:8002/v1", "http://main:8001/v1")
    assert names == ["a", "b", "c"] and urls["a"] == "http://main:8001/v1" and urls["c"] == "http://other:8002/v1"
    assert served == {"a": "a", "b": "b", "c": "c"}
    # 팔 이름 — 운영 교체 뒤 같은 서빙 이름(zzaimy-writer)이 다른 모델이어도 결과가 섞이지 않게
    names, urls, served = s210.parse_models("open-nvfp4:zzaimy-writer,sft-merged:writer-sft-merged=http://m:8002/v1", "http://main:8001/v1")
    assert names == ["open-nvfp4", "sft-merged"] and served["open-nvfp4"] == "zzaimy-writer"
    assert urls["sft-merged"] == "http://m:8002/v1" and served["sft-merged"] == "writer-sft-merged"


def test_pointwise_scores_parse_and_clamp():
    v = s210.parse_scores('{"지시 충실": 4, "맥락 이해": 5, "형식": 7, "근거 충실": "3", "문체": 0, "완결성": 4.4, "까닭": "x"}')
    assert v["형식"] == 5 and v["문체"] == 1 and v["근거 충실"] == 3 and v["완결성"] == 4
    assert s210.parse_scores('{"지시 충실": 4}') is None
    assert "맥락 이해" in s210.CRITERIA
