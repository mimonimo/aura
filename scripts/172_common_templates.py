#!/usr/bin/env python3
"""문서 갈래별 공통 양식 — 계획서·실적보고서처럼 어느 사업에든 쓰는 구글 독스 작업용 뼈대.

사용자 2026-10-04: "문서별로 공통양식 — 보고서 양식, 계획서 양식 이런 식으로 각 사업에 필요한 문서이자 다른 사업에도 쓸 수 있는 것".
사업마다 들어가야 할 항목은 에이전트가 채팅 지시 때 지식 그래프의 사업 단위 절(162 가 쓰는 것)과 지난 문서를 꺼내 채운다.
이 양식은 그 바탕이다 — 특정 사업·반·학과 이름을 박지 않는다.

뼈대: 지식 그래프의 절 노드에서 그 갈래 문서의 절 제목(번호 뗀 대조 키)을 모아, 서로 다른 사업 셋 이상의 문서에 나오는 것만 남긴다
(한 사업에만 나오는 절은 그 사업의 항목이다). 부모는 그 절이 가장 자주 놓인 공통 상위 절, 순서는 문서 안 상대 위치의 중앙값,
번호는 새로 매긴다(Ⅰ. → 1. → 가. → (1)). 표는 그 절 첫 표의 머리 행 가운데 가장 많은 사업이 쓴 꼴을 머리만 남겨 둔다.
다른 대학 자료(157 의 other_org)는 뺀다. 내용은 채우지 않는다(절대 규칙 10).

출력: data/generated/templates/common/<갈래>.md (md_docx 로 docx, 169 --common 으로 드라이브)
사용(VM): env PYTHONPATH=src .venv/bin/python scripts/172_common_templates.py [--min-programs 3]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.graph import kg_store  # noqa: E402
from zzaimy.graph.sections import title_key  # noqa: E402

_spec = importlib.util.spec_from_file_location("bt162", ROOT / "scripts" / "162_business_template.py")
_bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt)
label_ok, table_skeleton, _FRONT = _bt.label_ok, _bt.table_skeleton, _bt._FRONT

KINDS = {"plan": "사업계획서", "report": "실적보고서"}
_NUM = re.compile(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.．]?|(?:I{1,3}|IV|VI{0,3}|IX|X)[.．]|\d{1,2}(?:[.\-]\d{1,2})*[.．)]?|"
                  r"[가나다라마바사아자차카타파하][.．)]|[(（]\d{1,2}[)）]|\d{1,2}\))\s*")
# 연차·연도 꼬리표 — 「추진 실적(1차년도)」의 (1차년도), 「2024년 성과」의 2024년
_TIME = re.compile(r"\s*[(（]\s*(?:\d{1,2}\s*차\s*년도|(?:19|20)\d{2}[^)）]{0,8})\s*[)）]|(?:19|20)\d{2}\s*(?:학년도|년도|년)\s*|\d{1,2}\s*차\s*년도\s*")
MARKS = [lambda i: "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ"[i] + "." if i < 10 else f"{i + 1}.",
         lambda i: f"{i + 1}.",
         lambda i: "가나다라마바사아자차카타파하"[i] + "." if i < 14 else f"{i + 1}.",
         lambda i: f"({i + 1})"]


def clean_title(label: str) -> str:
    t = _NUM.sub("", re.sub(r"\s+", " ", label or "")).strip()
    t = _TIME.sub(" ", t).strip(" ·-")
    return re.sub(r"\s+", " ", t)


def build(nodes: dict, contains: list[dict], kind: str, min_programs: int, min_docs: int) -> list[dict]:
    """갈래 하나의 공통 절 목록 — [{key, title, parent, pos, programs, docs, secs}] (부모가 앞에 오는 순서)."""
    prog_of: dict[str, str] = {}
    for e in contains:
        if e["src"].startswith("program:"):
            prog_of[e["dst"]] = e["src"]
    for e in contains:
        if e["src"].startswith("year:") and e["src"] in prog_of:
            prog_of[e["dst"]] = prog_of[e["src"]]
    by_doc: dict[str, list[str]] = defaultdict(list)
    for nid, n in nodes.items():
        if n["type"] == "section":
            by_doc[nid.split(":sec:")[0]].append(nid)
    occ: dict[str, list[dict]] = defaultdict(list)       # 대조 키 → 나온 곳들
    for d, ids in by_doc.items():
        dn = nodes.get(d)
        if not dn or dn["props"].get("kind") != kind or dn["props"].get("other_org") or d not in prog_of:
            continue
        ids.sort(key=lambda i: int(nodes[i]["props"].get("seq") or 0))
        seen: set[str] = set()
        for k, sid in enumerate(ids):
            lab = nodes[sid]["label"]
            title = clean_title(lab)
            key = title_key(title)
            if not key or key in seen or not label_ok(title) or _FRONT.search(title) or len(key) < 2:
                continue
            seen.add(key)
            path = sid.split(":sec:")[1]
            anc = []
            cur = sid
            while "." in cur.split(":sec:")[-1]:
                cur = cur.rsplit(".", 1)[0]
                if cur in nodes:
                    anc.append(title_key(clean_title(nodes[cur]["label"])))
            occ[key].append({"doc": d, "prog": prog_of[d], "sec": sid, "title": title, "depth": path.count("."),
                             "pos": k / max(len(ids) - 1, 1), "anc": anc})
    common = {k: v for k, v in occ.items()
              if len({o["prog"] for o in v}) >= min_programs and len({o["doc"] for o in v}) >= min_docs}
    out = []
    for k, v in common.items():
        parents = Counter(next((a for a in o["anc"] if a in common and a != k), "") for o in v)
        out.append({"key": k, "title": Counter(o["title"] for o in v).most_common(1)[0][0],
                    "parent": parents.most_common(1)[0][0], "pos": statistics.median(o["pos"] for o in v),
                    "programs": len({o["prog"] for o in v}), "docs": len({o["doc"] for o in v}), "secs": [o["sec"] for o in v]})
    by_key = {x["key"]: x for x in out}
    # 부모 고리 끊기(서로를 부모로 삼는 두 절) — 더 앞에 놓이는 쪽을 위로
    for x in out:
        p, hops = x["parent"], 0
        while p and hops < 8:
            if p == x["key"]:
                x["parent"] = ""
                break
            p, hops = by_key[p]["parent"] if p in by_key else "", hops + 1
    return out


def render(items: list[dict], kind_ko: str, nodes: dict, db, max_depth: int = 4) -> tuple[str, int, int]:
    kids: dict[str, list[dict]] = defaultdict(list)
    for x in items:
        kids[x["parent"]].append(x)
    for v in kids.values():
        v.sort(key=lambda x: x["pos"])
    n_prog = max((x["programs"] for x in items), default=0)
    lines = [f"# {kind_ko} 공통 양식", "",
             f"> 작성 안내: 여러 사업의 지난 {kind_ko}에 공통으로 나오는 절과 표를 모았다. 사업마다 더 들어가야 할 항목은 "
             "에이전트에게 「○○사업 ○차년도 " + kind_ko + " 초안」처럼 지시하면 그 사업의 지난 문서 구조로 채운다.", ""]
    chunk_cache: dict[int, dict] = {}
    n_sec = n_tab = 0

    def table_for(x) -> str | None:
        votes: Counter = Counter()
        progs: dict[str, set] = defaultdict(set)
        for sid in x["secs"]:
            did = int(nodes[sid.split(":sec:")[0]].get("doc_id") or 0)
            if not did:
                continue
            chunks = chunk_cache.setdefault(did, {c["seq"]: c for c in db.list_doc_chunks(did)})
            for q in nodes[sid]["props"].get("chunks") or []:
                c = chunks.get(q)
                if c and c.get("kind") == "table":
                    tb = table_skeleton(str(c["content"]))
                    if tb:
                        votes[tb] += 1
                        progs[tb].add(sid.split(":sec:")[0])
                    break
        if not votes:
            return None
        best = max(votes, key=lambda t: (len(progs[t]), votes[t]))
        return best if len(progs[best]) >= 2 else None

    def walk(parent: str, depth: int):
        nonlocal n_sec, n_tab
        for i, x in enumerate(kids.get(parent, [])):
            if depth >= max_depth:
                return
            mark = MARKS[min(depth, 3)](i)
            lines.extend([f"{'#' * min(2 + depth, 6)} {mark} {x['title']}", ""])
            lines.extend([f"> 작성 안내: 지난 {kind_ko} {x['docs']}건(사업 {x['programs']}곳)에 나온 절", ""])
            n_sec += 1
            tb = table_for(x)
            if tb:
                lines.extend([tb, ""])
                n_tab += 1
            walk(x["key"], depth + 1)
    walk("", 0)
    _ = n_prog
    return "\n".join(lines) + "\n", n_sec, n_tab


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-programs", type=int, default=3, help="서로 다른 사업 몇 곳 이상의 문서에 나와야 공통 절인가")
    ap.add_argument("--min-docs", type=int, default=5)
    ap.add_argument("--out", default=str(ROOT / "data" / "generated" / "templates" / "common"))
    args = ap.parse_args()
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    nodes = {n["id"]: n for n in kg_store.nodes(db)}
    contains = kg_store.edges(db, "contains")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {}
    for kind, kind_ko in KINDS.items():
        items = build(nodes, contains, kind, args.min_programs, args.min_docs)
        md, n_sec, n_tab = render(items, kind_ko, nodes, db)
        (out_dir / f"{kind}.md").write_text(md, encoding="utf-8")
        report[kind] = {"sections": n_sec, "tables": n_tab, "candidates": len(items)}
        print(f"{kind_ko}: 절 {n_sec} · 표 {n_tab} → {out_dir / (kind + '.md')}")
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
