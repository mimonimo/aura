"""사업 분류 — 사업 카드와 문서별 사업·연차·갈래(ADR-0048 0항, 사용자 2026-10-01 "각 사업별 분류도 잘 되어야 함").

사업 이름은 코드에 넣지 않는다. 문서 제목·표지·앞머리에서 사업명(…지원사업·…육성사업 등)과 괄호 약칭(…(LINC 3.0))을 뽑고,
한 문서 안에서 '긴 이름(약칭)'·'약칭(긴 이름)'으로 함께 쓰인 표기를 같은 사업으로 묶는다(다른 이름은 문서가 알려 준다).
문서는 제목(무게 3)·앞머리(무게 1) 언급으로 점수를 매겨 사업을 고르고, 근거가 약하거나 엇갈리면 검토 대기로 둔다.
폴더 경로를 알면(DGX 원본은 사업별 폴더) 경로 언급도 무게 3 으로 더한다.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from zzaimy.graph.entities import _GENERIC, _PROGRAM, acronyms, clean_title, program_key

# 소수점 판번호가 붙은 영문 약칭(LINC 3.0·LINC3.0) — 사업명 옆 괄호가 없어도 제목에 홀로 쓰인다. 'Track7' 같은 번호 매김은 아니다
_VERSIONED = re.compile(r"(?<![A-Za-z])([A-Z]{2,}[A-Za-z+]{0,8})\s?(\d\.\d)(?![\d.])")
_PAIR = re.compile(r"([가-힣A-Za-z0-9·()+ ]{4,40}?(?:사업|사업단))\s*\(([A-Za-z][A-Za-z0-9+. ]{1,14})\)")
# 띄어 쓴 사업명 — 'AID(AI+Digital) 전환 중점 전문대학 지원사업'. 낱말 2~7개가 …사업으로 끝난다
_SPACED = re.compile(r"((?:[가-힣A-Za-z0-9()·+]+ ){1,6}[가-힣A-Za-z0-9()·+]*(?:지원|육성|혁신|선도|중점)?사업)(?![가-힣])")   # 한 줄 안에서만
_LEAD_DROP = re.compile(r"^(?:\d{6}_?|(?:19|20)\d{2}(?:학년도|년도|년)?|학년도|년도|제?\d+(?:차|단계)?|[가-힣]*[은는이가을를의에와과및]|및|등|위한|대한|관한|따른)$")


def _trim_name(name: str) -> str:
    """사업명 앞에 붙은 연도·조사로 끝나는 말·접속어를 뗀다('본인 및 참여 인력은 3단계 …사업' → '3단계 …사업')."""
    toks = name.split()
    cut = 0
    for i, t in enumerate(toks[:-1]):
        if _LEAD_DROP.match(t) and not re.match(r"^\d+단계$", t):
            cut = i + 1
    out = " ".join(toks[cut:]).strip()
    return out if len(re.sub(r"\s", "", out)) >= 6 else ""
# 약칭은 영문으로 시작한다 — 괄호 안 한글(기관명 「(영남이공대학교)」)·숫자(「(2025)」)는 약칭이 아니다
_LATIN_ACR = re.compile(r"\(([A-Za-z][A-Za-z0-9+.]{1,12})\)")
# 이름 맨 앞의 괄호 꼬리표 — 「(영남이공대학교)3단계 …」·「(내용추가) …」 — 이름의 일부가 아니다
_LEAD_LABEL = re.compile(r"^\s*\([^)]{1,20}\)\s*")
# 때를 가리키는 말 — 이것만 남는 이름(「1차년도(2025) 사업」)은 사업명이 아니다
_TIME_WORDS = re.compile(r"[1-9]\s*차\s*년도|\(?\s*(?:19|20)\d{2}\s*(?:학년도|년도|년)?\s*~?\s*\)?|학년도|년도|\d+\s*개|\d+\s*단계")
# 「대구 RISE사업」·「RISE 사업」 — 앞말 하나 + 영문 약칭 + 사업: 그 약칭의 사업이다
_BARE_ACR = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9+]{2,10})\s?사업(?![가-힣])")
_ACR_NAME = re.compile(r"^(?:[가-힣]{2,10}\s*)?([A-Z][A-Za-z0-9+.]{1,10})\s*사업$")


def _is_acr(a: str) -> bool:
    """약칭다운가 — 영문으로 시작, 소문자 풀이(AI+Digital) 아님, 판 표기(ver.4·v2.0) 아님."""
    a = (a or "").strip()
    return bool(re.match(r"[A-Za-z]", a)) and not re.search(r"[a-z]{3}", a) and not re.match(r"(?i)^v(?:er)?[\s._]*\d", a)


_KOR_PAREN = re.compile(r"(?<=[가-힣\s])\(([가-힣]{2,4})\)")
# 이름이 바뀌었음을 알리는 표기 — 「RISE(現 앵커)」·「앵커 추진방안(RISE 재구조화)」·「명칭 변경」
# 「현」 한 글자는 「현황」 같은 낱말에도 있다 — 「現」 또는 괄호 안의 「(현 X)」만
_RENAMED = re.compile(r"現\s*[가-힣A-Za-z]|\(\s*현\s+[가-힣A-Za-z]|재구조화|명칭\s*(?:을\s*)?변경")


def _name_ok(name: str) -> bool:
    core = re.sub(r"[\s()·]", "", _TIME_WORDS.sub("", name)).replace("사업", "")
    return len(re.findall(r"[가-힣A-Za-z]", core)) >= 4


def _norm_name(name: str) -> str:
    return _LEAD_LABEL.sub("", name or "").strip()


_ROUND = re.compile(r"([1-9])\s*차\s*년도")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*(?:년|학년도|\.)")
_EVAL_RESULT = re.compile(r"평가\s*(?:결과|의견)|종합\s*의견")
HEAD_CHARS = 4000


_FLAT = r"[\s._\-]+"          # 대조할 때 지우는 띄어쓰기·점·밑줄·붙임표 — 파일 이름은 띄어쓰기 대신 밑줄을 쓴다


def _acr(s: str) -> str:
    return re.sub(_FLAT, "", s or "").upper()


@dataclass
class ProgramCard:
    key: str                                   # 대표 키(program_key)
    names: Counter = field(default_factory=Counter)
    acrs: Counter = field(default_factory=Counter)
    renamed: list[str] = field(default_factory=list)      # 이름이 바뀐 같은 사업이라는 문서 근거(제목)

    @property
    def name(self) -> str:
        return self.names.most_common(1)[0][0] if self.names else (self.acrs.most_common(1)[0][0] if self.acrs else self.key)

    @property
    def node_id(self) -> str:
        a = self.acrs.most_common(1)[0][0] if self.acrs else ""
        return "program:" + (re.sub(r"[^0-9a-z가-힣]+", "", a.lower()) or self.key)

    def surfaces(self) -> set[str]:
        return {re.sub(_FLAT, "", n) for n in self.names} | {_acr(a) for a in self.acrs}


def _mentions(text: str) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """(사업명들, 약칭들, (긴 이름, 약칭) 짝들)."""
    names = [_norm_name(m.group(0)) for m in _PROGRAM.finditer(text or "") if m.group(0) not in _GENERIC]
    names += [_norm_name(t) for t in (_trim_name(m.group(1)) for m in _SPACED.finditer(text or "")) if t and t not in _GENERIC]
    # 띄어 쓴 가운뎃점·쉼표·빗금은 사업 둘을 늘어놓은 것이다(「…(COSS)사업 · …(HiVE)사업」) — 한 이름으로 묶지 않는다
    names = [_norm_name(part) for n in names for part in re.split(r"\s+[·,/]\s+", n) if part.endswith("사업")]
    names = [n for n in names if n and n not in _GENERIC and _name_ok(n)]
    pairs = [(t, m.group(2).strip()) for m in _PAIR.finditer(text or "")
             if (t := _norm_name(_trim_name(m.group(1).strip()))) and _name_ok(t) and _is_acr(m.group(2))]
    acrs = [f"{m.group(1)}{m.group(2)}" for m in _VERSIONED.finditer(text or "")]
    acrs += _BARE_ACR.findall(text or "")                     # 「RISE사업(2025~)」 — 홀로 쓰인 약칭+사업
    for n in names:
        # 괄호 속 한글 약칭 후보(「지역성장 인재양성체계(앵커)사업」의 앵커) — build_cards 가 다른 제목에서 「앵커사업」으로도 쓰이는지 본다
        for k in _KOR_PAREN.findall(n):
            pairs.append((n, "k:" + k))
        inner = [a for a in _LATIN_ACR.findall(n) if _is_acr(a)]          # 「(AI+Digital)」은 풀이, 「(ver.4)」는 판 표기
        acrs += inner
        pairs += [(n, a) for a in inner]                      # 「지역혁신중심 대학지원체계(RISE)사업」 — 이름 속 약칭과 같은 사업
        m = _ACR_NAME.match(n)
        if m:
            acrs.append(m.group(1))
            pairs.append((n, m.group(1)))                     # 「대구 RISE사업」 — 그 약칭의 사업
    return names, acrs, pairs


def build_cards(docs: list[dict]) -> list[ProgramCard]:
    """문서들에서 사업 카드를 만든다. docs = [{id, filename, head, path?}] — 같은 사업의 다른 표기를 문서 속 짝으로 묶는다."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    names: dict[str, Counter] = defaultdict(Counter)
    acr_seen: dict[str, Counter] = defaultdict(Counter)
    titled: set[str] = set()                    # 파일 이름·경로에 나온 키
    kor_alias: dict[str, set] = defaultdict(set)  # 괄호 속 한글 약칭 → 그 약칭을 품은 긴 이름 키
    title_texts: list[str] = []
    head_docs: dict[str, set] = defaultdict(set)  # 앞머리에만 나온 키 → 문서들
    for d in docs:
        tp = f"{clean_title(d.get('filename') or '')}\n{d.get('path') or ''}"
        title_texts.append(tp)
        tn, ta, tpairs = _mentions(tp)
        titled.update("n:" + program_key(n) for n in tn)
        titled.update("a:" + _acr(a) for a in ta)
        titled.update("n:" + program_key(l_) for l_, _s in tpairs)
        text = f"{tp}\n{(d.get('head') or '')[:HEAD_CHARS]}"
        ns, acs, pairs = _mentions(text)
        for n in ns:
            head_docs["n:" + program_key(n)].add(d.get("id"))
        for a in acs:
            head_docs["a:" + _acr(a)].add(d.get("id"))
        for n in ns:
            k = "n:" + program_key(n)
            find(k)
            names[k][n.strip()] += 1
        for a in acs:
            k = "a:" + _acr(a)
            find(k)
            acr_seen[k][a.strip()] += 1
        for long_, short in pairs:                              # 문서가 알려 주는 다른 이름
            if short.startswith("k:"):
                kor_alias[short[2:]].add("n:" + program_key(long_))
                continue
            kn, ka = "n:" + program_key(long_), "a:" + _acr(short)
            names[kn][long_] += 1
            acr_seen[ka][short] += 1
            union(kn, ka)
    # 글자 자리만 바뀐 약칭(오타 「RSIE」)은 열 배 넘게 흔한 같은 글자의 약칭과 같은 사업으로 본다
    acr_n = {k: sum(c.values()) for k, c in acr_seen.items()}
    for k, n in acr_n.items():
        a = k[2:]
        if len(a) < 4:
            continue
        for k2, n2 in acr_n.items():
            b = k2[2:]
            if k2 != k and n2 >= 10 * n and len(b) == len(a) and sorted(a) == sorted(b) \
                    and sum(x != y for x, y in zip(a, b)) == 2:
                union(k2, k)
                break
    # 괄호 속 한글 약칭은 다른 제목에서도 「약칭+사업」으로 쓰일 때만 약칭이다(「앵커사업」) — 「(주관)」·「(안)」은 그렇게 안 쓰인다
    joined = "\n".join(title_texts)
    for alias, longs in kor_alias.items():
        if re.search(rf"{re.escape(alias)}\s*사업(?![가-힣]*계획서)", joined) or re.search(rf"{re.escape(alias)}\s*\(", joined):
            ka = "a:" + alias
            acr_seen[ka][alias] += 1
            titled.add(ka)
            for kn in longs:
                union(kn, ka)
            # 「대구 앵커사업」 — 앞말 하나 + 받아들인 약칭 + 사업(영문 약칭의 「대구 RISE사업」과 같은 규칙)
            for k in list(parent):
                if k.startswith("n:") and re.fullmatch(rf"(?:[가-힣]{{2,10}})?{re.escape(alias)}(?:사업)?", k[2:]):
                    union(k, ka)
    # 이름 바뀜 — 한 제목이 두 사업을 함께 말하며 「現·재구조화·명칭 변경」을 쓰면 같은 사업이다(교육부 2026: RISE → 앵커)
    renamed_ev: dict[str, list[str]] = defaultdict(list)
    for tp in title_texts:
        tp = tp.split("\n")[0]                     # 파일 이름만 — 폴더 경로에는 여러 사업이 함께 나온다(「업무공유(LINC사업단)」 아래 LINC+·LINC3.0)
        if not _RENAMED.search(tp):
            continue
        flat_tp = re.sub(_FLAT, "", tp).upper()
        hit = []
        for k in list(parent):
            surf = k[2:]
            if len(surf) >= 2 and re.sub(_FLAT, "", surf).upper() in flat_tp:
                hit.append(k)
        roots = {find(k) for k in hit}
        if len(roots) >= 2:
            ks = sorted(roots)
            for k in ks[1:]:
                union(ks[0], k)
            renamed_ev[find(ks[0])].append(tp.split("\n")[0][:120])
    # 본문 앞머리 구절 하나가 사업이 되지 않게(「대상으로 사업」·「각종 결재 시 … 해당사업」): 파일 이름·경로에 나오거나
    # 문서 세 건 이상의 앞머리에 나온 표기가 하나라도 있는 묶음만 사업 카드로 둔다
    keep_root: set[str] = set()
    for k in list(parent):
        if k in titled or len(head_docs.get(k, ())) >= 3:
            keep_root.add(find(k))
    groups: dict[str, ProgramCard] = {}
    for k in list(parent):
        if find(k) not in keep_root:
            continue
        root = find(k)
        card = groups.setdefault(root, ProgramCard(key=root.split(":", 1)[1]))
        if not card.renamed and renamed_ev.get(root):
            card.renamed = sorted(set(renamed_ev[root]))[:5]
        if k.startswith("n:"):
            card.names.update(names[k])
        else:
            card.acrs.update(acr_seen[k])
    # 줄여 부른 이름(「혁신지원사업」)이 다른 카드 하나의 긴 이름(「전문대학 혁신지원사업」) 끝과 같으면 같은 사업이다.
    # 긴 이름을 가진 카드가 둘 이상이면(어느 사업인지 모름) 합치지 않는다
    # 앞에 붙은 말이 한 낱말뿐일 때만(「전문대학」+혁신지원사업). 「AID 전환 중점」+전문대학 지원사업처럼 고유한 말이 여럿 붙으면
    # 짧은 쪽은 범주 이름이지 그 사업이 아니다
    flat = lambda n: re.sub(r"[\s()·]", "", n)

    def extends(long_: str, short_: str) -> bool:
        lf, sf = flat(long_), flat(short_)
        if lf == sf or not lf.endswith(sf):
            return False
        return len(long_[: len(long_) - len(short_)].split()) <= 1 if long_.endswith(short_) else len(lf) - len(sf) <= 4
    cards = list(groups.values())
    merged: set[int] = set()
    for short in cards:
        if not short.names or short.acrs:
            continue
        hosts = [c for c in cards if c is not short and id(c) not in merged
                 and all(any(extends(ln, sn) for ln in c.names) for sn in short.names)]
        if len(hosts) == 1:
            hosts[0].names.update(short.names)
            merged.add(id(short))
    cards = [c for c in cards if id(c) not in merged]
    for card in cards:
        if card.names:
            card.key = program_key(card.names.most_common(1)[0][0])
    return cards


@dataclass
class Assignment:
    doc_id: int
    program: str = ""                # 노드 id
    program_name: str = ""
    year: int | None = None
    round: int | None = None
    kind: str = ""
    kind_reason: str = ""
    share: float = 0.0
    status: str = "review"           # auto | review
    evidence: list[str] = field(default_factory=list)


def classify(docs: list[dict], cards: list[ProgramCard]) -> list[Assignment]:
    """문서마다 사업·연차·갈래를 정한다. 제목 언급 무게 3, 경로 2, 앞머리 1. 1등이 3점 미만이거나 2등의 두 배에 못 미치면 검토 대기."""
    from zzaimy.app.doc_routing import guess_kind

    # 긴 표기부터. 영문 약칭은 낱말 경계에서만(「DIGITECH」 속 「TECH」는 아니다)
    pool = [(su, c, re.compile((r"(?<![A-Z])" if su[:1].isascii() and su[:1].isalpha() else "") + re.escape(su)
                               + (r"(?![A-Z])" if su[-1:].isascii() and su[-1:].isalpha() else "")))
            for su, c in sorted(((su, c) for c in cards for su in {x.upper() for x in c.surfaces()} if len(su) >= 3),
                                key=lambda t: -len(t[0]))]
    out = []
    for d in docs:
        title = clean_title(d.get("filename") or "")
        head = (d.get("head") or "")[:HEAD_CHARS]
        flat_title = re.sub(_FLAT, "", title).upper()                       # 약칭은 점·밑줄 없이 대조(LINC_3.0 = LINC30)
        flat_path = re.sub(_FLAT, "", d.get("path") or "").upper()
        flat_head = re.sub(_FLAT, "", head).upper()
        scores: Counter = Counter()
        why: dict[str, list[str]] = defaultdict(list)
        # 긴 표기부터 대조하고 대조된 자리는 가린다 — 짧은 이름('전문대학 지원사업')이 긴 이름('AID 전환 중점 전문대학 지원사업')
        # 안에서 또 세지면 모든 언급이 두 사업으로 갈린다(실측 2026-10-01: 1등 몫 0.3 대)
        for su, c, rx in pool:
            if rx.search(flat_title):
                scores[c.node_id] += 3
                why[c.node_id].append(f"제목에 「{su}」")
                flat_title = rx.sub(lambda m: "\0" * len(m.group(0)), flat_title)
            if rx.search(flat_path):                            # 폴더는 파일 제 이름보다 약한 단서 — 다른 사업 문서가 섞여 들어 있다
                scores[c.node_id] += 2
                why[c.node_id].append(f"경로에 「{su}」")
                flat_path = rx.sub(lambda m: "\0" * len(m.group(0)), flat_path)
            n = len(rx.findall(flat_head))
            if n:
                scores[c.node_id] += min(n, 5)
                why[c.node_id].append(f"앞머리에 「{su}」 {n}회")
                flat_head = rx.sub(lambda m: "\0" * len(m.group(0)), flat_head)
        a = Assignment(doc_id=int(d["id"]))
        if scores:
            best, top = scores.most_common(1)[0]
            card = next(c for c in cards if c.node_id == best)
            a.program, a.program_name = best, card.name
            a.share = round(top / sum(scores.values()), 2)
            second = scores.most_common(2)[1][1] if len(scores) > 1 else 0
            # 다른 사업을 함께 언급하는 문서가 많다(LINC3.0 보고서의 RISE·혁신지원 언급) — 몫보다 2등과의 차이로 판정한다
            # 경로만 말하는 파일(「RISE사업(2025~)/…/붙임1.hwp」)은 경로 2점으로 확정한다. 제 이름이 다른 사업을 말하면(3점) 2등의 두 배를 못 넘어 검토로 간다
            floor = 2 if any(w.startswith("경로") for w in why[best]) else 3
            a.status = "auto" if top >= floor and top >= 2 * second else "review"
            a.evidence = why[best][:4]
        else:
            a.evidence = ["사업명 언급을 찾지 못함"]
        m = _ROUND.search(title) or _ROUND.search(head[:600])
        a.round = int(m.group(1)) if m else None
        y = _YEAR.search(title) or _YEAR.search(head[:600])
        a.year = int(y.group(1)) if y else None
        if _EVAL_RESULT.search(title):                          # 평가 '기준'이 아니라 평가 '결과·의견'
            a.kind, a.kind_reason = "evaluation", "제목에 평가 결과·종합의견"
        else:
            a.kind, a.kind_reason = guess_kind(d.get("filename") or "", head)
        out.append(a)
    return out


# 경로·이름의 연도 — 「2022~2027」 같은 기간 표기(사업 전체 기간)는 연도가 아니다
_PATH_YEAR = re.compile(r"(?<![\d~])((?:19|20)\d{2})(?!\d|\s*~)")


def inherit_by_folder(docs: list[dict], assigned: list[Assignment], min_n: int = 10, share: float = 0.7,
                      min_year_docs: int = 3) -> int:
    """사업명이 없는 폴더의 파일에 상위 폴더의 사업을 물려준다 — 「링크/2차년도(2023)/…」처럼 폴더가 사업을 말하지 않을 때.

    가장 가까운 상위 폴더(확정 파일 min_n 건 이상)에서 한 사업이 share 이상이면 그 사업으로 두되 상태는 'folder'(검토 대상,
    자동 확정 아님). 파일 경로에 연도가 있으면 그 연도를 다루는 사업만 후보로 센다 — 사업마다 확정 파일의 연도 범위로
    운영 기간을 어림한다(LINC+ 2017~2021 폴더 아래의 2023 파일은 LINC+ 가 아니다). 물려준 건수를 돌려준다.
    """
    def ancestors(path: str) -> list[str]:
        parts = [p for p in re.split(r"[\\/]", path or "") if p]
        return ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]

    def year_of(d: dict) -> int | None:
        ys = _PATH_YEAR.findall(f"{d.get('path') or ''}/{d.get('filename') or ''}")
        return int(ys[-1]) if ys else None

    under: dict[str, Counter] = defaultdict(Counter)
    years: dict[str, Counter] = defaultdict(Counter)
    names = {}
    for d, a in zip(docs, assigned):
        if a.program and a.status == "auto":
            names[a.program] = a.program_name
            for anc in ancestors(d.get("path") or ""):
                under[anc][a.program] += 1
            y = a.year or year_of(d)
            if y:
                years[a.program][y] += 1
    span = {p: (min(ys), max(ys)) for p, c in years.items()
            if (ys := [y for y, n in c.items() if n >= min_year_docs])}
    n_inherited = 0
    for d, a in zip(docs, assigned):
        if a.program and a.status == "auto":
            continue
        y = year_of(d)
        for anc in ancestors(d.get("path") or ""):
            c = Counter({p: n for p, n in under.get(anc, {}).items()
                         if y is None or p not in span or span[p][0] <= y <= span[p][1]})
            total = sum(c.values())
            if total < min_n:
                continue
            best, n = c.most_common(1)[0]
            if n / total >= share and (not a.program or a.program == best):
                a.program, a.program_name, a.status = best, names.get(best, best), "folder"
                a.share = round(n / total, 2)
                a.evidence = [f"상위 폴더 「{anc[-40:]}」 확정 {total}건 중 {n}건이 이 사업"
                              + (f"(연도 {y} 를 다루는 사업만)" if y else "")]
                n_inherited += 1
            break
    return n_inherited
