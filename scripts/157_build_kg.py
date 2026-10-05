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
from zzaimy.graph import indicators, kg_store, programs, sections, units  # noqa: E402

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
# 절 첫머리가 밝힌 연차 — 「4차년도」, 사업 기간 「(’23.3.1~’24.2.29)」의 시작 연도
_ROUND_IN = re.compile(r"(?<!\d)([1-9])\s*차\s*년도")
_PERIOD_IN = re.compile(r"[‘'’]\s*(\d{2})\s*\.\s*\d{1,2}(?:\s*\.\s*\d{1,2})?\s*[~∼\-]")


def stated_years(text: str) -> tuple[set, set]:
    t = (text or "")[:500]
    return {int(x) for x in _ROUND_IN.findall(t)}, {int(x) for x in _PERIOD_IN.findall(t)}


def year_conflict(a: str, b: str) -> bool:
    """두 절이 밝힌 연차가 서로 다르면 같은 것의 계획과 실적이 아니다 — 같은 제목 「4. 사업 예산집행 계획」이 계획서는 4차년도,
    보고서는 3차년도를 다루던 짝(판정 big_c8: 남긴 짝 오류 11건 중 다수). 한쪽이라도 밝히지 않으면 판단하지 않는다."""
    ra, pa = stated_years(a)
    rb, pb = stated_years(b)
    if ra and rb and not (ra & rb):
        return True
    return bool(pa and pb and not (pa & pb))


ASPECT_BODY_MIN = 0.15          # 판정 all10 보정: 성과지표 0.02~0.04·예산 0.09~0.15(모두 다름), 추진 실적 0.15~0.24(대부분 같음)
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
    # 문서·프로젝트·조각을 묶어서 읽는다 — 문서마다 세 번씩 DB 를 오가면 3만 건에 10만 번(10/4 실측: 재구축이 몇 시간)
    want_ids = list(_ids(args.docs))
    doc_rows: dict[int, dict] = {}
    with db._conn() as conn:
        for i in range(0, len(want_ids), 1000):
            part = want_ids[i:i + 1000]
            for r in conn.execute(f"SELECT id, filename, stored_path, project_id FROM documents WHERE id IN ({','.join('?' * len(part))})",
                                  part).fetchall():
                doc_rows[int(r[0])] = {"filename": r[1], "stored_path": r[2], "project_id": r[3]}
            for r in conn.execute(f"SELECT * FROM doc_chunks WHERE doc_id IN ({','.join('?' * len(part))}) ORDER BY doc_id, seq",
                                  part).fetchall():
                r = dict(r)
                chunk_map.setdefault(int(r["doc_id"]), []).append(r)
        proj_names = {int(r[0]): r[1] for r in conn.execute("SELECT id, name FROM projects").fetchall()}
    for did in want_ids:
        d = doc_rows.get(did)
        if not d:
            continue
        chunks = chunk_map.setdefault(did, [])
        head = "\n".join(str(c["content"]) for c in chunks[:30])
        proj = {"name": proj_names.get(int(d["project_id"]), "")} if d.get("project_id") else None
        # 문서함 프로젝트(담당자가 정한 소속)는 폴더 경로처럼 강한 근거다
        origin = origins.get(did, "")
        path = str(Path(origin).parent) if origin else (proj or {}).get("name", "")
        docs.append({"id": did, "filename": d["filename"], "head": head, "path": path, "origin": origin,
                     "light": str(d.get("stored_path") or "").startswith("dgx://")})
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

    # 장부 항목 ↔ 카드는 주인 판정(가장 긴 표기)으로 — 앞 단계 이름이 섞인 카드(LINC+ 카드의 「산학협력 선도전문대학」)에
    # 앞 단계 항목(LINC 1단계)이 붙지 않게. 겹치기만 하고 주인이 아니면 새 카드(170 과 같은 규칙, programs.ledger_link)
    pre_link = programs.ledger_link(cards, ledger)

    def card_for(terms: list[str]):
        want = {flat(t) for t in terms}
        owner = pre_link["owner_of"].get(terms[0])
        if owner:
            return next((c for c in cards if c.node_id == owner), None)
        if terms[0] in pre_link["matched"]:
            return None
        # 「지방 전문대학 활성화」 = 문서 카드의 「지방 전문대학 활성화 사업」 — 사업명 꼬리(사업)를 뗀 앞부분이 같으면
        for c in cards:
            for sfc in (x.upper() for x in c.surfaces()):
                core = re.sub(r"(지원)?사업$", "", sfc)
                if len(core) >= 6 and core in want:
                    return c
        return None
    def card_by_surface(term: str):
        """관계(편입·앞 단계)의 대상 — 그 표기가 든 장부 항목의 표기들로 만들 수 있는 카드 id(program:rise·program:sck …)를
        가진 카드. 쓴 횟수로 고르면 여러 사업 이름을 흡수한 큰 카드(LINC+)가 「RISE」「특성화 전문대학 육성사업」의 주인이 됐다(10/5)."""
        entry = next((e for e in ledger.get("programs", []) if term in (e.get("terms") or [])), None)
        terms = (entry or {}).get("terms") or [term]
        ids = {"program:" + re.sub(r"[^0-9a-z가-힣]+", "", t.lower()) for t in terms}
        ids |= {"program:" + k for t in terms if (k := programs.program_key(t))}
        hit = next((c for c in cards if c.node_id in ids), None)
        return hit or card_for(terms)
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
    _res = programs.classify(docs, cards)
    # 원본 경로가 있는 문서는 폴더 검토 장부(에이전트·사람 판정)도 본다 — 원본 장부(170)와 같은 판정
    _rv_docs = [{**d, "path": str(Path(d["origin"]).parent) if d.get("origin") else d.get("path", "")} for d in docs]
    programs.apply_reviews(_rv_docs, _res, programs.load_reviews(ROOT / "data" / "platform" / "class_review.jsonl"), cards)
    # 연차·연도 보정, 기간 밖이면 앞뒤 단계 사업으로, 장부가 같다고 한 카드는 합침 — 원본 장부(170)와 같은 규칙
    link = programs.ledger_link(cards, ledger)
    cards = programs.merge_aliases(cards, link)
    programs.apply_display(cards, link)
    _np = programs.apply_not_programs(_res, cards, ledger, link)
    if _np:
        print(f"== 외부 확인 「사업 아님」 {_np}건 — 기관 일반 업무로", flush=True)
    _st = programs.fill_period(_rv_docs, _res, link["periods"], link["spans"], link["aliases"], {c.node_id: c.name for c in cards})
    print(f"== 연차·연도 보정 {_st}")
    assigns = {a.doc_id: a for a in _res}
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
        if c.not_program:
            continue                                  # 외부 확인으로 사업이 아닌 것(조사·평가)은 사업 노드를 두지 않는다
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
        if c.not_program or cat == "사업 아님":
            continue
        if cat:
            gid = f"group:{cat}"
            if not any(n_[0] == gid for n_ in nodes):
                nodes.append((gid, "program_group", cats.get(cat, {}).get("label", cat), {"sources": cats.get(cat, {}).get("sources", [])}, None))
            edges.append((gid, c.node_id, "contains", "분류", [f"외부 확인: {cats.get(cat, {}).get('label', cat)}"] + e["sources"][:2]))
        if e.get("integrated_into"):
            parent = card_by_surface(e["integrated_into"])
            if parent is not None and parent is not c:
                edges.append((c.node_id, parent.node_id, "integrated_into", "분류",
                              [f"{e.get('since', '')}년부터 편입" + (f" — {e['status']}" if e.get("status") else "")] + e["sources"][:2]))
        for pred in e.get("predecessor", []):
            pc = card_by_surface(pred)
            if pc is not None and pc is not c:
                edges.append((pc.node_id, c.node_id, "succeeded_by", "분류", [f"앞 단계 사업 → 다음 단계({e.get('period', '')})"] + e["sources"][:2]))
    # 연관 사업 — 문서가 두 사업을 함께 다룬 근거로(장부의 편입·전신과 별개, 기준 「식별자 일치」·근거는 문서 이름)
    fname = {d["id"]: d["filename"] for d in docs}
    known = {c.node_id for c in cards if not c.not_program}
    for r in programs.related_programs(_res):
        if r["src"] in known and r["dst"] in known:
            edges.append((r["src"], r["dst"], "related", "식별자 일치",
                          [f"이 사업 문서 {r['n']}건({r['share'] * 100:.0f}%)이 함께 다룸"]
                          + [f"#{i} {fname.get(i, '')[:50]}" for i in r["docs"][:3]], min(1.0, r["share"] * 5)))
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
    card_surf = {c.node_id: sorted({re.sub(r"[\s.()·\-_]+", "", x).upper() for x in c.surfaces() if len(x) >= 3}, key=len, reverse=True)
                 for c in cards}

    def is_program_heading(title: str, prog: str) -> bool:
        """절 제목이 사업 이름 머리글인가 — 사업 이름·약칭을 지우고 남는 글자가 6자 이하(「대구광역시 지역혁신중심 대학지원체계(RISE)」).
        이런 절은 과제가 아니라 문서 표지·머리글이라 제목이 같아도 계획↔실적 짝이 아니다(판정 2026-10-04: RISE 제목 짝 2/8)."""
        t = re.sub(r"[\s.()·\-_「」\[\]]+", "", title or "").upper()
        if not t:
            return False
        hit = False
        for su in card_surf.get(prog, []):
            if su and su in t:
                t = t.replace(su, "")
                hit = True
        return hit and len(re.sub(r"[^가-힣A-Z0-9]", "", t)) <= 6
    # 서로 다른 사업 셋 이상의 문서에 같은 절 제목으로 나오는 제목(학교 이름 머리글·「추진 성과」 같은 상투 제목)은 제목만으로 잇지 않는다 —
    # 제목이 같아도 한쪽은 목차, 다른 쪽은 실적 총괄표였다(판정 2026-10-04: RISE 「영남이공대학교」↔「영남이공대학교」 6쌍). 본문으로는 잇는다
    # 사업은 계열(앞 단계 → 다음 단계, 장부의 succeeded_by)로 묶어 센다 — LINC 1단계·LINC+·LINC3.0 이 함께 쓰는 「인력양성」
    # 「기업연계 기반 공유·협업 활동」은 계열 고유의 절이지 상투 제목이 아니다(판정 c7 dropped: 같은 짝 3/10). 편입은 계열이 아니다
    fam: dict[str, str] = {}

    def family(x: str) -> str:
        while fam.setdefault(x, x) != x:
            fam[x] = fam[fam[x]]
            x = fam[x]
        return x
    for e in edges:
        if e[2] == "succeeded_by":
            fam[family(e[0])] = family(e[1])
    title_progs: dict[str, set] = defaultdict(set)
    for d in docs:
        pg = assigns[d["id"]].program
        if pg:
            for sec in d.get("sections") or []:
                title_progs[sections.title_key(sec.title)].add(family(pg))
    boiler = {k for k, ps in title_progs.items() if len(ps) >= 3}
    print(f"== 상투 절 제목(사업 계열 셋 이상) {len(boiler)}개 — 제목 짝에서 뺀다", flush=True)
    # 다른 대학의 자료(협의회 공유본·공개용 사업계획서 「경복대학교 4차년도 사업수행계획서_공개용」 등)는 우리 학교의 계획↔실적 짝에서 뺀다
    # (판정 2026-10-04: 가톨릭상지대학교 계획서 ↔ 우리 실적보고서). 우리 학교는 기관 정보의 대학명(문서에서 인출) — 이름을 코드에 두지 않는다
    from zzaimy.app import institution as _inst
    own = (_inst.facts(db).get("대학명") or "").strip()
    own_stem = re.sub(r"(대학교|대학)$", "", own)
    uni = re.compile(r"([가-힣]{2,12}대학교)")

    def other_org(d: dict) -> str:
        if not own_stem:
            return ""
        for text in (d.get("filename") or "", (d.get("head") or "")[:200]):
            # 「전문대학교 연합」「각 대학교」처럼 학교 이름이 아닌 일반 표현은 뺀다
            names = {n for n in uni.findall(text) if own_stem not in n
                     and not re.fullmatch(r"(?:전문|각|타|해당|우리|소속|참여|협약|연합|지역|국내|대상)?대학교", n)}
            if own_stem in text:
                return ""
            if names:
                return sorted(names)[0]
        return ""
    other_of = {d["id"]: o for d in docs if (o := other_org(d))}
    print(f"== 타 기관 자료 {len(other_of)}건(우리 학교 「{own}」 아님) — 계획↔실적 짝에서 뺀다", flush=True)
    for n in nodes:                                       # 문서 노드에 표시 — 그래프·화면에서 우리 자료와 구분
        if n[1] == "doc" and len(n) > 4 and n[4] in other_of:
            n[3]["other_org"] = other_of[n[4]]
    code_sections: dict[str, list] = defaultdict(list)       # 과제 코드 → 그 코드를 단 보고서 절
    # 같은 사업·연차의 계획 ↔ 실적, 평가 → 대상
    for ynode, ids in by_year.items():
        # 계획↔실적 짝짓기는 뼈대 문서·플랫폼 업로드 사이에서만 — DGX 가볍게 처리한 문서(dgx://)는 문서·절·단위 노드로만 들어간다
        # (같은 연차 계획서×보고서를 모두 견주면 원본 수만 건에서 곱으로 늘어난다)
        plans = [i for i in ids if assigns[i].kind == "plan" and not docs_by_id[i].get("light") and i not in other_of]
        reports = [i for i in ids if assigns[i].kind == "report" and not docs_by_id[i].get("light") and i not in other_of]
        evals = [i for i in ids if assigns[i].kind == "evaluation"]
        for p in plans:
            for r in reports:
                edges.append((f"doc:{p}", f"doc:{r}", "plans_reports", "식별자 일치", [f"같은 사업·연차({ynode})의 계획서와 실적보고서"]))
                linked_b = set()
                prog = assigns[p].program
                for ps, s, why in sections.align_context(docs_by_id[p]["sections"], docs_by_id[r]["sections"], text_of, generic_parent=args.generic_parent,
                                                         skip_b=NEXT_YEAR):
                    if is_program_heading(ps.title, prog):
                        continue                          # 사업 이름 머리글 — 짝이 아니다(본문 잇기로도 넘기지 않는다)
                    if sections.title_key(ps.title) in boiler:
                        continue                          # 상투 제목 — 제목으로는 잇지 않는다(아래 본문 잇기에는 남는다)
                    if year_conflict(f"{ps.title} {text_full(ps)}", f"{s.title} {text_full(s)}"):
                        linked_b.add(s.path)              # 연차가 다른 같은 제목 — 잇지 않고, 본문 잇기에도 넘기지 않는다
                        continue
                    linked_b.add(s.path)
                    edges.append((f"doc:{p}:sec:{ps.path}", f"doc:{r}:sec:{s.path}", "plans_reports", "식별자 일치",
                                  [f"계획 「{ps.title[:60]}」", f"실적 「{s.title[:60]}」", why]))
                # 제목 짝이 없는 보고서 절은 본문으로(목차 틀이 다른 계획서·보고서)
                for ps, s, why in sections.align_content(docs_by_id[p]["sections"], docs_by_id[r]["sections"], text_full,
                                                         skip_b_paths=linked_b, skip_b=NEXT_YEAR):
                    if year_conflict(f"{ps.title} {text_full(ps)}", f"{s.title} {text_full(s)}"):
                        continue
                    edges.append((f"doc:{p}:sec:{ps.path}", f"doc:{r}:sec:{s.path}", "plans_reports", "유사도",
                                  [f"계획 「{ps.title[:60]}」", f"실적 「{s.title[:60]}」", why]))
        # 과제 코드로 잇기 — 과제마다 계획서를 내고 연차보고서는 「[2-3 과제] 추진 실적」처럼 과제 코드를 단 절로 쓰는 사업(RISE).
        # 계획서 파일 이름의 코드와 보고서 절 제목의 코드가 같으면 계획서(문서) ↔ 그 보고서 절
        # 코드 붙은 계획서 중 가장 흔한 머리 낱말(과제계획서)의 문서만 그 과제의 계획서다 — 「[2-3] 환경개선공사 사업계획서」 같은 관련 계획은 아니다
        heads = Counter(_head_noun(docs_by_id[p]["filename"]) for p in plans if units.doc_code(docs_by_id[p]["filename"]))
        main_head = heads.most_common(1)[0][0] if heads else ""
        # 과제 코드마다 계획서 하나 — 한글 원본 먼저, 그다음 최신(판본이 둘이면 같은 보고서 절이 두 번 이어지고 판정도 갈린다)
        by_code: dict[str, int] = {}
        for p in plans:
            fname = docs_by_id[p]["filename"]
            code = units.doc_code(fname)
            if not code or len(units._UNIT_CODE.findall(fname)) > 1 or _head_noun(fname) != main_head:
                continue
            cur = by_code.get(code)
            rank = (not fname.lower().endswith(".pdf"), p)
            if cur is None or rank > (not docs_by_id[cur]["filename"].lower().endswith(".pdf"), cur):
                by_code[code] = p
        for p in sorted(set(by_code.values())):
            fname = docs_by_id[p]["filename"]
            code = units.doc_code(fname)
            rx = re.compile(rf"(?<![\d.\-]){re.escape(code)}(?![\d.\-])")
            for r in reports:
                rsecs = docs_by_id[r]["sections"]
                for s in rsecs:
                    if rx.search(s.title) and len(s.title) <= 60:
                        code_sections[code].append((r, s.path, s.title))
                        # 과제 코드로 계획서를 좁힌 뒤, 보고서 절 제목의 핵심 낱말(예산·성과지표·추진)을 가진 계획서 절과만 잇는다
                        # (「예산 집행 실적」 ↔ 「예산 운용」). 맞는 측면 절이 없으면(우수사례) 단위과제 노드로만 묶는다 —
                        # 계획서 전체 ↔ 보고서 한 측면은 '같은 것의 계획과 실적'이 아니다(판정 all7: 23%)
                        desc = [y for y in rsecs if y.path == s.path or y.path.startswith(s.path + ".")]
                        rtext = " ".join(text_full(y) for y in desc)[:6000]
                        ps, why = sections.aspect_match(docs_by_id[p]["sections"], s.title, text_full, rtext)
                        if ps is not None:
                            # 측면 낱말이 같아도 본문이 겹치지 않으면(계획서 「성과지표 관리 계획」= CQI 관리 체계, 보고서 = 지표 수치) 같은 것의
                            # 계획과 실적이 아니다 — 그런 짝은 다음 층(성과지표 노드의 목표값↔달성값)에서 잇는다(판정 all10)
                            pdesc = [y for y in docs_by_id[p]["sections"] if y.path == ps.path or y.path.startswith(ps.path + ".")]
                            body = sections._jac(sections._words(" ".join(text_full(y) for y in pdesc)[:6000]), sections._words(rtext))
                            if body < ASPECT_BODY_MIN:
                                ps = None
                            else:
                                why = f"{why}, 본문 겹침 {body:.2f}"
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
            for p1 in (i for i in by_year[y1] if assigns[i].kind == "plan" and not docs_by_id[i].get("light")):
                for p2 in (i for i in by_year[y2] if assigns[i].kind == "plan" and not docs_by_id[i].get("light")):
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

    # 성과지표 — 표에서 지표 이름과 기준·목표·실적·달성률을 그대로 꺼내 사업마다 지표 노드로 묶는다(수치는 인출만, 절대 규칙 1·12).
    # 「N차년도」 칸은 그 문서의 연차·연도로 절대 연도를 푼다(문서 연차를 모르면 상대 시점 그대로)
    import hashlib
    ind_obs: dict[tuple[str, str], list[dict]] = defaultdict(list)
    ind_names: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for d in docs:
        a = assigns[d["id"]]
        if not a.program:
            continue
        seen = set()
        for seq, o in indicators.from_chunks(chunk_map.get(d["id"], [])):
            key = indicators.name_key(o.indicator)
            if len(key) < 2:
                continue
            year = None
            if o.period.endswith("차") and a.round and a.year:
                year = int(a.year) + int(o.period[:-1]) - int(a.round)
            elif o.period.isdigit():
                year = int(o.period)
            sig = (key, o.measure, o.period, o.value)
            if sig in seen:
                continue
            seen.add(sig)
            ind_names[(a.program, key)][o.indicator] += 1
            ind_obs[(a.program, key)].append({"doc_id": d["id"], "kind": a.kind, "doc_year": a.year, "doc_round": a.round, "seq": seq,
                                              "measure": o.measure, "period": o.period, "year": year, "value": o.value,
                                              "text": o.value_text, "unit": o.unit, "column": o.column[:40], "tag": o.tag, "group": o.group[:30]})
    n_ind = 0
    for (prog, key), obs in ind_obs.items():
        docs_of = sorted({x["doc_id"] for x in obs})
        label = ind_names[(prog, key)].most_common(1)[0][0]
        inode = f"ind:{prog.split(':', 1)[1]}:{hashlib.sha1(key.encode()).hexdigest()[:10]}"
        units_seen = Counter(x["unit"] for x in obs if x["unit"])
        tags = Counter(x["tag"] for x in obs if x["tag"])
        nodes.append((inode, "indicator", label, {"key": key, "unit": units_seen.most_common(1)[0][0] if units_seen else "",
                                                  "tags": [t for t, _ in tags.most_common(3)], "n_docs": len(docs_of), "obs": obs[:400]}, None))
        edges.append((prog, inode, "has_indicator", "추출", [f"문서 {len(docs_of)}건의 성과지표 표에 「{label[:50]}」"]))
        for did in docs_of:
            ex = next(x for x in obs if x["doc_id"] == did)
            edges.append((f"doc:{did}", inode, "measures", "추출", [f"표 조각 {ex['seq']}: {ex['column']} = {ex['text']}"]))
        n_ind += 1
    print(f"== 성과지표 {n_ind}개(관측값 {sum(len(v) for v in ind_obs.values())})")

    print("== 그래프")
    print("  노드", dict(Counter(n[1] for n in nodes)))
    print("  관계", dict(Counter((e[2], e[3]) for e in edges)))
    review = [d["id"] for d in docs if assigns[d["id"]].status != "auto"]
    print("  검토 대기 문서", f"{len(review)}건 (앞 20: {review[:20]})" if review else "없음")
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
        if not args.full:                     # 전체 다시 짓기는 위에서 다 지웠다 — 문서마다 LIKE 로 또 훑지 않는다
            for d in docs:
                kg_store.clear_doc(conn, f"doc:{d['id']}")
                conn.execute("DELETE FROM kg_edges WHERE src = ? OR dst = ?", (f"doc:{d['id']}", f"doc:{d['id']}"))
        kg_store.put_nodes(conn, nodes)
        kg_store.put_edges(conn, edges)
    print(f"썼다 — 노드 {len(nodes)} · 관계 {len(edges)}")
    if args.full:
        _export_assignments(docs, assigns)
    return 0


def _export_assignments(docs: list[dict], assigns: dict) -> None:
    """문서별 배정(사업·연도·연차·갈래) 스냅숏과 지난번 대비 변경 기록, 사업 × 연도별 확정 핵심 문서 목록을 내보낸다.

    학습 문답(데이터셋)은 근거 문서의 사업·연차가 바뀌면 다시 검수해야 한다(아스트라 C-199) — 변경은
    data/platform/doc_program_changes.jsonl 에 덧붙고, 문답 재료가 될 확정 문서는 program_core_docs.json 으로 나간다."""
    import time as _t
    plat = ROOT / "data" / "platform"
    snap_path = plat / "doc_assign_snapshot.json"
    old = json.loads(snap_path.read_text(encoding="utf-8")) if snap_path.is_file() else {}
    now = {str(d["id"]): [a.program or "", a.year, a.round, a.kind or "", a.status]
           for d in docs if (a := assigns.get(d["id"]))}
    stamp = _t.strftime("%Y-%m-%d %H:%M")
    changed = 0
    if old:
        with (plat / "doc_program_changes.jsonl").open("a", encoding="utf-8") as fh:
            for did, cur in now.items():
                prev = old.get(did)
                if prev and prev[:4] != cur[:4]:
                    fh.write(json.dumps({"at": stamp, "doc_id": int(did), "before": prev, "after": cur}, ensure_ascii=False) + "\n")
                    changed += 1
    tmp = snap_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(now, ensure_ascii=False), encoding="utf-8")
    tmp.replace(snap_path)
    core: dict = {}
    by_id = {d["id"]: d for d in docs}
    for did, a in assigns.items():
        if not a.program or a.status not in ("auto", "period") or a.kind not in ("plan", "report", "evaluation", "basic_plan"):
            continue
        d = by_id.get(did) or {}
        key = f"{a.program}|{a.year or ''}"
        ent = core.setdefault(key, {"program": a.program, "program_name": a.program_name, "year": a.year, "round": a.round, "docs": []})
        ent["docs"].append({"doc_id": did, "filename": d.get("filename"), "kind": a.kind,
                            "sections": len(d.get("sections") or []), "light": bool(d.get("light"))})
    out = sorted(core.values(), key=lambda e: (e["program_name"] or "", e["year"] or 0))
    (plat / "program_core_docs.json").write_text(json.dumps({"at": stamp, "programs": out}, ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
    print(f"배정 스냅숏 {len(now)}건 · 지난번과 달라진 문서 {changed}건 · 확정 핵심 문서 묶음 {len(out)}개", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
