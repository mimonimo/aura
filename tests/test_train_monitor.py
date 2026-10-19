"""학습 상태 판정(app/train_monitor) — 손실 추세·튐·과적합·메모리·멈춤을 일반 기준으로."""
from __future__ import annotations

import json
import time

from zzaimy.app import train_monitor as tm


def _run(tmp_path, losses, evals=(), mem=60000, running=True, age=0):
    d = tmp_path / "sft-capture-x"
    d.mkdir(parents=True, exist_ok=True)
    (d / "loss.csv").write_text("epoch,loss,grad_norm,learning_rate,step\n" + "".join(
        f"0.1,{l},{g},0.0001,{10 * (i + 1)}\n" for i, (l, g) in enumerate(losses)))
    (d / "eval.csv").write_text("epoch,eval_loss\n" + "".join(f"{i},{e}\n" for i, e in enumerate(evals)))
    (d / "gpu.csv").write_text("timestamp,util_pct,mem_mib,temp_c,power_w,sys_mem_used_mib\n2026/10/18 18:00:00.0,95,[N/A],60,40," + str(mem) + "\n")
    (d / "status.json").write_text(json.dumps({"running": running, "progress": {"step": 30, "total": 556, "sec_per_step": 500, "remain": "70:00:00"},
                                               "checkpoints": ["checkpoint-5"]}))
    r = tm.load("sft-capture-x", tmp_path)
    r["mtime"] = time.time() - age
    return {c["key"]: c for c in tm.judge(r)}


def test_falling_loss_is_ok_and_no_eval_is_unknown(tmp_path):
    c = _run(tmp_path, [(2.0, 1.0), (1.8, 1.1), (1.6, 0.9), (1.4, 1.0), (1.3, 1.0), (1.2, 0.95)])
    assert c["손실"]["level"] == "ok" and c["검증"]["level"] == "info" and c["안정성"]["level"] == "ok"
    assert c["진행"]["level"] == "ok" and c["메모리"]["level"] == "ok" and c["중간 저장"]["level"] == "ok"


def test_rising_loss_spike_overfit_memory_stall(tmp_path):
    c = _run(tmp_path, [(1.0, 1.0), (1.0, 1.0), (1.1, 1.0), (1.3, 1.0), (1.4, 1.0), (1.5, 9.0)],
             evals=(1.2, 1.0, 1.1), mem=110_000, age=20 * 60)
    assert c["손실"]["level"] == "bad" and c["안정성"]["level"] == "warn" and c["검증"]["level"] == "warn"
    assert c["메모리"]["level"] == "bad" and c["진행"]["level"] == "bad"


def test_path_guard_and_spark():
    assert tm.load("../x") is None and tm.spark([1.0]) == "" and len(tm.spark([1.0, 2.0, None, 3.0]).split()) == 3


def test_stopped_run_continued_by_resume():
    """같은 결과 폴더로 나중에 시작한 기록이 있으면 멈춤이 아니라 「이어 돌림」 으로 본다."""
    from zzaimy.app import train_monitor as tm
    a = {"name": "r7", "status": {"out": "/x/adapter", "started": "2026-10-18 18:03", "running": False, "ended": "2026-10-19 11:19",
                                  "progress": {"step": 113, "total": 556}}, "loss": [], "eval": [], "gpu": [], "mtime": 0}
    b = {"name": "r7-resume-110", "status": {"out": "/x/adapter", "started": "2026-10-19 18:43", "running": True}}
    got = tm.judge(a, now=1.0, all_runs=[b, a])
    assert got[0]["level"] == "ok" and "r7-resume-110" in got[0]["text"]
    assert tm.continued_by(a, [a]) == ""
