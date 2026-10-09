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
import os
import re
from pathlib import Path

from zzaimy.ingest import gdocs

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "질문에 대한 실제 답이나 근거를 포함한 검토 결과. 편집 계획 자체에 대한 내부 설명은 금지"},
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["insert", "replace", "style", "bold", "table", "fill", "figure", "rename", "move"]},
                    "section": {"type": "integer", "description": "insert·style·table·fill·figure 일 때 대상 절 번호"},
                    "old": {"type": "string", "description": "replace 일 때 문서에 있는 그대로의 글(한 문단·한 칸 안의 글만 — 여러 줄은 줄마다 replace 를 따로), bold 일 때 굵게 할 글귀"},
                    "text": {"type": "string", "description": "insert·replace 의 글. style 이면 TITLE|HEADING_1|HEADING_2|HEADING_3|NORMAL_TEXT. table 이면 행을 줄바꿈, 칸을 ' | ' 로 나눈 글. rename 이면 새 문서 이름. move 이면 옮길 프로젝트 이름. fill 이면 빈 글. figure 이면 도식 JSON"},
                    "table": {"type": "integer", "description": "fill 일 때 절 안의 표 번호([이 절에 이미 있는 양식 표] 의 '표 n'), 그 밖에는 0"},
                    "cells": {"type": "array", "description": "fill 일 때 넣을 칸들(row·col 은 0부터, 빈 칸 _ 자리). 그 밖에는 빈 배열",
                              "items": {"type": "object", "properties": {"row": {"type": "integer"}, "col": {"type": "integer"}, "text": {"type": "string"}},
                                        "required": ["row", "col", "text"]}},
                },
                "required": ["op", "section", "old", "text", "table", "cells"],
            },
        },
        "asks": {
            "type": "array",
            "description": "자료·문서·기관 정보 어디에도 없어 비워 둔 값 — 담당자에게 물을 것(무엇이든: 사업단명·책임자·예산·일정·수치). 없으면 빈 배열",
            "items": {"type": "object",
                      "properties": {"name": {"type": "string", "description": "값 이름(짧게, 예: 사업단명, 총괄책임자 성명, 1차년도 예산)"},
                                     "hint": {"type": "string", "description": "어디에 쓰이는 값인지 한 구절 — 칸 번호(r·c) 대신 절·표·항목 이름으로"}},
                      "required": ["name", "hint"]},
        },
    },
    "required": ["reply", "ops", "asks"],
}

_PROMPT = """당신은 대학 행정 문서를 함께 쓰는 에이전트다. 담당자가 구글 독스 문서를 열어 두고 채팅으로 지시한다.
지시를 읽고 문서를 어떻게 고칠지 편집 계획을 JSON 으로 낸다. 규칙:
- 지시가 문서를 고치라는 것이면 ops 에 넣기(insert: 해당 절의 끝에 새 문단)나 바꾸기(replace: old 를 text 로, old 는 문서에 있는 글 그대로 — 한 줄(한 문단) 안의 글만, 여러 줄이면 줄마다 따로)를 담는다.
- 서식 지시는 style(절 제목의 단계: TITLE·HEADING_1·HEADING_2·HEADING_3·NORMAL_TEXT), bold(old 에 적은 글귀를 굵게), table(section 절 끝에 표 —
  text 는 행마다 줄바꿈, 칸은 ' | ' 로) 로 낸다.
- 절에 이미 있는 양식 표(성과지표 총괄표·예산표·현황표·추진체계표 등, [이 절에 이미 있는 양식 표] 에 격자가 있다)는 새 표를 만들지 말고
  fill 로 채운다: section=그 절, table=표 번호, cells=[{{row, col, text}}] — 빈 칸(_)에만 값을 넣고 머리 칸·항목 이름 칸은 두며,
  값은 [지난 사업 자료]·근거·담당자가 알려 준 값에 있는 것만 넣는다. 자료에 없는 칸은 비워 두고 asks 에 적는다.
  칸 하나짜리 서식 상자(「□ (세부)과제명: 0000 / 1. 추진배경 / 2. 목표 …」처럼 항목 줄이 든 칸)를 채울 때는 항목 줄을 지우지 말고 그대로 둔 채
  각 항목 줄 아래에 내용을 넣은 글 전체를 그 칸의 text 로 낸다 — 항목을 상자 밖 새 문단으로 옮기지 않는다.
- 기관명·총장·담당자·연락처 같은 기입란은 담당자의 말·프로젝트 정보·근거에 있는 값만 넣는다. 모르는 값은 지어내지 말고
  원본의 ○○○·빈칸을 그대로 두고 reply 에 무엇이 비었는지 적는다.
- 문서 이름(제목)을 바꾸라는 지시는 rename(text 에 새 이름, 예: 사업명·연도·서류 종류)으로 낸다.
- 문서를 어느 프로젝트(폴더)로 옮기라는 지시는 move(text 에 프로젝트 이름)로 낸다.
- 지시가 질문이나 검토 요청이면 ops 는 비우고 reply 에 실제 답을 쓴다. 'ops를 비웠습니다' 같은 내부 처리 설명으로 답을 대신하지 않는다.
- 검토 결과는 확인한 절/내용, 근거와의 차이, 보완할 점을 구분해서 쓴다. 근거가 없거나 본문이 잘렸으면 검토하지 못한 범위를 명시한다.
- 원본의 00·○○ 같은 미기입 표시는 완성된 내용이 아니다. 확인 가능한 값이 없으면 미기입 항목으로 보고하고 임의로 채우지 않는다.
- 글은 문서의 말투와 격식을 따른다. 수치·금액·날짜는 아래 근거 조각이나 문서에 있는 것만 쓰고 지어내지 않는다.
- insert 의 text 에는 새 글만 담는다. 문서에 이미 있는 문장을 다시 쓰지 않는다(그 절의 마지막 문장을 따라 적지 않는다).
- 개인정보(전화·주민번호·계좌)는 쓰지 않는다. 편집 reply는 계획 요약이며 실행 성공을 미리 단정하지 않는다. 질문/검토 reply에는 필요한 설명을 충분히 쓴다.
- 자료·문서·기관 정보·[담당자가 알려 준 값] 어디에도 없어 비워 둔 값(사업단명, 책임자 성명, 예산액, 일정, 수치 등 무엇이든)은 지어내지 말고
  asks 에 이름과 쓰임을 적는다 — 화면이 담당자에게 입력 칸으로 묻는다. 담당자가 이미 알려 준 값은 그대로 쓴다.
- 절을 작성하라는 지시면: 그 절의 [양식 안내·작성방법]이 요구하는 항목을 모두 다루는 본문 문단들을 insert(section=그 절)로 쓴다.
  [문서 전체 작성 규칙]이 개조식(□ ○ - ※)을 요구하면 모든 절의 본문을 그 기호 체계로 쓴다 — 서술형 문단으로 바꾸지 않는다.
  선발 기준·일정·인원·장소·담당자 같은 운영 방침은 재료·담당자가 알려 준 값에 있을 때만 쓴다. 없으면 일반론으로 지어 채우지 말고
  그 자리에 「(확인 필요)」를 두고 asks 에 적는다.
  [지난 사업 자료]는 이 대학의 실제 여건·실적·계획이므로 그 사실·수치·명칭을 바탕으로 쓰되, 자료를 그대로 베끼지 말고 이 양식의 절 구성과
  평가지표에 맞게 재구성한다. 작성방법 상자의 안내문 자체는 옮기지 않는다. 자료에 없는 수치는 만들지 않는다.
- 완성본에서 그림(인포그래픽)으로 보이던 자리 — 정책 동향·산업 수요·대학 여건·대응 방향 같은 요약 도식, 추진 단계 흐름, 추진체계 도식 —
  는 figure 로 낸다: text 에 JSON {{"title": 큰 제목, "layout": "cards" 또는 "flow", "blocks": [{{"title": 상자 제목, "items": [요점 …]}} …], "footer": 아래 띠}}
  (상자 2~6개, 상자마다 요점 2~4줄, 요점은 40자 안). 사진·일러스트는 만들 수 없으니 그림 자리는 이런 도식으로 대신한다. 도식의 사실도 자료에 있는 것만.
- 완성된 사업계획서는 본문 문단만이 아니라 표·상자로 짜여 있다. [지난 사업 자료]의 같은 절이 표(칸을 ' | ' 로 적은 행)로 구성돼 있으면
  같은 머리 칸 구성의 표를 table 로 만들어 이 대학의 값을 넣는다(세부추진과제 표: 추진목표·주요 추진 내용 및 방법·1차년도·2차년도·성과지표,
  총괄표, 지표 근거표 등). 본문 문단과 표를 절의 흐름대로 섞어 낸다 — 문단 몇 개로 끝내지 않는다. 표에 넣을 값이 자료에 없으면 그 칸은 비운다.
  본문에 [절 N] 으로 표시된 소제목 뼈대가 있으면 내용을 그 소제목 절(section=N)에 나눠 넣는다 — 뼈대가 없으면 문단·표를 자유롭게 구성한다.
  분량은 작성방법이 요구하는 항목을 근거·수치·출처와 함께 다 다룰 만큼(소제목 하나에 두세 문단 또는 □ 항목 두세 개 정도, 작성방법에
  「분량 — 약 N자」가 있으면 그 정도) — 한두 문장으로 끝내지 않는다.
  작성방법의 「예시(모양만 참고…) — …」는 기호 깊이·표 한 줄의 채움 정도만 따른다. 예시의 낱말(협약 교육 등)·○○ 자리를 옮겨 쓰지 않고
  내용과 값은 재료에서 가져온다.

[문서 제목] {title}
[절 구조] (번호 · 제목 · 글자 수)
{outline}
[문서 본문]{focus_note}
{text}
{materials}
[근거 조각]
{evidence}
[담당자 지시]
{command}
"""


CONTEXT_TOKENS = int(os.environ.get("ZZAIMY_WRITER_CONTEXT", "16384"))
_CHARS_PER_TOKEN = 1.5
_MIN_OUTPUT = 3000


def _est_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN) + 64


def _fit_context(prompt: str, max_tokens: int, materials: str, text: str) -> tuple[str, int]:
    """재료·본문을 완결된 줄 단위로 축약하고, 고정 지시가 넘치면 전송 전에 거절한다.

    토큰 수는 문자 기반 추정이며 실제 토크나이저 계측을 대체하지 않는다.
    """
    limit = CONTEXT_TOKENS - 256
    over = _est_tokens(prompt) + max_tokens - limit
    if over <= 0:
        return prompt, max_tokens
    # 1) 출력 예산을 절반까지 양보
    give = min(over, max(max_tokens - _MIN_OUTPUT, 0))
    max_tokens -= give
    over -= give
    if over <= 0:
        return prompt, max_tokens
    # 2) 재료 → 본문. 지시에도 같은 문자열이 있으면 임의 위치를 바꾸지 않는다.
    for block, marker in ((materials, "\n[재료 일부 생략: 생략 범위의 사실은 확인하지 못함]\n"),
                          (text, "\n[본문 일부만 제공됨: 전체 검토 완료로 보고하지 마세요.]\n")):
        if not block or prompt.count(block) != 1:
            continue
        cut_chars = int(over * _CHARS_PER_TOKEN) + len(marker) + 200
        keep = max(0, len(block) - cut_chars)
        # 숫자·조건·표 셀을 문장 중간에서 자르지 않는다.
        boundary = block.rfind("\n", 0, keep + 1)
        shorter = (block[:boundary].rstrip() if boundary >= 0 else "") + marker
        if len(shorter) >= len(block):
            continue
        prompt = prompt.replace(block, shorter, 1)
        over = _est_tokens(prompt) + max_tokens - limit
        if over <= 0:
            return prompt, max_tokens
    raise ValueError("문맥 한도 안에서 지시와 근거를 보존할 수 없습니다. 대상 절이나 자료 범위를 좁혀 주세요.")


def _outline_lines(info: dict) -> str:
    return "\n".join(f"{s['index']} · {s['heading']} · {s['chars']}자" for s in info["sections"])


def plan(client, command: str, info: dict, evidence: list[dict] | None = None, max_text: int = 12000,
         materials: str = "", focus: dict | None = None) -> dict:
    """27B 에게 편집 계획을 받는다. client 는 VllmClient(.client 는 OpenAI 호환, .model, ._extra).

    materials 는 절 작성 재료(작성방법·평가지표·지난 자료·기관 정보, drafting.render_materials). focus 가 있으면 [문서 본문]에는
    그 절의 글만 준다(긴 문서는 앞 12000자만 보여 대상 절이 빠지던 문제)."""
    ev = "\n".join(f"- ({c.get('reg_title') or c.get('doc_title') or ''}) {str(c.get('content') or '')[:400]}"
                   for c in (evidence or [])[:5]) or "(없음)"
    focus_note = ""
    if focus is not None:
        from zzaimy.app import drafting

        visible_text = drafting.section_text(info, focus)[:max_text] or info["text"][:max_text]
        focus_note = f" (대상 절 「{focus.get('heading', '')}」 의 현재 글만)"
    else:
        visible_text = info["text"][:max_text]
        if len(info["text"]) > max_text:
            visible_text += "\n[본문 일부만 제공됨: 이후 내용은 확인하지 못했으므로 전체 검토 완료로 보고하지 마세요.]"
    rendered_materials = (materials.strip() + "\n") if materials.strip() else ""
    prompt = _PROMPT.format(title=info["title"], outline=_outline_lines(info), text=visible_text, focus_note=focus_note,
                            materials=rendered_materials,
                            evidence=ev, command=command.strip())
    # 절 하나를 통째로 쓰면 JSON 이 2048 토큰을 넘어 잘린다(실측 2026-09-27: 1.1 절 재작성이 'Unterminated string' 으로 실패).
    # 절 작성(focus)은 넉넉히, 잘리면 한 번 더 짧게 쓰라고 청한다.
    max_tokens = 8192 if focus is not None else 2048
    # 서빙 모델의 문맥 한도(토르 vLLM 16,384) 안에 입력+출력이 들어가야 한다(실측 2026-09-28: 재료가 길어 400 오류).
    # 한글은 대략 1.5자에 토큰 하나 — 입력을 먼저 재료·본문 순으로 줄이고, 그래도 넘치면 출력 예산을 낮춘다.
    prompt, max_tokens = _fit_context(prompt, max_tokens, rendered_materials, visible_text)
    data = None
    for attempt in range(2):
        resp = client.client.chat.completions.create(
            model=client.model,
            messages=[{"role": "user", "content": prompt if attempt == 0 else prompt + "\n(앞선 답이 너무 길어 잘렸다. 본문은 3,000자 안으로, JSON 을 반드시 닫아라.)"}],
            temperature=0.2, max_tokens=max_tokens,
            response_format={"type": "json_schema", "json_schema": {"name": "EditPlan", "schema": PLAN_SCHEMA}},
            extra_body=getattr(client, "_extra", None) or {},
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.strip("`").removeprefix("json").strip() if raw.startswith("`") else raw
        try:
            data = json.loads(raw)
            break
        except json.JSONDecodeError:
            if attempt == 1:
                raise
    raw_ops = []
    for o in data.get("ops", []):
        # 본문 넣기에 표 칸을 함께 실은 계획(실측 2026-10-08: insert 에 table=2·cells — 칸이 버려지고 답은 「채웠다」)은 둘로 나눈다
        if (o.get("op") == "insert" and int(o.get("table") or 0) > 0
                and isinstance(o.get("cells"), list) and any(isinstance(c, dict) for c in o["cells"])):
            raw_ops.append({**o, "table": 0, "cells": []})
            raw_ops.append({"op": "fill", "section": o.get("section"), "old": "", "text": "", "table": o["table"], "cells": o["cells"]})
        else:
            raw_ops.append(o)
    ops = [o for o in raw_ops if o.get("op") in ("insert", "replace", "style", "bold", "table", "fill", "figure", "rename", "move")
           and ((o.get("text") or "").strip() or o.get("op") == "bold"
                or (o.get("op") == "fill" and isinstance(o.get("cells"), list) and any(isinstance(c, dict) for c in o["cells"])))]
    ops = [o for o in ops if o["op"] not in ("rename", "move") or _asked_for(o["op"], command)]
    asks = [{"name": _plain(str(a.get("name") or ""))[:40], "hint": _plain(str(a.get("hint") or ""))[:80]}
            for a in (data.get("asks") or []) if isinstance(a, dict) and str(a.get("name") or "").strip()][:6]
    return {"reply": (data.get("reply") or "").strip(), "ops": ops, "asks": asks}


_RENAME_CUE = re.compile(r"(?:이름|제목|파일명|문서명).{0,12}(?:바꿔|바꾸|변경|수정|고쳐|해\s*줘|으로|로)|(?:으로|로)\s*(?:이름|제목).{0,6}(?:바꿔|바꾸|변경|지어|해)|이름\s*지어|제목\s*지어")
_MOVE_CUE = re.compile(r"(?:폴더|프로젝트).{0,12}(?:옮겨|옮기|이동|넣어|넣어\s*줘|으로|로)|(?:으로|로)\s*(?:옮겨|옮기|이동)")


_CELL_REF = re.compile(r"\s*표\s*\d*\s*의?\s*r\d+\s*,?\s*c\d+\s*칸?(?:에|의)?\s*|\s*\(?\s*r\d+\s*,?\s*c\d+\s*\)?\s*칸?(?:에|의)?\s*"
                       r"|\s*(?:표\s*\d+\s*의\s*)?r\d+(?:\s*[~-]\s*r?\d+)?\s*행\s*(?:\[?c\d+\]?\s*열)?(?:에|의)?\s*|\s*\[c\d+\]\s*열(?:에|의)?\s*")


def _plain(text: str) -> str:
    """담당자에게 보일 말 — 모델이 붙인 내부 칸 번호(「표의 r0 c1 칸에」)를 뗀다(리허설: 입력 요청 안내에 그대로 보임)."""
    return re.sub(r"\s{2,}", " ", _CELL_REF.sub(" ", text)).strip(" ,·")


def _asked_for(op: str, command: str) -> bool:
    """rename·move 는 담당자의 말에 그 뜻이 있어야 한다 — 모델이 근거 조각을 보고 제멋대로 이름을 바꾸거나 옮기지 못하게."""
    cue = _RENAME_CUE if op == "rename" else _MOVE_CUE
    return bool(cue.search(command or ""))


_SENT = re.compile(r"(?<=[.。!?])\s+|\n+")


def _norm(s: str) -> str:
    return re.sub(r"[\s'\"‘’“”·,]+", "", s)


def _drop_existing(text: str, doc_text: str) -> str:
    """넣을 글에서 문서에 이미 있는 줄·문장을 뺀다 — 모델이 절의 마지막 문장을 따라 적거나(실측 2026-09-23) 방금 넣은 S·W·O·T
    문단을 한 덩어리로 다시 붙이는 버릇(실측 2026-09-25: 1.1 절에 515자 중복). 줄 단위 → 문장 단위 두 번 거른다."""
    have_lines = {ln.strip() for ln in (doc_text or "").splitlines() if ln.strip()}
    have_sents = {_norm(x) for x in _SENT.split(doc_text or "") if len(_norm(x)) >= 12}
    kept_lines = []
    for ln in (text or "").splitlines():
        if not ln.strip() or ln.strip() in have_lines:
            continue
        sents = [x for x in _SENT.split(ln) if x.strip()]
        fresh = [x for x in sents if len(_norm(x)) < 12 or _norm(x) not in have_sents]
        if not fresh:
            continue
        if len(fresh) < len(sents):
            ln = " ".join(x.strip() for x in fresh)
        kept_lines.append(ln)
    return "\n".join(kept_lines)


_PIPE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _pipe_cells(line: str) -> list[str] | None:
    """' | ' 로 나눈 표 행이면 칸 목록(양 끝 | 는 뗀다), 아니면 None."""
    if "|" not in line:
        return None
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") else body
    cells = [c.strip() for c in body.split("|")]
    return cells if len(cells) >= 2 else None


def split_pipe_tables(ops: list[dict]) -> list[dict]:
    """insert 글 속의 ' | ' 표(칸 수가 같은 줄이 둘 이상 이어짐)를 table 동작으로 떼어 낸다 — 모델이 표를 글로 내면 독스에
    '가 | 나' 글줄이 그대로 들어갔다(실측 2026-09-29). 앞뒤 글은 insert 로 남고 순서는 그대로(둘 다 절 끝에 붙는다)."""
    out: list[dict] = []
    for o in ops:
        if o.get("op") != "insert" or "|" not in (o.get("text") or ""):
            out.append(o)
            continue
        lines = [ln for ln in o["text"].splitlines() if not ("|" in ln and _PIPE_SEP.match(ln))]     # 마크다운 구분 줄(---|---)
        parts: list[tuple[str, list[str]]] = []                 # ("text"|"table", 줄들)
        i = 0
        while i < len(lines):
            cells = _pipe_cells(lines[i])
            j = i
            if cells is not None:
                while j + 1 < len(lines) and (c := _pipe_cells(lines[j + 1])) is not None and len(c) == len(cells):
                    j += 1
            if cells is not None and j > i:
                parts.append(("table", lines[i:j + 1]))
                i = j + 1
                continue
            if parts and parts[-1][0] == "text":
                parts[-1][1].append(lines[i])
            else:
                parts.append(("text", [lines[i]]))
            i += 1
        for kind, ls in parts:
            if kind == "table":
                out.append(dict(o, op="table", text="\n".join(" | ".join(_pipe_cells(ln) or []) for ln in ls)))
            elif "\n".join(ls).strip():
                out.append(dict(o, text="\n".join(ls)))
    return out


def apply(ops: list[dict], account: str, doc: str, *, user: str, data_dir: Path, scrub=None, http=None,
          doc_text: str = "", figure_folder: str | None = None, headings: set[str] | None = None) -> list[str]:
    """계획을 문서에 적용하고 한 줄씩 결과를 돌려준다. 한 항목이 실패해도 나머지는 계속한다.
    figure_folder 는 도식 그림(PNG)을 올릴 드라이브 폴더(프로젝트 그림 폴더)."""
    lines: list[str] = []
    for o in split_pipe_tables(ops):
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
                if headings and _norm(o["old"]) in headings:
                    # 절 제목 글을 본문 문장으로 바꾸면 절 구조가 깨진다(리허설 2026-10-08: 「사업 개요」 제목이 문장으로) — 제목 바꾸기는 지시가 있을 때만
                    lines.append(f"「{o['old'][:30]}」 은 절 제목이라 바꾸지 않음")
                    continue
                r = gdocs.replace_text(account, doc, o["old"], o["text"], user=user, data_dir=data_dir,
                                       scrub=scrub, http=http)
                if not r["count"] and "\n" in o["old"].strip():
                    # 독스의 찾아 바꾸기는 문단 하나 안에서만 찾는다(리허설 2026-10-08: 서식 상자의 여러 줄을 한 번에 → 0곳).
                    # 줄 수가 같으면 줄마다 바꾼다 — 낱말이 있고 6자 이상인 줄만(「-」 같은 흔한 줄이 문서 전체에서 바뀌지 않게)
                    olds = [x.strip() for x in o["old"].strip().split("\n")]
                    news = [x.strip() for x in str(o.get("text") or "").strip().split("\n")]
                    n = 0
                    if len(olds) == len(news):
                        for a, b in zip(olds, news):
                            if a != b and len(a) >= 6 and re.search(r"[가-힣A-Za-z]", a):
                                n += gdocs.replace_text(account, doc, a, b, user=user, data_dir=data_dir, scrub=scrub, http=http)["count"]
                    r = {"count": n}
                if not r["count"]:
                    r = {"count": _replace_in_cell(o["old"], str(o.get("text") or ""), account, doc, user=user, data_dir=data_dir,
                                                   scrub=scrub, http=http)}
                lines.append(f"「{o['old'][:30]}」 → 「{o['text'][:30]}」 {r['count']}곳")
            elif o["op"] == "style":
                r = gdocs.set_section_style(account, doc, int(o["section"]), o["text"], user=user, data_dir=data_dir, http=http)
                lines.append(f"절 {o['section']} 제목 서식 → {r['style']}")
            elif o["op"] == "bold":
                r = gdocs.emphasize(account, doc, o.get("old") or o.get("text") or "", user=user, data_dir=data_dir, http=http)
                lines.append(f"「{(o.get('old') or o.get('text') or '')[:30]}」 굵게 {r['count']}곳")
            elif o["op"] == "move":
                lines.append(f"프로젝트 「{o['text'][:40]}」 로 옮기기 요청")      # 실제 이동은 호출부(프로젝트 조회 필요)
            elif o["op"] == "rename":
                from zzaimy.ingest.gdrive_files import rename_document

                r = rename_document(account, gdocs.doc_id(doc), o["text"], http=http)
                gdocs._audit(data_dir, {"user": user, "doc": gdocs.doc_id(doc), "action": "rename", "title": r["title"]})
                lines.append(f"문서 이름 → 「{r['title']}」")
            elif o["op"] == "table":
                rows = [[c.strip() for c in ln.split("|")] for ln in (o.get("text") or "").splitlines() if ln.strip()]
                r = gdocs.insert_table(account, doc, int(o["section"]), rows, user=user, data_dir=data_dir, scrub=scrub, http=http)
                lines.append(f"「{r['section']}」 아래에 표 {r['rows']}×{r['cols']}")
            elif o["op"] == "figure":
                r = _apply_figure(o, account, doc, user=user, data_dir=data_dir, http=http, folder=figure_folder)
                lines.append(r)
            elif o["op"] == "fill":
                r = gdocs.fill_table(account, doc, int(o["section"]), int(o.get("table") or 1), [c for c in (o.get("cells") or []) if isinstance(c, dict)],
                                     user=user, data_dir=data_dir, scrub=scrub, http=http)
                lines.append(f"「{r['section']}」 표 {r['table']} 의 칸 {r['cells']}개 채움" + (f"·{r['cleared']}개 비움" if r.get("cleared") else "") + (f"(건너뜀 {r['skipped']})" if r["skipped"] else ""))
        except Exception as e:      # 계정·문서 상태 문제 — 무엇이 안 됐는지 채팅에 남긴다
            lines.append(f"적용 실패({type(e).__name__}): {str(e)[:80]}")
    return lines


def _apply_figure(o: dict, account: str, doc: str, *, user: str, data_dir: Path, http=None, folder: str | None) -> str:
    """도식: 모델의 spec → PNG → 드라이브에 올려 잠깐 공개 → 절 끝에 그림 → 공개 닫기. 그림 파일은 프로젝트 그림 폴더에 남는다."""
    from zzaimy.app import infographic
    from zzaimy.ingest import gdrive_files

    spec = infographic.parse_spec(o.get("text") or "")
    if not spec:
        return "도식 내용(JSON)이 아니라 건너뜀"
    png = infographic.render(spec)
    name = f"도식 {str(spec.get('title') or '')[:30] or o.get('section')}.png"
    up = gdrive_files.upload_file(account, png, name, "image/png", folder, http=http, reuse=False)
    perm = gdrive_files.share_anyone(account, up["id"], http=http)
    try:
        r = gdocs.insert_image(account, doc, int(o["section"]), f"https://drive.google.com/uc?export=download&id={up['id']}",
                               user=user, data_dir=data_dir, http=http)
    finally:
        gdrive_files.unshare(account, up["id"], perm, http=http)
    return f"「{r['section']}」 아래에 도식 「{str(spec.get('title') or '')[:24]}」 (상자 {len(spec.get('blocks') or [])}개)"


def describe(ops: list[dict], info: dict) -> str:
    """확인 후 적용 모드에서 담당자에게 보여 줄 계획 요약."""
    heads = {s["index"]: s["heading"] for s in info["sections"]}
    out = []
    for i, o in enumerate(ops, 1):
        if o["op"] == "insert":
            out.append(f"{i}) 「{heads.get(int(o['section']), o['section'])}」 아래에 추가:\n{o['text']}")
        elif o["op"] == "replace":
            out.append(f"{i}) 바꾸기: 「{o.get('old', '')[:60]}」 → 「{o['text'][:60]}」")
        elif o["op"] == "figure":
            out.append(f"{i}) 「{heads.get(int(o['section']), o['section'])}」 아래에 도식: {o['text'][:80]}")
        elif o["op"] == "fill":
            cells = [c for c in (o.get("cells") or []) if isinstance(c, dict)]
            out.append(f"{i}) 「{heads.get(int(o['section']), o['section'])}」 표 {o.get('table')} 채우기: " +
                       ", ".join(f"({c.get('row')},{c.get('col')})={str(c.get('text') or '')[:16]}" for c in cells[:8]) + (" …" if len(cells) > 8 else ""))
        else:
            out.append(f"{i}) 서식({o['op']}): 절 {o.get('section')} · {o.get('old') or ''} {o.get('text') or ''}"[:120])
    return "\n".join(out)


def run(db, session_id: int, owner: str, command: str, link: dict, *, client, data_dir: Path, scrub=None,
        evidence: list[dict] | None = None, confirm: bool = False, http=None, materials: str = "",
        focus: dict | None = None, info: dict | None = None, references: list[dict] | None = None,
        before_apply=None, figure_folder: str | None = None) -> tuple[str, list[dict]]:
    """명령 하나를 처리해 (채팅에 남길 글, 적용/보류한 ops) 를 돌려준다.

    절 작성이면(focus) 재료와 함께 부르고, 실행 기록(재료·지시·모델의 초안·참고 정답)을 남긴다 — Writer 학습 데이터 공방의 재료."""
    info = info or gdocs.get(link["account"], link["doc"], http)
    if focus is None and "[이 절에 이미 있는 양식 표" not in materials:
        try:                                            # 절을 지목하지 않은 명령에도 양식 표 격자를 준다(번호 짐작 방지)
            grids = gdocs.doc_table_grids(link["account"], link["doc"], http=http, info=info)
            if grids:
                materials = (materials + "\n\n" if materials else "") + gdocs.render_table_grids(grids, max_rows=12)
        except Exception:
            pass                                        # 격자를 못 읽어도 편집은 계속
    p = plan(client, command, info, evidence, materials=materials, focus=focus)
    # 모델이 물어야 한다고 한 값 — 호출부가 입력 양식 선택지로 띄운다(chat_asks 에 쌓아 둠, 답이 오면 지운다)
    if p.get("asks"):
        try:
            prev = json.loads(db.get_setting(f"chat_asks:{session_id}", "") or "[]")
        except Exception:
            prev = []
        names = {a["name"] for a in prev}
        db.set_setting(f"chat_asks:{session_id}", json.dumps(prev + [a for a in p["asks"] if a["name"] not in names], ensure_ascii=False))
    if focus is not None:
        score = None
        if references and p["ops"]:
            from zzaimy.app import drafting

            draft_text = "\n".join(str(o.get("text") or "") for o in p["ops"]) + "\n" + "\n".join(
                str(c.get("text") or "") for o in p["ops"] if o.get("op") == "fill" for c in (o.get("cells") or []) if isinstance(c, dict))
            score = drafting.score_against_reference(draft_text, "\n".join(r.get("text") or "" for r in references))
        _episode(data_dir, {"session": session_id, "user": owner, "doc": gdocs.doc_id(link["doc"]), "section": focus.get("heading"),
                            "command": command, "materials": materials, "reply": p["reply"],
                            "draft": [{"op": o.get("op"), "text": o.get("text"), **({"table": o.get("table"), "cells": o.get("cells")} if o.get("op") == "fill" else {})} for o in p["ops"]],
                            "references": references or [], "score": score})
    if not p["ops"]:
        return p["reply"] or "문서를 고칠 내용은 없습니다.", []
    unsupported = number_check(p["ops"], [materials, info.get("text") or "", command]
                               + [str(c.get("content") or "") for c in (evidence or [])])
    if unsupported:
        # 절대 규칙 1 — 생성된 수치는 인출된 근거에 있어야 한다. 넣기는 하되(70% 초안) 담당자가 바로 확인하게 짚는다
        p["reply"] = (p["reply"] + "\n근거에서 찾지 못한 수치(확인 필요): " + ", ".join(unsupported[:8])).strip()
    stray = source_check(p["ops"], [materials, command] + [str(c.get("reg_title") or "") + " " + str(c.get("content") or "") for c in (evidence or [])])
    if stray:
        p["reply"] = (p["reply"] + "\n재료에 없는 출처(확인 필요): " + ", ".join(stray[:6])).strip()
    if confirm:
        db.set_setting(f"chat_google_doc_pending:{session_id}", json.dumps(p["ops"], ensure_ascii=False))
        return (p["reply"] + "\n\n확인 후 적용이 켜져 있어 아직 문서에 쓰지 않았습니다. 아래 계획을 확인하고 적용을 누르세요.\n"
                + describe(p["ops"], info)), p["ops"]
    pre: list[str] = []
    if before_apply is not None:
        try:
            pre = list(before_apply() or [])            # 계획이 나온 뒤에만 비운다 — 모델이 실패하면 문서는 그대로
        except Exception as e:
            pre = [f"비우기 실패({type(e).__name__}) — 덧붙입니다"]
    heads = None if re.search(r"제목", command or "") else {_norm(x["heading"]) for x in info.get("sections", []) if x.get("heading")}
    lines = apply(p["ops"], link["account"], link["doc"], user=owner, data_dir=data_dir, scrub=scrub, http=http, headings=heads,
                  doc_text=info["text"] if not pre else "", figure_folder=figure_folder)
    return (p["reply"] + "\n\n적용됨:\n" + "\n".join(f"- {ln}" for ln in pre + lines)), p["ops"]


def _replace_in_cell(old: str, new: str, account: str, doc: str, *, user: str, data_dir: Path, scrub=None, http=None) -> int:
    """찾아 바꾸기가 0곳일 때 — 바꿀 글이 어느 표 칸(서식 상자) 안에 띄어쓰기만 다른 채로 있으면 그 칸 글을 고쳐 칸째 채운다.
    독스의 찾아 바꾸기는 칸 속 여러 줄을 한 번에 못 찾는다(리허설 8: 「□ (세부)과제명: 0000 / 1. 추진배경 …」 상자가 그대로 남음). 한 칸만 고친다."""
    chars = [c for c in (old or "") if not c.isspace()]
    if len(chars) < 6:
        return 0
    rx = re.compile(r"\s*".join(re.escape(c) for c in chars))
    try:
        for g in gdocs.doc_table_grids(account, doc, http=http, sep="\n"):        # 칸 안 줄바꿈을 살려 읽는다(상자 모양 유지)
            for ri, row in enumerate(g["rows"]):
                for ci, cell in enumerate(row):
                    if (ri, ci) in (g.get("covered") or set()) or not rx.search(cell or ""):
                        continue
                    text = rx.sub(lambda _m: new, cell, count=1)
                    gdocs.fill_table(account, doc, int(g["section_index"]), int(g["n"]), [{"row": ri, "col": ci, "text": text}],
                                     user=user, data_dir=data_dir, scrub=scrub, http=http)
                    return 1
    except Exception:
        return 0
    return 0


def number_check(ops: list[dict], evidence_texts: list[str]) -> list[str]:
    """편집 계획이 넣는 글·칸의 수치 가운데 근거(재료·기준 조각·현재 문서·지시)에 없는 것 — 결정론(verify.numbers)."""
    from zzaimy.verify.numbers import verify_numbers

    draft = "\n".join(str(o.get("text") or "") for o in ops if o.get("op") in ("insert", "replace", "table"))
    draft += "\n" + "\n".join(str(c.get("text") or "") for o in ops if o.get("op") == "fill"
                              for c in (o.get("cells") or []) if isinstance(c, dict))
    if not draft.strip():
        return []
    from zzaimy.verify.numbers import _context_of

    audit = verify_numbers(draft, [t for t in evidence_texts if t])
    return [_context_of(draft, v, radius=10) for v in audit.violations]       # 「…에서 45.7%로 높…」 꼴로 짧게


_CITE = re.compile(r"[(（]\s*(?:출처|근거)\s*[:：]\s*([^)）\n]{2,80})[)）]")


def source_check(ops: list[dict], evidence_texts: list[str]) -> list[str]:
    """넣는 글의 「(출처: …)」 가 재료(문서 제목·본문)에 없는 이름이면 돌려준다 — 모델이 그럴듯한 문서 이름을 지어내던 것(시험 2026-10-09).
    출처 이름의 앞 열두 글자(띄어쓰기 무시)가 재료 어디에도 없으면 지어낸 것으로 본다."""
    draft = "\n".join(str(o.get("text") or "") for o in ops if o.get("op") in ("insert", "replace", "table"))
    hay = re.sub(r"\s+", "", "\n".join(t for t in evidence_texts if t))
    out = []
    for m in _CITE.finditer(draft):
        for name in re.split(r"[,;·/]|\s및\s", m.group(1)):
            key = re.sub(r"\s+", "", name)[:12]
            if len(key) >= 4 and not re.fullmatch(r"[\d.,\-~쪽p]+", key) and key not in hay and name.strip() not in out:
                out.append(name.strip())
    return out


def apply_pending(db, session_id: int, owner: str, link: dict, *, data_dir: Path, scrub=None, http=None) -> list[str]:
    """확인 후 적용 모드에서 보류한 계획을 적용한다."""
    raw = db.get_setting(f"chat_google_doc_pending:{session_id}", "") or ""
    if not raw:
        return []
    ops = json.loads(raw)
    lines = apply(ops, link["account"], link["doc"], user=owner, data_dir=data_dir, scrub=scrub, http=http)
    db.set_setting(f"chat_google_doc_pending:{session_id}", "")
    return lines


def _episode(data_dir: Path, rec: dict) -> None:
    """절 작성 한 번의 기록 — data/platform/drafting_episodes.jsonl. 재료·지시·초안·참고 정답을 함께 두어 학습 재료로 고를 수 있게."""
    import datetime as _dt

    try:
        rec = dict(rec, at=_dt.datetime.now().astimezone().isoformat(timespec="seconds"))
        with (Path(data_dir) / "drafting_episodes.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
