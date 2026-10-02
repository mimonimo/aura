#!/usr/bin/env python3
"""새로 받은 원본을 이어서 반영 — DGX 원본 보관소 → 사업별 뼈대 문서 고르기 → 아직 안 들인 것만 VM 문서함 반입 → 그래프·양식 다시 짓기.

사용자 2026-10-02: "계속 업데이트 해야지.. 새로 받아오는중인데." 원본은 DGX 로 계속 들어온다(rclone). 이 스크립트를 되풀이해 돌리면
새 뼈대 문서만 차례로 들어온다(이미 들인 원본은 VM 원본 경로 장부 data/platform/origins.jsonl 로 건너뛴다, 156 은 내용 해시로도 건너뛴다).

운영 PC(두 서버에 SSH 가 되는 곳)에서 돈다 — DGX 에서 VM 으로 직접 가는 길이 없어 파일은 이 PC 를 거쳐 흘려 보낸다(디스크에 남기지 않음).

  python3 scripts/163_sync_core.py                 # 무엇이 새로 들어올지만
  python3 scripts/163_sync_core.py --apply         # 반입 + 그래프(157 --full) + 양식(162)
  python3 scripts/163_sync_core.py --apply --max 200   # 한 번에 최대 200건
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys

DGX = ["ssh", "-o", "BatchMode=yes", "-p", "8022", "aura@211.170.162.110"]
VM = ["ssh", "-o", "BatchMode=yes", "aura@192.168.16.226"]
PRI = {"evaluation": 0, "plan": 1, "report": 2, "basic_plan": 3, "announcement": 4, "criteria": 5, "guideline": 6}


def run(cmd: list[str], inp: bytes | None = None, check: bool = True) -> str:
    r = subprocess.run(cmd, input=inp, capture_output=True)
    if check and r.returncode != 0:
        sys.exit(f"실패: {' '.join(cmd[:4])} … {r.stderr.decode('utf-8', 'replace')[-400:]}")
    return r.stdout.decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max", type=int, default=400, help="한 번에 들일 최대 건수(우선순위: 평가·계획·보고 먼저)")
    ap.add_argument("--min-files", type=int, default=10, help="이보다 작은 사업은 건너뛴다(161 과 같음)")
    args = ap.parse_args()

    # 1) DGX: 최신 코드로 사업 분류·뼈대 고르기(본문은 열지 않음)
    run(DGX + ["cd ~/zzaimy-capstone && git pull -q --ff-only && rm -rf ~/core_selection && "
               f"PYTHONPATH=src .venv-train/bin/python scripts/161_select_core.py --root ~/data --out ~/core_selection --min-files {args.min_files} > /tmp/161.log 2>&1"])
    summary = json.loads(run(DGX + ["cat ~/core_selection/summary.json"]))
    chosen = []
    for pid, info in summary.items():
        prog = json.loads(run(DGX + [f"cat ~/core_selection/{shlex.quote(info['list'][:-4])}.json"]))
        for c in prog["chosen"]:
            rel = c["path"].split("/data/", 1)[-1]
            chosen.append({"program": pid, "name": info["name"], "kind": c["kind"], "rel": rel})
    # 2) VM: 이미 들인 원본
    led = run(VM + ["cat ~/zzaimy-capstone/data/platform/origins.jsonl 2>/dev/null || true"], check=False)
    have = set()
    for line in led.splitlines():
        try:
            have.add(json.loads(line)["origin"])
        except (ValueError, KeyError):
            continue
    new = [c for c in chosen if c["rel"] not in have]
    new.sort(key=lambda c: (PRI.get(c["kind"], 9), c["program"], c["rel"]))
    new = new[: args.max]
    by_prog: dict[str, list] = {}
    for c in new:
        by_prog.setdefault(c["program"], []).append(c)
    print(f"뼈대 {len(chosen)}건 중 이미 들인 것 {len(chosen) - len([c for c in chosen if c['rel'] not in have])}, 새로 {len(new)}건(최대 {args.max})")
    for pid, items in by_prog.items():
        print(f"  {items[0]['name'][:30]} ({pid}): {len(items)}건 — " + ", ".join(sorted({i['kind'] for i in items})))
    if not args.apply or not new:
        if not args.apply:
            print("미리 보기입니다 — --apply 로 실행")
        return 0
    # 3) 사업마다: DGX → (이 PC 를 흘러) → VM 받은 편지함, 156 반입(원본 경로 장부)
    for pid, items in by_prog.items():
        key = pid.split(":", 1)[1]
        inbox = f"data/inbox/core/{key}"
        rels = "\n".join(i["rel"] for i in items) + "\n"
        tar = subprocess.Popen(DGX + ["cd ~/data && tar -cf - -T -"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        put = subprocess.Popen(VM + [f"mkdir -p ~/zzaimy-capstone/{inbox} && tar -xf - -C ~/zzaimy-capstone/{inbox}"], stdin=tar.stdout)
        tar.stdin.write(rels.encode("utf-8"))
        tar.stdin.close()
        put.wait()
        tar.wait()
        files = " ".join(shlex.quote(f"{inbox}/{i['rel']}") for i in items)
        project = f"{items[0]['name'][:40]}"
        cmd = ("cd ~/zzaimy-capstone && set -a && . ./.env.local && set +a && "
               "ZZAIMY_ROLE_CONN=\"review=92a94f3f,vision=92a94f3f\" PYTHONPATH=src .venv/bin/python scripts/156_intake_files.py "
               f"--project {shlex.quote(project)} --sector grant --owner zzdev --origin-base {inbox} --jobs 3 --timeout 30 --apply {files}")
        out = run(VM + [cmd], check=False)
        print(f"  반입 {project[:30]}: " + (out.strip().splitlines()[-1] if out.strip() else "출력 없음"))
    # 4) 그래프 전체 다시 짓기 + 사업별 양식
    ids = run(VM + ["cd ~/zzaimy-capstone && set -a && . ./.env.local && set +a && PYTHONPATH=src .venv/bin/python -c "
                    "\"import os; from pathlib import Path; from zzaimy.app.db import Database; db=Database(Path(os.environ['ZZAIMY_PLATFORM_SQLITE_PATH']));"
                    "c=db._conn().__enter__(); r=c.execute('select min(id), max(id) from documents where id >= 557').fetchone(); print(f'{r[0]}-{r[1]}')\""]).strip()
    print("그래프:", run(VM + [f"cd ~/zzaimy-capstone && set -a && . ./.env.local && set +a && PYTHONPATH=src .venv/bin/python scripts/157_build_kg.py --docs {ids} --apply --full | tail -1"]).strip())
    print("양식:", run(VM + ["cd ~/zzaimy-capstone && set -a && . ./.env.local && set +a && PYTHONPATH=src .venv/bin/python scripts/162_business_template.py | tail -3"]).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
