"""대화에 연결된 구글 독스를 에이전트가 직접 고친다 — 명령 → 편집 계획(형식 강제) → 즉시 적용 → 채팅에 결과.

사용자가 원한 모습(2026-09-23): 왼쪽 구글 독스, 오른쪽 에이전트 채팅. 채팅으로 "추진 배경을 세 문장으로 보강해"라고
하면 담당자가 답을 옮겨 넣는 것이 아니라 에이전트가 그 문서를 바로 고치고, 채팅에는 무엇을 고쳤는지가 남는다.

어떻게. 27B 에게 문서의 절 구조와 본문, 관련 근거 조각, 명령을 주고 편집 계획을 JSON 스키마로 강제해 받는다
(vLLM guided_json — 공고 스키마 추출과 같은 방식). 계획은 절에 넣기(insert)·글 바꾸기(replace)·아무것도 안 함이다.
플랫폼이 계획을 독스 API 로 적용한다. 넣는 글은 개인정보 검사기를 거치고, 모든 쓰기는 gdocs_audit.jsonl 에 남는다.
되돌리기는 구글 독스의 버전 기록으로 한다. "확인 후 적용" 을 켠 대화에서는 계획만 보여 주고 담당자가 적용을 누른다.
수치는 근거 조각에 있는 것만 쓰라고 지시한다(절대 규칙 1). 검증기까지 거치지는 않는다 — 초안 생성기와 같은 한계.
"""

from __future__ import annotations

import json
from pathlib import Path

from zzaimy.ingest import gdocs

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "담당자에게 보낼 한두 문장. 무엇을 어떻게 고쳤는지 또는 질문에 대한 답"},
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["insert", "replace", "style", "bold", "table"]},
                    "section": {"type": "integer", "description": "insert·style·table 일 때 대상 절 번호"},
                    "old": {"type": "string", "description": "replace 일 때 문서에 있는 그대로의 글, bold 일 때 굵게 할 글귀"},
                    "text": {"type": "string", "description": "insert·replace 의 글. style 이면 TITLE|HEADING_1|HEADING_2|HEADING_3|NORMAL_TEXT. table 이면 행을 줄바꿈, 칸을 ' | ' 로 나눈 글"},
                },
                "required": ["op", "section", "old", "text"],
            },
        },
    },
    "required": ["reply", "ops"],
}

_PROMPT = """당신은 대학 행정 문서를 함께 쓰는 에이전트다. 담당자가 구글 독스 문서를 열어 두고 채팅으로 지시한다.
지시를 읽고 문서를 어떻게 고칠지 편집 계획을 JSON 으로 낸다. 규칙:
- 지시가 문서를 고치라는 것이면 ops 에 넣기(insert: 해당 절의 끝에 새 문단)나 바꾸기(replace: old 를 text 로, old 는 문서에 있는 글 그대로)를 담는다.
- 서식 지시는 style(절 제목의 단계: TITLE·HEADING_1·HEADING_2·HEADING_3·NORMAL_TEXT), bold(old 에 적은 글귀를 굵게), table(section 절 끝에 표 —
  text 는 행마다 줄바꿈, 칸은 ' | ' 로) 로 낸다.
- 지시가 질문이나 검토 요청이면 ops 는 비우고 reply 에만 답한다.
- 글은 문서의 말투와 격식을 따른다. 수치·금액·날짜는 아래 근거 조각이나 문서에 있는 것만 쓰고 지어내지 않는다.
- insert 의 text 에는 새 글만 담는다. 문서에 이미 있는 문장을 다시 쓰지 않는다(그 절의 마지막 문장을 따라 적지 않는다).
- 개인정보(전화·주민번호·계좌)는 쓰지 않는다. reply 는 한두 문장, 무엇을 어느 절에 어떻게 했는지.

[문서 제목] {title}
[절 구조] (번호 · 제목 · 글자 수)
{outline}
[문서 본문]
{text}
[근거 조각]
{evidence}
[담당자 지시]
{command}
"""


def _outline_lines(info: dict) -> str:
    return "\n".join(f"{s['index']} · {s['heading']} · {s['chars']}자" for s in info["sections"])


def plan(client, command: str, info: dict, evidence: list[dict] | None = None, max_text: int = 12000) -> dict:
    """27B 에게 편집 계획을 받는다. client 는 VllmClient(.client 는 OpenAI 호환, .model, ._extra)."""
    ev = "\n".join(f"- ({c.get('reg_title') or c.get('doc_title') or ''}) {str(c.get('content') or '')[:400]}"
                   for c in (evidence or [])[:5]) or "(없음)"
    prompt = _PROMPT.format(title=info["title"], outline=_outline_lines(info), text=info["text"][:max_text],
                            evidence=ev, command=command.strip())
    resp = client.client.chat.completions.create(
        model=client.model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2, max_tokens=2048,
        response_format={"type": "json_schema", "json_schema": {"name": "EditPlan", "schema": PLAN_SCHEMA}},
        extra_body=getattr(client, "_extra", None) or {},
    )
    raw = (resp.choices[0].message.content or "").strip()
    raw = raw.strip("`").removeprefix("json").strip() if raw.startswith("`") else raw
    data = json.loads(raw)
    ops = [o for o in data.get("ops", []) if o.get("op") in ("insert", "replace", "style", "bold", "table")
           and ((o.get("text") or "").strip() or o.get("op") == "bold")]
    return {"reply": (data.get("reply") or "").strip(), "ops": ops}


def _drop_existing(text: str, doc_text: str) -> str:
    """넣을 글에서 문서에 이미 그대로 있는 줄을 뺀다 — 모델이 절의 마지막 문장을 따라 적는 버릇(실측 2026-09-23)."""
    have = {ln.strip() for ln in (doc_text or "").splitlines() if ln.strip()}
    kept = [ln for ln in (text or "").splitlines() if ln.strip() and ln.strip() not in have]
    return "\n".join(kept)


def apply(ops: list[dict], account: str, doc: str, *, user: str, data_dir: Path, scrub=None, http=None,
          doc_text: str = "") -> list[str]:
    """계획을 문서에 적용하고 한 줄씩 결과를 돌려준다. 한 항목이 실패해도 나머지는 계속한다."""
    lines: list[str] = []
    for o in ops:
        if o["op"] == "insert" and doc_text:
            o = dict(o, text=_drop_existing(o["text"], doc_text))
            if not o["text"].strip():
                lines.append("문서에 이미 있는 글이라 넣지 않음")
                continue
        try:
            if o["op"] == "insert":
                r = gdocs.insert_into_section(account, doc, int(o["section"]), o["text"], user=user,
                                              data_dir=data_dir, scrub=scrub, http=http)
                lines.append(f"「{r['section']}」 아래에 {r['chars']}자 추가")
            elif o["op"] == "replace":
                if not (o.get("old") or "").strip():
                    lines.append("바꿀 글이 비어 있어 건너뜀")
                    continue
                r = gdocs.replace_text(account, doc, o["old"], o["text"], user=user, data_dir=data_dir,
                                       scrub=scrub, http=http)
                lines.append(f"「{o['old'][:30]}」 → 「{o['text'][:30]}」 {r['count']}곳")
            elif o["op"] == "style":
                r = gdocs.set_section_style(account, doc, int(o["section"]), o["text"], user=user, data_dir=data_dir, http=http)
                lines.append(f"절 {o['section']} 제목 서식 → {r['style']}")
            elif o["op"] == "bold":
                r = gdocs.emphasize(account, doc, o.get("old") or o.get("text") or "", user=user, data_dir=data_dir, http=http)
                lines.append(f"「{(o.get('old') or o.get('text') or '')[:30]}」 굵게 {r['count']}곳")
            elif o["op"] == "table":
                rows = [[c.strip() for c in ln.split("|")] for ln in (o.get("text") or "").splitlines() if ln.strip()]
                r = gdocs.insert_table(account, doc, int(o["section"]), rows, user=user, data_dir=data_dir, scrub=scrub, http=http)
                lines.append(f"「{r['section']}」 아래에 표 {r['rows']}×{r['cols']}")
        except Exception as e:      # 계정·문서 상태 문제 — 무엇이 안 됐는지 채팅에 남긴다
            lines.append(f"적용 실패({type(e).__name__}): {str(e)[:80]}")
    return lines


def describe(ops: list[dict], info: dict) -> str:
    """확인 후 적용 모드에서 담당자에게 보여 줄 계획 요약."""
    heads = {s["index"]: s["heading"] for s in info["sections"]}
    out = []
    for i, o in enumerate(ops, 1):
        if o["op"] == "insert":
            out.append(f"{i}) 「{heads.get(int(o['section']), o['section'])}」 아래에 추가:\n{o['text']}")
        elif o["op"] == "replace":
            out.append(f"{i}) 바꾸기: 「{o.get('old', '')[:60]}」 → 「{o['text'][:60]}」")
        else:
            out.append(f"{i}) 서식({o['op']}): 절 {o.get('section')} · {o.get('old') or ''} {o.get('text') or ''}"[:120])
    return "\n".join(out)


def run(db, session_id: int, owner: str, command: str, link: dict, *, client, data_dir: Path, scrub=None,
        evidence: list[dict] | None = None, confirm: bool = False, http=None) -> tuple[str, list[dict]]:
    """명령 하나를 처리해 (채팅에 남길 글, 적용/보류한 ops) 를 돌려준다."""
    info = gdocs.get(link["account"], link["doc"], http)
    p = plan(client, command, info, evidence)
    if not p["ops"]:
        return p["reply"] or "문서를 고칠 내용은 없습니다.", []
    if confirm:
        db.set_setting(f"chat_google_doc_pending:{session_id}", json.dumps(p["ops"], ensure_ascii=False))
        return (p["reply"] + "\n\n확인 후 적용이 켜져 있어 아직 문서에 쓰지 않았습니다. 아래 계획을 확인하고 적용을 누르세요.\n"
                + describe(p["ops"], info)), p["ops"]
    lines = apply(p["ops"], link["account"], link["doc"], user=owner, data_dir=data_dir, scrub=scrub, http=http,
                  doc_text=info["text"])
    return (p["reply"] + "\n\n적용됨:\n" + "\n".join(f"- {ln}" for ln in lines)), p["ops"]


def apply_pending(db, session_id: int, owner: str, link: dict, *, data_dir: Path, scrub=None, http=None) -> list[str]:
    """확인 후 적용 모드에서 보류한 계획을 적용한다."""
    raw = db.get_setting(f"chat_google_doc_pending:{session_id}", "") or ""
    if not raw:
        return []
    ops = json.loads(raw)
    lines = apply(ops, link["account"], link["doc"], user=owner, data_dir=data_dir, scrub=scrub, http=http)
    db.set_setting(f"chat_google_doc_pending:{session_id}", "")
    return lines
