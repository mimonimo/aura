"""양식 작업본의 절을 쓰는 에이전트의 재료 모으기 — 검토 → 문서함에서 지난 사업 자료 찾기 → 맥락 → 초안.

사용자 지시 2026-09-27: 실전에는 합본(정답지)이 처음엔 없다. 에이전트는 공고·기본계획·평가편람·양식을 검토하고, 문서함을 뒤져
지난 사업 자료를 찾아 맥락을 만들고, 양식의 작성방법에 맞춰 절마다 초안을 쓴다. 그 뒤 사람과 에이전트가 고도화한다.
여기서는 27B 가 절 하나를 쓰는 데 필요한 재료를 고른다 — 글은 모델이 쓴다. 특정 문서에 맞춘 규칙은 없다.

재료:
  instructions  그 절의 작성방법 상자·안내 문구(양식이 무엇을 요구하는가)
  criteria      평가지표·공고·기본계획에서 그 절과 관련된 조각(기준 문서 검색)
  past          문서함의 지난 사업 자료 — 같은 목차의 문서는 같은 절을 통째로(section_context), 아니면 낱말이 겹치는 조각
  institution   기관 정보(대학명·주소 등, 문서함에서 인출·설정으로 보정)
"""

from __future__ import annotations

import re

from zzaimy.app import section_context

_SECTION_NO = re.compile(r"(?<![\d.])(\d+(?:\.\d+)+)\.?\s*(?:절|항|장)?")
_NEXT = re.compile(r"다음\s*절|이어서|빈\s*절|아직\s*(?:비어|안\s*쓴)")
_ALL = re.compile(r"전체|모든\s*절|전부|처음부터\s*끝까지|다\s*채|다\s*써")
_DRAFT = re.compile(r"작성|채워|채우|써\s*줘|써줘|쓰자|초안|넣어\s*줘|보강|다시\s*써")
_PLACEHOLDER = re.compile(r"[○◯]{2,}|OOO|000|\(\s*\)|_{3,}")
MAX_SECTIONS_PER_TURN = 4


def looks_like_section_draft(command: str) -> bool:
    """절을 쓰라는 지시인가 — 절 번호·'다음 절'·'전체' 중 하나와 쓰기 동사가 함께 있다."""
    c = command or ""
    return bool(_DRAFT.search(c) and (_SECTION_NO.search(c) or _NEXT.search(c) or _ALL.search(c)))


def is_unfilled(section: dict) -> bool:
    """본문이 없는 절 — 작성방법 상자(표)만 있거나 글이 없다."""
    return int(section.get("table_end") or 0) > int(section.get("end") or 0) - 1 or int(section.get("chars") or 0) == 0


def writable(section: dict) -> bool:
    """쓸 수 있는 절 — 번호가 두 마디 이상(1.1, 2.1.1)인 본문 절. 장 제목(1., Ⅰ.)이나 앞머리는 아니다."""
    num, _ = section_context.split_number(section.get("heading") or "")
    return "." in num


def target_sections(info: dict, command: str, filled_ok: bool = False) -> list[dict]:
    """지시가 가리키는 절들 — 번호를 말했으면 그 절, '다음 절'이면 첫 빈 절, '전체'면 빈 절 전부(한 번에 MAX 개)."""
    secs = [s for s in info.get("sections", []) if s.get("index", 0) > 0]
    c = command or ""
    nums = [m.group(1).rstrip(".") for m in _SECTION_NO.finditer(c)]
    if nums:
        out = []
        for n in nums:
            hit = next((s for s in secs if section_context.split_number(s.get("heading") or "")[0] == n), None)
            if hit is not None and hit not in out:
                out.append(hit)
        return out[:MAX_SECTIONS_PER_TURN]
    empties = [s for s in secs if writable(s) and (filled_ok or is_unfilled(s))]
    if _ALL.search(c):
        return empties[:MAX_SECTIONS_PER_TURN]
    if _NEXT.search(c) or _DRAFT.search(c):
        return empties[:1]
    return []


def section_text(info: dict, section: dict) -> str:
    """그 절의 현재 글(작성방법 상자 포함). 독스 구조에서 자른 절 글(outline 의 text)이 있으면 그것 —
    본문에서 제목 글자를 찾으면 앞쪽 목차의 같은 제목에 걸린다(실측 2026-09-27: 1.2 절의 작성방법이 모델에 안 갔다)."""
    if (section.get("text") or "").strip():
        return section["text"].strip()
    text = info.get("text") or ""
    heading = (section.get("heading") or "").strip()
    i = text.find(heading)
    if i < 0:
        return ""
    secs = sorted(info.get("sections", []), key=lambda s: s.get("start", 0))
    later = [s for s in secs if s.get("start", 0) > section.get("start", 0)]
    j = -1
    for s in later:
        j = text.find((s.get("heading") or "").strip(), i + len(heading))
        if j >= 0:
            break
    return text[i: j if j >= 0 else len(text)].strip()


def instruction_keywords(instructions: str, limit: int = 12) -> list[str]:
    """작성방법 문장에서 검색에 쓸 낱말 — 2자 이상 한글·영문 낱말(흔한 안내 낱말은 뺀다)."""
    stop = {"작성", "방법", "기재", "제시", "내용", "관련", "경우", "포함", "이내", "분량", "참고", "필요", "대학", "사업", "계획", "작성방법", "증빙자료", "증빙"}
    words = re.findall(r"[가-힣A-Za-z]{2,}", instructions or "")
    out: list[str] = []
    for w in words:
        if w in stop or w in out:
            continue
        out.append(w)
        if len(out) >= limit:
            break
    return out


class Materials:
    """프로젝트 하나의 재료 창고 — 지난 자료의 절 정렬은 문서마다 한 번만 계산해 둔다."""

    def __init__(self, db, project: dict | None, form_source_ids: set[int], find_relevant, extract_nouns, criteria_chunks: list[dict] | None):
        self.db = db
        self.project = project
        self.form_source_ids = set(form_source_ids)
        self.find_relevant = find_relevant
        self.extract_nouns = extract_nouns
        self.criteria_chunks = criteria_chunks or []
        self._plans: dict[int, list[dict]] = {}
        self._chunks: dict[int, list[dict]] = {}

    def past_docs(self) -> list[dict]:
        if not self.project:
            return []
        docs = self.db.list_documents(self.project["sector"], project_id=int(self.project["id"]))
        return [d for d in docs if d.get("status") == "reviewed" and int(d["id"]) not in self.form_source_ids
                and d.get("kind") != "form"]

    def _chunks_of(self, doc_id: int) -> list[dict]:
        if doc_id not in self._chunks:
            self._chunks[doc_id] = self.db.list_doc_chunks(doc_id)
        return self._chunks[doc_id]

    def aligned(self, doc: dict, info: dict, section: dict, budget: int) -> str:
        did = int(doc["id"])
        if did not in self._plans:
            self._plans[did] = section_context.plan(info["sections"], self._chunks_of(did))
        return section_context.context_for(section, self._plans[did], budget=budget)

    def keyword_hits(self, doc: dict, query: str, limit: int = 3) -> list[str]:
        nouns = self.extract_nouns(query)
        if not nouns:
            return []
        scored = []
        for ch in self._chunks_of(int(doc["id"])):
            text = ch.get("content") or ""
            if ch.get("kind") == "table":
                try:
                    import json
                    text = json.loads(text).get("text") or text
                except Exception:
                    pass
            hit = len(nouns & self.extract_nouns(text[:1500]))
            if hit:
                scored.append((hit, text))
        scored.sort(key=lambda x: -x[0])
        return [t[:500] for _h, t in scored[:limit]]

    def for_section(self, info: dict, section: dict, command: str, title_of, budget: int = 3500) -> dict:
        instructions = section_text(info, section)
        query = f"{section.get('heading', '')} {' '.join(instruction_keywords(instructions))} {command}"
        criteria: list[dict] = []
        try:
            if self.criteria_chunks:
                criteria = self.find_relevant(self.db, query, top_k=5, chunks=self.criteria_chunks)
        except Exception:
            criteria = []
        past: list[dict] = []
        for d in self.past_docs():
            title = title_of(d.get("filename") or "")
            text = self.aligned(d, info, section, budget)
            how = "같은 절"
            if not text:
                hits = self.keyword_hits(d, query)
                text = "\n---\n".join(hits)
                how = "낱말 겹침"
            if text.strip():
                past.append({"title": title, "how": how, "text": text})
        return {"instructions": instructions, "criteria": criteria, "past": past}


def render_materials(m: dict, institution: dict | None = None) -> str:
    lines: list[str] = []
    if m.get("instructions"):
        lines.append("[이 절의 양식 안내·작성방법]\n" + m["instructions"][:2500])
    crit = m.get("criteria") or []
    if crit:
        lines.append("[평가지표·공고·기본계획 근거]\n" + "\n".join(
            f"- ({c.get('reg_title') or ''}) {str(c.get('content') or '')[:400]}" for c in crit[:5]))
    past = m.get("past") or []
    if past:
        blocks = [f"《{p['title']}》 ({p['how']})\n{p['text']}" for p in past]
        lines.append("[문서함의 지난 사업 자료 — 이 절과 관련된 부분]\n" + "\n\n".join(blocks))
    if institution:
        known = [f"{k}: {v}" for k, v in institution.items() if v]
        unknown = [k for k, v in institution.items() if not v]
        lines.append("[기관 정보]\n" + ("\n".join(known) if known else "(없음)") + (f"\n모르는 값: {', '.join(unknown)} — 지어내지 말고 ○○○ 로 둔다" if unknown else ""))
    return "\n\n".join(lines)
