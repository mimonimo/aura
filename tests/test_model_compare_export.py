"""모델 견주기 결과 묶기(scripts/212) — 가짜 결과 폴더로 summary·samples·docs·html 이 화면 꼴대로 나오는지."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("s212", ROOT / "scripts" / "212_model_compare_export.py")
s212 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s212)

DIMS = ["지시 충실", "맥락 이해", "형식", "근거 충실", "문체", "완결성"]


def _jl(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


def test_mask_names_and_contacts():
    t = s212.mask("담당자: 홍길동, 김철수 교수, 010-1234-5678, a.b@ync.ac.kr")
    assert "홍길동" not in t and "김철수" not in t and "1234" not in t and "ync.ac.kr" not in t
    assert "교수" in t


def test_export_writes_admin_files(tmp_path):
    work = tmp_path / "work"
    rows = [{"prompt": [{"role": "system", "content": "s"}, {"role": "user", "content": f"질문 {i} 홍길동 팀장"}],
             "completion": [{"role": "assistant", "content": "정답"}], "chat_template_kwargs": {}} for i in range(4)]
    _jl(work / "test.jsonl", rows)
    _jl(work / "test-index.jsonl", [{"job": "j", "track": "t", "turn": 1 + i % 2} for i in range(4)])
    for arm, txt in (("open-nvfp4", "답 가"), ("sft-merged", "답 나")):
        _jl(work / "test-run" / f"gen-{arm}.jsonl",
            [{"key": f"test-{i:03d}", "arm": arm, "text": txt, "finish": "stop", "usage": {"completion_tokens": 5}, "sec": 1} for i in range(4)])
        _jl(work / "test-run" / f"score-{arm}.jsonl",
            [{"key": f"test-{i:03d}", "arm": arm, "scores": {**{d: 3 + (arm == "sft-merged") for d in DIMS}, "까닭": "x"}} for i in range(4)])
        _jl(work / "test-run" / f"speed-{arm}.jsonl", [{"key": "test-000", "ttft": 0.5, "tok_per_sec": 20, "sec": 3, "usage": {"completion_tokens": 50}}])
    _jl(work / "test-run" / "judge-open-nvfp4__sft-merged.jsonl",
        [r for i in range(4) for r in (
            {"key": f"test-{i:03d}|open-nvfp4", "item": f"test-{i:03d}", "a_is": "open-nvfp4", "verdict": {"종합": "B", **{d: "B" for d in DIMS}}},
            {"key": f"test-{i:03d}|sft-merged", "item": f"test-{i:03d}", "a_is": "sft-merged", "verdict": {"종합": "A", **{d: "A" for d in DIMS}}})])
    ctx = work / "context-run"
    ctx.mkdir(parents=True)
    (ctx / "cases.json").write_text(json.dumps([{"id": "cf-00", "kind": "conflict", "messages": [{"role": "system", "content": "s"},
                                                {"role": "user", "content": "표"}], "expect": {}}], ensure_ascii=False), encoding="utf-8")
    (ctx / "report.json").write_text(json.dumps({"arms": {"open-nvfp4": {"conflict": {"n": 1, "ok": 1, "rate": 1.0, "invented_any": 0.0}}},
                                                 "rows": [{"id": "cf-00", "kind": "conflict", "arm": "open-nvfp4", "ok": True}]}), encoding="utf-8")
    _jl(ctx / "gen-open-nvfp4.jsonl", [{"key": "cf-00", "text": "| 총사업비 | 확인 필요 |"}])
    dest = tmp_path / "dest"
    html_out = tmp_path / "page" / "index.html"
    rc = s212.main(["--run-id", "r1", "--work", str(work), "--dest", str(dest), "--html-out", str(html_out),
                    "--arm", "open-nvfp4|오픈|zzaimy-writer|토르 02 :8001|open",
                    "--arm", "sft-merged|병합|writer-sft-merged|토르 02 :8002|sft-merged"])
    assert rc == 0
    s = json.loads((dest / "r1" / "summary.json").read_text(encoding="utf-8"))
    assert [a["kind"] for a in s["arms"]] == ["open", "sft-merged"]
    assert s["labels"]["chrf"] == "정답 겹침(chrF)" and "invented_any" in s["percent_keys"]
    assert s212.fmt("invented_any", 0.164) == "16.4%" and s212.fmt("sec", 37.129) == "37.13"
    blocks = {b["key"]: b for b in s["blocks"]}
    assert set(blocks) >= {"heldout220", "context", "speed"}
    pw = blocks["heldout220"]["pairwise"][0]
    assert (pw["a"], pw["b"], pw["win"], pw["n"]) == ("sft-merged", "open-nvfp4", 4, 4)
    assert blocks["heldout220"]["per_dimension"]["형식"] == {"open-nvfp4": 3.0, "sft-merged": 4.0}
    samples = json.loads((dest / "r1" / "samples.json").read_text(encoding="utf-8"))
    assert samples and all("홍길동" not in x["input"] for x in samples)
    assert json.loads((dest / "r1" / "docs.json").read_text(encoding="utf-8")) == []
    page = html_out.read_text(encoding="utf-8")
    assert "<title>모델 비교 보고</title>" in page and "192.168" not in page
    assert "질문에 없는 숫자가 든 답 비율" in page and ">gold_frame<" not in page
    assert not list((dest / "r1").glob("*.tmp"))
