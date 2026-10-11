"""문서 양식 고르기 — 27B 가 요청·대화·프로젝트 자료를 보고 쓸 양식을 고른다(2026-10-11 사용자: "27b 학습하면 27b 가 문서 양식
끌고 와서 작업해야").

코드는 후보만 모으고(프로젝트의 작성 서식, 대화에서 정한 목차, 공통 양식 5종) 모델의 고름이 후보 안인지 확인한다. 모델이 못 고르면
지금까지의 규칙(목차 지칭 → 목차, 서식 있으면 서식, 서류 갈래 낱말 → 공통 양식)으로 물러난다. 양식의 모양(표·지침·들여쓰기)은
코드가 만든다 — 모델은 「무엇을 쓸지」만 고른다.

고른 기록(form_choice_episodes.jsonl)은 학습 재료다 — 요청·후보·모델 고름·규칙 고름·담당자가 바꿨는지. 데이터셋 생성은 아스트라 몫이라
여기서는 기록만 남긴다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

_FORM_TITLE = re.compile(r"서식|양식")
_POINTER = re.compile(r"(?:위|앞|이|그|방금|정한|잡은|만든)\s*(?:의\s*)?(?:목차|구성|개요|틀)|목차\s*(?:대로|에\s*따라|에\s*맞춰|를\s*바탕)")

PROMPT = """당신은 행정 문서 작성 에이전트다. 담당자의 요청을 보고, 아래 후보 가운데 문서의 뼈대로 쓸 양식 하나를 고른다.

판단 기준(순서대로):
1. 담당자가 대화에서 목차·구성을 정했고 그대로 쓰라고 했으면 「대화에서 정한 목차」.
2. 사업 공고·주관 기관이 준 작성 서식(프로젝트 문서)이 요청한 서류에 맞으면 그 서식 — 평가는 서식대로 받는다.
3. 맞는 서식이 없으면 서류 갈래(계획서·실적보고서·단위 프로그램 계획/결과·회의록)에 맞는 공통 양식.

[담당자 요청]
{request}

[후보]
{candidates}

JSON 하나만 답한다: {{"choice": "후보 id", "why": "고른 까닭 한 문장"}}"""


def candidates(db, project: dict | None, history: list[dict], request: str) -> list[dict]:
    """[{id, kind(form|outline|common), label, note, doc?, spec?}] — 프로젝트 작성 서식, 대화에서 정한 목차, 공통 양식."""
    from zzaimy.app import storage
    from zzaimy.ingest import gdocs_templates

    out: list[dict] = []
    if project:
        docs = [db.get_document(i) for i in db.get_project_criteria_ids(int(project["id"]))]
        docs += db.list_documents(project["sector"], project_id=int(project["id"]))
        seen = set()
        for d in docs:
            if not d or d["id"] in seen:
                continue
            seen.add(d["id"])
            title = storage.title_of(d.get("filename") or "")
            if _FORM_TITLE.search(title) or d.get("kind") == "form":
                out.append({"id": f"form:{d['id']}", "kind": "form", "label": title[:60], "doc": d,
                            "note": "프로젝트에 올린 작성 서식(한글·워드 원본) — 서식의 표·항목 그대로 채운다"})
    prev = next((m["content"] for m in reversed(history or []) if m.get("role") == "assistant"), "")
    spec = gdocs_templates.outline_spec(gdocs_templates.SPECS["plan"], prev) if prev else None
    if spec:
        n_ch = sum(1 for b in spec["blocks"] if b.get("h") == 1)
        n_sec = sum(1 for b in spec["blocks"] if b.get("h") == 2)
        heads = [b["text"] for b in spec["blocks"] if b.get("h") == 1][:4]
        out.append({"id": "outline", "kind": "outline", "label": f"대화에서 정한 목차(장 {n_ch}·절 {n_sec})", "spec": spec,
                    "note": "앞 답에서 정한 목차: " + " / ".join(heads)})
    for sid, sp in gdocs_templates.SPECS.items():
        out.append({"id": f"common:{sid}", "kind": "common", "label": sp["title"].replace("(구글 독스)", "").strip(), "spec": sp,
                    "note": "여러 사업 문서의 공통 뼈대"})
    return out


def rule_choice(cands: list[dict], request: str) -> str:
    """규칙 고름(모델이 못 고를 때·학습 견줌용) — 목차 지칭 → 목차, 맞는 서식 → 서식, 서류 갈래 → 공통 양식, 그 밖엔 계획서."""
    from zzaimy.ingest import gdocs_templates

    ids = {c["id"] for c in cands}
    if _POINTER.search(request or "") and "outline" in ids:
        return "outline"
    spec = gdocs_templates.pick(request or "")
    pats = dict(gdocs_templates.PICK).get(spec["id"], ()) if spec else ()
    forms = [c for c in cands if c["kind"] == "form" and all(re.search(p, c["label"]) for p in pats[-1:])]
    if forms:
        return forms[0]["id"]
    if spec and f"common:{spec['id']}" in ids:
        return f"common:{spec['id']}"
    return "common:plan"


def render_candidates(cands: list[dict]) -> str:
    return "\n".join(f"- id={c['id']} · {c['label']} — {c['note']}" for c in cands)


def decide(client, request: str, cands: list[dict]) -> dict:
    """{choice, why, by(model|rule), rule} — 모델이 후보 밖을 고르거나 실패하면 규칙."""
    rule = rule_choice(cands, request)
    ids = {c["id"] for c in cands}
    if client is not None:
        try:
            resp = client.client.chat.completions.create(
                model=client.model, temperature=0.0, max_tokens=200, extra_body=getattr(client, "_extra", {}),
                messages=[{"role": "user", "content": PROMPT.format(request=request[:1500], candidates=render_candidates(cands))}])
            raw = resp.choices[0].message.content or ""
            m = re.search(r"\{.*\}", raw, re.S)
            got = json.loads(m.group(0)) if m else {}
            choice = str(got.get("choice") or "").strip()
            if choice in ids:
                return {"choice": choice, "why": str(got.get("why") or "")[:200], "by": "model", "rule": rule}
        except Exception:
            pass
    return {"choice": rule, "why": "", "by": "rule", "rule": rule}


def record(data_dir: Path, rec: dict) -> None:
    p = Path(data_dir) / "form_choice_episodes.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **rec}, ensure_ascii=False) + "\n")
