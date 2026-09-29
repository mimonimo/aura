"""문서 구조 단계 문답(CoT) — 사업명 → 개요 → 목차 → 절의 요구 항목 → 절의 뼈대, 단계마다 짧은 생각과 답(사용자 지시 2026-09-29).

목적: 모델이 '사업 계획서의 구조를 세우는 능력'을 배운다. 학습된 모델이 새 사업의 구조(목차·절마다 뼈대)를 먼저 쓰고,
그 뼈대의 칸을 검색(RAG)·추가 문서의 근거로 채운다(채우기 능력은 real_pairs 의 절 작성 쌍이 가르친다). 지식(사업 목적·
선정 규모 같은 사실)은 입력의 근거 조각에서만 오게 하여(규칙 8) 답의 수치가 입력에 없으면 폐기한다(규칙 1).

단계와 입력·출력:
  1 사업명 → 개요        입력 = 공고·기본계획의 목적·개요 조각 + "이 사업이 뭐야?"  출력 = 사업명 + 개요(근거에서)
  2 개요 → 목차          입력 = 사업명·개요 + 평가편람의 평가영역 조각          출력 = 계획서 목차(부·절)
  3 목차 → 절의 요구 항목  입력 = 사업명·개요·목차 + 그 절의 평가 착안점 조각       출력 = 그 절이 다뤄야 할 항목(작성방법)
  4 절 → 절의 뼈대       입력 = 위 사슬 + 요구 항목                              출력 = 소제목·표 머리·도식 자리(내용 없는 뼈대)
출력은 '[1단계: …] [2단계: …] …' 로 번호 붙인 추론 단계 + '[답]' (참고: daddynkidsmakers 2025-06 CoT 파인튜닝 글의
instruction/input/output 형식 — output 에 "[N단계: 설명]" 추론 뒤 최종 답). 대화형(단계 누적)과 단발형(사슬을 입력에 적음) 둘 다 내고,
단발형은 Alpaca 꼴(instruction·input·output)로도 낸다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from zzaimy.dataset.real_pairs import FormSection, _numbers, fact_numbers

_ROMAN = "ⅠⅡⅢⅣⅤⅥⅦⅧ"
_SUBHEAD = re.compile(r"^\s*(?:\(\d+\)|[❑❐□■○◎◇◆▣▶►]|[가-힣]\.|\d+\)|[①-⑳]|[-])\s*\S")
_BULLET_LINE = re.compile(r"^\s*[•▪∙·\-–]\s*")

PREFACE = "당신은 대학 행정 문서의 구조를 세우는 에이전트다. 아래 근거만 보고 [1단계: …] [2단계: …] 처럼 추론 단계를 적은 뒤 [답]을 쓴다. 근거에 없는 사실·수치는 쓰지 않는다."


@dataclass
class Node:
    heading: str
    level: int                       # 0 부, 1 절, 2 소절, 3 세부
    part: str = ""
    instructions: str = ""
    skeleton: list[str] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)


def part_titles(chunks: list[dict], limit: int = 8) -> list[str]:
    """완성본 앞쪽(차례)의 'Ⅰ. …' 줄 — 부 제목. 같은 로마 숫자는 처음 것만."""
    out: dict[str, str] = {}
    for c in chunks:
        if len(out) >= limit or int(c.get("seq") or 0) > 40:
            break
        for ln in (c.get("content") or "").split("\n"):
            m = re.match(r"^\s*([" + _ROMAN + r"])\s*[.．]?\s*(\S.{2,50})$", ln.strip())
            if m and m.group(1) not in out:
                out[m.group(1)] = f"{m.group(1)}. {m.group(2).strip()}"
    return [out[k] for k in _ROMAN if k in out]


def build_tree(sections: list[FormSection], parts: list[str], plan: list[dict]) -> list[Node]:
    """양식 절 목록 → 부(번호가 1 로 되돌아가는 자리)별 절 나무. 절 뼈대는 완성본의 같은 절에서(소제목·표 머리·도식 제목)."""
    by_heading = {e["heading"]: e for e in plan}
    roots: list[Node] = []
    stack: list[Node] = []
    for sec in sections:
        num = sec.heading.split(" ", 1)[0].rstrip(".")
        if sec.level == 1 and num == "1":
            title = parts[len(roots)] if len(roots) < len(parts) else f"{_ROMAN[len(roots)] if len(roots) < len(_ROMAN) else len(roots) + 1}. 제{len(roots) + 1}부"
            roots.append(Node(heading=title, level=0))
            stack = [roots[-1]]
        if not roots:
            roots.append(Node(heading="Ⅰ. 본문", level=0))
            stack = [roots[-1]]
        node = Node(heading=sec.heading, level=sec.level, part=roots[-1].heading, instructions=sec.instructions,
                    skeleton=skeleton_of(by_heading.get(sec.heading)))
        while len(stack) > 1 and stack[-1].level >= sec.level:
            stack.pop()
        stack[-1].children.append(node)
        stack.append(node)
    return roots


def skeleton_of(entry: dict | None, limit: int = 14) -> list[str]:
    """완성본 절의 뼈대 — 소제목 줄(짧은 표지 줄), 표는 머리 칸, 도식은 제목·상자 제목. 본문 문장·수치는 넣지 않는다."""
    if not entry or not entry.get("parts"):
        return []
    out: list[str] = []
    for kind, payload in entry["parts"]:
        if kind == "text":
            for ln in str(payload).split("\n"):
                s = ln.strip()
                if not s or len(s) > 42 or _BULLET_LINE.match(s):
                    continue
                if _SUBHEAD.match(s) or (s.startswith("<") and s.endswith(">")) or s.startswith("["):
                    if not re.search(r"\d{2,}", re.sub(r"^\(\d+\)|\[증빙[^\]]*\]", "", s)):
                        out.append(f"소제목: {s}")
        elif kind == "table":
            rows = payload.get("rows") or []
            if rows:
                head = [c for c in rows[0] if c.strip()][:8]
                if head:
                    out.append("표: " + " | ".join(head))
        elif kind == "figure":
            titles = [str(b.get("title"))[:20] for b in payload.get("blocks") or [] if b.get("title")]
            out.append("도식: " + (payload.get("title") or "")[:40] + (" (" + " · ".join(titles[:4]) + ")" if titles else ""))
        if len(out) >= limit:
            break
    dedup: list[str] = []
    for x in out:
        if x not in dedup:
            dedup.append(x)
    return dedup[:limit]


def outline_text(roots: list[Node]) -> str:
    lines: list[str] = []

    def walk(n: Node):
        lines.append(("  " * max(0, n.level - 1) if n.level else "") + n.heading)
        for c in n.children:
            walk(c)

    for r in roots:
        walk(r)
    return "\n".join(lines)


def required_items(instructions: str, limit: int = 8) -> list[str]:
    """작성방법 상자 → 요구 항목 줄(번호·글머리 단위). 상자 표시 글은 뺀다."""
    text = re.sub(r"【[^】]*】", "\n", instructions or "")
    items: list[str] = []
    for piece in re.split(r"(?=(?:\s|^)(?:\d+\)|[①-⑳]|[※▪•◦○□■❍-]\s))", text):
        s = " ".join(piece.split())
        s = re.sub(r"^(?:\d+\)|[①-⑳]|[※▪•◦○□■❍-])\s*", "", s).strip(" .")
        if 4 <= len(s) <= 160:
            items.append(s)
    return items[:limit]


def _areas(evidence: list[str]) -> list[str]:
    """평가편람 조각에서 '영역(배점)' 표기 — 'Ⅰ. 사업추진 목표(15)' 처럼 보이는 것만."""
    found: list[str] = []
    for e in evidence:
        for m in re.finditer(r"([" + _ROMAN + r"])\s*[.．]?\s*([가-힣A-Za-z·･ ]{2,30}?)\s*\((\d{1,3})\)", e):
            item = f"{m.group(1)}. {m.group(2).strip()}({m.group(3)})"
            if item not in found:
                found.append(item)
    return found


def _cite(text: str, n: int = 60) -> str:
    """근거 글의 앞부분을 따옴표로 — 추론이 입력의 어디를 썼는지 보이게."""
    t = " ".join(text.split())
    t = re.sub(r"^\([^)]*\)\s*", "", t)
    return "“" + t[:n].rstrip() + ("…" if len(t) > n else "") + "”"


def _pick(variant: int, *options: str) -> str:
    return options[variant % len(options)]


def _think(*steps: tuple[str, str] | str) -> str:
    """추론 단계 — ("제목", "내용") 마다 '[N단계: 제목] 내용' 한 줄."""
    lines = []
    for st in steps:
        title, body = st if isinstance(st, tuple) else ("생각", st)
        lines.append(f"[{len(lines) + 1}단계: {title}] {body.strip()}")
    return "\n".join(lines)


_DROP_LINE = re.compile(r"상세\s*내용은|참조\s*\[?붙임|^\d+\.\s*\S{1,12}$")


def overview_lines(evidence: list[str], limit: int = 8) -> list[str]:
    """근거 조각(출처 표시 + 글) → 개요 줄들. ◦·□·※ 글머리로 나누고, '상세내용은 기본계획 참조' 같은 안내 줄과 잘린 제목 조각은 뺀다."""
    out: list[str] = []
    for e in evidence:
        text = e.split(") ", 1)[-1] if e.startswith("(") else e
        for piece in re.split(r"\s*[◦○□■※▪•]\s*", text):
            piece = " ".join(piece.split()).strip(" .")
            piece = re.sub(r"\s+\d+\.\s*[가-힣 ]{2,10}$", "", piece)          # 뒤에 붙은 다음 제목('2. 사업 개요')
            if len(piece) >= 8 and not _DROP_LINE.search(piece) and not piece.endswith("공고"):   # 공고 제목 줄은 이름이지 개요가 아니다
                out.append(piece)
    dedup: list[str] = []
    for x in out:
        if x not in dedup:
            dedup.append(x)
    return dedup[:limit]


def short_name(program: str) -> str:
    """'2026학년도 AID 전환 중점 전문대학 지원사업' → 'AID 전환 중점 전문대학 지원사업' (연도 표기를 뗀 이름)."""
    return re.sub(r"^\d{4}\s*(?:학년도|년도|년)?\s*", "", program).strip()


def step1(program: str, overview_evidence: list[str], variant: int = 0) -> dict:
    """사업명 → 개요."""
    ev = "\n".join(f"- {e}" for e in overview_evidence)
    qs = [f"{short_name(program)}이 뭐야?", f"{program}은 어떤 사업이야? 개요만 알려줘", f"{short_name(program).split()[0]} 사업이 뭔지 설명해 줘"]
    human = f"{PREFACE}\n\n[근거: 공고·기본계획]\n{ev}\n\n[질문] {qs[variant % len(qs)]}"
    lines = overview_lines(overview_evidence)
    srcs = [e.split(")", 1)[0].strip("( ") for e in overview_evidence if e.startswith("(")]
    first = next((e for e in overview_evidence if "목적" in e.split(")", 1)[0]), overview_evidence[0])
    gpt = _think(("질문 파악", _pick(variant, "사업이 무엇인지 묻는다 — 사업명과 개요(목적·추진 방향·규모)까지만 답하고 목차·세부는 아직 다루지 않는다.",
                                    "묻는 것은 사업의 정체다. 답의 범위는 사업명과 개요이고, 계획서 구조는 다음 질문에서 다룬다.",
                                    "'뭐야'는 개요를 묻는 말이다. 사업명·목적·방향·규모 순으로 짧게 답한다.")),
                 ("근거 확인", f"근거는 {', '.join(srcs)} 이다. 사업명은 공고 제목 그대로, 목적은 {_cite(first)} 에서 시작한다."),
                 ("정리", f"근거의 글머리 줄을 그대로 옮겨 개요 {len(lines)}줄로 정리한다. 근거에 없는 사실은 더하지 않는다.")) + \
        f"\n[답] 사업명: {program}\n개요:\n" + "\n".join(f"- {x}" for x in lines)
    return {"human": human, "gpt": gpt, "step": 1, "node": program}


def step2(program: str, overview: str, roots: list[Node], area_evidence: list[str], variant: int = 0) -> dict:
    """개요 → 목차."""
    ev = "\n".join(f"- {e}" for e in area_evidence)
    qs = ["이 사업의 계획서는 어떤 목차로 써야 해?", "사업계획서 구조(부·절)를 잡아 줘", "계획서 목차부터 세워 줘"]
    human = (f"{PREFACE}\n\n[사업명] {program}\n[개요]\n{overview}\n\n[근거: 평가편람의 평가영역]\n{ev}\n\n"
             f"[질문] {qs[variant % len(qs)]}")
    parts = "\n".join(f"{r.heading}\n" + "\n".join(f"  {c.heading}" for c in r.children) for r in roots)
    areas = _areas(area_evidence)
    seen = ("평가편람에 " + "·".join(areas[:4]) + (" 등" if len(areas) > 4 else "") + " 영역이 배점과 함께 보인다.") if areas else \
        "평가편람의 선정평가 항목이 영역(Ⅰ·Ⅱ…)과 지표로 나뉘어 있다."
    gpt = _think(("질문 파악", _pick(variant, "계획서 목차(부·절)를 묻는다. 사업 개요는 앞에서 확인했다.",
                                    "구조를 세우라는 요청이다 — 부와 절의 뼈대만 잡고 각 절의 내용은 뒤로 미룬다.",
                                    "목차를 묻는다. 어디서 목차의 근거를 찾을지가 먼저다.")),
                 ("근거 확인", seen + " 계획서는 평가 항목 순서대로 심사되므로 목차도 이를 따른다."),
                 ("구조 도출", f"영역이 부(Ⅰ·Ⅱ…)가 되고 지표가 절이 된다. 부 {len(roots)}개, 절 {sum(len(r.children) for r in roots)}개, 절 번호는 부마다 1부터.")) + f"\n[답]\n{parts}"
    return {"human": human, "gpt": gpt, "step": 2, "node": "목차"}


def step_part(program: str, overview: str, roots: list[Node], root: Node, criteria: list[str]) -> dict | None:
    """목차 → 부의 절 구성(절·소절과 각 절이 다룰 것 한 줄)."""
    if not root.children:
        return None
    ev = "\n".join(f"- {c}" for c in criteria) or "- (없음)"
    parts = "\n".join(f"{r.heading}\n" + "\n".join(f"  {c.heading}" for c in r.children) for r in roots)
    lines: list[str] = []

    def walk(n: Node):
        first = required_items(n.instructions, limit=1)
        lines.append(("  " * (n.level - 1)) + n.heading + (f" — {first[0][:70]}" if first else ""))
        for c in n.children:
            walk(c)

    for c in root.children:
        walk(c)
    human = (f"{PREFACE}\n\n[사업명] {program}\n[개요]\n{overview}\n[목차]\n{parts}\n\n[근거: 이 부의 평가지표]\n{ev}\n\n"
             f"[질문] 「{root.heading}」 에는 어떤 절이 들어가고, 절마다 무엇을 다뤄?")
    areas = _areas(criteria)
    gpt = _think(("질문 파악", f"목차 가운데 「{root.heading}」 한 부의 절 구성을 묻는다."),
                 ("근거 확인", (f"평가지표에 {'·'.join(areas[:3])} 가 보인다 — " if areas else "") +
                  f"이 부의 평가지표와 배점이 절의 순서·무게를 정한다. 첫 지표는 {_cite(criteria[0]) if criteria else '(근거 없음)'} 이다."),
                 ("구조 도출", f"지표를 절 {len(root.children)}개와 그 소절로 펼치고, 절마다 평가가 먼저 요구하는 것을 한 줄로 단다. 세부 뼈대는 절을 골라 따로 잡는다.")) + \
        "\n[답]\n" + "\n".join(lines)
    return {"human": human, "gpt": gpt, "step": 2.5, "node": root.heading}


def step3(program: str, overview: str, roots: list[Node], node: Node, criteria: list[str]) -> dict | None:
    """목차 → 절의 요구 항목."""
    items = required_items(node.instructions)
    if not items or not criteria:
        return None
    ev = "\n".join(f"- {c}" for c in criteria)
    box = " ".join(re.sub(r"【[^】]*】", " ", node.instructions).split())[:1200]
    human = (f"{PREFACE}\n\n[사업명] {program}\n[개요]\n{overview}\n[목차]\n{outline_text(roots)}\n\n"
             f"[근거: 이 절의 평가 착안점]\n{ev}\n[근거: 양식의 작성방법 상자]\n{box}\n\n[질문] 「{node.heading}」 절에는 무엇을 써야 해?")
    v = sum(map(ord, node.heading)) % 3
    gpt = _think(("질문 파악", _pick(v, f"「{node.heading}」 절에 써야 할 것을 묻는다 — 이 절은 {node.part} 아래 절이다.",
                                f"「{node.heading}」 은 {node.part} 의 절이다. 이 절이 다룰 항목을 묻는다.",
                                f"질문의 대상은 {node.part} 아래 「{node.heading}」 절 하나다.")),
                 ("근거 확인", f"착안점은 {_cite(criteria[0], 70)} 라고 묻고, 양식의 작성방법 상자가 같은 요구를 항목으로 적어 두었다. "
                  "평가가 구체적으로 제시했는지 묻는 것이 곧 써야 할 항목이다."),
                 ("항목화", _pick(v, f"착안점의 요구를 {len(items)}개 항목으로 나누고, 수치·증빙은 나중에 근거로 채울 자리로 남긴다.",
                               f"요구를 항목 {len(items)}개로 쪼갠다. 각 항목은 뒤에서 소제목이나 표가 된다.",
                               f"{len(items)}개 항목으로 정리한다. 값은 아직 없으니 항목 이름만 둔다."))) + \
        "\n[답] 이 절이 다룰 항목:\n" + "\n".join(f"{i}. {it}" for i, it in enumerate(items, 1))
    return {"human": human, "gpt": gpt, "step": 3, "node": node.heading}


def step4(program: str, overview: str, roots: list[Node], node: Node) -> dict | None:
    """절의 요구 항목 → 절의 뼈대(소제목·표·도식 자리)."""
    items = required_items(node.instructions)
    if not node.skeleton or len(node.skeleton) < 2:
        return None
    human = (f"{PREFACE}\n\n[사업명] {program}\n[개요]\n{overview}\n[목차]\n{outline_text(roots)}\n"
             f"[「{node.heading}」 절이 다룰 항목]\n" + "\n".join(f"{i}. {it}" for i, it in enumerate(items, 1)) +
             f"\n\n[질문] 「{node.heading}」 절의 뼈대(소제목·표·도식 자리)를 잡아 줘. 내용은 나중에 근거로 채운다.")
    n_tbl = sum(1 for x in node.skeleton if x.startswith("표:"))
    n_fig = sum(1 for x in node.skeleton if x.startswith("도식:"))
    v = sum(map(ord, node.heading)) % 3
    first_tbl = next((x for x in node.skeleton if x.startswith("표:")), "")
    first_fig = next((x for x in node.skeleton if x.startswith("도식:")), "")
    how = []
    if items:
        how.append(f"항목 1 {_cite(items[0], 40)} 은 소제목이 된다")
    if first_tbl:
        how.append(f"나열되는 것은 표({first_tbl[3:][:40]})로")
    if first_fig:
        how.append(f"한눈에 보일 것은 도식({first_fig[4:][:30]})으로")
    gpt = _think(("질문 파악", _pick(v, f"「{node.heading}」 절의 뼈대(소제목·표·도식 자리)를 묻는다. 내용은 아직 쓰지 않는다.",
                                f"이번엔 「{node.heading}」 의 틀이다 — 자리만 잡고 칸은 비운다.",
                                f"「{node.heading}」 절을 어떤 소제목·표·도식으로 짤지 묻는다.")),
                 ("형식 결정", "; ".join(how) + ". 실적·계획·지표처럼 나열되는 것은 표, 여건·체계·흐름처럼 한눈에 보일 것은 도식이다."),
                 ("뼈대 배치", f"소제목 {len(node.skeleton) - n_tbl - n_fig}개, 표 {n_tbl}개, 도식 {n_fig}개를 절의 흐름대로 놓는다. 표는 머리 칸만, 도식은 상자 제목만 적는다.")) + \
        "\n[답]\n" + "\n".join(f"- {s}" for s in node.skeleton)
    return {"human": human, "gpt": gpt, "step": 4, "node": node.heading}


def missing_numbers(rec: dict) -> set[str]:
    """답에 있고 입력에 없는 사실 수치(real_pairs.fact_numbers) — 단위 붙은 수는 자릿수와 상관없이 사실(9명·27명),
    절 번호꼴(1.2·2.1.1)과 단위 없는 두 자리 이하 수('3-Tier'·'Step 1')는 구조 단계(2 이후)에서 구조 표기로 본다."""
    reasoning, _, answer = rec["gpt"].partition("[답]")
    # 근거 설명의 '부 4개·항목 3개·개요 8줄' 은 답을 세어 나온 수라 근거가 답 자체다 — 셈 단위(개·줄·단계·턴)는 뺀다
    counted = {re.sub(r"[^\d]", "", m.group(0)) for m in re.finditer(r"\d+\s*(?:개|줄|단계|턴)", reasoning)}
    miss = fact_numbers(answer, strict=rec["step"] == 1) | (fact_numbers(reasoning, strict=False) - counted)
    return miss - _numbers(rec["human"])


def to_pair(rec: dict, program: str, doc_id: int) -> dict | None:
    """단발 학습쌍(conversations + meta). 답의 수치가 입력에 없으면 None."""
    if missing_numbers(rec):
        return None
    return {"conversations": [{"from": "human", "value": rec["human"]}, {"from": "gpt", "value": rec["gpt"]}],
            "meta": {"source": f"tree-step{rec['step']}".replace(".5", "p"), "doc_id": doc_id, "section": rec["node"], "program": program,
                     "shown": rec["gpt"], "step": rec["step"], "view": view_fields(rec["human"], rec["gpt"])}}


def view_fields(human: str, gpt: str) -> dict:
    """검수 화면에 따로 보일 네 칸 — 질문 / 근거(사슬 포함, 머리말은 뺌) / 근거 설명([N단계] 줄) / 답."""
    head, _, question = human.rpartition("[질문]")
    reasoning, _, answer = gpt.partition("[답]")
    return {"question": question.strip() or human, "evidence": head.replace(PREFACE, "").strip(), "reasoning": reasoning.strip(),
            "answer": answer.strip()}


def chain_conversation(steps: list[dict], program: str, doc_id: int) -> dict:
    """대화형 — 1·2·3·4 단계를 한 대화로 잇는다. 뒤 턴의 질문은 앞 턴의 답을 전제로 짧게 묻는다."""
    convs: list[dict] = []
    for i, st in enumerate(steps):
        human = st["human"] if i == 0 else st["human"].split("[질문]", 1)[-1].strip()
        if i > 0:
            # 앞 턴에 없던 근거만 다시 준다
            ev = re.search(r"\[근거[^\]]*\]\n(?:- .*\n?)+", st["human"])
            if ev:
                human = ev.group(0).strip() + "\n\n[질문] " + human
            else:
                human = "[질문] " + human
        convs += [{"from": "human", "value": human}, {"from": "gpt", "value": st["gpt"]}]
    return {"conversations": convs, "meta": {"source": "tree-chain", "doc_id": doc_id, "section": steps[-1]["node"], "program": program,
                                            "shown": steps[-1]["gpt"], "step": len(steps),
                                            "view": dict(view_fields(steps[-1]["human"], steps[-1]["gpt"]),
                                                         evidence="\n\n".join(f"[{i + 1}턴 답]\n{st['gpt']}" for i, st in enumerate(steps[:-1])))}}


def to_alpaca(pair: dict) -> dict | None:
    """단발형 → Alpaca 꼴 {instruction, input, output}: instruction = 질문 줄, input = 근거·사슬. 대화형은 None."""
    if len(pair["conversations"]) != 2:
        return None
    human = pair["conversations"][0]["value"]
    head, _, question = human.rpartition("[질문]")
    return {"instruction": question.strip() or human, "input": head.replace(PREFACE, "").strip(), "output": pair["conversations"][1]["value"],
            "meta": pair["meta"]}


def to_messages(pair: dict) -> dict:
    return {"messages": [{"role": "user" if t["from"] == "human" else "assistant", "content": t["value"]} for t in pair["conversations"]],
            "meta": pair["meta"]}
