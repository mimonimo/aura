#!/usr/bin/env python3
"""VM 혼자 도는 원본 갱신 — DGX 원본 보관소를 읽기 전용 rsync 로 보고(파일 내용은 고른 것만), 원본 목록 장부·사업 분류·뼈대 반입·그래프·양식을 갱신한다.

사용자 2026-10-02: "계속 업데이트 해야지.. 새로 받아오는중인데", "지금 계정으로는 안되는거야?" — DGX aura 계정에 VM 전용 키를
「rrsync -ro ~/data」로 묶어 등록했다(명령 실행·쓰기 불가, 확인함). 그래서 운영 PC 없이 VM 의 cron 으로 돈다.

순서: ① rsync --list-only 로 경로·크기·시각 ② 원본 목록 장부(app/archive) ③ 사업 분류·뼈대 고르기(scripts/161 규칙)
      ④ 아직 안 들인 뼈대만 rsync 로 받아 받은 편지함에 ⑤ 164(반입·그래프 --full·양식·docx)
겹쳐 돌지 않게 cron 에서 flock 으로 감싼다.

사용(VM): env PYTHONPATH=src .venv/bin/python scripts/170_vm_sync.py [--max 200] [--dry]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import archive  # noqa: E402
from zzaimy.app.db import Database  # noqa: E402
from zzaimy.graph import programs  # noqa: E402

KEY = str(Path.home() / ".ssh" / "id_ed25519_dgx_ro")
REMOTE = "aura@211.170.162.110:"
RSH = f"ssh -i {KEY} -p 8022 -o BatchMode=yes"
_LINE = re.compile(r"^-\S+\s+([\d,]+)\s+(\d{4}/\d{2}/\d{2})\s+(\d{2}:\d{2}:\d{2})\s+(.+)$")
PRI = {"evaluation": 0, "plan": 1, "report": 2, "basic_plan": 3, "announcement": 4, "criteria": 5, "guideline": 6}


def _sel():
    spec = importlib.util.spec_from_file_location("select_core", ROOT / "scripts" / "161_select_core.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def listing() -> list[dict]:
    out = subprocess.run(["rsync", "-e", RSH, "-r", "--list-only", REMOTE + "./"], capture_output=True, check=True).stdout.decode("utf-8", "replace")
    files = []
    for line in out.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        rel = m.group(4)
        name = rel.rsplit("/", 1)[-1]
        if name.startswith(".") or name.startswith("~$"):
            continue
        mt = int(time.mktime(time.strptime(m.group(2) + " " + m.group(3), "%Y/%m/%d %H:%M:%S")))
        files.append({"rel": rel, "filename": name, "size": int(m.group(1).replace(",", "")), "mtime": mt,
                      "ext": name.rsplit(".", 1)[-1].lower() if "." in name else "", "area": rel.split("/", 1)[0],
                      "path": rel.rsplit("/", 1)[0] if "/" in rel else ""})
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=200)
    ap.add_argument("--min-files", type=int, default=10)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    files = listing()
    first: dict = {}
    for i, f in enumerate(files):
        f["id"] = i
        f["head"] = ""
        key = (f["filename"], f["size"])
        f["dup_of"] = first.get(key, "")
        first.setdefault(key, f["rel"])
    docs = [f for f in files if not f["dup_of"]]
    cards = programs.build_cards(docs)
    res = {a.doc_id: a for a in programs.classify(docs, cards)}
    programs.inherit_by_folder(docs, [res[d["id"]] for d in docs])
    by_rel = {d["rel"]: res[d["id"]] for d in docs}
    rows = []
    for f in files:
        a = by_rel.get(f["dup_of"] or f["rel"])
        r = {k: f[k] for k in ("rel", "size", "mtime", "ext", "area", "dup_of")}
        if a is not None:
            r.update({"program": a.program, "program_name": a.program_name, "status": a.status, "kind": a.kind, "year": a.year, "round": a.round})
        rows.append(r)
    origins = {}
    led = ROOT / "data" / "platform" / "origins.jsonl"
    if led.is_file():
        for line in led.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
                origins[o["origin"]] = int(o["doc_id"])
            except (ValueError, KeyError):
                continue
    if not args.dry:
        archive.load(db, rows, origins)
    print(f"원본 {len(files)} · 중복 제외 {len(docs)} · 사업 카드 {len(cards)} · 목록 {time.time() - t0:.0f}초", flush=True)
    # 뼈대 고르기 — 161 과 같은 규칙(사업마다 갈래·연차·줄기별 최신판)
    sel = _sel()
    per_prog = defaultdict(list)
    for d in docs:
        a = by_rel[d["rel"]]
        if a.program:
            per_prog[a.program].append((d, a))
    chosen = []
    for pid, items in per_prog.items():
        if len(items) < args.min_files:
            continue
        card = next((c for c in cards if c.node_id == pid), None)
        surfaces = card.surfaces() if card else set()
        groups = defaultdict(list)
        for d, a in items:
            ext = d["ext"]
            if a.kind not in sel.CORE or ext not in sel.DOC_RANK or sel.EVIDENCE.search(d["rel"]) or not sel.is_core(d, a.kind, surfaces):
                continue
            when = a.round or a.year or (programs._PATH_YEAR.findall(d["path"]) or [""])[-1]
            groups[(a.kind, str(when), sel.stem(d["filename"]))].append((d, a))
        for cands in groups.values():
            best = max(cands, key=lambda t: (sel.latest_key(t[0]), -sel.DOC_RANK[t[0]["ext"]]))
            chosen.append({"program": pid, "name": best[1].program_name, "kind": best[1].kind, "rel": best[0]["rel"]})
    new = [c for c in chosen if c["rel"] not in origins]
    new.sort(key=lambda c: (PRI.get(c["kind"], 9), c["program"], c["rel"]))
    new = new[: args.max]
    print(f"뼈대 {len(chosen)} · 새로 {len(new)}(최대 {args.max})", flush=True)
    if args.dry or not new:
        return 0
    by_prog = defaultdict(list)
    for c in new:
        by_prog[c["program"]].append(c)
    manifest = []
    for pid, items in by_prog.items():
        inbox = f"data/inbox/core/{pid.split(':', 1)[1]}"
        (ROOT / inbox).mkdir(parents=True, exist_ok=True)
        lst = ROOT / inbox / ".files-from"
        lst.write_text("\n".join(i["rel"] for i in items) + "\n", encoding="utf-8")
        subprocess.run(["rsync", "-e", RSH, "-a", "--files-from", str(lst), REMOTE + "./", str(ROOT / inbox) + "/"], check=False)
        manifest.append({"program": pid, "project": items[0]["name"][:40], "inbox": inbox, "files": [i["rel"] for i in items]})
        print(f"  받음 {items[0]['name'][:30]}: {len(items)}건", flush=True)
    (ROOT / "data" / "inbox" / "core" / "sync_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return subprocess.call([sys.executable, str(ROOT / "scripts" / "164_sync_apply.py")], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
