#!/usr/bin/env python3
"""실문서로 Writer 학습쌍을 만들고 Label Studio 검수 프로젝트에 올린다(사용자 지시 2026-09-29: "내가 제공한 실문서를 라벨해서 학습에 쓸 수 있도록").

  양식(--form 문서 id, 기본 557 작성서식 ver5) × 완성본(--done 문서 id, 기본 562 사업계획서 Ver.3.3) → 절마다
  입력 = 서빙 프롬프트 틀(작성방법·근거·사실 목록·양식 표 격자), 출력 = 완성본 절의 편집 계획 JSON(insert·table·fill).
  근거 문서(--criteria, 기본 = 검토 완료된 기준 문서 전부)에서 절마다 관련 조각 5개를 붙인다(운영 검색 경로 find_relevant).
  정제: 출력 수치가 입력에 없으면 폐기(브리프 규칙 1). 절 작성 쌍·표 채우기 쌍·DPO 쌍(drafting_episodes 의 초안 = rejected).

  산출(data/training/):
    real_pairs.jsonl   검수용 원쌍(conversations + meta.shown)
    real_sft.jsonl     학습용(messages) — scripts/83_sft_writer_qlora.py --data 로
    real_dpo.jsonl     {prompt, chosen, rejected} — DPO 단계용
    real_report.md     절별 통계와 폐기 사유

  VM 에서 .env.local 을 읽고(PostgreSQL) 실행한다:
    set -a; . .env.local; set +a; .venv/bin/python scripts/152_build_real_sft.py --push
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

REVIEW_CONFIG = """<View>
  <Header value="양식 절 작성 — 지시와 재료(작성방법·근거·사실 목록·양식 표 격자)에 대해 출력(완성본에서 옮긴 절)이 학습에 쓸 만한지 판정"/>
  <Header value="담당자 지시"/><Text name="question" value="$question"/>
  <Header value="출력 (문단·표·양식 표 채움·도식)"/><Text name="output" value="$answer"/>
  <Collapse><Panel value="재료 (작성방법·근거·사실 목록·양식 표)"><Text name="evidence" value="$evidence"/></Panel></Collapse>
  <Collapse><Panel value="원문 입력 전체(시스템 지침 포함)"><Text name="input" value="$input"/></Panel></Collapse>
  <Choices name="decision" toName="output" choice="single" showInline="true" required="true">
    <Choice value="채택"/><Choice value="수정"/><Choice value="폐기"/>
  </Choices>
  <Header value="수정본 (수정을 고른 경우 — 출력을 고쳐 적는다)"/>
  <TextArea name="corrected" toName="output" rows="10" editable="true" maxSubmissions="1"/>
</View>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", type=int, default=557)
    ap.add_argument("--done", type=int, default=562)
    ap.add_argument("--criteria", type=int, nargs="*", default=None, help="근거 문서 id (기본: 검토 완료 기준 문서 전부)")
    ap.add_argument("--out", default=str(ROOT / "data" / "training"))
    ap.add_argument("--push", action="store_true", help="Label Studio 프로젝트에 올린다")
    ap.add_argument("--project", default="ZZAIMY 실문서 절 작성")
    ap.add_argument("--replace", action="store_true", help="올리기 전에 프로젝트의 기존 태스크를 지운다")
    ap.add_argument("--keep-missing", action="store_true", help="입력에 없는 수치가 있는 쌍도 남긴다(검수용)")
    args = ap.parse_args()

    from zzaimy.app.db import Database
    from zzaimy.app.regulations import find_relevant
    from zzaimy.dataset import provenance as pv
    from zzaimy.dataset import real_pairs as rp
    from zzaimy.dataset import tree_cot as tc

    db = Database(ROOT / "data" / "platform" / "platform.db")
    form_doc, done_doc = db.get_document(args.form), db.get_document(args.done)
    if not form_doc or not done_doc:
        print("양식·완성본 문서를 찾지 못했습니다", file=sys.stderr)
        return 2
    form_path = ROOT / form_doc["stored_path"]
    title = (done_doc.get("title") or done_doc.get("filename") or "").rsplit(".", 1)[0]
    sections = rp.form_model(form_path)
    print(f"양식 {form_doc['filename']}: 절 {len(sections)}개, 양식 표 {sum(len(s.grids) for s in sections)}개")
    done_chunks = db.list_doc_chunks(args.done)
    plan_ = rp.section_parts(sections, done_chunks)
    matched = [e for e in plan_ if e["matched"] and e["parts"]]
    print(f"완성본 {done_doc['filename']}: 조각 {len(done_chunks)}개, 맞춘 절 {len(matched)}/{len(sections)}")

    if args.criteria is None:
        crit_ids = [d["id"] for d in db.list_documents("regulation") if d["status"] == "reviewed"]
    else:
        crit_ids = args.criteria
    crit_chunks = db.chunks_for_docs(crit_ids)
    print(f"근거 문서 {len(crit_ids)}건, 조각 {len(crit_chunks)}개")

    # 근거 기록(아스트라 C-135 품질 관문): 입력 줄마다 문서·조각을 단다. 검수 판정은 사람이 남긴다
    program_id = f"docset:{args.form}:{args.done}"
    sources = (pv.Sources().add("form", db.list_doc_chunks(args.form)).add("done", done_chunks)
               .add("criteria", crit_chunks, prefix="reg:"))
    path_of: dict[str, list[str]] = {}

    def fill_paths(n, trail):
        path_of[n.heading] = trail + [n.heading]
        for c in n.children:
            fill_paths(c, trail + [n.heading])

    for r in tc.build_tree(sections, tc.part_titles(done_chunks), plan_):
        fill_paths(r, [])
    by_index = {s.index: s for s in sections}
    pairs: list[dict] = []
    dropped: list[tuple[str, str]] = []
    report = ["# 실문서 학습쌍 — 절별 통계", "", f"양식 {form_doc['filename']} / 완성본 {done_doc['filename']}", "",
              "| 절 | 글자 | 표 | 사실 | 문단 | 새 표 | 채운 칸 | 판정 |", "|---|---|---|---|---|---|---|---|"]
    for e in plan_:
        sec = by_index[e["index"]]
        if not e["matched"] or not e["parts"]:
            report.append(f"| {sec.heading} | 0 | 0 | 0 | | | | 완성본에 없음 |")
            continue
        query = f"{sec.heading} {sec.instructions[:300]}"
        try:
            criteria = find_relevant(db, query, top_k=5, chunks=crit_chunks)
        except Exception as exc:
            print(f"  근거 검색 실패({sec.heading}): {exc}", file=sys.stderr)
            criteria = []
        pr = rp.build_section_pair(sec, sections, e["parts"], criteria, title=title)
        if pr is None:
            continue
        st = pr["stats"]
        verdict = "채택"
        if pr["missing_numbers"] and not args.keep_missing:
            verdict = f"폐기(입력에 없는 수치 {len(pr['missing_numbers'])}: {', '.join(pr['missing_numbers'][:5])})"
            dropped.append((sec.heading, verdict))
        elif st["chars"] < 80 and not st["filled"] and not st["tables"] and not st.get("figures"):
            verdict = "폐기(너무 짧음)"
            dropped.append((sec.heading, verdict))
        report.append(f"| {sec.heading} | {st['chars']} | {e['tables']} | {st['facts']} | {st['inserts']} | {st['tables']} | {st['filled']} | {verdict} |")
        if verdict != "채택":
            continue
        pairs.append({"conversations": [{"from": "human", "value": pr["human"]}, {"from": "gpt", "value": pr["output"]}],
                      "meta": {"source": "real-section", "doc_id": args.done, "form_id": args.form, "section": sec.heading,
                               "shown": pr["shown"], "stats": st, "_known": {k: (args.done, str(v)) for k, v in pr["fact_sources"].items()},
                               "view": {"question": pr["human"].rpartition("[담당자 지시]")[-1].strip(), "evidence": pr["materials"],
                                        "reasoning": "", "answer": pr["shown"]}}})
        # 표 채우기 쌍 — 같은 절의 표 부분만 따로
        used_grids: set = set()
        for kind, payload in e["parts"]:
            if kind != "table" or not payload:
                continue
            g = rp.match_grid(sec, payload["cells"], used_grids)
            if g is None:
                continue
            fp = rp.build_fill_pair(sec, sections, g, payload["cells"], title=title, chunk_id=payload.get("chunk_id"))
            if fp and not fp["missing_numbers"]:
                pairs.append({"conversations": [{"from": "human", "value": fp["human"]}, {"from": "gpt", "value": fp["output"]}],
                              "meta": {"source": "real-fill", "doc_id": args.done, "form_id": args.form, "section": sec.heading,
                                       "shown": fp["shown"], "stats": fp["stats"],
                                       "_known": {k: (args.done, str(v)) for k, v in fp["fact_sources"].items()},
                                       "view": {"question": fp["human"].rpartition("[담당자 지시]")[-1].strip(), "evidence": fp["materials"],
                                                "reasoning": "", "answer": fp["shown"]}}})

    # DPO — 같은 절의 에이전트 초안(drafting_episodes)을 rejected, 완성본 편집 계획을 chosen 으로
    dpo: list[dict] = []
    ep_path = ROOT / "data" / "platform" / "drafting_episodes.jsonl"
    chosen_by_section = {p["meta"]["section"]: p for p in pairs if p["meta"]["source"] == "real-section"}
    if ep_path.exists():
        for ln in ep_path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError:
                continue
            ch = chosen_by_section.get(rec.get("section") or "")
            if not ch or not rec.get("draft"):
                continue
            rejected = json.dumps({"reply": rec.get("reply") or "", "ops": [
                {"op": o.get("op"), "section": 0, "old": "", "text": o.get("text") or "", "table": o.get("table") or 0, "cells": o.get("cells") or []}
                for o in rec["draft"]], "asks": []}, ensure_ascii=False)
            dpo.append({"prompt": ch["conversations"][0]["value"], "chosen": ch["conversations"][1]["value"], "rejected": rejected,
                        "meta": {"section": rec.get("section"), "episode_at": rec.get("at"), "episode_score": rec.get("score")}})

    pairs = [pv.attach(dict(p, meta={k: v for k, v in p["meta"].items() if k != "_known"}), sources, program_id,
                       [title] + path_of.get(p["meta"]["section"], [p["meta"]["section"]]), known=p["meta"].get("_known"))
             for p in pairs]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "real_pairs.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in pairs), encoding="utf-8")
    (out / "real_sft.jsonl").write_text("".join(json.dumps({"messages": [
        {"role": "user", "content": p["conversations"][0]["value"]}, {"role": "assistant", "content": p["conversations"][1]["value"]}],
        "meta": p["meta"]}, ensure_ascii=False) + "\n" for p in pairs), encoding="utf-8")
    (out / "real_dpo.jsonl").write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in dpo), encoding="utf-8")
    n_sec = sum(1 for p in pairs if p["meta"]["source"] == "real-section")
    n_fill = len(pairs) - n_sec
    report += ["", f"채택: 절 작성 {n_sec}쌍, 표 채우기 {n_fill}쌍, DPO {len(dpo)}쌍 · 폐기 {len(dropped)}절", ""]
    if dropped:
        report += ["폐기 사유:"] + [f"- {h}: {why}" for h, why in dropped]
    (out / "real_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report[-(len(dropped) + 3):]))
    print(f"→ {out}/real_pairs.jsonl · real_sft.jsonl · real_dpo.jsonl · real_report.md")

    if args.push and pairs:
        from zzaimy.dataset.ls_client import LabelStudioClient

        client = LabelStudioClient(db.get_setting("labelstudio_url"), db.get_setting("labelstudio_token"))
        pid = client.ensure_project(args.project, label_config=REVIEW_CONFIG,
                                    description="실문서(양식×완성본)에서 만든 절 작성·표 채우기 쌍 검수")
        if args.replace:
            print(f"기존 태스크 {client.clear_tasks(pid)}건 삭제")
        n = client.push_tasks(pid, pairs)
        print(f"Label Studio 프로젝트 「{args.project}」(id {pid}) 에 {n}건 올렸습니다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
