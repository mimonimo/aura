#!/usr/bin/env python3
"""학습셋 목록 — DGX 의 학습 준비 폴더(data/train 의 staging·releases·baselines)에서 설명 정보만 모아 ~/parsed/trainsets.json 에 쓴다.

왜: 학습셋은 DGX 에서 만들고 승인한다(데이터 원문은 DGX 밖으로 나가지 않는다). VM 의 데이터·학습 화면은 DGX 를 직접 볼 수 없어
「승인된 학습 데이터 없음」으로 보였다. 이 목록은 이름·상태·건수·갈래별 수·지문(sha256)·승인 범위·한계·학습 시작 여부만 담고,
VM 이 처리 결과와 같은 길(170 --parsed, ~/parsed)로 5분마다 받아 화면에 보인다. 문답 원문·근거 글은 담지 않는다.
DGX 주기 처리(zz_dgx_cycle.sh)가 돌린다. 사용(DGX): python3 scripts/197_trainset_catalog.py [--root DIR ...] [--out ~/parsed/trainsets.json]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import time
from pathlib import Path

KEEP = ("status", "scope", "meaning", "model", "measured_at", "seq_len", "required_before_training", "training_started", "hold_reason")


def _read(p: Path) -> dict:
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _when(p: Path) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))


def _rows(p: Path) -> int:
    with p.open("rb") as fh:
        return sum(1 for line in fh if line.strip())


def staging(d: Path) -> dict:
    m = _read(d / "manifest.json")
    counts = m.get("counts") or {}
    out = {"kind": "staging", "name": d.name, "at": _when(d / "manifest.json"), **{k: m[k] for k in KEEP if k in m},
           "splits": {s: {"rows": (c or {}).get("rows"), "tracks": (c or {}).get("tracks") or {}, "sha256": (c or {}).get("sha256")}
                      for s, c in counts.items() if isinstance(c, dict)},
           "files": [{"name": f.name, "bytes": f.stat().st_size, "rows": _rows(f)}
                     for f in sorted(d.glob("*.jsonl")) if not f.name.endswith("-index.jsonl")]}
    audit = _read(d / "token-audit-8192.json")
    if audit:
        out["token_audit"] = {"status": audit.get("status"), "seq_len": audit.get("seq_len")}
    return out


def release(p: Path) -> dict:
    m = _read(p)
    rv = m.get("review") or {}
    return {"kind": "release", "name": p.stem, "at": _when(p), **{k: m[k] for k in KEEP if k in m},
            "counts": m.get("counts") or {}, "train_sha256": m.get("train_sha256"),
            "limitations": str(rv.get("limitations") or "")[:600]}


def baseline(p: Path) -> dict:
    m = _read(p)
    j = m.get("judgment") or {}
    return {"kind": "baseline", "name": p.parent.name + "/" + p.stem, "at": _when(p), **{k: m[k] for k in KEEP if k in m},
            "n_eval": m.get("n_eval"), "pass": j.get("pass"), "needs_fix": j.get("needs_fix"),
            "limitations": str(j.get("limitations") or "")[:400]}


def _running_roots() -> str:
    """돌고 있는 학습기(83 … --out) 명령줄 — 어느 준비 폴더의 학습인지 가리는 데만 쓴다."""
    try:
        out = subprocess.run(["pgrep", "-af", "83_sft_writer_qlora.py"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return "\n".join(l for l in out.splitlines() if "--out" in l and "--check" not in l)


def training_state(root: Path, running: str) -> str:
    """준비 폴더의 학습 상태 — running(학습기 실행 중) · done(어댑터 저장됨) · stopped(중간 저장만 있고 학습기 없음) · ""(시작 전)."""
    if str(root.parent.parent) + "/" in running:
        return "running"
    out = root / "models"
    if any(out.glob("*/*/adapter_model.safetensors")):
        return "done"
    if any(out.glob("*/*/checkpoint-*")):
        return "stopped"
    return ""


def _newest(root: Path) -> float:
    files = list((root / "releases").glob("*.json")) + list((root / "staging").glob("*/manifest.json"))
    return max((f.stat().st_mtime for f in files), default=0.0)


def collect(roots: list[Path]) -> list[dict]:
    """준비 폴더마다 학습셋·승인 기록·학습 전 측정. 가장 최근에 고친 폴더만 current — 화면은 이전 판을 접어 둔다.
    학습셋은 같은 폴더의 SFT 승인 기록과 학습 파일 지문이 같으면 approved_by 로 잇는다(승인은 releases 에 따로 적히므로)."""
    items: list[dict] = []
    running = _running_roots()
    current = max(roots, key=_newest) if roots else None
    for root in roots:
        tag = {"root": root.parent.parent.name, "current": root == current}
        rel = [{**release(p), **tag, "training": training_state(root, running)} for p in sorted((root / "releases").glob("*.json"))]
        ok = {r.get("train_sha256"): r["name"] for r in rel if r.get("status") == "sft_approved" and r.get("train_sha256")}
        for d in sorted((root / "staging").glob("*/")):
            if (d / "manifest.json").is_file():
                st = {**staging(d), **tag}
                sha = ((st.get("splits") or {}).get("train") or {}).get("sha256")
                if sha in ok:
                    st["approved_by"] = ok[sha]
                items.append(st)
        items += rel
        for p in sorted((root / "baselines").glob("*/baseline.json")):
            items.append({**baseline(p), **tag})
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", default=[], help="data/train 폴더(여럿 가능) — 기본은 ~/sft-ready-*/data/train 과 저장소의 data/train")
    ap.add_argument("--out", default=str(Path.home() / "parsed" / "trainsets.json"))
    args = ap.parse_args()
    roots = [Path(r) for r in args.root] or [Path(p) for p in sorted(glob.glob(os.path.expanduser("~/sft-ready-*/data/train")))
                                             + [os.path.expanduser("~/zzaimy-capstone/data/train")] if Path(p).is_dir()]
    items = collect(roots)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M"), "host": "DGX", "items": items}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(out)
    print(f"TRAINSETS {len(items)}개 → {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
