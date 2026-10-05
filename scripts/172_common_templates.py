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

# 문서 갈래 — 연차 사업계획서·실적보고서는 사업마다 대표 뼈대 하나씩(문서가 많은 사업으로 쏠리지 않게), 프로그램 단위 서류는
# 파일 제목으로 고르고 사업마다 상한. 10/5 첫 판들: 절 수로만 가르면 기자재 구입 서식·표지 띠가 「공통」을 차지했다
_ANNUAL = re.compile(r"사업\s*계획서|실적\s*보고서|성과\s*보고서|결과\s*보고서|자체\s*평가|수행\s*계획서")
_PROG_PLAN = re.compile(r"계획")                     # 절 3~25개 조건과 함께 — 운영·실시·추진·개최 계획(안) 등 작은 계획서
_PROG_REPORT = re.compile(r"결과\s*보고|운영\s*결과|실시\s*결과|개최\s*결과")
GENRES = [
    ("annual_plan", "plan", "연차 사업계획서", {"title": _ANNUAL, "min_sec": 30, "one_per_program": True}),
    ("annual_report", "report", "연차 실적보고서", {"title": _ANNUAL, "min_sec": 30, "one_per_program": True}),
    ("program_plan", "plan", "프로그램 운영계획서", {"title": _PROG_PLAN, "min_sec": 3, "max_sec": 25, "cap": 30}),
    ("program_report", "report", "프로그램 결과보고서", {"title": _PROG_REPORT, "min_sec": 3, "max_sec": 25, "cap": 30}),
]
_ORG_ONLY = re.compile(r"^[가-힣]{2,12}(?:대학교|대학|재단|공사|센터)$")
# 양식의 절이 아닌 것 — 목차·표지·붙임 표시·회사 이름
_SKIP_TITLE = re.compile(r"^(?:목\s*차|차\s*례|contents|표\s*지|붙\s*임|별\s*첨|참\s*고|첨\s*부)", re.I)
_ORG = re.compile(r"㈜|\(주\)|주식회사")
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


def build(nodes: dict, contains: list[dict], kind: str, min_programs: int, min_docs: int,
          size_ok=lambda n: True, min_share: float = 0.0, spec: dict | None = None) -> list[dict]:
    """갈래 하나의 공통 절 목록 — [{key, title, parent, pos, programs, docs, secs}] (부모가 앞에 오는 순서).
    size_ok: 문서의 절 수로 고르는 크기 조건, min_share: 그 갈래 문서 가운데 이 비율 이상에 나와야 공통 절."""
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
    n_docs = 0
    spec = spec or {}
    # 대상 문서 고르기 — 갈래·크기·제목, 사업마다 대표 하나(one_per_program) 또는 상한(cap)
    cand: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for d, ids in by_doc.items():
        dn = nodes.get(d)
        if not dn or dn["props"].get("kind") != kind or dn["props"].get("other_org") or d not in prog_of or not size_ok(len(ids)):
            continue
        if len(ids) < spec.get("min_sec", 0) or len(ids) > spec.get("max_sec", 10 ** 6):
            continue
        if spec.get("title") is not None and not spec["title"].search(dn["label"] or ""):
            continue
        cand[prog_of[d]].append((len(ids), d))
    chosen: set[str] = set()
    for pid, lst in cand.items():
        lst.sort(key=lambda t: (not (nodes[t[1]]["label"] or "").lower().endswith(".pdf"), t[0]), reverse=True)
        take = lst[:1] if spec.get("one_per_program") else lst[: spec.get("cap", len(lst))]
        chosen.update(d for _n, d in take)
    for d, ids in by_doc.items():
        if d not in chosen:
            continue
        n_docs += 1
        ids.sort(key=lambda i: int(nodes[i]["props"].get("seq") or 0))
        seen: set[str] = set()
        for k, sid in enumerate(ids):
            lab = nodes[sid]["label"]
            title = clean_title(lab)
            key = title_key(title)
            if not key or key in seen or not label_ok(title) or _FRONT.search(title) or len(key) < 2 \
                    or _SKIP_TITLE.search(title) or _ORG.search(title) or _ORG_ONLY.match(title.replace(" ", "")):
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
    if spec.get("one_per_program"):                     # 사업마다 하나 — 사업 셋 이상이고 뼈대가 있는 사업의 30% 이상
        min_share = max(min_share, 0.3)
    need_docs = max(min_docs, int(min_share * n_docs + 0.999))
    common = {k: v for k, v in occ.items()
              if len({o["prog"] for o in v}) >= min_programs and len({o["doc"] for o in v}) >= need_docs}
    out = []
    for k, v in common.items():
        parents = Counter(next((a for a in o["anc"] if a in common and a != k), "") for o in v)
        out.append({"key": k, "title": Counter(o["title"] for o in v).most_common(1)[0][0],
                    "parent": parents.most_common(1)[0][0], "pos": statistics.median(o["pos"] for o in v),
                    "programs": len({o["prog"] for o in v}), "docs": len({o["doc"] for o in v}), "secs": [o["sec"] for o in v],
                    "prog_of": {o["sec"]: o["prog"] for o in v}})
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
    n_sec = n_tab = 0

    def table_for(x, max_secs: int = 40) -> str | None:
        """그 절 첫 표의 머리 — 사업마다 고르게 최대 max_secs 절만 본다(문서마다 조각 전체를 읽으면 수십 분, 10/5 실측)."""
        votes: Counter = Counter()
        progs: dict[str, set] = defaultdict(set)
        by_prog: dict[str, list[str]] = defaultdict(list)
        for sid in x["secs"]:
            by_prog[x.get("prog_of", {}).get(sid, "")].append(sid)
        picked: list[str] = []
        while len(picked) < max_secs and any(by_prog.values()):
            for k in list(by_prog):
                if by_prog[k]:
                    picked.append(by_prog[k].pop(0))
        for sid in picked[:max_secs]:
            did = int(nodes[sid.split(":sec:")[0]].get("doc_id") or 0)
            seqs = [int(q) for q in (nodes[sid]["props"].get("chunks") or [])][:30]
            if not did or not seqs:
                continue
            with db._conn() as conn:
                rows = conn.execute(f"SELECT seq, kind, content FROM doc_chunks WHERE doc_id = ? AND seq IN ({','.join('?' * len(seqs))})"
                                    " ORDER BY seq", (did, *seqs)).fetchall()
            for r in rows:
                if r[1] == "table":
                    tb = table_skeleton(str(r[2]))
                    if tb:
                        votes[tb] += 1
                        progs[tb].add(sid.split(":sec:")[0])
                    break
        if not votes:
            return None
        best = max(votes, key=lambda t: (len(progs[t]), votes[t]))
        # 머리 꼴이 같은 문서가 셋 이상, 사업 둘 이상 — 한 사람·한 반의 값이 머리 행처럼 잡힌 표(이름·학과)를 양식에 넣지 않는다
        sec_prog = x.get("prog_of", {})
        n_prog = len({sec_prog.get(s_) for s_ in x["secs"] if s_.split(":sec:")[0] in progs[best]})
        return best if len(progs[best]) >= 3 and n_prog >= 2 else None

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


# ── 사업 공통 구조(27B 묶기 + 코드 검증) ─────────────────────────────────────────────
# 제목이 똑같은 절만 공통으로 세면 사업마다 다른 말(「추진 배경」「사업 필요성」)을 놓쳐 양식이 비었다(10/5: 연차 계획서 절 0).
# 사업별 뼈대를 모델에 보여 뜻이 같은 절을 묶게 하고, 모델이 댄 출처(사업 번호·그 사업의 제목 그대로)를 코드가 뼈대와 대조해
# 사업 셋 이상이 확인된 절만 남긴다. 모델은 구조만 다룬다 — 수치·내용을 만들지 않는다(절대 규칙 1·10).
CONSENSUS_PROMPT = """아래는 여러 정부 재정지원사업에서 실제로 쓴 {kind_ko}의 목차(사업별)이다.
사업이 달라도 같은 내용을 다루는 절(예: 「추진 배경」·「사업 필요성」·「추진 목적」)을 하나로 묶어, 어느 사업에든 쓸 수 있는
{kind_ko} 공통 목차를 만들어라.

규칙:
- 서로 다른 사업 셋 이상에 뜻이 같은 절이 있을 때만 공통 절로 넣는다
- 공통 절 제목은 특정 사업·학과·반·기관 이름 없이 일반 명칭으로(예: 「사업 개요」, 「추진 체계」, 「성과지표 및 목표」)
- level 1 은 장, level 2 는 그 장 안의 절. 장 안에서 여러 사업이 함께 쓰는 절(예: 「추진 배경」·「목표」·「추진 일정」)도
  셋 이상 사업에 있으면 level 2 로 넣는다. 순서는 여러 사업 목차에서 흔한 순서를 따른다
- sources 에는 그 절에 해당하는 각 사업의 번호(p)와 그 사업 목차의 제목을 글자 그대로(t) 적는다
- 내용·수치는 쓰지 않는다

출력은 JSON 하나만:
{{"sections": [{{"title": "...", "level": 1, "sources": [{{"p": 1, "t": "..."}}]}}]}}

{skeletons}"""


def md_skeleton(text: str, max_items: int = 70) -> list[dict]:
    """사업별 양식 md(162) → [{level, title, table}] — 「##」 장, 「###」 절, 바로 아래 첫 표."""
    out: list[dict] = []
    for line in text.splitlines():
        m = re.match(r"^(#{2,3})\s+(.*)$", line)
        if m:
            title = clean_title(m.group(2))
            if title and not title.startswith("(항목") and len(out) < max_items:
                out.append({"level": len(m.group(1)) - 1, "title": title, "table": None})
            continue
        if line.startswith("<table") and out and out[-1]["table"] is None:
            out[-1]["table"] = line.strip()
    return out


def _norm(t: str) -> str:
    return title_key(clean_title(t or ""))


def consensus(skeletons: list[tuple[str, list[dict]]], kind_ko: str, ask, min_programs: int = 3) -> list[dict]:
    """skeletons: [(사업 이름, 뼈대)], ask(prompt) → 모델 응답 글. 검증을 통과한 공통 절 [{title, level, programs, table}]."""
    blocks = []
    for i, (name, sk) in enumerate(skeletons, 1):
        lines = [("  " if it["level"] == 2 else "") + it["title"] for it in sk]
        blocks.append(f"[사업 {i}]\n" + "\n".join(lines))
    raw = ask(CONSENSUS_PROMPT.format(kind_ko=kind_ko, skeletons="\n\n".join(blocks)))
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S)
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        got = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        got = {}
    keys = [{_norm(it["title"]): it for it in sk} for _n, sk in skeletons]
    out = []
    for sec in got.get("sections") or []:
        title = clean_title(str(sec.get("title") or ""))
        if not title or not label_ok(title) or _ORG.search(title):
            continue
        ok: dict[int, dict] = {}
        for src in sec.get("sources") or []:
            try:
                p = int(src.get("p")) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= p < len(keys):
                hit = keys[p].get(_norm(str(src.get("t") or "")))
                if hit and p not in ok:
                    ok[p] = hit                              # 모델이 댄 제목이 그 사업 뼈대에 실제로 있다
        if len(ok) < min_programs:
            continue
        table = shared_table([h.get("table") for h in ok.values()])
        out.append({"title": title, "level": 2 if int(sec.get("level") or 1) >= 2 else 1, "programs": len(ok),
                    "from": [skeletons[p][0] for p in sorted(ok)], "table": table})
    return out


_DATED = re.compile(r"[‘'’]\s*\d{2}|\d\s*차\s*년도|(?:19|20)\d{2}")


def _heads(table: str) -> set[str]:
    return {re.sub(r"\s+", "", h) for h in re.findall(r"<th[^>]*>(.*?)</th>", table or "") if h.strip()}


def shared_table(tables: list[str | None], min_programs: int = 2, sim: float = 0.6) -> str | None:
    """공통 양식의 표 — 머리 칸 구성이 비슷한(겹침 sim 이상) 표를 사업 min_programs 곳 이상이 쓸 때만, 그중 가장 흔한 꼴.
    특정 연도·차년도가 박힌 머리(「1차년도(‘22.3∼‘23.2)」)는 한 사업의 표라 넣지 않는다(10/5: LINC3.0 지표 표가 공통 양식에)."""
    cands = [t for t in tables if t and not any(_DATED.search(h) for h in _heads(t))]
    best, best_n = None, 0
    for t in cands:
        ht = _heads(t)
        n = sum(1 for u in cands if ht and len(ht & _heads(u)) / max(len(ht | _heads(u)), 1) >= sim)
        if n > best_n:
            best, best_n = t, n
    return best if best_n >= min_programs else None


def render_consensus(items: list[dict], kind_ko: str, n_programs: int) -> tuple[str, int, int]:
    lines = [f"# {kind_ko} 공통 양식", "",
             f"> 작성 안내: 사업 {n_programs}곳의 지난 {kind_ko} 목차에서 셋 이상이 함께 쓰는 절을 묶었다. 사업마다 더 들어가야 할 항목은 "
             f"에이전트에게 「○○사업 ○차년도 {kind_ko} 초안」처럼 지시하면 그 사업의 지난 문서 구조로 채운다.", ""]
    n_sec = n_tab = 0
    i1 = i2 = 0
    for it in items:
        if it["level"] == 1:
            i1, i2 = i1 + 1, 0
            mark = MARKS[0](i1 - 1)
            lines.append(f"## {mark} {it['title']}")
        else:
            i2 += 1
            mark = MARKS[1](i2 - 1)
            lines.append(f"### {mark} {it['title']}")
        lines += ["", f"> 작성 안내: 사업 {it['programs']}곳에 있는 절 — 예: {', '.join(it['from'][:4])}", ""]
        n_sec += 1
        if it.get("table"):
            lines += [it["table"], ""]
            n_tab += 1
    return "\n".join(lines) + "\n", n_sec, n_tab


def _ask_model():
    from zzaimy.generate.client import VllmClient
    client = VllmClient(role="review")

    def ask(prompt: str) -> str:
        resp = client.client.with_options(timeout=600).chat.completions.create(
            model=client.model, messages=[{"role": "user", "content": prompt}], temperature=0, max_tokens=6000,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}})
        return resp.choices[0].message.content or ""
    return ask


def program_doc_skeletons(db, kind: str, title_rx, per_program: int = 2, limit: int = 24) -> list[tuple[str, list[dict]]]:
    """프로그램 단위 서류(운영계획·결과보고) 뼈대 — 사업마다 절이 많은 순으로 per_program 건, 절 3~25개, 표 머리 포함."""
    nodes = {n["id"]: n for n in kg_store.nodes(db, "doc")}
    progs = {n["id"]: n["label"] for n in kg_store.nodes(db, "program")}
    prog_of: dict[str, str] = {}
    with db._conn() as conn:
        rows = conn.execute("SELECT src, dst FROM kg_edges WHERE kind = 'contains' AND (src LIKE 'program:%' OR src LIKE 'year:%')"
                            " AND dst NOT LIKE '%:sec:%'").fetchall()
    year_prog = {r[1]: r[0] for r in rows if r[0].startswith("program:") and r[1].startswith("year:")}
    for src, dst in rows:
        if dst.startswith("doc:"):
            p = src if src in progs else year_prog.get(src)
            if p:
                prog_of[dst] = p
    by_prog: dict[str, list[tuple[int, str, list]]] = defaultdict(list)
    for did, n in nodes.items():
        if did not in prog_of or n["props"].get("kind") != kind or n["props"].get("other_org") or not title_rx.search(n["label"] or ""):
            continue
        with db._conn() as conn:
            secs = conn.execute("SELECT id, label, props FROM kg_nodes WHERE type = 'section' AND doc_id = ?", (n["doc_id"],)).fetchall()
        if 3 <= len(secs) <= 25:
            by_prog[prog_of[did]].append((len(secs), did, secs))
    out = []
    for pid, lst in sorted(by_prog.items(), key=lambda kv: -len(kv[1])):
        for _n, did, secs in sorted(lst, key=lambda t: -t[0])[:per_program]:
            secs = sorted(secs, key=lambda r: int(json.loads(r[2] or "{}").get("seq") or 0))
            sk = []
            for sid, label, props in secs:
                depth = sid.split(":sec:")[1].count(".")
                title = clean_title(label)
                if depth > 1 or not title or not label_ok(title):
                    continue
                table = None
                seqs = [int(q) for q in (json.loads(props or "{}").get("chunks") or [])][:20]
                if seqs:
                    with db._conn() as conn:
                        t = conn.execute(f"SELECT content FROM doc_chunks WHERE doc_id = ? AND kind = 'table' AND seq IN ({','.join('?' * len(seqs))})"
                                         " ORDER BY seq LIMIT 1", (nodes[did]["doc_id"], *seqs)).fetchone()
                    table = table_skeleton(str(t[0])) if t else None
                sk.append({"level": depth + 1, "title": title, "table": table})
            if len(sk) >= 3:
                out.append((progs.get(pid, pid), sk))
        if len(out) >= limit:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-programs", type=int, default=3, help="서로 다른 사업 몇 곳 이상에 있어야 공통 절인가")
    ap.add_argument("--templates", default=str(ROOT / "data" / "generated" / "templates"), help="사업별 양식(162) 폴더")
    ap.add_argument("--out", default=str(ROOT / "data" / "generated" / "templates" / "common"))
    args = ap.parse_args()
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ask = _ask_model()
    report = {}
    jobs = []
    for kind, kind_ko, name in (("plan", "연차 사업계획서", "annual_plan"), ("report", "연차 실적보고서", "annual_report")):
        sks = []
        for f in sorted(Path(args.templates).glob(f"*_{kind}.md")):
            text = f.read_text(encoding="utf-8")
            label = re.sub(r"^#\s*|\s*(계획서|실적보고서)\s*공통 양식\s*$", "", text.splitlines()[0]) if text else f.stem
            sk = md_skeleton(text)
            if len(sk) >= 8:
                sks.append((label, sk))
        jobs.append((name, kind_ko, sorted(sks, key=lambda t: -len(t[1]))[:20]))
    for kind, kind_ko, name, rx in (("plan", "프로그램 운영계획서", "program_plan", _PROG_PLAN),
                                    ("report", "프로그램 결과보고서", "program_report", _PROG_REPORT)):
        jobs.append((name, kind_ko, program_doc_skeletons(db, kind, rx)))
    import hashlib
    cache_path = out_dir / ".replies.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    for name, kind_ko, sks in jobs:
        # 모델 응답은 입력(뼈대)별로 저장 — 입력이 같으면 모델을 부르지 않고, 검증·표 고르기·쓰기는 매번 다시(규칙을 고치면 바로 반영)
        digest = hashlib.sha256(json.dumps(sks, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

        def cached_ask(prompt: str, _d=digest) -> str:
            if _d not in cache:
                cache[_d] = ask(prompt)
            return cache[_d]
        try:
            items = consensus(sks, kind_ko, cached_ask, args.min_programs) if len(sks) >= args.min_programs else []
        except Exception as e:                          # 모델이 안 되면 그 갈래는 옛 양식을 그대로 둔다
            print(f"{kind_ko}: 모델 묶기 실패({type(e).__name__}) — 옛 양식 유지", flush=True)
            continue
        md, n_sec, n_tab = render_consensus(items, kind_ko, len(sks))
        (out_dir / f"{name}.md").write_text(md, encoding="utf-8")
        report[name] = {"title": kind_ko, "sections": n_sec, "tables": n_tab, "programs": len(sks)}
        print(f"{kind_ko}: 뼈대 {len(sks)}개 → 공통 절 {n_sec} · 표 {n_tab}", flush=True)
    live = {hashlib.sha256(json.dumps(sks, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest() for _n, _k, sks in jobs}
    cache_path.write_text(json.dumps({k: v for k, v in cache.items() if k in live}, ensure_ascii=False), encoding="utf-8")
    for old in ("plan", "report"):
        for ext in (".md", ".docx"):
            (out_dir / f"{old}{ext}").unlink(missing_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / ".inputs.json").unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
