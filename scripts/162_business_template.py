#!/usr/bin/env python3
"""사업별 공통 양식 — 지식 그래프의 단위(여러 연차 문서에 되풀이되는 절)로 사업 × 문서 갈래(계획서·실적보고서)의 공통 뼈대를 만든다.

사용자 2026-10-02: "사업 문서들을 보고 구글독스에서 작업하기 편한 공통 양식을 생성해서 제공". 내용은 채우지 않는다 — 절 제목과
작성 안내(그 절이 나온 지난 문서·연차)만 둔다. 정답을 채워 찍어 내지 않는다(working-notes 9/27, 절대 규칙 10).

뼈대: 단위(unit)마다 그 갈래 문서 둘 이상에 나온 것만, 문서 안 상대 위치의 중앙값 순서로, 깊이는 원문 절 깊이의 최빈값.
출력: data/generated/templates/<사업>_<갈래>.md (md_docx 로 docx·독스로 이어 쓴다).

사용(VM): env PYTHONPATH=src .venv/bin/python scripts/162_business_template.py [--program program:linc30] [--min-docs 2]
"""
from __future__ import annotations

import argparse
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

KIND_KO = {"plan": "계획서", "report": "실적보고서"}
# 양식의 절 제목은 짧은 명사구만 — 동의서·서약 문장(「본인은 …」·「과제의 선정에 관한 사무: …」)이 개요 번호를 달고 절로 잡힌 것은 뺀다
_SENTENCE = re.compile(r"(?:다|함|음|임|됨|요|니다)\s*[.。]?\s*$|^본인|동의|서약|개인\s*정보|[:：]")
LABEL_MAX = 40
BODY_ROWS = 2


_FRONT = re.compile(r"동의서|서약|확약|귀하|개인\s*정보|청렴|보안\s*각서")
_NUMBERED = re.compile(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|(?:I{1,3}|IV|VI{0,3}|IX|X)[.．]|\d{1,2}(?:[.\-]\d{1,2})*[.．)]|[가나다라마바사아자차카타파하][.．)]|\(\d{1,2}\))")


def label_ok(label: str) -> bool:
    t = (label or "").strip()
    return 2 <= len(t) <= LABEL_MAX and not _SENTENCE.search(t) and not re.fullmatch(r"[\d\s.~\-]+", t)


def table_skeleton(content: str) -> str | None:
    """표 조각(JSON) → 머리 행만 남긴 HTML 표(병합 칸 그대로) + 빈 입력 행. 값은 비운다(양식이지 답이 아니다)."""
    import json as _json
    try:
        t = _json.loads(content)
    except ValueError:
        return None
    n_rows, n_cols = int(t.get("n_rows") or 0), int(t.get("n_cols") or 0)
    cells = t.get("cells") or []
    if n_rows < 2 or n_cols < 2 or n_cols > 12:
        return None
    head_rows = {int(c[0]) for c in cells if len(c) >= 6 and int(c[4] or 0)}
    depth = (max(head_rows) + 1) if head_rows else max((int(c[2]) for c in cells if int(c[0]) == 0), default=1)
    depth = max(1, min(depth, 3, n_rows - 1))
    heads = [c for c in cells if int(c[0]) < depth]
    if len(heads) == 1 and int(heads[0][3]) >= n_cols:
        return None                                     # 한 칸이 모든 열을 덮는 띠(표지·제목 상자)
    texts = [" ".join(str(c[5]).split())[:30] for c in heads]
    if not any(texts) or sum(bool(re.fullmatch(r"[\d,.%\s\-]+", x)) for x in texts if x) > len(texts) / 2:
        return None                                     # 머리가 비었거나 숫자뿐이면(값 행) 양식 표가 아니다
    rows = []
    for r in range(depth):
        tds = []
        for c in sorted((c for c in heads if int(c[0]) == r), key=lambda c: int(c[1])):
            rs = min(int(c[2]), depth - r)
            attrs = (f' rowspan="{rs}"' if rs > 1 else "") + (f' colspan="{int(c[3])}"' if int(c[3]) > 1 else "")
            tds.append(f"<th{attrs}>{' '.join(str(c[5]).split())[:30]}</th>")
        rows.append("<tr>" + "".join(tds) + "</tr>")
    for _ in range(BODY_ROWS):
        rows.append("<tr>" + "<td></td>" * n_cols + "</tr>")
    return "<table>" + "".join(rows) + "</table>"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", default="", help="사업 노드 id(없으면 전부)")
    ap.add_argument("--min-docs", type=int, default=2)
    ap.add_argument("--out", default=str(ROOT / "data" / "generated" / "templates"))
    args = ap.parse_args()
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    nodes = {n["id"]: n for n in kg_store.nodes(db)}
    contains = kg_store.edges(db, "contains")
    prog_of: dict[str, str] = {}
    for e in contains:
        if e["src"].startswith("program:"):
            prog_of[e["dst"]] = e["src"]
    for e in contains:
        if e["src"].startswith("year:") and e["src"] in prog_of:
            prog_of[e["dst"]] = prog_of[e["src"]]
    year_of = {e["dst"]: nodes[e["src"]]["label"] for e in contains if e["src"].startswith("year:") and e["src"] in nodes}
    # 문서마다 절 순서(상대 위치)
    order: dict[str, list[str]] = defaultdict(list)
    for nid, n in nodes.items():
        if n["type"] == "section":
            order[nid.split(":sec:")[0]].append(nid)
    pos: dict[str, float] = {}
    for d, ids in order.items():
        ids.sort(key=lambda i: int(nodes[i]["props"].get("seq") or 0))
        for k, i in enumerate(ids):
            pos[i] = k / max(len(ids) - 1, 1)
    members: dict[str, list[str]] = defaultdict(list)
    for e in kg_store.edges(db, "instance_of"):
        if ":sec:" in e["src"]:
            members[e["dst"]].append(e["src"])
    chunk_cache: dict[int, dict] = {}

    def ancestor_ids(sec_id: str) -> list[str]:
        out, cur = [], sec_id
        while "." in cur.split(":sec:")[-1]:
            cur = cur.rsplit(".", 1)[0]
            if cur in nodes:
                out.append(cur)
        return out
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    unit_of: dict[str, str] = {}
    for uid, secs in members.items():
        for x in secs:
            unit_of[x] = uid
    progs = [p for p in nodes.values() if p["type"] == "program" and (not args.program or p["id"] == args.program)]
    for prog in progs:
        pkey = prog["id"].split(":", 1)[1]
        for kind, kind_ko in KIND_KO.items():
            docs = [d for d, ids in order.items() if prog_of.get(d) == prog["id"] and nodes[d]["props"].get("kind") == kind]
            if not docs:
                continue
            # 뼈대 = 단위 절을 가장 많이 가진 문서(같으면 최근) — 원문 목차 그대로의 계층을 쓴다
            def n_units(d):
                return sum(1 for x in order[d] if unit_of.get(x, "").startswith(f"unit:{pkey}:"))
            skel = max(docs, key=lambda d: (n_units(d), int(nodes[d].get("doc_id") or 0)))
            ids = order[skel]
            keep: set[str] = set()
            for x in ids:
                uid = unit_of.get(x, "")
                if not uid.startswith(f"unit:{pkey}:") or not label_ok(nodes[uid]["label"]):
                    continue
                chain = [x] + [a for a in ancestor_ids(x)]
                if any(_FRONT.search(nodes[c]["label"]) for c in chain):
                    continue
                top = chain[-1]
                # 로마 숫자·아라비아 숫자 장 아래만 — 표지·별첨 묶음, 맨 위 가나다 항목(서약서의 「나. … 사무」)은 양식 절이 아니다
                if not re.match(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|(?:I{1,3}|IV|VI{0,3}|IX|X)[.．]|\d{1,2}[.．)])", nodes[top]["label"]):
                    continue
                keep.update(chain)
            if len(keep) < 3:
                continue
            lines = [f"# {prog['label']} {kind_ko} 공통 양식", "",
                     f"지난 {kind_ko}들에 되풀이되는 절(지식 그래프의 단위)을 「{nodes[skel]['label'][:50]}」의 목차 순서로 놓았다. "
                     "내용은 비워 두었다 — 각 절의 안내는 그 절이 나온 연차다.", ""]
            base = min(nodes[x]["id"].split(":sec:")[1].count(".") for x in keep)
            n_sec = 0
            last_title = ""
            for x in ids:
                if x not in keep:
                    continue
                if nodes[x]["label"] == last_title:
                    continue                              # 같은 제목이 잇달아 나오면(목차·간지 되풀이) 한 번만
                last_title = nodes[x]["label"]
                depth = x.split(":sec:")[1].count(".") - base
                title = re.sub(r"\s+", " ", nodes[x]["label"]).strip()[:LABEL_MAX + 20]
                lines += [f"{'#' * min(2 + depth, 6)} {title}", ""]
                n_sec += 1
                uid = unit_of.get(x, "")
                if uid.startswith(f"unit:{pkey}:"):
                    years = []
                    for s_ in members[uid]:
                        y = year_of.get(s_.split(":sec:")[0], "")
                        m = re.search(r"([1-9])차년도", y) or re.search(r"(20\d{2})", y)
                        if m and nodes.get(s_.split(":sec:")[0], {}).get("props", {}).get("kind") == kind:
                            years.append(m.group(0))
                    if years:
                        lines += [f"> 작성 안내: 지난 {kind_ko}의 같은 절 — {', '.join(sorted(set(years)))}", ""]
                # 이 절의 첫 표를 양식 표로(머리 행만, 값은 비움)
                ldoc = int(nodes[skel].get("doc_id") or 0)
                chunks = chunk_cache.setdefault(ldoc, {c["seq"]: c for c in db.list_doc_chunks(ldoc)})
                for q in nodes[x]["props"].get("chunks") or []:
                    c = chunks.get(q)
                    if c and c.get("kind") == "table":
                        tb = table_skeleton(str(c["content"]))
                        if tb:
                            lines += [tb, ""]
                            break
            safe = re.sub(r"[^0-9A-Za-z가-힣]+", "_", pkey)
            dest = out_dir / f"{safe}_{kind}.md"
            dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"{prog['label'][:30]} {kind_ko}: 절 {n_sec} (뼈대 #{nodes[skel].get('doc_id')}) → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
