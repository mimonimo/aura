#!/usr/bin/env python3
"""온톨로지 v3 그래프를 만든다 — 사업 → 연차 → 문서 → 절, 관계마다 기준·근거(ADR-0048).

  사업 분류(graph/programs): 문서 제목·앞머리의 사업명·약칭으로 사업 카드와 문서별 사업·연차·갈래(근거·검토 대기 포함).
  절 트리(graph/sections): 저장된 제목 조각으로 목차를 세우고 조각을 절에 매단다.
  관계: contains(분류·구조), plans_reports(같은 사업·연차의 계획↔실적 — 문서, 그리고 제목이 같은 절),
        evaluates(평가 결과 → 같은 연차 문서), continues(다음 연차 계획서의 같은 절 — 연차 비교).

사용(VM, .env.local 을 읽고):
  env PYTHONPATH=src .venv/bin/python scripts/157_build_kg.py --docs 557-585            # 미리 보기(분류·절·관계 수)
  ... --apply                                                                              # kg_nodes·kg_edges 에 쓴다
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app.db import Database  # noqa: E402
from zzaimy.graph import kg_store, programs, sections, units  # noqa: E402

KIND_LABEL = {"plan": "계획서", "report": "실적보고서", "evaluation": "평가 결과", "form": "양식", "criteria": "평가 기준",
              "announcement": "공고", "basic_plan": "기본계획", "guideline": "지침·매뉴얼", "regulation": "규정"}


def _ids(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part.strip():
            out.append(int(part))
    return out


# 실적보고서 안의 다음 연차 계획 부분(「차년도 사업계획」·「향후 추진 계획」) — 같은 연차 계획서와 짝짓지 않는다
NEXT_YEAR = re.compile(r"차년도\s*(?:사업\s*)?계획|향후\s*(?:추진\s*)?계획|다음\s*연도")


_HEAD_NOUN = re.compile(r"과제계획서|사업계획서|수행계획서|실적보고서|연차보고서|계획서|보고서")


def _head_noun(filename: str) -> str:
    found = _HEAD_NOUN.findall(filename or "")
    return found[-1] if found else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", required=True, help="문서 id 목록(예: 557-585,601)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--full", action="store_true", help="그래프 전체를 지우고 이 문서들로 다시 짓는다(사업·연차·단위 노드까지)")
    ap.add_argument("--generic-parent", type=float, default=0.5, help="흔한 반복 제목의 바로 위 절 제목 겹침 하한(0 이면 끔)")
    args = ap.parse_args()
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))

    # 원본 보관소(DGX) 경로 장부 — 156 --origin-base 가 적는다. 폴더 경로가 사업의 강한 단서다
    origins = {}
    led = ROOT / "data" / "platform" / "origins.jsonl"
    if led.is_file():
        for line in led.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
                origins[int(o["doc_id"])] = o["origin"]
            except (ValueError, KeyError):
                continue
    docs, chunk_map = [], {}
    for did in _ids(args.docs):
        d = db.get_document(did)
        if not d:
            continue
        chunks = db.list_doc_chunks(did)
        chunk_map[did] = chunks
        head = "\n".join(str(c["content"]) for c in chunks[:30])
        proj = db.get_project(int(d["project_id"])) if d.get("project_id") else None
        # 문서함 프로젝트(담당자가 정한 소속)는 폴더 경로처럼 강한 근거다
        origin = origins.get(did, "")
        path = str(Path(origin).parent) if origin else (proj or {}).get("name", "")
        docs.append({"id": did, "filename": d["filename"], "head": head, "path": path, "origin": origin})
    cards = programs.build_cards(docs)
    # 외부 확인 장부의 「이름 바뀜」 사실(출처 있는 것만)은 사업 카드의 다른 이름으로 — 문서만으로 약칭이 서지 않을 때(VM 뼈대 문서에 「앵커사업」 꼴이 없음)
    ext_path = ROOT / "data" / "platform" / "kg_external.json"
    ext_facts = json.loads(ext_path.read_text(encoding="utf-8")).get("facts", []) if ext_path.is_file() else []
    for f in ext_facts:
        if f.get("relation") != "renamed" or not f.get("sources"):
            continue
        terms = f.get("terms", [])
        for c in cards:
            surf = {x.upper() for x in c.surfaces()}
            if any(re.sub(r"[\s.]+", "", t).upper() in surf for t in terms):
                for t in terms:
                    c.acrs[t] += 0
                    c.acrs[t] = max(c.acrs[t], 1)
                c.renamed = list(c.renamed) + [f"외부 확인: {f.get('fact', '')[:80]}"]
    # 외부 확인 장부의 사업 체계(programs) — 문서에 아직 없는 사업도 체계의 노드로 두고, 그 이름·약칭으로 문서를 분류할 수 있게 카드를 만든다
    ledger = json.loads(ext_path.read_text(encoding="utf-8")) if ext_path.is_file() else {}
    flat = lambda t: re.sub(r"[\s.]+", "", t).upper()

    def card_for(terms: list[str]):
        want = {flat(t) for t in terms}
        for c in cards:
            if want & {x.upper() for x in c.surfaces()}:
                return c
        # 「지방 전문대학 활성화」 = 문서 카드의 「지방 전문대학 활성화 사업」 — 사업명 꼬리(사업)를 뗀 앞부분이 같으면
        for c in cards:
            for sfc in (x.upper() for x in c.surfaces()):
                core = re.sub(r"(지원)?사업$", "", sfc)
                if len(core) >= 6 and core in want:
                    return c
        return None
    ledger_cards: dict[str, object] = {}
    for e in ledger.get("programs", []):
        if not e.get("sources"):
            continue                                  # 출처 없는 항목은 쓰지 않는다
        c = card_for(e["terms"])
        if c is None:
            c = programs.ProgramCard(key=programs.program_key(e["terms"][-1]) or flat(e["terms"][0]).lower())
            cards.append(c)
        for t in e["terms"]:
            if re.match(r"[A-Za-z]", t):
                c.acrs[t] = max(c.acrs[t], 1)
            else:
                c.names[t] = max(c.names[t], 1)
        ledger_cards[e["terms"][0]] = (c, e)
    assigns = {a.doc_id: a for a in programs.classify(docs, cards)}
    used = {a.program for a in assigns.values() if a.program} | {c.node_id for c, _e in ledger_cards.values()}
    cards = [c for c in cards if c.node_id in used]

    print("== 사업 카드")
    for c in cards:
        print(f"  {c.node_id}  「{c.name}」  다른 이름 {sorted(set(c.names) | set(c.acrs))[:6]}")
    print("== 문서 분류")
    for d in docs:
        a = assigns[d["id"]]
        print(f"  #{d['id']:<4} {a.status:6} {a.program or '-':22} {a.round or '-'}차 {a.year or '-'} {KIND_LABEL.get(a.kind, a.kind or '?'):6}"
              f" 몫 {a.share:.2f}  {d['filename'][:34]}  ← {'; '.join(a.evidence[:2])}")

    nodes: list[tuple] = []
    edges: list[tuple] = []
    # 사업마다 연도 → 차수 대응을 문서에서 배운다(「2017년도(1차년도) 실적보고서」). 연도만 있는 문서도 그 차수 노드로 보내
    # 같은 연차가 r1·y2017 두 노드로 갈라지지 않게 한다(RISE r1/y2025, LINC+ r1/y2017 실측)
    year_round: dict[tuple[str, int], Counter] = defaultdict(Counter)
    for d in docs:
        a = assigns[d["id"]]
        if a.program and a.round and a.year:
            year_round[(a.program, a.year)][a.round] += 1
    for d in docs:
        a = assigns[d["id"]]
        if a.program and a.year and not a.round:
            got = year_round.get((a.program, a.year))
            if got:
                r, n = got.most_common(1)[0]
                if n >= 1 and n >= 2 * sum(v for k, v in got.items() if k != r):
                    a.round = r
                    a.evidence = list(a.evidence) + [f"{a.year}년 = {r}차년도(같은 사업 문서 {n}건)"]
    by_year: dict[str, list[int]] = defaultdict(list)
    # 외부 확인 장부(data/platform/kg_external.json) — 사업이 같은지·이름이 바뀌었는지 같은 판단을 외부 검색으로 확인한 사실과 출처.
    # 그래프 관계는 문서 근거로 만들고, 장부는 그 판단의 외부 확인으로 붙인다(사용자 2026-10-02: 판단이 필요하면 외부 검색으로 도움)
    ext = []
    ext_path = ROOT / "data" / "platform" / "kg_external.json"
    if ext_path.is_file():
        ext = json.loads(ext_path.read_text(encoding="utf-8")).get("facts", [])
    for c in cards:
        props = {"names": sorted(c.names), "acronyms": sorted(c.acrs)}
        if c.renamed:
            props["renamed_evidence"] = c.renamed
        surf = {re.sub(r"[\s.]+", "", x).upper() for x in list(c.names) + list(c.acrs)}
        facts = [f for f in ext if all(any(re.sub(r"[\s.]+", "", t).upper() in s_ for s_ in surf) for t in f.get("terms", []))]
        if facts:
            props["external"] = facts
        nodes.append((c.node_id, "program", c.name, props, None))
    # 사업 체계 — 분류(일반재정지원·앵커·특수목적) → 사업, 사업 → 앵커 편입(연도), 앞 단계 → 다음 단계
    cats = ledger.get("categories", {})
    for key, (c, e) in ledger_cards.items():
        info = {k: e[k] for k in ("period", "status", "yncu", "since", "note") if e.get(k)}
        if info:
            for n_ in nodes:
                if n_[0] == c.node_id:
                    n_[3]["ledger"] = info | {"sources": e["sources"]}
        cat = e.get("category")
        if cat:
            gid = f"group:{cat}"
            if not any(n_[0] == gid for n_ in nodes):
                nodes.append((gid, "program_group", cats.get(cat, {}).get("label", cat), {"sources": cats.get(cat, {}).get("sources", [])}, None))
            edges.append((gid, c.node_id, "contains", "분류", [f"외부 확인: {cats.get(cat, {}).get('label', cat)}"] + e["sources"][:2]))
        if e.get("integrated_into"):
            parent = card_for([e["integrated_into"]])
            if parent is not None and parent is not c:
                edges.append((c.node_id, parent.node_id, "integrated_into", "분류",
                              [f"{e.get('since', '')}년부터 편입" + (f" — {e['status']}" if e.get("status") else "")] + e["sources"][:2]))
        for pred in e.get("predecessor", []):
            pc = card_for([pred])
            if pc is not None and pc is not c:
                edges.append((pc.node_id, c.node_id, "succeeded_by", "분류", [f"앞 단계 사업 → 다음 단계({e.get('period', '')})"] + e["sources"][:2]))
    for d in docs:
        a = assigns[d["id"]]
        dnode = f"doc:{d['id']}"
        nodes.append((dnode, "doc", d["filename"], {"kind": a.kind, "kind_label": KIND_LABEL.get(a.kind, a.kind), "year": a.year,
                                                    "round": a.round, "share": a.share, "status": a.status,
                                                    "evidence": a.evidence}, d["id"]))
        if a.program:
            tag = f"{a.round}차년도" if a.round else (str(a.year) if a.year else "")
            if tag:
                ynode = f"year:{a.program.split(':', 1)[1]}:" + (f"r{a.round}" if a.round else f"y{a.year}")
                label = f"{a.program_name} {tag}" + (f" ({a.year})" if a.round and a.year else "")
                nodes.append((ynode, "year", label, {"round": a.round, "year": a.year}, None))
                edges.append((a.program, ynode, "contains", "분류", [f"문서 #{d['id']} 분류: " + "; ".join(a.evidence[:2])]))
                edges.append((ynode, dnode, "contains", "분류", a.evidence[:3] or ["분류"]))
                by_year[ynode].append(d["id"])
            else:
                edges.append((a.program, dnode, "contains", "분류", a.evidence[:3] or ["분류"]))
        secs = sections.build(chunk_map[d["id"]])
        d["sections"] = secs
        for s in secs:
            snode = f"{dnode}:sec:{s.path}"
            nodes.append((snode, "section", s.title, {"level": s.level, "seq": s.seq, "chunks": s.chunks}, d["id"]))
            parent = f"{dnode}:sec:{s.parent}" if s.parent else dnode
            edges.append((parent, snode, "contains", "구조", [f"목차: {s.title}"]))
    docs_by_id = {d["id"]: d for d in docs}
    content = {(did, int(c["seq"])): str(c["content"]) for did, chunks in chunk_map.items() for c in chunks}
    sec_doc = {id(s): d["id"] for d in docs for s in d["sections"]}

    def text_of(s) -> str:
        """절 본문 앞부분(낱말 겹침용)."""
        return " ".join(content.get((sec_doc.get(id(s)), q), "")[:300] for q in s.chunks[:6])

    def text_full(s) -> str:
        """절 본문 전체(본문으로 잇기용, 4000자까지)."""
        return " ".join(content.get((sec_doc.get(id(s)), q), "") for q in s.chunks)[:4000]
    code_sections: dict[str, list] = defaultdict(list)       # 과제 코드 → 그 코드를 단 보고서 절
    # 같은 사업·연차의 계획 ↔ 실적, 평가 → 대상
    for ynode, ids in by_year.items():
        plans = [i for i in ids if assigns[i].kind == "plan"]
        reports = [i for i in ids if assigns[i].kind == "report"]
        evals = [i for i in ids if assigns[i].kind == "evaluation"]
        for p in plans:
            for r in reports:
                edges.append((f"doc:{p}", f"doc:{r}", "plans_reports", "식별자 일치", [f"같은 사업·연차({ynode})의 계획서와 실적보고서"]))
                linked_b = set()
                for ps, s, why in sections.align_context(docs_by_id[p]["sections"], docs_by_id[r]["sections"], text_of, generic_parent=args.generic_parent,
                                                         skip_b=NEXT_YEAR):
                    linked_b.add(s.path)
                    edges.append((f"doc:{p}:sec:{ps.path}", f"doc:{r}:sec:{s.path}", "plans_reports", "식별자 일치",
                                  [f"계획 「{ps.title[:60]}」", f"실적 「{s.title[:60]}」", why]))
                # 제목 짝이 없는 보고서 절은 본문으로(목차 틀이 다른 계획서·보고서)
                for ps, s, why in sections.align_content(docs_by_id[p]["sections"], docs_by_id[r]["sections"], text_full,
                                                         skip_b_paths=linked_b, skip_b=NEXT_YEAR):
                    edges.append((f"doc:{p}:sec:{ps.path}", f"doc:{r}:sec:{s.path}", "plans_reports", "유사도",
                                  [f"계획 「{ps.title[:60]}」", f"실적 「{s.title[:60]}」", why]))
        # 과제 코드로 잇기 — 과제마다 계획서를 내고 연차보고서는 「[2-3 과제] 추진 실적」처럼 과제 코드를 단 절로 쓰는 사업(RISE).
        # 계획서 파일 이름의 코드와 보고서 절 제목의 코드가 같으면 계획서(문서) ↔ 그 보고서 절
        # 코드 붙은 계획서 중 가장 흔한 머리 낱말(과제계획서)의 문서만 그 과제의 계획서다 — 「[2-3] 환경개선공사 사업계획서」 같은 관련 계획은 아니다
        heads = Counter(_head_noun(docs_by_id[p]["filename"]) for p in plans if units.doc_code(docs_by_id[p]["filename"]))
        main_head = heads.most_common(1)[0][0] if heads else ""
        for p in plans:
            fname = docs_by_id[p]["filename"]
            code = units.doc_code(fname)
            if not code or len(units._UNIT_CODE.findall(fname)) > 1 or _head_noun(fname) != main_head:
                continue
            rx = re.compile(rf"(?<![\d.\-]){re.escape(code)}(?![\d.\-])")
            for r in reports:
                rsecs = docs_by_id[r]["sections"]
                for s in rsecs:
                    if rx.search(s.title) and len(s.title) <= 60:
                        code_sections[code].append((r, s.path, s.title))
                        # 과제 코드로 계획서를 좁힌 뒤, 보고서 절 제목의 핵심 낱말(예산·성과지표·추진)을 가진 계획서 절과만 잇는다
                        # (「예산 집행 실적」 ↔ 「예산 운용」). 맞는 측면 절이 없으면(우수사례) 단위과제 노드로만 묶는다 —
                        # 계획서 전체 ↔ 보고서 한 측면은 '같은 것의 계획과 실적'이 아니다(판정 all7: 23%)
                        ps, why = sections.aspect_match(docs_by_id[p]["sections"], s.title)
                        if ps is not None:
                            edges.append((f"doc:{p}:sec:{ps.path}", f"doc:{r}:sec:{s.path}", "plans_reports", "식별자 일치",
                                          [f"과제 코드 {code}: 「{fname[:40]}」 ↔ 보고서 절 「{s.title[:50]}」", why]))
        for e in evals:
            for t in plans + reports:
                edges.append((f"doc:{e}", f"doc:{t}", "evaluates", "분류", [f"같은 사업·연차({ynode})의 평가 결과"]))
    # 연차 비교 — 다음 연차 계획서의 같은 절
    prog_years = defaultdict(list)
    for ynode in by_year:
        prog, key = ynode.rsplit(":", 1)
        prog_years[(prog, key[0])].append((int(key[1:]), ynode))      # 차수끼리(r)·연도끼리(y)만 잇는다
    for prog, ys in prog_years.items():
        ys.sort()
        for (k1, y1), (k2, y2) in zip(ys, ys[1:]):
            for p1 in (i for i in by_year[y1] if assigns[i].kind == "plan"):
                for p2 in (i for i in by_year[y2] if assigns[i].kind == "plan"):
                    for s, t, why in sections.align_context(docs_by_id[p1]["sections"], docs_by_id[p2]["sections"], text_of, generic_parent=args.generic_parent):
                        edges.append((f"doc:{p1}:sec:{s.path}", f"doc:{p2}:sec:{t.path}", "continues", "식별자 일치",
                                      [f"{y1} 「{s.title[:50]}」", f"{y2} 「{t.title[:50]}」", why]))

    # 사업별 일의 단위 — 둘 이상의 문서에 같은 제목으로 나오는 절(평가지표 항목·장·과제)을 단위 노드로
    per_prog: dict[str, list] = defaultdict(list)
    for d in docs:
        a = assigns[d["id"]]
        if a.program and d.get("sections"):
            per_prog[a.program].append((d["id"], d["sections"]))
    for prog, items in per_prog.items():
        codes = {did: c for did, _s in items if (c := units.doc_code(docs_by_id[did]["filename"]))}
        # 코드마다 문서 둘 이상(연차·판본)이고 그런 코드가 셋 이상일 때만 코드를 단위로 쓴다 — 과제마다 문서를 내는 사업(RISE)의 꼴.
        # 우연히 번호가 붙은 파일 몇 개로 다른 사업의 단위를 쪼개지 않는다
        cnt = Counter(codes.values())
        kept_codes = {c for c, n in cnt.items() if n >= 2}
        codes = {d: c for d, c in codes.items() if c in kept_codes} if len(kept_codes) >= 3 else {}
        built = units.build(items, codes=codes)
        unode_of = {u.key: u.node_id(prog) for u in built}
        for u in built:
            unode = unode_of[u.key]
            nodes.append((unode, "unit", u.label, {"key": u.key, "n_docs": len(u.docs), "n_sections": len(u.members)}, None))
            parent_key = u.key.split("/", 1)[0] if "/" in u.key else ""
            if parent_key and parent_key in unode_of:
                edges.append((unode_of[parent_key], unode, "contains", "식별자 일치", [f"단위과제 {parent_key[1:]} 문서들의 같은 절 「{u.label[:60]}」"]))
            else:
                edges.append((prog, unode, "contains", "식별자 일치", [f"문서 {len(u.docs)}건에 같은 제목의 절 「{u.label[:60]}」"]
                              if not u.key.startswith("#") else [f"파일 이름의 단위과제 코드 {u.key[1:]} — 문서 {len(u.docs)}건"]))
            if u.key.startswith("#") and "/" not in u.key:
                for did in u.docs:
                    edges.append((f"doc:{did}", unode, "instance_of", "식별자 일치", [f"파일 이름에 단위과제 코드 {u.key[1:]}"]))
                for r, path, title in sorted(set(code_sections.get(u.key[1:], []))):
                    edges.append((f"doc:{r}:sec:{path}", unode, "instance_of", "식별자 일치", [f"보고서 절 「{title[:50]}」에 과제 코드 {u.key[1:]}"]))
            for did, path in u.members:
                edges.append((f"doc:{did}:sec:{path}", unode, "instance_of", "식별자 일치", [f"절 제목이 단위 「{u.label[:60]}」와 같음"]))

    print("== 그래프")
    print("  노드", dict(Counter(n[1] for n in nodes)))
    print("  관계", dict(Counter((e[2], e[3]) for e in edges)))
    review = [d["id"] for d in docs if assigns[d["id"]].status != "auto"]
    print("  검토 대기 문서", review or "없음")
    if not args.apply:
        print("미리 보기입니다 — --apply 로 쓴다")
        return 0
    kg_store.ensure(db)
    with db._conn() as conn:
        if args.full:
            # 전체 다시 짓기 — 문서 노드에 붙은 연차·사업 관계와 사업·연차·단위 노드는 clear_doc 이 지우지 않아 옛 빌드의 것(옛 연차 노드
            # y2025, 옛 사업 id)이 쌓였다(2026-10-02 실측). 그래프 전체를 지우고 쓴다
            conn.execute("DELETE FROM kg_edges")
            conn.execute("DELETE FROM kg_nodes")
        for d in docs:
            kg_store.clear_doc(conn, f"doc:{d['id']}")
            conn.execute("DELETE FROM kg_edges WHERE src = ? OR dst = ?", (f"doc:{d['id']}", f"doc:{d['id']}"))
        for n in nodes:
            kg_store.put_node(conn, *n)
        for e in edges:
            kg_store.put_edge(conn, *e)
    print(f"썼다 — 노드 {len(nodes)} · 관계 {len(edges)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
