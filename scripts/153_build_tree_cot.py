#!/usr/bin/env python3
"""문서 구조 단계 문답(CoT) 학습쌍 — 사업명 → 개요 → 목차 → 절의 요구 항목 → 절의 뼈대(사용자 지시 2026-09-29).

  구조를 세우는 능력을 가르친다. 학습된 모델이 새 사업의 계획서 구조를 먼저 쓰고, 뼈대의 칸은 검색·추가 문서의 근거로 채운다.
  입력은 짧다(근거 조각 + 사슬 + 질문). 답은 [생각] 한두 문장 + [답]. 답의 수치가 입력에 없으면 폐기.

  산출(data/training/): tree_cot_pairs.jsonl(검수용) · tree_cot_sft.jsonl(messages, 단발+대화형) · tree_cot_alpaca.jsonl(instruction·input·output,
  단발형) · tree_cot_report.md
  VM 에서: set -a; . .env.local; set +a; .venv/bin/python scripts/153_build_tree_cot.py --push --replace
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

REVIEW_CONFIG = """<View>
  <Header value="문서 구조 단계 문답 — 근거·사슬·질문(입력)에 대한 [생각]+[답]이 맞는지 판정"/>
  <View style="display:flex; gap:1em">
    <View style="width:50%"><Header value="입력"/><Text name="input" value="$input"/></View>
    <View style="width:50%"><Header value="출력"/><Text name="output" value="$output"/></View>
  </View>
  <Choices name="decision" toName="output" choice="single" showInline="true" required="true">
    <Choice value="채택"/><Choice value="수정"/><Choice value="폐기"/>
  </Choices>
  <Header value="수정본 (수정을 고른 경우)"/>
  <TextArea name="corrected" toName="output" rows="8" editable="true" maxSubmissions="1"/>
</View>"""


def _heading_block(chunks: list[dict], pattern: str, n_after: int = 3, limit: int = 600) -> str:
    """제목 조각(정규식) 다음의 글 조각 몇 개."""
    for k, c in enumerate(chunks):
        if c.get("kind") == "heading" and re.search(pattern, c.get("content") or ""):
            text = " ".join(" ".join((x.get("content") or "").split()) for x in chunks[k + 1:k + 1 + n_after] if x.get("kind") == "text")
            if text.strip():
                return text[:limit]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", type=int, default=557)
    ap.add_argument("--done", type=int, default=562)
    ap.add_argument("--notice", type=int, default=561, help="공고 문서 id")
    ap.add_argument("--basic", type=int, default=560, help="기본계획 문서 id")
    ap.add_argument("--manual", type=int, default=564, help="평가편람 문서 id")
    ap.add_argument("--out", default=str(ROOT / "data" / "training"))
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--project", default="ZZAIMY 문서 구조 문답")
    args = ap.parse_args()

    from zzaimy.app.db import Database
    from zzaimy.app.regulations import find_relevant
    from zzaimy.dataset import real_pairs as rp
    from zzaimy.dataset import tree_cot as tc

    db = Database(ROOT / "data" / "platform" / "platform.db")
    form_doc = db.get_document(args.form)
    sections = rp.form_model(ROOT / form_doc["stored_path"])
    done_chunks = db.list_doc_chunks(args.done)
    plan = rp.section_parts(sections, done_chunks)
    parts = tc.part_titles(done_chunks)
    roots = tc.build_tree(sections, parts, plan)

    notice, basic = db.list_doc_chunks(args.notice), db.list_doc_chunks(args.basic)
    title_line = next((c["content"] for c in notice if c.get("kind") == "heading"), "") or db.get_document(args.notice)["filename"]
    program = re.sub(r"[-]", "", title_line).replace("공고", "").strip(" .\n")
    overview_evidence = [e for e in [
        "(공고 제목) " + program + " 공고",
        "(공고 · 사업 목적) " + _heading_block(notice, r"사업\s*목적"),
        "(기본계획 · 목적) " + _heading_block(basic, r"^□\s*목적"),
        "(기본계획 · 추진 방향) " + _heading_block(basic, r"^□\s*추진\s*방향"),
        "(기본계획 · 사업 개요) " + _heading_block(basic, r"^사업\s*개요"),
    ] if len(e.split(") ", 1)[-1].strip()) > 10]
    if not overview_evidence:
        print("공고·기본계획에서 사업 목적·개요를 찾지 못했습니다", file=sys.stderr)
        return 2
    manual_chunks = db.chunks_for_docs([args.manual])
    area_hits = find_relevant(db, "평가영역 평가지표 배점 " + " ".join(r.heading for r in roots), top_k=4, chunks=manual_chunks) if manual_chunks else []
    area_evidence = [f"({c.get('reg_title') or ''}) " + " ".join(str(c.get("content") or "").split())[:300] for c in area_hits] or ["(없음)"]

    s1 = tc.step1(program, overview_evidence)
    overview = s1["gpt"].split("개요:", 1)[-1].strip()
    s2 = tc.step2(program, overview, roots, area_evidence)
    pairs: list[dict] = []
    dropped: list[str] = []

    def keep(rec) -> bool:
        p = tc.to_pair(rec, program, args.done)
        if p:
            pairs.append(p)
            return True
        dropped.append(f"단계 {rec['step']} {rec['node']}: {', '.join(sorted(tc.missing_numbers(rec))[:6])}")
        return False

    for v in range(3):                                               # 질문 표현을 바꾼 변형 셋
        keep(tc.step1(program, overview_evidence, variant=v))
        keep(tc.step2(program, overview, roots, area_evidence, variant=v))
    part_recs: dict[str, dict] = {}
    for root in roots:
        hits = find_relevant(db, f"{root.heading} 평가지표 배점", top_k=3, chunks=manual_chunks) if manual_chunks else []
        sp = tc.step_part(program, overview, roots, root, [" ".join(str(c.get("content") or "").split())[:300] for c in hits])
        if sp and keep(sp):
            part_recs[root.heading] = sp
    report = ["# 문서 구조 단계 문답 — 통계", "", f"사업명: {program}", f"부 {len(roots)}개: " + " / ".join(r.heading for r in roots), "",
              "| 절 | 요구 항목 | 뼈대 줄 | 3단계 | 4단계 | 대화형 |", "|---|---|---|---|---|---|"]

    def walk(n):
        yield n
        for c in n.children:
            yield from walk(c)

    n_chain = 0
    for root in roots:
        for node in walk(root):
            if node.level == 0:
                continue
            crit_hits = find_relevant(db, f"{node.heading} {node.instructions[:200]}", top_k=3, chunks=manual_chunks) if manual_chunks and node.instructions else []
            criteria = [" ".join(str(c.get("content") or "").split())[:300] for c in crit_hits]
            s3 = tc.step3(program, overview, roots, node, criteria)
            s4 = tc.step4(program, overview, roots, node)
            ok3 = bool(s3) and keep(s3)
            ok4 = bool(s4) and keep(s4)
            chain = ""
            if ok3 and ok4:
                steps = [s1, s2] + ([part_recs[node.part]] if node.part in part_recs else []) + [s3, s4]
                pairs.append(tc.chain_conversation(steps, program, args.done))
                n_chain += 1
                chain = "O"
            report.append(f"| {node.heading} | {len(tc.required_items(node.instructions))} | {len(node.skeleton)} | {'O' if ok3 else ''} | {'O' if ok4 else ''} | {chain} |")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "tree_cot_pairs.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in pairs), encoding="utf-8")
    (out / "tree_cot_sft.jsonl").write_text("".join(json.dumps(tc.to_messages(p), ensure_ascii=False) + "\n" for p in pairs), encoding="utf-8")
    alp = [a for a in (tc.to_alpaca(p) for p in pairs) if a]
    (out / "tree_cot_alpaca.jsonl").write_text("".join(json.dumps(a, ensure_ascii=False) + "\n" for a in alp), encoding="utf-8")
    from collections import Counter

    kinds = Counter(p["meta"]["source"] for p in pairs)
    report += ["", "건수: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())) + f" · 폐기 {len(dropped)}", ""]
    if dropped:
        report += ["폐기(입력에 없는 수치):"] + [f"- {d}" for d in dropped]
    (out / "tree_cot_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report[-(len(dropped) + 3):]))
    print(f"→ {out}/tree_cot_pairs.jsonl · tree_cot_sft.jsonl · tree_cot_alpaca.jsonl({len(alp)}) · tree_cot_report.md")

    if args.push and pairs:
        from zzaimy.dataset.ls_client import LabelStudioClient

        client = LabelStudioClient(db.get_setting("labelstudio_url"), db.get_setting("labelstudio_token"))
        pid = client.ensure_project(args.project, label_config=REVIEW_CONFIG, description="사업명→개요→목차→절 항목→절 뼈대 단계 문답 검수")
        if args.replace:
            print(f"기존 태스크 {client.clear_tasks(pid)}건 삭제")
        print(f"Label Studio 「{args.project}」(id {pid}) 에 {client.push_tasks(pid, pairs)}건 올렸습니다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
