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
from zzaimy.graph.sections import title_key as sections_title_key  # noqa: E402

KIND_KO = {"plan": "계획서", "report": "실적보고서"}
# 양식의 절 제목은 짧은 명사구만 — 동의서·서약 문장(「본인은 …」·「과제의 선정에 관한 사무: …」)이 개요 번호를 달고 절로 잡힌 것은 뺀다
_SENTENCE = re.compile(r"(?:다|함|음|임|됨|[세에어아해]요|니다)\s*[.。]?\s*$|^본인|동의|서약|개인\s*정보|[:：]")
LABEL_MAX = 40
BODY_ROWS = 2


_FRONT = re.compile(r"동의서|서약|확약|귀하|개인\s*정보|청렴|보안\s*각서")
_NUMBERED = re.compile(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|(?:I{1,3}|IV|VI{0,3}|IX|X)[.．]|\d{1,2}(?:[.\-]\d{1,2})*[.．)]|[가나다라마바사아자차카타파하][.．)]|\(\d{1,2}\))")


def label_ok(label: str) -> bool:
    t = (label or "").strip()
    return 2 <= len(t) <= LABEL_MAX and not _SENTENCE.search(t) and not re.fullmatch(r"[\d\s.~\-]+", t)


_CONTENT_MARK = re.compile(r"[☒☐□■▪◦○●◆▶※]")


def head_label(text: str) -> str:
    """머리 칸 글 — 머리 이름만. 원문 머리 행에 내용이 섞여 있으면(「추진배경 / ☒ 지역 특화형 …」) 「/」 앞 이름만, 이름이 없으면 비운다."""
    t = " ".join(str(text).split())
    if _CONTENT_MARK.search(t) or len(t) > 20:
        head = re.split(r"\s*/\s*|\s*[☒☐□■▪◦○●◆▶※]", t)[0].strip()
        return head if 1 <= len(head) <= 20 else ""
    return t


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
    texts = [head_label(c[5]) for c in heads]
    # 장 제목 띠(「Ⅲ | 산업체 참여 실적 및 계획」「별지 | …」) — 다음 장의 표지라 양식 표가 아니다
    if n_cols <= 2 and n_rows <= 3 and texts and re.fullmatch(
            r"\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|(?:I{1,3}|IV|VI{0,3}|IX|X)|별지\s*\d*|붙임\s*\d*|\d{1,2})\s*[.．]?\s*", texts[0] or ""):
        return None
    if any(re.search(r"(?i)\b(?:chapter|part)\b", t or "") for t in texts):
        return None                                     # 「CHAPTER | Ⅲ 성과관리」 장 표지 띠
    if len({t for t in texts if t}) < 2:
        return None                                     # 머리 이름이 하나뿐(「LINC 3.0」 꼬리표)인 장식 표
    if not any(texts) or sum(bool(re.fullmatch(r"[\d,.%\s\-]+", x)) for x in texts if x) > len(texts) / 2:
        return None                                     # 머리가 비었거나 숫자뿐이면(값 행) 양식 표가 아니다
    rows = []
    for r in range(depth):
        tds = []
        for c in sorted((c for c in heads if int(c[0]) == r), key=lambda c: int(c[1])):
            rs = min(int(c[2]), depth - r)
            attrs = (f' rowspan="{rs}"' if rs > 1 else "") + (f' colspan="{int(c[3])}"' if int(c[3]) > 1 else "")
            # 「항목명 | 값」 서식 행 — 첫 칸이 아니면서 여러 열(3열 이상)에 걸친 칸은 값이다(「프로그램명 | 임플란트 전문 …」). 비워 둔다
            label = "" if int(c[1]) > 0 and int(c[3]) >= 3 and len([x for x in heads if int(x[0]) == r]) == 2 \
                else head_label(c[5])
            tds.append(f"<th{attrs}>{label}</th>")
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
    written: set[str] = set()
    for prog in progs:
        pkey = prog["id"].split(":", 1)[1]
        for kind, kind_ko in KIND_KO.items():
            # 다른 대학 자료(협의회 공유본 등, 157 의 other_org)는 우리 학교 양식의 뼈대가 되지 않는다
            docs = [d for d, ids in order.items() if prog_of.get(d) == prog["id"] and nodes[d]["props"].get("kind") == kind
                    and not nodes[d]["props"].get("other_org")]
            if not docs:
                continue
            # 뼈대 = 단위 절을 가장 많이 가진 문서(같으면 최근) — 원문 목차 그대로의 계층을 쓴다
            def n_units(d):
                return sum(1 for x in order[d] if unit_of.get(x, "").startswith(f"unit:{pkey}:"))
            # 구조가 살아 있는 한글 원본을 PDF 보다 먼저(PDF 글자층의 쪽 글에는 도식 글자 줄이 섞인다)
            def is_pdf(d):
                return nodes[d]["label"].lower().endswith(".pdf")
            top_n = max(n_units(d) for d in docs)
            # 한글 원본을 더 강하게 먼저 — PDF 글자층은 표가 조각으로 안 나와 양식에 표가 빠진다(10/4: LINC+ 보고서 양식 표 0)
            ranked = sorted(docs, key=lambda d: (not is_pdf(d) and n_units(d) >= 0.3 * top_n, n_units(d), int(nodes[d].get("doc_id") or 0)),
                            reverse=True)

            def keep_for(skel_doc):
                kept: set[str] = set()
                for x in order[skel_doc]:
                    uid = unit_of.get(x, "")
                    if not uid.startswith(f"unit:{pkey}:") or not label_ok(nodes[uid]["label"]):
                        continue
                    chain = [x] + [a for a in ancestor_ids(x)]
                    if any(_FRONT.search(nodes[c]["label"]) for c in chain):
                        continue
                    top = chain[-1]
                    if not re.match(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|(?:I{1,3}|IV|VI{0,3}|IX|X)[.．]|\d{1,2}[.．)])", nodes[top]["label"]):
                        continue
                    kept.update(chain)
                return kept
            # 앞 후보에서 남는 절이 모자라면(목차가 깨진 판) 다음 후보로
            skel, keep = ranked[0], set()
            # 한글 원본 후보를 12개까지 먼저, 맞는 것이 없을 때만 PDF(표가 조각으로 남는 원본이 양식에 낫다)
            cands = [d for d in ranked if not is_pdf(d)][:12] + [d for d in ranked if is_pdf(d)][:4]
            for cand in cands:
                k = keep_for(cand)
                if len(k) >= max(3, len(keep)) and (len(k) >= 10 or not keep):
                    skel, keep = cand, k
                    if len(k) >= 10:
                        break
            ids = order[skel]
            if len(keep) < 3:
                continue
            lines = [f"# {prog['label']} {kind_ko} 공통 양식", "",
                     f"지난 {kind_ko}들에 되풀이되는 절(지식 그래프의 단위)을 「{nodes[skel]['label'][:50]}」의 목차 순서로 놓았다. "
                     "내용은 비워 두었다 — 각 절의 안내는 그 절이 나온 연차다.", ""]
            base = min(nodes[x]["id"].split(":sec:")[1].count(".") for x in keep)
            # 사례 반복 접기 — 같은 부모 아래 형제가 셋 이상이고 하위 절 제목 구성이 서로 비슷하면(협약반마다 같은 프로그램 목록)
            # 하나만 「(항목 이름)」 자리로 남기고 실제 이름은 작성 안내의 예시로. 공통 양식에 특정 반·학과 이름을 박지 않는다
            def sid(x):
                return x.split(":sec:")[1]
            kids: dict[str, list[str]] = defaultdict(list)       # 뼈대 문서의 전체 절로(양식에 남지 않는 형제도 비교에 쓴다)
            for x in ids:
                if "." in sid(x):
                    kids[x.rsplit(".", 1)[0]].append(x)
            def child_titles(x):
                return {sections_title_key(nodes[c]["label"]) for c in kids.get(x, [])}
            collapse_rep: dict[str, list[str]] = {}       # 대표 절 → 예시 이름들
            skip: set[str] = set()
            for par, ch in kids.items():
                with_kids = [c for c in ch if kids.get(c)]
                if len(with_kids) < 3:
                    continue
                rep = with_kids[0]
                rt = child_titles(rep)
                sims = [c for c in with_kids[1:] if rt and len(rt & child_titles(c)) / max(len(rt | child_titles(c)), 1) >= 0.5]
                if len(sims) >= 2:
                    group = [rep] + sims
                    shown = next((c for c in group if c in keep), None)   # 양식에 남는 첫 형제를 대표로
                    if shown is None:
                        continue
                    collapse_rep[shown] = [nodes[c]["label"] for c in group]
                    for c in group:
                        if c != shown:
                            skip.update(y for y in ids if y == c or y.startswith(c + "."))
            n_sec = 0
            last_title = ""
            for x in ids:
                if x not in keep or x in skip:
                    continue
                if nodes[x]["label"] == last_title:
                    continue                              # 같은 제목이 잇달아 나오면(목차·간지 되풀이) 한 번만
                last_title = nodes[x]["label"]
                depth = x.split(":sec:")[1].count(".") - base
                title = re.sub(r"\s+", " ", nodes[x]["label"]).strip()[:LABEL_MAX + 20]
                # 연차 표기는 떼고(「사업 예산집행(1차년도)」 → 「사업 예산집행」) 사례 대표는 자리 표시로
                title = re.sub(r"\s*[(（]\s*(?:\d{1,2}\s*차\s*년도|(?:19|20)\d{2}(?:\s*학?년도?)?)\s*[)）]", "", title).strip()
                if x in collapse_rep:
                    num = re.match(r"^\s*([(（]?[0-9A-Za-z가-하ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]{1,3}[.)）]|\d{1,2}[.)])\s*", title)
                    title = (num.group(0) if num else "") + "(항목 이름)"
                lines += [f"{'#' * min(2 + depth, 6)} {title}", ""]
                if x in collapse_rep:
                    ex = ", ".join(re.sub(r"^\s*\S{1,4}[.)）]\s*", "", t)[:30] for t in collapse_rep[x][:5])
                    lines += [f"> 작성 안내: 같은 짜임으로 항목마다 되풀이한다 — 지난 문서의 예: {ex}", ""]
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
            safe = re.sub(r"[^0-9A-Za-z가-힣]+", "_", pkey).strip("_") or "program"
            dest = out_dir / f"{safe}_{kind}.md"
            dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
            written.add(dest.name)
            print(f"{prog['label'][:30]} {kind_ko}: 절 {n_sec} (뼈대 #{nodes[skel].get('doc_id')}) → {dest}")
    if not args.program:
        # 이번에 만들지 않은 옛 사업별 양식은 지운다 — 사업 id 가 바뀌면(카드 정리·이름 규칙) 옛 이름 양식이 남아 드라이브까지 올라갔다
        for f in [*out_dir.glob("*_plan.*"), *out_dir.glob("*_report.*")]:
            if f.suffix in (".md", ".docx") and f.with_suffix(".md").name not in written:
                f.unlink(missing_ok=True)
                print(f"옛 양식 지움: {f.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
