"""사업 이름 정리 — 원본 보관소·라이브러리·프로젝트 검색이 같은 규칙으로 사업 이름을 보이고 묶는다.

DB 의 원래 값(archive_files.program_name, projects.name …)은 고치지 않는다. 보일 때·묶을 때만 이 규칙을 쓴다.
규칙에는 특정 사업 이름을 적지 않는다. 표기 습관(공백·괄호·앞 부처명·때 꼬리표)과 이름의 짜임(사업을 가리키는 낱말,
고유한 낱말이 남는가)만 보므로 앞으로 들어올 사업 이름에도 그대로 맞는다.

- display_name: 보일 이름. 공백·괄호 정리, 앞 부처명(…부·…처·…청) 떼기, 앞뒤의 연도·연차 꼬리표 떼기
- is_program_name: 사업 이름다운가(아니면 화면에서 「사업 미분류」로 둔다)
- group_key: 같은 이름 판정용 열쇠(띄어쓰기·점·괄호 기호·끝 「사업」 차이를 지운 것)
- group_names: 이름 여럿을 근거가 있을 때만 묶는다(같은 열쇠 · 외부 확인 장부 · 앞 수식어만 뗀 이름)
- split_year_round: 「2018년 ○○사업(2차년도)」 → (○○사업, 2018, 2)
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Iterable

# ── 보일 이름 ────────────────────────────────────────────────────────────────
_SPACE = re.compile(r"\s+")
_IN_OPEN = re.compile(r"\(\s+")
_IN_CLOSE = re.compile(r"\s+\)")
_LEAD_MARK = re.compile(r"^[\s·ㆍ•∙\-–—*,.:;○●□■▶▷※]+")
# 앞 부처·중앙 행정기관 — 세 글자 이상이고 「부·처·청」으로 끝나는 첫 낱말(교육부·고용노동부·인사혁신처·특허청 …).
# 이름이 아니라 주관 기관을 덧붙인 것이다. 두 글자(학부·본부)는 이름의 일부일 수 있어 떼지 않는다.
_LEAD_MINISTRY = re.compile(r"^[가-힣]{2,9}(?:부|처|청)\s+(?=\S)")
# 때 꼬리표 — 연도(2025년·2025학년도·(2025))와 연차(2차년도·(2차년도)). 「3단계」「LINC 3.0」 같은 단계·판은 이름이라 두지 않는다.
_YEAR = r"(?:19|20)\d{2}"
_LEAD_YEAR = re.compile(rf"^\(?({_YEAR})\)?\s*(?:학년도|년도|년)?\s*(?:\(\s*\d\s*차\s*년도\s*\))?\s*(?=[가-힣A-Za-z(])")
_LEAD_ROUND = re.compile(rf"^([1-9])\s*차\s*년도\s*(?:\(\s*({_YEAR})\s*\))?\s*")
_TAIL_ROUND = re.compile(rf"\s*\(?\s*([1-9])\s*차\s*년도\s*(?:\(\s*({_YEAR})\s*\))?\s*\)?\s*$")
_TAIL_YEAR = re.compile(rf"\s*\(\s*({_YEAR})\s*(?:학년도|년도|년)?\s*\)\s*$")


def _tidy(raw: str) -> str:
    s = unicodedata.normalize("NFKC", raw or "")          # 전각 괄호·공백 → 보통 글자
    s = _SPACE.sub(" ", s).strip()
    s = _IN_CLOSE.sub(")", _IN_OPEN.sub("(", s))           # 「( BRIDGE )」 → 「(BRIDGE)」
    return _LEAD_MARK.sub("", s).strip()


def split_year_round(raw: str) -> tuple[str, int | None, int | None]:
    """(때 꼬리표를 뗀 이름, 연도, 연차). 꼬리표가 이름 전체면 원래 이름을 돌려준다."""
    s = _tidy(raw)
    year = rnd = None
    for _ in range(3):                                    # 「2023년 2차년도 …」처럼 겹친 것도
        m = _LEAD_YEAR.match(s)
        if m and len(s) > m.end():
            year = year or int(m.group(1))
            s = s[m.end():].strip()
            continue
        m = _LEAD_ROUND.match(s)
        if m and len(s) > m.end():
            rnd = rnd or int(m.group(1))
            year = year or (int(m.group(2)) if m.group(2) else None)
            s = s[m.end():].strip()
            continue
        break
    for _ in range(2):
        m = _TAIL_ROUND.search(s)
        if m and m.start() > 0:
            rnd = rnd or int(m.group(1))
            year = year or (int(m.group(2)) if m.group(2) else None)
            s = s[:m.start()].strip()
            continue
        m = _TAIL_YEAR.search(s)
        if m and m.start() > 0:
            year = year or int(m.group(1))
            s = s[:m.start()].strip()
            continue
        break
    return (s or _tidy(raw)), year, rnd


def display_name(raw: str) -> str:
    """보일 이름 — 공백·괄호를 고르고, 앞 부처명과 앞뒤 연도·연차 꼬리표를 뗀다. 남는 것이 없으면 정리만 한 원래 이름."""
    s, _y, _r = split_year_round(raw)
    m = _LEAD_MINISTRY.match(s)
    if m and len(s[m.end():].replace(" ", "")) >= 4:
        s = s[m.end():]
    return s.strip()


# ── 사업 이름다운가 ──────────────────────────────────────────────────────────
_PROGRAM_WORD = re.compile(r"사업|프로젝트|프로그램")
# 영문 약칭 이름 — 대문자로 시작하고 소문자가 셋 이상 잇따르지 않는다(WCC·BRIDGE3.0·HiVE). 소문자 낱말이 섞인 제품·파일 이름
# (「Setup2.1」「WInstall3.1」)은 약칭이 아니다
_ACRONYM = re.compile(r"^[A-Z][A-Za-z0-9+.\-]{1,14}$")
# 어떤 사업인지가 아니라 사업을 어떻게 다루는지(회계·절차·범위)를 말하는 일반어 — 이것과 일반어만 남으면 사업 하나를 가리키지 않는다
_PROCEDURE = re.compile(r"비수익|수익|수행|통합|공고|과제|이월|관련|대상|해당|신규|계속|세부|단위|하위|정산|집행|예산|"
                        r"성과조사|컨소시엄|참여|주관|협약|차년도|연차")
_TIME = re.compile(rf"{_YEAR}\s*(?:학년도|년도|년)?|[1-9]\s*차\s*년도")


def is_program_name(name: str, classified: bool = False) -> bool:
    """사업 이름으로 보일 만한가 — 두 조건을 모두 맞아야 한다.

    classified=True 는 사업 분류기(graph.programs)가 이미 사업 카드로 세운 이름(원본 장부의 사업 열쇠가 있는 이름)이다.
    그때는 1을 「사업 낱말이 없어도 되지만, 영문만으로 된 이름이면 약칭 꼴이어야 한다」로 늦춘다 — 외부 확인 장부의
    정식 이름(「○○ 인재양성체계(구 △△)」)은 「사업」 낱말 없이 쓰이기 때문이다. 2는 그대로 건다.

    1. 사업을 가리키는 꼴: 「사업·프로젝트·프로그램」 낱말이 있거나, 영문 약칭 하나(대문자 시작, 소문자 셋 이상 잇따르지 않음)
    2. 고유한 낱말: 때 표기·절차 일반어(수익·수행·통합·공고·과제·이월 …)·사업 일반어(지원·육성·대학 …,
       graph.programs.vague_name)를 지우고도 글자가 둘 이상 남는다

    사업마다 이름을 적어 두는 대신 이름의 짜임만 보므로 처음 보는 사업 이름에도 같은 판정이 난다.
    「통합공고 사업」「과제 1차년도 이월사업」은 2에서, 「Setup2.1」 같은 파일·제품 이름은 1에서 걸린다."""
    from zzaimy.graph.programs import vague_name

    s = display_name(name)
    if not s:
        return False
    bare = s.replace(" ", "")
    acronym = bool(_ACRONYM.match(bare)) and not re.search(r"[a-z]{3}", bare)
    if classified:
        if re.fullmatch(r"[A-Za-z0-9+.\-]+", bare) and not acronym:      # 「Setup2.1」 같은 영문 파일·제품 이름
            return False
    elif not _PROGRAM_WORD.search(s) and not acronym:
        return False
    core = _PROCEDURE.sub("", _TIME.sub("", s))
    return not vague_name(core)


# ── 같은 이름 판정 ──────────────────────────────────────────────────────────
_FLAT = re.compile(r"[\s.·ㆍ\-_()\[\]]+")
_TAIL_WORD = re.compile(r"(?:사업단|사업)$")
_LATIN = re.compile(r"[A-Z][A-Z0-9+]{1,}")


def group_key(name: str) -> str:
    """같은 사업 이름 판정 열쇠 — 띄어쓰기·점·괄호 기호와 끝의 「사업(단)」을 지우고 영문은 대문자로.
    「LINC 3.0 사업」 = 「LINC3.0사업」, 「부트캠프 사업」 = 「부트캠프사업」. 괄호 안 글자는 남긴다(「(LINC)」≠「(LINC+)」)."""
    flat = _FLAT.sub("", display_name(name)).upper()
    core = _TAIL_WORD.sub("", flat)
    return core or flat


def _acronyms(name: str) -> set[str]:
    return set(_LATIN.findall(unicodedata.normalize("NFKC", name or "").upper().replace(" ", "")))


def load_ledger(path: Path | None = None) -> dict:
    """외부 확인 장부(data/platform/kg_external.json) — 없으면 빈 장부."""
    if path is None:
        from zzaimy.app import paths
        path = paths.platform_dir() / "kg_external.json"
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _ledger_sets(ledger: dict | None) -> list[set[str]]:
    """장부가 출처와 함께 「같은 사업」이라고 적은 이름 묶음(열쇠 집합) — 사업 항목의 이름·별칭(terms)과 「이름 바뀜」 사실."""
    out = []
    for e in (ledger or {}).get("programs", []) or []:
        terms = [t for t in (e.get("terms") or []) + [e.get("name") or ""] if t]
        if e.get("sources") and terms and e.get("category") != "사업 아님":
            out.append({group_key(t) for t in terms})
    for f in (ledger or {}).get("facts", []) or []:
        if f.get("relation") == "renamed" and f.get("sources") and f.get("terms"):
            out.append({group_key(t) for t in f["terms"] if t})
    return [s for s in out if s]


def ledger_terms(ledger: dict | None) -> set[str]:
    """외부 확인 장부가 출처와 함께 사업으로 적은 이름·별칭의 열쇠(group_key) 전부 — 「사업 아님」 항목은 뺀다."""
    return set().union(*_ledger_sets(ledger)) if _ledger_sets(ledger) else set()


_BARE_ACR = re.compile(r"^([A-Z][A-Z+]{2,9})(\d+(?:\.\d+)?)?$")


def spelling_variant(short: str, other_acronyms: Iterable[str]) -> str | None:
    """약칭 이름 하나(「ABCD3.0」처럼 영문 약칭 + 판 번호뿐)가 다른 사업 약칭의 한 글자 오타인가 — 맞으면 그 약칭.

    조건: 둘 다 영문 약칭 + 같은 판 번호, 영문 부분의 길이가 같고(4자 이상) 정확히 한 글자만 다르다. 세 글자 약칭은
    한 글자 차이로 전혀 다른 약칭이 흔해(WCC·WCU) 보지 않는다. 판 번호가 다르면 다른 단계 사업이다."""
    m = _BARE_ACR.match(_FLAT.sub("", unicodedata.normalize("NFKC", short or "")).upper())
    if not m or len(m.group(1)) < 4:
        return None
    hits = set()
    for o in other_acronyms:
        n = _BARE_ACR.match(_FLAT.sub("", unicodedata.normalize("NFKC", o or "")).upper())
        if not n or (n.group(2) or "").replace(".", "") != (m.group(2) or "").replace(".", ""):
            continue
        a, b = m.group(1), n.group(1)
        if len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1:
            hits.add(o)
    return hits.pop() if len(hits) == 1 else None


def group_names(counts: dict[str, int] | Iterable[str], ledger: dict | None = None,
                aliases: dict[str, Iterable[str]] | None = None, known: Iterable[str] = (),
                classified: bool = False) -> dict[str, str]:
    """원래 이름 → 대표 보일 이름. 사업 이름이 아니면 ""(화면은 「사업 미분류」로 둔다).

    aliases: 원래 이름 → 그 사업의 다른 이름·약칭(그래프 사업 노드 등). 오타 판정(4)에만 쓴다.
    known: 사업 노드·외부 확인 장부가 이미 사업으로 확인한 이름 — is_program_name 검사 없이 사업으로 둔다
    (장부의 정식 이름은 「지역성장 인재양성체계(앵커)」처럼 「사업」 낱말이 없을 수 있다).
    classified: 이름 전부가 사업 분류기가 세운 사업 카드의 이름일 때 — is_program_name(classified=True) 로 검사한다.

    다른 이름을 하나로 묶는 근거는 넷뿐이다. 근거가 없으면 따로 둔다.
    1. 같은 열쇠(group_key) — 띄어쓰기·괄호·앞 부처명·때 꼬리표만 다른 이름
    2. 외부 확인 장부 — 출처가 있는 한 사업 항목의 이름·별칭이거나 「이름 바뀜」 사실로 이어진 이름
    3. 앞 수식어만 뗀 이름 — 짧은 이름이 긴 이름의 앞 낱말 몇 개를 통째로 뗀 것과 같을 때(「○○ △△ 부트캠프 사업」 ⊃
       「부트캠프 사업」). 우리말 사업 이름은 꾸밈말이 앞, 중심 낱말이 끝에 오기 때문이다. 다만
       - 짧은 이름에 일반어를 빼고 세 글자 이상 고유한 말이 남아야 하고(「인재육성사업」 같은 일반 이름은 묶지 않는다)
       - 그런 긴 이름이 하나뿐이어야 하고(여럿이면 어느 사업인지 모른다)
       - 두 이름의 영문 약칭이 서로 다르거나 장부가 서로 다른 사업으로 적었으면 묶지 않는다
    4. 약칭 오타 — 영문 약칭 + 판 번호뿐인 이름이 다른 묶음 하나의 약칭(이름 속 약칭·aliases)과 한 글자만 다르고(spelling_variant)
       그 묶음의 파일이 열 배 이상 많을 때. 드문 표기가 많이 쓰인 약칭의 오타라는 근거다
    대표 이름은 묶음에서 건수가 가장 많은 이름(같으면 긴 이름)이다."""
    from zzaimy.graph.programs import vague_name

    if not isinstance(counts, dict):
        counts = {n: 1 for n in counts}
    known_set = set(known)
    out: dict[str, str] = {}
    shown: dict[str, str] = {}
    by_key: dict[str, list[str]] = defaultdict(list)
    for raw in counts:
        disp = display_name(raw)
        if not raw or (raw not in known_set and not is_program_name(disp, classified)):
            out[raw] = ""
            continue
        shown[raw] = disp
        by_key[group_key(disp)].append(raw)
    parent = {k: k for k in by_key}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    entry_of: dict[str, int] = {}
    for i, keys in enumerate(_ledger_sets(ledger)):
        hit = [k for k in by_key if k in keys]
        for k in hit:
            entry_of.setdefault(k, i)
        for k in hit[1:]:
            union(hit[0], k)
    # 3. 앞 수식어만 뗀 이름
    tails: dict[str, set[str]] = defaultdict(set)          # 뒤 낱말 열쇠 → 그 꼬리를 가진 긴 이름 열쇠
    for k, raws in by_key.items():
        toks = shown[raws[0]].split(" ")
        for i in range(1, len(toks)):
            tk = group_key(" ".join(toks[i:]))
            if tk and tk != k:
                tails[tk].add(k)
    for k in list(by_key):
        longer = {find(x) for x in tails.get(k, ())} - {find(k)}
        if len(longer) != 1:
            continue
        distinct = re.sub(r"[^가-힣A-Za-z]", "", _PROCEDURE.sub("", k))
        if vague_name(k) or len(re.sub(r"(?:지원|육성|운영|활성화|사업단?)", "", distinct)) < 3:
            continue
        (other,) = longer
        members = [x for x in by_key if find(x) == other]
        mine_acr = set().union(*(_acronyms(shown[r]) for r in by_key[k]))
        their_acr = set().union(*(_acronyms(shown[r]) for x in members for r in by_key[x]))
        if mine_acr and their_acr and mine_acr != their_acr:
            continue
        mine_e = entry_of.get(k)
        their_e = {entry_of[x] for x in members if x in entry_of}
        if mine_e is not None and their_e and mine_e not in their_e:
            continue
        union(other, k)
    # 4. 약칭 오타
    size: dict[str, int] = defaultdict(int)
    acrs: dict[str, set[str]] = defaultdict(set)
    for k, raws in by_key.items():
        for r in raws:
            size[find(k)] += int(counts.get(r) or 0)
            acrs[find(k)] |= {a for a in re.findall(r"[A-Z][A-Za-z+]{2,9}\s?\d*(?:\.\d+)?", unicodedata.normalize("NFKC", r))}
            acrs[find(k)] |= {str(a) for a in (aliases or {}).get(r, ()) if a}
    for k, raws in by_key.items():
        root = find(k)
        if not _BARE_ACR.match(k) or len(by_key) < 2:
            continue
        cands = {}
        for other in {find(x) for x in by_key} - {root}:
            hit = spelling_variant(shown[raws[0]], acrs[other])
            if hit:
                cands[other] = hit
        if len(cands) == 1:
            (other,) = cands
            if size[other] >= 10 * max(size[root], 1):
                union(other, root)
    groups: dict[str, list[str]] = defaultdict(list)
    for k, raws in by_key.items():
        groups[find(k)].extend(raws)
    for raws in groups.values():
        total: dict[str, int] = defaultdict(int)
        for r in raws:
            total[shown[r]] += int(counts.get(r) or 0)
        rep = max(total, key=lambda d: (total[d], len(d)))
        for r in raws:
            out[r] = rep
    return out
