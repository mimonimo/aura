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
_SUMMARY = re.compile(r"요약")
_REPORT_NAME = re.compile(r"(?:실적|결과|성과)\s*보고서?")
_TOC = re.compile(r"\s*(?:목\s*차|차\s*례|CONTENTS)\b", re.I)
# 문서 전체 초안 요청(「사업계획서 초안 작성해 줘」) — 절을 말하지 않아도 빈 절부터 몇 개씩 쓴다(리허설 2026-10-08: 일반 편집으로 가서 한 글자도 안 씀)
_DOC_DRAFT = re.compile(r"(?:계획서|보고서|신청서|제안서|문서|서식|양식).{0,12}(?:초안|작성|써)")
DOC_DRAFT_FIRST = 2


def looks_like_section_draft(command: str) -> bool:
    """절을 쓰라는 지시인가 — 절 번호·'다음 절'·'전체' 중 하나와 쓰기 동사가 함께 있다."""
    c = command or ""
    return bool(_DRAFT.search(c) and (_SECTION_NO.search(c) or _NEXT.search(c) or _ALL.search(c) or _DOC_DRAFT.search(c)))


def doc_draft_only(command: str) -> bool:
    """절을 짚지 않은 문서 전체 초안 요청인가(절 번호·다음 절·전체 없이 「○○계획서 초안 써 줘」)."""
    c = command or ""
    return bool(_DOC_DRAFT.search(c) and not (_SECTION_NO.search(c) or _NEXT.search(c) or _ALL.search(c)))


def is_unfilled(section: dict) -> bool:
    """본문이 없는 절 — 작성방법 상자를 뺀 본문 글자(outline 의 body_chars)가 0. 옛 구조(body_chars 없음)면 '상자로 끝나거나 글이 없음'.
    실측 2026-09-27: 모델이 절 끝에 SWOT 표를 넣자 '상자로 끝남'으로 보여 빈 절로 취급, 다시 쓰기가 비우지 않고 덧붙였다."""
    if "body_chars" in section:
        if int(section.get("body_chars") or 0) == 0:
            return True
        # 글 문단은 없고 양식 표만 있는데 그 표의 칸이 절반 넘게 비었으면 아직 안 쓴 절이다(표 채우기, 2026-09-29) — 머리 칸만 있는 총괄표·예산표
        cells, empty = int(section.get("tbl_cells") or 0), int(section.get("tbl_empty") or 0)
        return int(section.get("para_chars") or 0) == 0 and cells > 0 and empty * 2 >= cells
    return int(section.get("table_end") or 0) > int(section.get("end") or 0) - 1 or int(section.get("chars") or 0) == 0


def family_unfilled(info: dict, section: dict) -> bool:
    """절과 그 소제목 절들에 모두 본문이 없는가 — 소제목 뼈대에 나눠 쓴 절(1.1)은 절 자체가 비어 보여도 쓴 절이다(실측 2026-09-28)."""
    if not is_unfilled(section):
        return False
    return all(is_unfilled(s) for s in subsections(info, section))


def writable(section: dict) -> bool:
    """쓸 수 있는 절 — 번호가 두 마디 이상(1.1, 2.1.1)인 본문 절, 또는 끝 절(소제목이 없는 절 — 「Ⅰ. → 1. → 가.」 체계의 공통 양식,
    「1. → 2.」 한 단계뿐인 단위 프로그램 양식). 소제목을 거느린 장 제목(Ⅰ.)·문서 제목·앞머리는 아니다."""
    num, rest = section_context.split_number(section.get("heading") or "")
    if not has_words(rest):
        return False                                    # 「2cm」 같은 치수·기호만 있는 줄은 절이 아니다(리허설 2026-10-08: 변환본 여백 표기가 첫 빈 절로 뽑혀 맴돎)
    return "." in num or (bool(section.get("leaf")) and int(section.get("level") or 0) >= 1)


def has_words(text: str) -> bool:
    """뜻 있는 낱말이 있는가 — 한글 두 자 이상 또는 단위가 아닌 영문 세 자 이상."""
    t = re.sub(r"\d+(?:\.\d+)?\s*(?:cm|mm|pt|px|%|㎝|㎜)", " ", text or "", flags=re.I)
    return len(re.findall(r"[가-힣]", t)) >= 2 or bool(re.search(r"[A-Za-z]{3,}", t))


def target_sections(info: dict, command: str, filled_ok: bool = False, skip: set[str] | None = None) -> list[dict]:
    """지시가 가리키는 절들 — 번호를 말했으면 그 절, '다음 절'이면 첫 빈 절, '전체'면 빈 절 전부(한 번에 MAX 개).
    skip 은 이 대화에서 이미 다룬 절 제목 — 「다음 절」이 같은 절에서 맴돌지 않게(번호로 짚으면 다시 쓴다)."""
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
    empties = [s for s in secs if writable(s) and (filled_ok or family_unfilled(info, s)) and s.get("heading") not in (skip or set())]
    # 요약 절은 본문을 다 쓴 뒤에 옮겨 쓴다 — 빈 절 차례의 맨 뒤로(리허설: 첫 차례에 요약을 골라 모델이 비워 둔 채 한 번을 썼다)
    empties = [s for s in empties if not _SUMMARY.search(s.get("heading") or "")] + [s for s in empties if _SUMMARY.search(s.get("heading") or "")]
    if _ALL.search(c):
        return empties[:MAX_SECTIONS_PER_TURN]
    if _DOC_DRAFT.search(c) and not _NEXT.search(c):
        return empties[:DOC_DRAFT_FIRST]
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


def useful_chunk(text: str) -> bool:
    """재료로 쓸 만한 조각인가 — 목차(「목차」로 시작하거나 짧은 번호 항목만 늘어선 것)·그림 표시뿐인 조각이 아니다."""
    if _TOC.match(text or ""):
        return False
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text or "")              # 그림 표시(![image](…))는 글이 아니다
    if len(re.sub(r"\s+", "", body)) < 80:
        return False
    head = re.sub(r"\s+", " ", body).strip()[:300]                     # 검색 조각은 앞뒤로 늘어나 있다 — 앞머리로 목차형인지 본다
    items = re.findall(r"(?:^|\s)[IVⅠ-Ⅹ]*\d{0,2}\.\s*[가-힣A-Za-z][^.]{1,14}?(?=\s\S*\d{1,2}\.|\s[IVⅠ-Ⅹ]|$)", head)
    return not (len(items) >= 4 and sum(len(x) for x in items) > 0.5 * len(head))


class Materials:
    """프로젝트 하나의 재료 창고 — 지난 자료의 절 정렬은 문서마다 한 번만 계산해 둔다."""

    def __init__(self, db, project: dict | None, form_source_ids: set[int], find_relevant, extract_nouns, criteria_chunks: list[dict] | None,
                 scope: dict | None = None):
        self.scope = scope                     # 문서함 사업 문서 검색(RAG)의 열람 범위 — 없으면 문서함 검색을 하지 않는다
        self.db = db
        self.project = project
        self.form_source_ids = set(form_source_ids)
        self.find_relevant = find_relevant
        self.extract_nouns = extract_nouns
        self.criteria_chunks = criteria_chunks or []
        self._plans: dict[int, list[dict]] = {}
        self._lib_seen: set = set()
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
        from zzaimy.app.chunk_path import attach_paths

        nouns = self.extract_nouns(query)
        if not nouns:
            return []
        scored = []
        for ch in attach_paths(self._chunks_of(int(doc["id"]))):
            text = ch.get("content") or ""
            if ch.get("kind") == "table":
                try:
                    import json
                    text = json.loads(text).get("text") or text
                except Exception:
                    pass
            hit = len(nouns & self.extract_nouns(text[:1500]))
            if hit:
                where = ch.get("path_text") or ""
                scored.append((hit, (f"[{where}] " if where else "") + text))      # 어느 절의 글인지 모델이 알게(제목 계층)
        scored.sort(key=lambda x: -x[0])
        return [t[:560] for _h, t in scored[:limit]]

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
        past += self.library_hits(section, query)
        return {"instructions": instructions, "criteria": criteria, "past": past, "doc_rules": doc_guides(info)}

    def library_hits(self, section: dict, query: str, k: int = 3) -> list[dict]:
        """문서함 전체의 사업 문서(같은 사업의 지난 계획서·실적보고서 …)에서 이 절에 맞는 조각 — 그래프로 사업·연차를 좁히는 사업 문서 RAG.
        프로젝트에 올린 문서만 보던 것을 넓힌다(리허설 2026-10-09: 지난 자료가 개설과목 엑셀 하나뿐). 열람 범위(scope)를 따른다."""
        sc = self.scope
        if not sc or sc.get("role") == "student" or sc.get("grant") is False:
            return []
        try:
            from zzaimy.app import grant_search
            pname = (self.project or {}).get("name", "")
            q = f"{pname} {section.get('heading', '')} {query}"[:400]
            own = {int(d["id"]) for d in self.past_docs()}
            hits = [dict(h, how="문서함 검색") for h in grant_search.search(self.db, q, k=k + 2, user=sc.get("user"), depts=sc.get("grant_depts"))["hits"]]
            if _REPORT_NAME.search(pname):
                # 실적·결과보고서는 같은 사업·연차 계획서와 견주어 쓴다 — 계획서도 따로 찾아 앞에 둔다(시험: 「계획 대비 실적」 절이 0자)
                q2 = f"{_REPORT_NAME.sub('계획서', pname)} {section.get('heading', '')} {query}"[:400]
                plan_hits = grant_search.search(self.db, q2, k=3, user=sc.get("user"), depts=sc.get("grant_depts"))["hits"]
                hits = [dict(h, how="문서함 검색(같은 연차 계획서)") for h in plan_hits[:2]] + hits
        except Exception:
            return []
        out = []
        for h in hits:
            if int(h["doc_id"]) in own or int(h["doc_id"]) in self.form_source_ids:
                continue
            text = h.get("content") or ""
            cid = h.get("chunk_id")
            if not useful_chunk(text) or (cid is not None and cid in self._lib_seen):
                continue                                  # 목차·그림 표시뿐인 조각, 앞 절에 이미 준 조각은 빼다(실측: 같은 그림 조각이 모든 절 1순위)
            if cid is not None:
                self._lib_seen.add(cid)
            path = [str(x) for x in (h.get("path") or [])]
            title = re.sub(r"\.(hwpx?|pdf|docx?|xlsx?|pptx?)$", "", path[-1] if path else "문서", flags=re.I)
            where = " > ".join(path[:-1])[-60:]
            out.append({"title": title[:80], "how": (h.get("how") or "문서함 검색") + (f" · {where}" if where else ""),
                        "text": (h.get("content") or "")[:700]})
            if len(out) >= k + (2 if _REPORT_NAME.search((self.project or {}).get("name", "")) else 0):
                break
        return out


def doc_guides(info: dict) -> str:
    """문서 전체 지침 — 첫 절 앞(앞머리·표지)의 작성 지침 문단(공통 양식의 「작성 지침 — …」: 개조식 기호·문체·지침 지우기).
    절 재료에는 그 절의 지침만 들어가 문서 전체 규칙(개조식)이 빠지던 것(실데이터 시험: 초안이 서술형 문단)."""
    from zzaimy.ingest.gdocs import GUIDE_PREFIX

    head = next((s for s in info.get("sections", []) if s.get("level", 9) >= 1), None)
    lines = []
    for s in info.get("sections", []):
        if s is head:
            break
        lines += [ln.strip() for ln in (s.get("text") or "").split("\n") if ln.strip().startswith(GUIDE_PREFIX.strip())]
    return "\n".join(lines)[:800]


def render_materials(m: dict, institution: dict | None = None) -> str:
    lines: list[str] = []
    if m.get("doc_rules"):
        lines.append("[문서 전체 작성 규칙 — 모든 절에 적용(문체·기호)]\n" + m["doc_rules"])
    if m.get("instructions"):
        lines.append("[이 절의 양식 안내·작성방법]\n" + m["instructions"][:2500])
    crit = m.get("criteria") or []
    if crit:
        lines.append("[평가지표·공고·기본계획 근거]\n" + "\n".join(
            f"- ({c.get('reg_title') or ''}) {str(c.get('content') or '')[:400]}" for c in crit[:5]))
    past = m.get("past") or []
    if past:
        blocks = [f"《{p['title']}》 ({p['how']})\n{p['text']}" for p in past]
        lines.append("[문서함의 지난 사업 자료 — 이 절과 관련된 부분. 출처를 달 때는 《 》 안의 문서 제목만 쓴다 — 재료에 없는 문서 이름을 지어내지 않는다]\n"
                     + "\n\n".join(blocks))
    if institution:
        known = [f"{k}: {v}" for k, v in institution.items() if v]
        if known:
            lines.append("[담당자가 알려 준 값·기관 정보]\n" + "\n".join(known))
    return "\n\n".join(lines)


def subsections(info: dict, section: dict) -> list[dict]:
    """절에 딸린 소제목 절들 — 양식이 준 뼈대(1. 대외여건 분석 → 1) 지역 동향 …). 다음 본문 절(번호 두 마디 이상) 앞까지."""
    secs = sorted(info.get("sections", []), key=lambda s: s.get("start", 0))
    out: list[dict] = []
    seen = False
    for s in secs:
        if s is section or (s.get("index") == section.get("index") and s.get("heading") == section.get("heading")):
            seen = True
            continue
        if not seen:
            continue
        if writable(s) or s.get("level", 9) <= 1:
            break
        out.append(s)
    return out


def family_text(info: dict, section: dict) -> str:
    """절 본문 + 소제목 절들의 글(절 번호를 앞에 붙여 모델이 어느 절에 넣을지 고를 수 있게)."""
    parts = [section_text(info, section)]
    for s in subsections(info, section):
        parts.append(f"[절 {s['index']}] " + (section_text(info, s) or s.get("heading", "")))
    return "\n".join(p for p in parts if p)


_NUM_TOKEN = re.compile(r"\d[\d,.]*\s*(?:%|억|만|천|백|명|개|건|년|월|일|점|호|회|시간|학점|과목|원)?")
_TERM_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+·\-]{2,}|[가-힣]{2,}(?:추진단|대학|센터|위원회|사업단|플랫폼|시스템|아카데미|랩|스쿨|모델|트랙|프로그램)")


_JUNK = re.compile(r"^(?:BIN\d+|IMG\d+|image\d+|0\d{3,}|png|jpg|jpeg|gif|bmp|pdf|hwp|docx?)$", re.I)      # 그림 참조·파일 확장자·0으로 시작하는 번호


def key_facts(text: str) -> tuple[set[str], set[str]]:
    """정답지의 '핵심 사실' — 수치(단위 포함)와 고유명사꼴 낱말(영문 약어·기관·조직 이름). 채점의 기준."""
    nums = {re.sub(r"[^0-9A-Za-z가-힣%]", "", m.group(0)) for m in _NUM_TOKEN.finditer(text or "")}
    # 단위 없는 한두 자리 수(표의 행 번호·쪽수)와 0 만의 수는 사실이 아니다
    nums = {n for n in nums if any(ch.isdigit() for ch in n) and not _JUNK.match(n)
            and not (n.isdigit() and (len(n) <= 2 or set(n) == {"0"}))}
    terms = {t for t in (m.group(0) for m in _TERM_TOKEN.finditer(text or "")) if not _JUNK.match(t)}
    return nums, terms


def score_against_reference(draft: str, reference: str) -> dict:
    """초안이 정답지의 핵심 사실을 얼마나 담았나 — {covered, total, ratio, missing:[...]} (수치·고유명사 기준, 0~1)."""
    nums, terms = key_facts(reference)
    facts = nums | terms
    if not facts:
        return {"covered": 0, "total": 0, "ratio": None, "missing": []}
    body = re.sub(r"[^0-9A-Za-z가-힣%+·\-]", "", draft or "")
    hit = {f for f in facts if f in body}
    missing = sorted(facts - hit, key=lambda x: (x in terms, x))[:12]
    return {"covered": len(hit), "total": len(facts), "ratio": round(len(hit) / len(facts), 2), "missing": missing}
