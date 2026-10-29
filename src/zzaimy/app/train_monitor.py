"""학습 상태 — DGX 학습 기록기(ops sft_capture)가 1분마다 남기는 숫자 기록을 읽어 학습 상태(진행·손실·검증·메모리·중간 저장)를 판정한다.

기록 파일(DGX ~/parsed/sft-live/<기록 폴더>/ → VM data/inbox/parsed/sft-live/, 170 --parsed 가 1분마다 받음):
- loss.csv   본학습 손실·기울기 크기·학습률(학습기가 10단계마다 찍는 값)
- eval.csv   검증 손실(학습기가 평가를 켰을 때만)
- gpu.csv    GPU 사용률·온도·전력·시스템 메모리(1분)
- status.json 진행(단계/전체·단계당 초·남은 시간)·체크포인트·실행 여부
원값 그래프는 TensorBoard(/tb/)가 보이고, 이 화면은 판정과 요약을 맡는다. 판정 기준은 일반 규칙(추세·튐·과적합·메모리 여유·멈춤)뿐이다.
"""
from __future__ import annotations

import csv
import json
import statistics
import time
from pathlib import Path

LIVE = Path("data/inbox/parsed/sft-live")
MEM_TOTAL_MIB = 124_610          # DGX 통합 메모리(free -m 총계) — 보호 장치가 여유 16GiB 아래에서 학습을 멈춘다
MEM_WARN_MIB = MEM_TOTAL_MIB - 20 * 1024


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _csv(p: Path) -> list[dict]:
    try:
        with p.open(encoding="utf-8") as fh:
            return [r for r in csv.DictReader(fh) if r.get("timestamp") != "timestamp"]
    except OSError:
        return []


def runs(live: Path | None = None) -> list[dict]:
    """기록 폴더 목록 — 최근에 갱신된 것부터."""
    live = live or LIVE
    out = []
    for d in live.glob("*/") if live.is_dir() else []:
        try:
            st = json.loads((d / "status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {}
        mt = max((f.stat().st_mtime for f in d.iterdir() if f.is_file()), default=0)
        out.append({"name": d.name, "status": st, "mtime": mt})
    return sorted(out, key=lambda r: -r["mtime"])


def load(name: str, live: Path | None = None) -> dict | None:
    live = live or LIVE
    if not name or "/" in name or name.startswith("."):
        return None
    d = live / name
    if not d.is_dir():
        return None
    try:
        st = json.loads((d / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    loss = [{"step": int(_num(r.get("step")) or 10 * (i + 1)), "loss": _num(r.get("loss")), "grad": _num(r.get("grad_norm")),
             "lr": _num(r.get("learning_rate")), "epoch": _num(r.get("epoch"))} for i, r in enumerate(_csv(d / "loss.csv"))]
    loss = [r for r in loss if r["loss"] is not None]
    ev = [{"epoch": _num(r.get("epoch")), "eval_loss": _num(r.get("eval_loss"))} for r in _csv(d / "eval.csv")]
    ev = [r for r in ev if r["eval_loss"] is not None]
    gpu = [{"t": r.get("timestamp", "")[:16], "util": _num(r.get("util_pct")), "mem": _num(r.get("sys_mem_used_mib"))} for r in _csv(d / "gpu.csv")]
    mt = max((f.stat().st_mtime for f in d.iterdir() if f.is_file()), default=0)
    return {"name": name, "status": st, "loss": loss, "eval": ev, "gpu": gpu, "mtime": mt}


def continued_by(run: dict, all_runs: list[dict]) -> str:
    """같은 결과 폴더(--out)로 나중에 시작한 기록 — 멈춘 학습을 중간 저장에서 이어 돌린 것이다. 없으면 빈 문자열."""
    out, started = (run.get("status") or {}).get("out"), (run.get("status") or {}).get("started", "")
    if not out:
        return ""
    later = [r for r in all_runs if r["name"] != run["name"] and (r.get("status") or {}).get("out") == out
             and (r.get("status") or {}).get("started", "") > started]
    return min(later, key=lambda r: r["status"]["started"])["name"] if later else ""


def judge(run: dict, now: float | None = None, all_runs: list[dict] | None = None) -> list[dict]:
    """판정 줄 — level: ok(좋음)·warn(살펴볼 것)·bad(문제)·info(아직 모름)."""
    now = now or time.time()
    st, loss, ev, gpu = run["status"], run["loss"], run["eval"], run["gpu"]
    out: list[dict] = []
    prog = st.get("progress") or {}
    running = st.get("running", False)
    nxt = "" if running else continued_by(run, all_runs or [])

    # 1. 진행·멈춤
    if nxt:
        out.append({"key": "진행", "level": "ok", "text": f"{prog.get('step', '?')}/{prog.get('total', '?')}단계에서 멈춘 뒤 중간 저장으로 재개, 「{nxt}」"})
    elif running and now - run["mtime"] > 10 * 60:
        out.append({"key": "진행", "level": "bad", "text": f"기록이 {int((now - run['mtime']) // 60)}분째 갱신 없음, 학습·장비 멈춤 확인 필요"})
    elif prog.get("step") is not None:
        txt = f"{prog['step']}/{prog['total']}단계 · 단계당 {prog.get('sec_per_step')}초 · 남은 시간 약 {prog.get('remain')}"
        out.append({"key": "진행", "level": "ok" if running else "info", "text": txt if running else txt + " (지금은 멈춰 있음)"})
    elif not running and st.get("ended"):
        out.append({"key": "진행", "level": "ok" if st.get("finished_ok") else "bad",
                    "text": "학습 완료" if st.get("finished_ok") else f"{st.get('ended')}에 끝까지 못 가고 멈춤"})
    else:
        out.append({"key": "진행", "level": "info", "text": "학습 단계 기록 없음, 모델 올리는 중이거나 시작 전"})

    # 2. 손실 추세
    if len(loss) < 3:
        out.append({"key": "손실", "level": "info", "text": f"손실 값 {len(loss)}개, 3개(약 30단계)부터 추세 판단"})
    else:
        k = max(1, min(3, len(loss) // 3))
        first = statistics.mean(r["loss"] for r in loss[:k])
        last = statistics.mean(r["loss"] for r in loss[-k:])
        change = (last - first) / first * 100 if first else 0
        if change <= -2:
            out.append({"key": "손실", "level": "ok", "text": f"처음 {first:.3f} → 최근 {last:.3f} ({change:+.0f}%), 내려가는 중"})
        elif change < 2:
            out.append({"key": "손실", "level": "warn" if len(loss) >= 10 else "info",
                        "text": f"처음 {first:.3f} → 최근 {last:.3f} ({change:+.1f}%), 변화 거의 없음"})
        else:
            out.append({"key": "손실", "level": "bad", "text": f"처음 {first:.3f} → 최근 {last:.3f} ({change:+.0f}%), 오르고 있음(학습률·데이터 확인)"})

    # 3. 기울기 튐
    grads = [r["grad"] for r in loss if r["grad"] is not None]
    if len(grads) >= 4:
        med = statistics.median(grads[:-1])
        if med and grads[-1] > 5 * med:
            out.append({"key": "안정성", "level": "warn", "text": f"최근 기울기 크기 {grads[-1]:.2f}, 평소({med:.2f})의 5배 넘게 튐"})
        else:
            out.append({"key": "안정성", "level": "ok", "text": f"기울기 크기 평소 {med:.2f} · 최근 {grads[-1]:.2f}, 튐 없음"})

    # 4. 과적합(검증 손실)
    if not ev:
        out.append({"key": "검증", "level": "info", "text": "검증 손실 없음, 학습 중 평가 꺼짐(외우기만 하는지 보려면 평가 켜기)"})
    else:
        best = min(ev, key=lambda r: r["eval_loss"])
        last = ev[-1]
        if len(ev) >= 2 and last["eval_loss"] > best["eval_loss"] * 1.02:
            out.append({"key": "검증", "level": "warn", "text": f"검증 손실 최저 {best['eval_loss']:.3f}(에폭 {best['epoch']}) → 최근 {last['eval_loss']:.3f}, 오르기 시작(외우기 의심) · 최저 근처 체크포인트가 후보"})
        else:
            out.append({"key": "검증", "level": "ok", "text": f"검증 손실 최근 {last['eval_loss']:.3f}, 최저 근처 유지"})

    # 5. 메모리 여유
    mems = [g["mem"] for g in gpu if g["mem"] is not None]
    if mems:
        cur, peak = mems[-1], max(mems)
        level = "bad" if cur > MEM_WARN_MIB else ("warn" if peak > MEM_WARN_MIB else "ok")
        out.append({"key": "메모리", "level": level,
                    "text": f"지금 {cur / 1024:.0f}GB · 최고 {peak / 1024:.0f}GB / 전체 {MEM_TOTAL_MIB / 1024:.0f}GB, 여유 20GB 아래면 보호 장치가 학습 중단"})
    cks = st.get("checkpoints") or []
    out.append({"key": "중간 저장", "level": "ok" if cks else "info",
                "text": f"{len(cks)}개 · 최근 {cks[-1]}" if cks else "아직 없음, 멈추면 처음부터 다시"})
    return out


def spark(values: list[float | None], width: int = 560, height: int = 120) -> str:
    """값 목록 → SVG polyline 점들(빈 값 건너뜀). 화면이 바깥 그림 라이브러리 없이 그린다."""
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    span = (hi - lo) or 1.0
    n = max(i for i, _ in pts) or 1
    return " ".join(f"{i / n * (width - 8) + 4:.1f},{height - 6 - (v - lo) / span * (height - 12):.1f}" for i, v in pts)
