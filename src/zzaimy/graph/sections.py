"""절 트리 — 저장된 제목 조각으로 문서의 목차를 세우고 조각을 절에 매단다(ADR-0048 1·2항).

처리기가 남긴 제목 조각(kind=heading)의 번호 모양으로 깊이를 정한다(chunk_path.level_of — Ⅰ. · 1. · 1.1. · 가. …). 절 id 는 문서 안
차례 경로(예: 2.1.3 — 실제 번호가 아니라 깊이별 순번)라 같은 문서 판본에서 고정이다. 조각 자르기를 절 단위로 바꾸기 전에도
'이 조각은 어느 절의 것인가'를 저장된 순서로 정한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from zzaimy.app.chunk_path import level_of

# 절 번호 — 가나다 번호는 그 글자 뒤에 점·괄호가 있을 때만(「[가-하]」 범위에 점 없이 두면 「영남이공대학교」의 「영」까지 떼였다)
_NUM = re.compile(r"^\s*(?:(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|(?:I{1,3}|IV|VI{0,3}|IX|X)(?=[.．])|\d+(?:[.-]\d+)*|\(\d+\)|\d+\))[.．)]?|[가나다라마바사아자차카타파하][.．)])\s*")


@dataclass
class Section:
    path: str                     # 깊이별 순번 경로 '2.1.3'
    title: str
    level: float                  # 번호 꼴 깊이(번호 없는 묶음 머리는 반 단계)
    seq: int                      # 제목 조각의 seq
    chunks: list[int] = field(default_factory=list)   # 매달린 조각 seq
    parent: str = ""


def title_key(title: str) -> str:
    """절 제목 대조 키 — 번호를 떼고 공백·기호를 지운다(계획서와 보고서의 같은 절을 잇는 '식별자 일치')."""
    t = _NUM.sub("", title or "")
    return re.sub(r"[^0-9A-Za-z가-힣]", "", t)


# 글머리표로 시작하는 줄은 제목이 아니라 본문 항목이다(「□ 사업목표」·「❐ …」·「○ …」)
_BULLET = re.compile(r"^\s*[□■❐❏❑❒○◦●◎❍❂◉◈▶▷►▸▹➢➤➔→◆◇♦•·∙※☞✓✔▪▫★☆\-–]")
# 개요 번호: Ⅰ. / 1. / 1.1. / 15-2. / 가. / (1) / 1)
# 가. 나. 다. 는 그 글자들만(「가-하」 범위는 「등)」 같은 낱말까지 잡는다), 숫자는 0 으로 시작하지 않는다(「01.」은 표 속 코드)
_OUTLINE = re.compile(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.．]?|(?:I{1,3}|IV|VI{0,3}|IX|X)[.．]|[1-9]\d?(?:[.\-][1-9]\d?){0,3}[.．)]|[가나다라마바사아자차카타파하][.．)]|\([1-9]\d?\))\s*(?=[가-힣A-Za-z「『\[(])")
_HYPHEN = re.compile(r"^\s*(\d{1,2}(?:-\d{1,2})+)[.．)]?\s")
_SENTENCE_END = re.compile(r"(?:다|함|음|임|됨|요)\s*[.。]?\s*$")
TEXT_HEADING_MAX = 60
# 사설 영역 글자 — 원문자(①②)를 기호 글꼴로 그린 한글 문서에서 제목 앞에 붙는다(「\U000f02b1 [1-1 과제] 추진 실적」)
_PUA = re.compile(r"[\ue000-\uf8ff\U000f0000-\U0010ffff]")
# 번호를 새로 여는 첫 번호
_FIRST = re.compile(r"^\s*(?:Ⅰ\s*[.．]?|1[.．)]|1-1[.．)]?|가[.．)]|\(1\))\s")


def _level(title: str) -> int | None:
    """제목 깊이. 글머리표 줄은 None(제목 아님), 번호 없는 제목은 0(지금 깊이 아래 잎)."""
    if _BULLET.match(title):
        return None
    m = _HYPHEN.match(title)
    if m:                                          # 「15-2.」 = 15 아래 2 — 「1.1.」과 같은 깊이
        return min(2 + m.group(1).count("-"), 6)
    return level_of(title)


def heading_text(c: dict) -> str | None:
    """조각이 제목 노릇을 하면 제목 글을 돌려준다. 처리기가 제목으로 표시한 조각 말고도 우리 공문서에 흔한 두 꼴을 제목으로 본다:
    한두 칸짜리 띠 표(「Ⅰ | 사업비전 및 목표」 — 장 제목을 표로 그린다), 개요 번호로 시작하는 짧은 한 줄 본문(「1. 추진의 필요성…」)."""
    kind = c.get("kind")
    content = _PUA.sub("", str(c.get("content") or ""))
    if kind == "heading":
        return " ".join(content.split())[:200] or None
    if kind == "text":
        line = " ".join(content.split())
        if "\n" in content.strip() or not (2 <= len(line) <= TEXT_HEADING_MAX):
            return None
        if _OUTLINE.match(line) and not _SENTENCE_END.search(line) and not _BULLET.match(line):
            return line
        return None
    if kind == "table":
        try:
            t = json.loads(content)
        except ValueError:
            return None
        cells = [str(x[-1]).strip() for x in t.get("cells") or [] if str(x[-1]).strip()]
        if t.get("n_rows", 9) > 2 or not (1 <= len(cells) <= 3):
            return None
        # 띠의 첫 칸이 그대로 장 제목이면(「Ⅴ. 지속가능성」 옆 칸은 장식 문구) 그 칸만
        first = " ".join(cells[0].split())
        if len(first) <= TEXT_HEADING_MAX and re.match(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|I{1,3}|IV|VI{0,3}|IX|X)\s*[.．]\s*[가-힣]", first):
            return first
        text = " ".join(" ".join(cells).split())
        if len(text) > TEXT_HEADING_MAX or "![" in text:
            return None
        if re.fullmatch(r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|\d{1,2}", cells[0]) and len(cells) >= 2:
            return f"{cells[0]}. {' '.join(cells[1:])}"
        # 이름 칸 표(「우수사례명 | 산학연협력 체제 강화를 위한 …」·「과제명 | …」) — 값이 그 부분의 제목이다
        if len(cells) == 2 and re.fullmatch(r"[가-힣 ]{1,10}명", cells[0]) and 4 <= len(cells[1]) <= TEXT_HEADING_MAX:
            return re.sub(r"^[○◦∘·\-\s]+", "", cells[1])
        # 꺾쇠 띠(「[비전 및 산학연협력 체제] 우수사례 2」)
        if re.match(r"^\s*[\[【][^\]】]{2,30}[\]】]", text):
            return text
        if _OUTLINE.match(text) and not _SENTENCE_END.search(text):
            return text
    return None


TOC_RUN = 5


PAGE_TEXT_MIN = 200


def _line_heading(line: str) -> str | None:
    line = " ".join(line.split())
    if not (2 <= len(line) <= TEXT_HEADING_MAX) or _BULLET.match(line):
        return None
    if _OUTLINE.match(line) and not _SENTENCE_END.search(line) and not re.search(r"\d\s*$", line[-3:] if len(line) > 40 else ""):
        return line
    return None


def _running_heads(ordered: list[dict]) -> set[str]:
    """쪽마다 위에 되풀이되는 머리 줄(「Ⅱ. 사업 추진내용」) — 쪽 글 조각 셋 이상의 앞 세 줄에 나오면 머리 줄이다."""
    seen: dict[str, int] = {}
    for c in ordered:
        content = str(c.get("content") or "")
        if c.get("kind") != "text" or len(content) < PAGE_TEXT_MIN or "\n" not in content:
            continue
        firsts = [" ".join(x.split()) for x in content.splitlines() if x.strip()][:3]
        for f in set(firsts):
            seen[f] = seen.get(f, 0) + 1
    return {k for k, n in seen.items() if n >= 3}


def _inner_headings(c: dict, running: set[str]) -> list[str]:
    """여러 줄 쪽 글 조각(PDF 글자층) 안의 제목 줄들 — 처리기가 쪽째로 넣은 조각에서 절을 찾는다."""
    content = str(c.get("content") or "")
    if c.get("kind") != "text" or len(content) < PAGE_TEXT_MIN or "\n" not in content:
        return []
    out, lines = [], 0
    for raw in content.splitlines():
        line = " ".join(raw.split())
        if not line or line in running or re.fullmatch(r"[\d\s\-–]+", line):
            continue
        lines += 1
        if re.search(r"[·.…]{4,}\s*\d+\s*$", line):          # 점선 뒤 쪽 번호 — 목차 줄
            out.append("\0toc")
            continue
        h = _line_heading(line)
        if h:
            out.append(h)
    if out and (out.count("\0toc") >= 3 or (len(out) >= TOC_RUN and len(out) >= 0.6 * lines)):
        return []                                             # 목차 쪽
    return [h for h in out if h != "\0toc"]


def _ordered(chunks: list[dict]) -> list[dict]:
    """쪽 번호가 있으면 (쪽, seq) 순 — 한글 변환에서 장 간지 제목이 문서 맨 앞 seq 로 나오는 일이 있다(실측 2026-10-01: #584 의 Ⅰ~Ⅵ 가
    seq 2~7 인데 쪽은 14·67·101 …). 쪽 번호가 없는 조각이 섞이면 seq 순."""
    if chunks and all(c.get("page_no") for c in chunks):
        return sorted(chunks, key=lambda c: (int(c["page_no"]), c["seq"]))
    return sorted(chunks, key=lambda c: c["seq"])


def _toc_seqs(ordered: list[dict]) -> set[int]:
    """목차로 보이는 조각 — 제목 꼴 조각(처리기 표시든 개요 번호 줄이든)이 본문 없이 TOC_RUN 개 이상 잇달아 나오면 목차다.
    목차 줄을 제목으로 세우면 목차 마지막 장(「Ⅵ. 컨설팅 반영 사항」) 아래로 본문 절이 전부 들어간다. 목차에는 처리기가 제목으로
    표시한 줄과 그냥 본문 줄이 섞여 있다(실측 #584)."""
    out: set[int] = set()
    run: list[int] = []
    page = None
    for c in ordered + [{"seq": -1, "kind": "text", "content": "본문"}]:
        promoted = heading_text(c) is not None
        if promoted and (page is None or c.get("page_no") == page):   # 쪽이 바뀌면 끊는다 — 목차 다음 쪽의 첫 본문 제목들은 목차가 아니다
            run.append(int(c["seq"]))
            page = c.get("page_no")
            continue
        if len(run) >= TOC_RUN:
            out.update(run)
        run, page = ([int(c["seq"])], c.get("page_no")) if promoted else ([], None)
    return out


def build(chunks: list[dict]) -> list[Section]:
    """chunks = doc_chunks. 제목 조각이 없으면 빈 목록."""
    sections: list[Section] = []
    stack: list[Section] = []
    counters: dict[str, int] = {}
    ordered = _ordered(chunks)
    toc = _toc_seqs(ordered)
    running = _running_heads(ordered)
    # 사건 목록: (조각, 제목, 깊이) — 쪽 글 조각은 안의 제목 줄마다 사건 하나, 조각은 그 사건들의 절에 함께 매단다
    events: list[tuple[dict, str | None, int | None]] = []
    for c in ordered:
        inner = _inner_headings(c, running)
        if inner:
            events.append((c, None, None))                 # 조각 앞부분은 지금 절의 본문
            events += [(c, t, _level(t)) for t in inner]
            continue
        title = heading_text(c) if int(c["seq"]) not in toc else None
        events.append((c, title, _level(title) if title else None))
    heads = [(t, lv) for _c, t, lv in events]
    for i, (c, title, lvl) in enumerate(events):
        if title and lvl is not None:
            if lvl == 0:
                # 번호 없는 제목: 바로 다음 제목이 번호를 새로 여는 「1.」·「가.」·「Ⅰ.」이면 그 목록을 묶는 머리(사례 제목 → 1. 추진배경 …),
                # 아니면 지금 절 아래 잎(표지 줄·「□」 꼴 소제목이 맨 위로 올라가 뒤 절을 다 품지 않게)
                nxt = next(((t, lv) for t, lv in heads[i + 1:] if t and lv is not None), (None, None))
                if nxt[0] and nxt[1] and _FIRST.match(nxt[0]):
                    lvl = max(nxt[1] - 0.5, 0.5)              # 그 목록 바로 위 — 앞의 장(Ⅵ.) 아래에 들어가고 앞 사례와는 형제
                else:
                    lvl = min((stack[-1].level + 1) if stack else 1, 7)
            while stack and stack[-1].level >= lvl:
                stack.pop()
            parent = stack[-1].path if stack else ""
            n = counters.get(parent, 0) + 1                # 형제 순번은 부모마다 하나 — 깊이로 나누면 「1.」과 「Ⅰ.」이 같은 id 를 가진다
            counters[parent] = n
            sec = Section(path=f"{parent}.{n}" if parent else str(n), title=title, level=lvl, seq=int(c["seq"]), parent=parent)
            if c.get("kind") == "text" and len(str(c.get("content") or "")) >= PAGE_TEXT_MIN:
                sec.chunks.append(int(c["seq"]))          # 쪽 글 조각 안에서 시작한 절은 그 조각을 본문으로 가진다
            sections.append(sec)
            stack.append(sec)
        elif stack and (not stack[-1].chunks or stack[-1].chunks[-1] != int(c["seq"])):
            stack[-1].chunks.append(int(c["seq"]))
    return sections


def align(a: list[Section], b: list[Section], min_key: int = 4) -> list[tuple[Section, Section, str]]:
    """두 문서의 같은 절 짝 — 제목이 같고, 제목이 문서 안에서 반복되면 상위 절 제목까지 같아야 한다(한 절은 짝 하나).
    '4. 기대효과 및 향후 과제'처럼 과제마다 되풀이되는 제목이 서로 다른 과제끼리 이어지던 것(실측 2026-10-01: 계획↔실적 절 연결의
    83% 가 모호)을 막는다. 돌려주는 것: (a 절, b 절, 근거 설명)."""
    def index(secs: list[Section]):
        by_path = {x.path: x for x in secs}

        def ancestors(x: Section) -> tuple[str, ...]:
            out, cur = [], x.parent
            while cur and len(out) < 3:
                par = by_path.get(cur)
                if not par:
                    break
                out.append(title_key(par.title))
                cur = par.parent
            return tuple(out)
        groups: dict[str, list[tuple[Section, tuple[str, ...]]]] = {}
        for x in secs:
            k = title_key(x.title)
            if len(k) >= min_key:
                groups.setdefault(k, []).append((x, ancestors(x)))
        return groups
    ga, gb = index(a), index(b)
    pairs, used_b = [], set()
    for k, xs in ga.items():
        ys = gb.get(k)
        if not ys:
            continue
        if len(xs) == 1 and len(ys) == 1:
            x, y = xs[0][0], ys[0][0]
            pairs.append((x, y, "제목이 두 문서에서 하나뿐"))
            used_b.add(id(y))
            continue
        for x, ax in xs:
            def depth(ay: tuple[str, ...]) -> int:
                n = 0
                for p_, q_ in zip(ax, ay):
                    if p_ != q_:
                        break
                    n += 1
                return n
            scored = sorted(((depth(ay), y) for y, ay in ys if id(y) not in used_b), key=lambda t: -t[0])
            if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
                continue                                         # 상위 절이 안 맞거나 같은 점수 후보가 여럿 — 잇지 않는다
            y = scored[0][1]
            used_b.add(id(y))
            pairs.append((x, y, f"제목과 상위 절 {scored[0][0]}단계가 같음"))
    return pairs


def _bigrams(t: str) -> set[str]:
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", t or "")
    return {t[i:i + 2] for i in range(len(t) - 1)} or ({t} if t else set())


def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", t or "")}


def _jac(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _wjac(a: set, b: set, idf: dict) -> float:
    """낱말 희소성 가중 겹침 — 어디에나 나오는 낱말(LINC·산학협력·운영)은 가볍게(5차 반복)."""
    if not a or not b:
        return 0.0
    w = lambda xs: sum(idf.get(x, 1.0) for x in xs)
    return w(a & b) / (w(a | b) or 1.0)


# 번호 붙은 사례·과제(「[인력양성] 우수사례 1」) — 제목은 자리 표시일 뿐, 무엇인지는 본문이 정한다
_INSTANCE = re.compile(r"[가-힣A-Za-z]\s*\d{1,2}(?=\s|$)")


def align_context(a: list[Section], b: list[Section], text_of=lambda s: "", min_key: int = 4,
                  accept: float = 0.25, margin: float = 0.08, generic_parent: float = 0.5,
                  generic_body: float = 0.15, skip_b: re.Pattern | None = None,
                  instance_min: float = 0.5) -> list[tuple[Section, Section, str]]:
    """같은 제목의 절 짝을 맥락 점수로 고른다(2차 반복, 2026-10-01).
    점수 = 0.5 × 상위 절 제목 글자 겹침(가까운 조상일수록 무게) + 0.5 × 본문·하위 절 제목 낱말 겹침.
    1차(상위 절 제목이 정확히 같아야)는 계획서·보고서가 상위 제목을 조금씩 다르게 써서 맞는 짝을 놓쳤고(버린 후보의 15% 가 같은
    과제), 제목이 하나뿐인 짝은 맥락을 안 봐 30% 가 틀렸다. 1등이 accept 를 넘고 2등과 margin 이상 차이 날 때만 잇는다."""
    def index(secs: list[Section]):
        by_path = {x.path: x for x in secs}
        kids: dict[str, list[str]] = {}
        for x in secs:
            kids.setdefault(x.parent, []).append(x.title)
        info = {}
        for x in secs:
            chain, cur = [], x.parent
            while cur and len(chain) < 3:
                par = by_path.get(cur)
                if not par:
                    break
                chain.append(_bigrams(_NUM.sub("", par.title)))
                cur = par.parent
            info[id(x)] = (chain, _words(" ".join(kids.get(x.path, [])) + " " + (text_of(x) or "")))
        # 사례 절은 하위 절 제목이 틀(추진배경·추진과정)이라 같아 보인다 — 자기와 하위 절의 본문 글만 따로
        own: dict[int, set] = {}
        for x in secs:
            if _INSTANCE.search(x.title):
                desc = [y for y in secs if y.path.startswith(x.path + ".")]
                own[id(x)] = _words(" ".join([text_of(x) or ""] + [text_of(y) or "" for y in desc]))
        info["own"] = own
        groups: dict[str, list[Section]] = {}
        for x in secs:
            k = title_key(x.title)
            if len(k) >= min_key:
                groups.setdefault(k, []).append(x)
        return groups, info
    ga, ia = index(a)
    gb, ib = index(b)
    by_a = {x.path: x for x in a}
    by_b = {x.path: x for x in b}
    if skip_b is not None:
        # 실적보고서 끝의 「차년도 사업계획」 아래 절은 다음 연차 계획이다 — 같은 연차 계획서와 잇지 않는다(9차 반복 오류의 1/4)
        def under_skip(y: Section) -> bool:
            cur = y
            while cur is not None:
                if skip_b.search(cur.title):
                    return True
                cur = by_b.get(cur.parent)
            return False
        gb = {k: [y for y in ys if not under_skip(y)] for k, ys in gb.items()}
    # 두 문서의 절 본문을 문서 모음으로 보고 낱말 희소성(idf)을 잰다
    import math
    bags = [v[1] for k, v in ia.items() if k != "own"] + [v[1] for k, v in ib.items() if k != "own"]
    df: dict[str, int] = {}
    for bag in bags:
        for wd in bag:
            df[wd] = df.get(wd, 0) + 1
    n_bags = max(len(bags), 1)
    idf = {wd: math.log((n_bags + 1) / (c + 0.5)) for wd, c in df.items()}

    def score(x: Section, y: Section, generic: bool = False) -> float:
        ca, wa = ia[id(x)]
        cb, wb = ib[id(y)]
        weights = (0.6, 0.3, 0.1)
        anc = sum(w * _jac(p_, q_) for w, p_, q_ in zip(weights, ca, cb)) / (sum(weights[:min(len(ca), len(cb))]) or 1)
        if not ca and not cb:
            anc = 1.0                                         # 둘 다 최상위 절
        if not wa and not wb:
            return 0.0                                        # 본문도 하위 절도 없는 머리글(표지 줄 등)끼리는 잇지 않는다(5차)
        body = _wjac(wa, wb, idf)
        if id(x) in ia["own"] and id(y) in ib["own"]:
            body = _wjac(ia["own"][id(x)], ib["own"][id(y)], idf)
        if generic:
            # 흔한 제목('1. 추진배경 및 개요')은 같은 상위 제목 아래 과제 사례마다 되풀이된다 — 무엇에 대한 절인지는 본문이 정한다(4차)
            return 0.3 * anc + 0.7 * body if body >= generic_body else 0.0
        return 0.5 * anc + 0.5 * body

    def anc_sim(x: Section, y: Section) -> float:
        ca, _ = ia[id(x)]
        cb, _ = ib[id(y)]
        return _jac(ca[0], cb[0]) if ca and cb else (1.0 if not ca and not cb else 0.0)

    # 번호 붙은 사례 — 이름표(「우수사례 1」)는 문서마다 번호가 바뀐다(계획서 사례 1 = 보고서 사례 2, 실측 0.91). 사례끼리 본문만 견줘
    # 1:1 로 잇는다(같은 사례 0.73~0.96, 다른 사례 0.1~0.3 실측)
    inst_a = [x for x in a if _INSTANCE.search(x.title) and id(x) in ia["own"]]
    inst_b = [y for y in b if _INSTANCE.search(y.title) and id(y) in ib["own"]]
    inst_c = sorted(((_jac(ia["own"][id(x)], ib["own"][id(y)]), x, y) for x in inst_a for y in inst_b), key=lambda t: -t[0])
    inst_pairs, ua, ub = [], set(), set()
    for sc, x, y in inst_c:
        if sc < instance_min or id(x) in ua or id(y) in ub:
            continue
        ua.add(id(x))
        ub.add(id(y))
        inst_pairs.append((x, y, f"사례 본문 겹침 {sc:.2f}"))
    inst_linked = {(id(x), id(y)) for x, y, _w in inst_pairs}

    cands = []
    for k, xs in ga.items():
        ys = gb.get(k, [])
        # 문서 안에서 3번 이상 되풀이되는 흔한 제목('1. 추진배경 및 개요')은 바로 위 절 제목이 충분히 같아야 한다(3차 반복:
        # 상위 과제 이름이 '성과'만 겹쳐도 이어지던 오류)
        if any(_INSTANCE.search(z.title) for z in xs[:1] + ys[:1]):
            continue                                          # 번호 붙은 사례는 아래에서 본문으로 따로 맞춘다
        generic = len(xs) >= 3 or len(ys) >= 3
        for x in xs:
            for y in ys:
                px, py = by_a.get(x.parent), by_b.get(y.parent)
                under_instances = px is not None and py is not None and _INSTANCE.search(px.title) and _INSTANCE.search(py.title)
                if under_instances:
                    if (id(px), id(py)) not in inst_linked:
                        continue                              # 다른 사례의 「1. 추진배경 및 개요」
                elif generic and anc_sim(x, y) < generic_parent:
                    continue
                cands.append((score(x, y, generic), x, y, generic))
    cands.sort(key=lambda t: -t[0])
    pairs, used_a, used_b = list(inst_pairs), set(), set()
    for sc, x, y, generic in cands:
        if sc < accept:
            continue
        if not generic:
            # 흔하지 않은 제목이 한 문서에 두 번까지(요약 절과 상세 절) — 기준을 넘는 짝은 모두 잇는다(4차: 하나만 잇던 탓에 놓침)
            pairs.append((x, y, f"제목 같음·맥락 점수 {sc:.2f}"))
            continue
        if id(x) in used_a or id(y) in used_b:
            continue
        # 같은 절이 걸린 다른 후보(아직 안 쓰인 것) 중 가장 높은 점수와 margin 이상 차이 나야 잇는다
        rival = max((s2 for s2, x2, y2, _g in cands
                     if (x2 is x) != (y2 is y) and id(x2) not in used_a and id(y2) not in used_b), default=0.0)
        if sc - rival < margin:
            continue
        used_a.add(id(x))
        used_b.add(id(y))
        pairs.append((x, y, f"흔한 제목·본문 맥락 점수 {sc:.2f}"))
    # 흔한 제목 짝은 부모끼리도 이어져야 한다 — 부모 제목이 같은데(같은 「우수사례 1」) 부모 짝이 안 이어졌으면 다른 사례의 절이다.
    # 부모가 떨어지면 그 아래도 떨어지므로 바뀌지 않을 때까지 되풀이
    while True:
        linked = {(id(x), id(y)) for x, y, _w in pairs}
        keep = []
        for x, y, why in pairs:
            px, py = by_a.get(x.parent), by_b.get(y.parent)
            same_parent_title = px is not None and py is not None and title_key(px.title) == title_key(py.title) \
                and len(title_key(px.title)) >= min_key
            is_generic = why.startswith("흔한")
            if (is_generic or _INSTANCE.search(px.title if px else "")) and same_parent_title and (id(px), id(py)) not in linked:
                continue
            keep.append((x, y, why))
        if len(keep) == len(pairs):
            return pairs
        pairs = keep


def align_content(a: list[Section], b: list[Section], text_of, skip_b_paths: set[str] = frozenset(),
                  min_sim: float = 0.2, margin: float = 0.05, min_chars: int = 200,
                  skip_b: re.Pattern | None = None) -> list[tuple[Section, Section, str]]:
    """제목이 아니라 본문으로 잇기(근거 '유사도') — 계획서와 실적보고서의 목차 틀이 다른 사업(LINC+: 계획서는 평가 항목별, 대구시
    실적보고서는 프로그램별)에서 제목 짝이 하나도 없을 때. 보고서 절(본문 min_chars 이상)마다 계획서 절(자기+하위 절 본문)과
    희소 낱말 가중 겹침을 재서 1등이 min_sim 을 넘고 2등과 margin 이상 차이 나면 잇는다. 이미 제목으로 이어진 보고서 절은 건너뛴다."""
    import math

    def full(tree: list[Section]) -> dict[int, str]:
        out = {}
        for x in tree:
            desc = [y for y in tree if y.path == x.path or y.path.startswith(x.path + ".")]
            out[id(x)] = " ".join(text_of(y) or "" for y in desc)[:6000]
        return out
    by_b = {y.path: y for y in b}

    def under_skip(y: Section) -> bool:
        cur = y
        while cur is not None and skip_b is not None:
            if skip_b.search(cur.title):
                return True
            cur = by_b.get(cur.parent)
        return False
    fa, fb = full(a), full(b)
    # 문서 전체를 품는 큰 절(요약표·표지 묶음·장 하나에 다 든 꼴)은 낱말이 많아 늘 이긴다 — 하위 절까지 합친 본문이 문서 전체의
    # 30% 를 넘는 절은 후보에서 뺀다(LINC+ 판정: 「【…사업수행계획서】」 요약 절 ↔ 「Ⅱ. 추진경과」)
    total_a = sum(len(text_of(x) or "") for x in a) or 1
    own_full = {id(x): sum(len(text_of(y) or "") for y in a if y.path == x.path or y.path.startswith(x.path + ".")) for x in a}
    wa = {id(x): _words(t) for x in a if len(t := fa[id(x)]) >= min_chars and own_full[id(x)] <= 0.3 * total_a}
    leaf_b = [y for y in b if y.path not in skip_b_paths and not under_skip(y)
              and len(text_of(y) or "") >= min_chars and not any(z.parent == y.path for z in b)]
    wb = {id(y): _words(text_of(y) or "") for y in leaf_b}
    bags = list(wa.values()) + list(wb.values())
    df: dict[str, int] = {}
    for bag in bags:
        for wd in bag:
            df[wd] = df.get(wd, 0) + 1
    idf = {wd: math.log((len(bags) + 1) / (c + 0.5)) for wd, c in df.items()}
    a_by_id = {id(x): x for x in a}
    pairs = []
    for y in leaf_b:
        scored = sorted(((_wjac(wa[k], wb[id(y)], idf), k) for k in wa), reverse=True)
        if not scored:
            continue
        top, k = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        # 같은 갈래(조상·자손) 절끼리의 2등은 경쟁자가 아니다 — 하위 절이 상위 절 본문의 일부라서 점수가 비슷하다
        x = a_by_id[k]
        rivals = [s_ for s_, k2 in scored[1:] if not (a_by_id[k2].path.startswith(x.path + ".") or x.path.startswith(a_by_id[k2].path + "."))]
        second = rivals[0] if rivals else 0.0
        # 1등이 2등의 두 배 이상일 때만(판정 보정 2026-10-02: 틀린 짝은 2등과 점수 차가 작았다 — 차이 0.05 기준 정밀도 약 67%,
        # 두 배 기준 약 90% 어림)
        if top >= min_sim and top >= 2 * second and top - second >= margin:
            # 가장 깊은(구체적인) 같은 갈래 절로 내린다 — 점수가 1등과 거의 같으면
            deeper = [a_by_id[k2] for s_, k2 in scored if s_ >= top - 0.02 and a_by_id[k2].path.startswith(x.path + ".")]
            if deeper:
                x = max(deeper, key=lambda z: z.path.count("."))
            pairs.append((x, y, f"본문 낱말 겹침 {top:.2f}(2등 {second:.2f})"))
    return pairs


def best_section(tree: list[Section], text_of, query: str, min_sim: float = 0.08) -> tuple[Section | None, float]:
    """한 문서 안에서 query 글과 가장 잘 맞는 절(자기+하위 절 본문, 희소 낱말 가중) — 과제 코드로 문서를 좁힌 뒤 그 안의 대응 절을 고를 때
    (보고서 「과제2-1 예산 집행 실적」 → 계획서 「Ⅴ. 예산 운용」). 맞는 절이 없으면 (None, 0)."""
    import math
    full = {}
    for x in tree:
        desc = [y for y in tree if y.path == x.path or y.path.startswith(x.path + ".")]
        full[id(x)] = _words(" ".join(text_of(y) or "" for y in desc)[:6000] + " " + x.title)
    q = _words(query)
    bags = list(full.values()) + [q]
    df: dict[str, int] = {}
    for bag in bags:
        for wd in bag:
            df[wd] = df.get(wd, 0) + 1
    idf = {wd: math.log((len(bags) + 1) / (c + 0.5)) for wd, c in df.items()}
    scored = sorted(((_wjac(full[id(x)], q, idf), x.path.count("."), x) for x in tree), key=lambda t: (-t[0], -t[1]))
    if not scored or scored[0][0] < min_sim:
        return None, 0.0
    top = scored[0][0]
    # 점수가 거의 같으면 더 깊은(구체적인) 절
    best = max((t for t in scored if t[0] >= top - 0.01), key=lambda t: t[1])
    return best[2], best[0]
