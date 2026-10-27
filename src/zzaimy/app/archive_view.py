"""원본 관리 화면(/dev/archive, 관리자) — 원본 목록 장부(app/archive)를 사업 → 연도 → 문서 종류 → 처리 상태로 훑고,
조건을 겹쳐 원본을 찾고, 처리하지 않는 원본을 정리하고, 처리 실패 원본을 다시 읽게 한다.

문서 하나의 반입 결과(개요·조각·색인·그래프)는 하위 화면 「반입 결과 보기」(/dev/archive/doc/{id})에서 본다.

교직원은 이 화면을 쓰지 않는다 — 문서는 라이브러리 한 곳에서 찾고, 원본 파일은 문서 줄·문서 화면의 「원본」(/doc/{id}/original)으로
연다(권한은 문서 경로 검사 그대로).

- 묶음 수는 SQL 한 번(GROUP BY 사업·종류·연도·연차·처리 상태)으로 세고, 고른 조건은 파이썬에서 겹쳐 단계마다 건수를 낸다
- 목록은 같은 조건의 SQL 에 LIMIT/OFFSET 으로 한 쪽씩
- 사업 이름은 화면에서만 정리·묶는다(app/program_names). 장부 값은 그대로 둔다
- 문서 종류 이름은 라이브러리와 같은 목록(doc_routing.KINDS)
- 처리 상태는 반입 연동 점검과 같은 기준(app/archive_state)
- 보이는 경로·제목은 내부 마스킹 설정(ADR-0044)이 켜져 있으면 같은 마스킹기로 가린다
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.background import BackgroundTask

from zzaimy.app import archive, archive_state, program_names, vocab
from zzaimy.app.intake_status import ARCHIVE_STATUS

router = APIRouter()

PAGE = "/dev/archive"           # 원본 관리 화면 — 개발 메뉴 「관리」(app/dev_nav)

UNSORTED = vocab.UNSORTED_KIND
NO_PROGRAM = "사업 미분류"
NONE = "none"                   # 조건 값 — 사업 미분류 · 분류 전 · 연도 미상


def kind_labels() -> dict[str, str]:
    """문서 종류 이름 — 화면 용어 한 곳(app/vocab, 라이브러리와 같은 목록)."""
    return vocab.kind_labels()


def kind_label(code: str, labels: dict[str, str] | None = None) -> str:
    labels = kind_labels() if labels is None else labels
    return labels.get(code or "", "") or (UNSORTED if not code else "기타")


# 형식 — 확장자다운 값(영문·숫자 1~5자, 영문 하나 이상)만 선택지로. 「다운로드」나 「a/b」 같은 값은 확장자가 아니다
_EXT_OK = re.compile(r"^(?=[a-z0-9]*[a-z])[a-z0-9]{1,5}$")


def ext_of(raw: str, rel: str = "") -> str:
    e = (raw or "").lower().strip().lstrip(".")
    if e == vocab.OTHER_FORMAT:
        return e
    if not e and "." in rel.rsplit("/", 1)[-1]:
        e = rel.rsplit(".", 1)[-1].lower()
    return e if _EXT_OK.match(e) else ""


def ext_label(e: str) -> str:
    return vocab.format_label(e)


def _size(n) -> str:
    n = int(n or 0)
    if n < 1_000_000:
        return f"{n / 1000:,.1f} KB"
    if n < 1_000_000_000:
        return f"{n / 1e6:,.1f} MB"
    return f"{n / 1e9:,.2f} GB"


def short_path(rel: str) -> str:
    """저장 위치는 마지막 폴더 셋만 — 전체 경로는 펼침으로."""
    folders = rel.split("/")[:-1]
    return ("… / " if len(folders) > 3 else "") + " / ".join(folders[-3:])


# ── 마스킹(ADR-0044 내부 마스킹 설정) ─────────────────────────────────────────
_MASKER = None


def masker(db):
    """내부 마스킹이 켜져 있으면 글을 가리는 함수, 꺼져 있으면 None. 설정 형식이 깨졌으면 가리는 쪽으로 둔다."""
    from zzaimy.app import privacy_policy
    try:
        policy = privacy_policy.load(db)
    except ValueError:
        policy = {"enabled": True, "entities": list(privacy_policy.ENTITIES)}
    if not policy["enabled"]:
        return None
    global _MASKER
    from zzaimy.ingest.pii import PiiMasker, RawDocument
    if _MASKER is None:
        _MASKER = PiiMasker()
    entities = policy["entities"]
    return lambda text: _MASKER.mask(RawDocument(doc_id="archive", text=text or ""), entities=entities)[0].text if text else text


# ── 사업 묶음 ────────────────────────────────────────────────────────────────
_LEDGER: dict = {}


def _ledger() -> dict:
    if "v" not in _LEDGER:
        _LEDGER["v"] = program_names.load_ledger()
    return _LEDGER["v"]


class Programs:
    """사업 묶음 — 묶음 열쇠는 원본 장부의 사업 열쇠(archive_files.program) 가운데 하나라서 이름 정리 규칙이 바뀌어도
    거르기가 흔들리지 않는다. 그래프 노드 id 는 그래프를 다시 지을 때 바뀔 수 있어 열쇠로 쓰지 않고 보일 이름·약칭에만 쓴다.

    by_key: 장부 열쇠 → 묶음 열쇠("" 은 사업 미분류) · name: 묶음 열쇠 → 보일 이름 · terms: 묶음 열쇠 → 찾을 말(이름·약칭·옛 이름)
    - 그래프 사업 노드(id 또는 이름·약칭으로 맞춘 것)나 외부 확인 장부에 있는 이름은 사업으로 확인된 것이라
      이름 검사(is_program_name)로 떨어뜨리지 않는다
    - 묶음 열쇠는 묶음 안에서 파일이 가장 많은 장부 열쇠, 보일 이름은 그 열쇠에 맞춘 그래프 노드 이름(없으면 group_names 의 대표 이름)
    """

    def __init__(self, names: dict[str, tuple[str, int]], surfaces: dict[str, list[str]] | None = None,
                 nodes: dict[str, str] | None = None):
        from zzaimy.graph import programs as gp
        surfaces, nodes = surfaces or {}, nodes or {}
        self.surfaces = surfaces
        counts: dict[str, int] = defaultdict(int)
        aliases: dict[str, list[str]] = defaultdict(list)
        ledger_keys = program_names.ledger_terms(_ledger())
        known = {nm for nm, _n in names.values() if nm and program_names.group_key(nm) in ledger_keys}
        for key, (name, n) in names.items():
            if not key:
                continue
            nm = name or nodes.get(key) or key
            counts[nm] += n
            aliases[nm] += list(surfaces.get(key, []))
            if key in nodes:
                known.add(nm)
        rep = program_names.group_names(counts, _ledger(), aliases, known=known, classified=True)   # 장부 열쇠 = 분류기의 사업 카드
        groups: dict[str, list[str]] = defaultdict(list)
        for key, (name, _n) in names.items():
            g = rep.get(name or nodes.get(key) or key, "") if key else ""
            if g:
                groups[g].append(key)
        self.by_key = {k: "" for k in names}
        self.name: dict[str, str] = {}
        self.count: dict[str, int] = {}
        self.choices: list[dict] = []          # 마지막 resolve 가 하나로 정하지 못했을 때의 후보(많은 순)
        self.terms: dict[str, list[str]] = {}       # 묶음 제 이름(장부 이름·보일 이름·노드 이름)
        self.alias: dict[str, list[str]] = {}       # 그래프에서 온 다른 이름·약칭(거른 것)
        self.own: dict[str, set[str]] = {}         # 묶음 제 약칭 — 장부 열쇠 꼬리·제 이름 속 약칭
        self.key_acr: dict[str, set[str]] = {}     # 장부 열쇠 꼬리의 약칭(program:abcd → ABCD)
        gids: dict[str, list[str]] = {}
        rep_of: dict[str, str] = {}
        for g, keys in groups.items():
            gid = max(keys, key=lambda k: (names[k][1], k))
            gids[gid], rep_of[gid] = keys, g
            self.count[gid] = sum(names[k][1] for k in keys)
            for k in keys:
                self.by_key[k] = gid
            self.name[gid] = program_names.display_name(nodes.get(gid) or "") or g
        self.collisions = self._settle_names(gids, rep_of, names, nodes)
        for gid, keys in gids.items():
            g = rep_of[gid]
            trusted = {k: nodes[k] for k in keys if k in nodes and gid not in self.collisions}
            terms = {g, self.name[gid]}
            for k in keys:
                terms |= {names[k][0] or "", trusted.get(k, "")}
            self.terms[gid] = sorted(t for t in terms if t)
            self.key_acr[gid] = set().union(*(gp._id_acr(k) for k in keys))
            als = {t for k in keys for t in surfaces.get(k, []) if t}
            if gid in self.collisions:                 # 맞춘 노드가 남의 사업이었다 — 그 노드의 별칭은 버리고 열쇠 꼬리 약칭만
                als = {t for t in als if gp._acr(t) in self.key_acr[gid]}
            self.alias[gid] = sorted(als - set(self.terms[gid]))
            self.own[gid] = self.key_acr[gid] | {gp._acr(a) for t in self.terms[gid] for a in gp._ACR_TOKEN.findall(t)}
        # 남의 제 약칭이 든 별칭은 이 묶음을 가리키지 않는다(그래프 카드에 섞여 들어온 다른 사업 약칭)
        for gid in self.alias:
            theirs = set().union(*(o for g2, o in self.own.items() if g2 != gid)) if len(self.own) > 1 else set()
            theirs = {a for a in theirs if not any(gp._same_acr(a, m) for m in self.own[gid])}
            def foreign(t: str) -> bool:
                toks = {gp._acr(a) for a in gp._ACR_TOKEN.findall(t)} | ({gp._acr(t)} if gp._is_acr(t) else set())
                return bool(toks & theirs)
            self.alias[gid] = [t for t in self.alias[gid] if not foreign(t)]

    def _settle_names(self, gids: dict[str, list[str]], rep_of: dict[str, str], names: dict, nodes: dict) -> set[str]:
        """묶음 둘이 같은 보일 이름을 갖지 않게 한다(자가 점검) — 그래프 노드 이름은 다시 지을 때 앞뒤 단계 사업 이름으로
        바뀌거나 여러 장부 열쇠가 한 노드에 맞을 수 있다. 같은 이름(group_key)을 가진 묶음 가운데 제 장부 이름이 그 이름과
        같은 사업(same_program)인 묶음 하나만 노드 이름을 쓰고, 나머지는 제 장부 대표 이름으로 돌린다.
        돌려주는 것: 노드 이름을 쓰지 않게 된 묶음 열쇠들."""
        reverted: set[str] = set()
        for _round in range(3):
            seen: dict[str, list[str]] = defaultdict(list)
            for gid in gids:
                seen[program_names.group_key(self.name[gid])].append(gid)
            clash = [gs for gs in seen.values() if len(gs) > 1]
            if not clash:
                break
            for gs in clash:
                own = [g for g in gs if g not in reverted
                       and any(same_program(names[k][0] or "", self.name[g]) for k in gids[g])]
                keep = own[0] if len(own) == 1 else None
                for g in gs:
                    if g != keep and g not in reverted:
                        reverted.add(g)
                        self.name[g] = rep_of[g]
        return reverted

    def name_clashes(self) -> list[list[str]]:
        """보일 이름이 같은 묶음들 — 비어 있어야 한다(화면 생성 때 _settle_names 가 풀고, 점검·테스트가 이것으로 확인한다)."""
        seen: dict[str, list[str]] = defaultdict(list)
        for gid, nm in self.name.items():
            seen[program_names.group_key(nm)].append(gid)
        return [sorted(gs) for gs in seen.values() if len(gs) > 1]

    def of(self, key: str) -> str:
        return self.by_key.get(key or "", "")

    def label(self, key: str) -> str:
        gid = self.of(key)
        return self.name.get(gid, "") if gid else ""

    def keys_of(self, gid: str) -> list[str]:
        return sorted(k for k, g in self.by_key.items() if (g or NONE) == gid)

    def candidates(self, value: str) -> list[tuple[int, int, str]]:
        """사업 조건 값에 맞는 묶음 — (순위, -원본 수, 묶음 열쇠) 순으로. 순위가 작을수록 강한 근거다.

        0 제 열쇠·제 이름: 값이 장부 열쇠 꼬리의 약칭(program:abcd ↔ ABCD)과 같거나, 묶음 제 이름과 같다(group_key)
        1 제 이름 속 약칭: 값이 묶음 제 이름에 든 약칭과 같다(「…(구 ABCD)」)
        2 그래프 별칭: 값이 거른 다른 이름·약칭 하나와 같다 — 별칭만 맞는 묶음은 제 이름·열쇠로 맞는 묶음을 이기지 못한다
        3 같은 낱말 · 4 이름 일부"""
        from zzaimy.graph import programs as gp
        flat = program_names.group_key(value)
        vac = gp._acr(flat)
        low = value.lower()
        out = []
        for g, ts in self.terms.items():
            als = self.alias.get(g, [])
            if vac and any(gp._same_acr(vac, a) for a in self.key_acr.get(g, ())) or any(
                    t == value or program_names.group_key(t) == flat for t in ts):
                rank = 0
            elif vac and any(gp._same_acr(vac, a) for a in self.own.get(g, ())):
                rank = 1
            elif any(t == value or program_names.group_key(t) == flat for t in als):
                rank = 2
            else:
                rank = 5
                for t in ts + als:
                    words = {program_names.group_key(w) for w in re.split(r"[\s()\[\]·,/_\-]+", t) if w}
                    if flat and flat in words:
                        rank = min(rank, 3)
                    elif low in t.lower():
                        rank = min(rank, 4)
            if rank < 5:
                out.append((rank, -self.count.get(g, 0), g))
        return sorted(out)

    def resolve(self, value: str) -> str:
        """사업 조건 값 → 묶음 열쇠. 장부 열쇠 · 사업 미분류 · 그 밖에는 candidates 순위로.

        제 열쇠·제 이름·별칭이 그대로 맞은 후보(순위 0~2)가 있으면 가장 높은 순위에서 원본이 가장 많은 묶음으로 정한다.
        낱말·일부만 맞은 후보가 하나면 그것, 여럿이면 정하지 않고 choices 에 후보를 남긴다(화면이 「이 사업을 찾으셨나요?」로 보인다)."""
        self.choices = []
        value = (value or "").strip()
        if not value or value == NONE:
            return value
        if value == NO_PROGRAM:
            return NONE
        if value in self.by_key:
            return self.by_key[value] or NONE
        cands = self.candidates(value)
        if not cands:
            return value                      # 모르는 값 — 0건(조건 칩으로 지울 수 있다)
        best = cands[0][0]
        top = [c for c in cands if c[0] == best]
        if best <= 2 or len(top) == 1:
            return top[0][2]
        self.choices = [{"value": g, "name": self.name.get(g, g), "n": -neg} for _r, neg, g in cands[:6]]
        return value


def program_index(db) -> dict[str, tuple[str, int]]:
    """장부 열쇠 → (장부 이름, 원본 수) — 검색어·형식 조건과 무관한 전체 기준이라 묶음이 조건에 따라 바뀌지 않는다."""
    archive.ensure(db)
    where, args = _base_where(parse_filters())
    with db._conn() as conn:
        rows = conn.execute(f"SELECT a.program, MAX(a.program_name), COUNT(*) FROM archive_files a{where} GROUP BY a.program",
                            args).fetchall()
    return {str(r[0] or ""): (str(r[1] or ""), int(r[2])) for r in rows}


_PAREN = re.compile(r"\([^()]*\)")


def _name_sig(name: str) -> tuple[str, set[str]]:
    """같은 사업 이름 판정 재료 — (괄호를 뺀 이름의 group_key, 이름 속 영문 약칭들). 약칭은 판 번호의 점을 지운 꼴(LINC 3.0 = LINC30)."""
    from zzaimy.graph import programs as gp
    core = program_names.group_key(_PAREN.sub("", name or "")) or program_names.group_key(name or "")
    return core, {gp._acr(a).replace(".", "") for a in gp._ACR_TOKEN.findall(name or "")}


def same_program(a: str, b: str) -> bool:
    """두 이름이 같은 사업을 부르는가 — 괄호 밖 이름(group_key)이 같고 양쪽에 약칭이 있으면 하나라도 같다
    (「…대학(ABCD+) 육성사업」 = 「…대학(ABCD+)육성사업」, 「…대학(ABCD) 육성사업」 ≠ 「…대학(ABCD+) 육성사업」).
    또는 이름 하나가 약칭뿐이면 다른 이름 속 약칭과 같다(「ABCD사업」 = 「새 이름(구 ABCD)」)."""
    if not a or not b:
        return False
    if program_names.group_key(a) == program_names.group_key(b):
        return True
    (ca, xa), (cb, xb) = _name_sig(a), _name_sig(b)
    if ca and ca == cb:
        return not (xa and xb) or bool(xa & xb)
    bare = lambda core, acr: bool(acr) and core in {x.upper() for x in acr}
    return bool(xa & xb) and (bare(ca, xa) or bare(cb, xb))


def _key_tail(key: str) -> str:
    m = re.fullmatch(r"program:([a-z][a-z0-9+.]{1,15})", key or "")
    return re.sub(r"[^0-9A-Z]", "", m.group(1).upper()) if m else ""


def graph_match(db, names: dict[str, tuple[str, int]]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """장부 열쇠마다 그래프 사업 노드를 맞춘다 — (열쇠 → 노드의 믿을 만한 다른 이름·약칭, 열쇠 → 노드 이름).

    노드 id 는 그래프를 다시 지을 때 바뀌고, 다시 지은 노드의 대표 이름이 앞뒤 단계 사업 이름으로 바뀔 수도 있어 id 만으로 믿지 않는다.
    1) 노드 id 가 장부 열쇠와 같고 노드 이름이 장부 이름과 같은 사업(same_program)이면 그것
    2) 노드 이름이 장부 이름과 같은 사업인 노드가 하나면 그것(여럿이면 id 꼬리 약칭이 열쇠 꼬리와 같은 것)
    3) 장부 이름이 노드의 거른 별칭 하나와 같은 열쇠(group_key)인 노드가 하나뿐이면 그것
    - 노드의 이름·약칭 목록에는 다른 사업의 이름·약칭이나 한 글자 오기가 섞여 들어올 수 있다 — 그래프 화면과 같은 거르기
      (graph.programs.alias_filters·alias_list: 남의 약칭이 든 이름, 정체 약칭의 한 글자 오기를 뺌, 장부 표기는 둠)를 거친 것만
      별칭으로 쓰고 맞추기에도 쓴다(거르지 않은 원시 이름 목록으로 맞추면 남의 사업 노드로 간다)"""
    import json

    from zzaimy.graph import programs as gp
    try:
        with db._conn() as conn:
            rows = conn.execute("SELECT id, label, props FROM kg_nodes WHERE type = 'program'").fetchall()
    except Exception:
        return {}, {}
    progs = []
    for nid, label, props in rows:
        p = props if isinstance(props, dict) else json.loads(props or "{}")
        acr = p.get("acronyms") or []
        p = {**p, "acronyms": list(acr.keys()) if isinstance(acr, dict) else list(acr)}
        progs.append({"id": str(nid), "label": str(label or ""), "props": p})
    ledger = _ledger()
    nodes: dict[str, tuple[str, list[str]]] = {}
    by_term: dict[str, set[str]] = defaultdict(set)
    for prog in progs:
        others = [o for o in progs if o is not prog]
        canon, foreign, allowed = gp.alias_filters(prog, others, ledger)
        kept, _more = gp.alias_list([str(x) for x in prog["props"].get("names") or []], prog["props"]["acronyms"], limit=10_000,
                                    foreign=foreign, canonical=canon, allowed=allowed)
        nodes[prog["id"]] = (prog["label"], [prog["label"]] + kept)
        for t in [prog["label"]] + kept:
            if t:
                by_term[program_names.group_key(t)].add(prog["id"])

    def pick(cands: set[str], key: str) -> str | None:
        if len(cands) == 1:
            return next(iter(cands))
        tail = _key_tail(key)
        hit = [n for n in cands if tail and _key_tail(n) == tail]
        return hit[0] if len(hit) == 1 else None

    surf, label = {}, {}
    for key, (name, _n) in names.items():
        if not key:
            continue
        nid = None
        if key in nodes and (not name or same_program(name, nodes[key][0])):
            nid = key
        if nid is None and name:
            nid = pick({i for i, (lb, _a) in nodes.items() if same_program(name, lb)}, key)
        if nid is None and name:
            nid = pick(by_term.get(program_names.group_key(name), set()), key)
        if nid is not None:
            label[key], surf[key] = nodes[nid][0], nodes[nid][1]
    return surf, label


def load_programs(db) -> tuple[dict[str, tuple[str, int]], "Programs"]:
    """장부 열쇠별 원본 수와 사업 묶음 — 화면과 점검이 같은 길로 만든다."""
    names = program_index(db)
    surf, labels_g = graph_match(db, names)
    acr = _key_acronyms(names)
    return names, Programs(names, {k: surf.get(k, []) + acr.get(k, []) for k in names if k}, labels_g)


def _key_acronyms(names: dict) -> dict[str, list[str]]:
    """열쇠가 영문 약칭에서 온 경우(「program:abcd30」) 그 약칭 — 그래프 노드가 없을 때도 오타 판정의 근거가 된다."""
    out = {}
    for k in names:
        m = re.fullmatch(r"program:([a-z][a-z0-9+]{2,15})", k or "")
        if m:
            out[k] = [m.group(1).upper()]
    return out


_FLAT = re.compile(r"[\s._\-·()]+")


def named_in_file(filename: str, program_name: str, surfaces: list[str]) -> bool:
    """파일 이름 자체가 그 사업을 부르는가 — 사업 이름(정리한 열쇠)이나 그래프의 이름·약칭이 들어 있으면 그렇다.
    아니면 그 분류는 폴더 위치에서 온 것이다(scripts/165: 경로의 사업명, 상위 폴더 물려받기)."""
    flat = _FLAT.sub("", filename or "").upper()
    terms = [program_names.group_key(program_name)] + [_FLAT.sub("", s).upper() for s in surfaces]
    return any(len(t) >= 3 and t in flat for t in terms if t)


# ── 조건 ────────────────────────────────────────────────────────────────────
PAGE_SIZE = 50
SORTS = {"name": ("경로·이름순", "a.rel"),
         "size": ("큰 파일 먼저", "a.size DESC, a.rel"),
         "year": ("최근 연도 먼저", "a.year IS NULL, a.year DESC, a.round IS NULL, a.round DESC, a.rel")}
FILTER_KEYS = ("q", "program", "year", "round", "kind", "ext", "state", "scope")
# 보는 범위 — 기본은 학교 원본만(archive_state.original_sql), 「all」 은 장부 전체(플랫폼 사본·변환 부산물·정리함 포함).
# 옛 데이터 현황 「원본 장부」 탭(?tab=archive&all=1)이 하던 장부 전체 보기를 여기 한 곳으로 옮겼다(10-27)
SCOPES = {"": "학교 원본", "all": "장부 전체(사본·부산물·정리함 포함)"}

# 처리 기록(analysis) 값 가운데 뜻이 바로 안 읽히는 것 — 줄의 장부 값에 짧은 풀이(마우스를 올리면)
ANALYSIS_NOTES = {
    "가벼운 처리": "DGX 에서 판독·조각까지만 하고 검토 의견 생성은 건너뛴 처리 — 원본은 DGX 에 두고 글·조각만 들임, OCR 품질 미검사",
    "부산물": "변환기 출력·웹 페이지 저장 자료 폴더의 파일 — 처리 대상 아님",
}


def _int(v) -> int | str:
    v = str(v or "").strip()
    return int(v) if v.isdigit() and len(v) <= 4 else ""


VIEWS = ("table", "card")                 # 원본 목록 보기 — 「목록」(표, 기본)·「카드」. 조건이 아니라 보는 방식이라 칩·조건 지우기에 넣지 않는다


def parse_filters(q="", program="", year="", round="", kind="", ext="", state="", sort="", page="1", view="", scope="") -> dict:
    y = NONE if year == NONE else _int(year)
    return {"q": (q or "").strip()[:200], "program": (program or "").strip()[:200], "year": y, "round": _int(round),
            "kind": kind if kind == NONE or re.fullmatch(r"[a-z_]{1,24}", kind or "") else "",
            "ext": ext_of(ext), "state": state if state in archive_state.STATES else "",
            "sort": sort if sort in SORTS else "name", "page": max(1, _int(page) or 1),
            "view": view if view in VIEWS else VIEWS[0], "scope": scope if scope in SCOPES else ""}


def _base_where(f: dict) -> tuple[str, list]:
    """묶음 수에도 목록에도 거는 조건 — 학교 원본만(archive_state.original_sql, 범위 「장부 전체」면 빼지 않음)·검색어·형식."""
    q, args = " WHERE a.removed_at = ''", []
    if f.get("scope") != "all":
        orig, orig_args = archive_state.original_sql("a.")
        q += " AND " + orig
        args += orig_args
    if f["ext"] == vocab.OTHER_FORMAT:         # 원본 문서 형식 갈래(app/vocab.FORMAT_GROUPS) 밖의 파일 전부
        allowed = vocab.original_formats()
        q += f" AND LOWER(REPLACE(a.ext, '.', '')) NOT IN ({', '.join('?' * len(allowed))})"
        args += list(allowed)
    elif f["ext"]:
        q += " AND LOWER(REPLACE(a.ext, '.', '')) = ?"
        args.append(f["ext"])
    for term in f["q"].split()[:6]:          # 여러 낱말은 모두 들어간 파일만(순서 무관)
        q += " AND LOWER(a.rel) LIKE ? ESCAPE '!'"
        args.append("%" + term.lower().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%")
    return q, args


def facet_rows(db, f: dict) -> list[dict]:
    """사업·이름·종류·연도·연차·처리 상태별 파일 수와 크기 — 한 번의 GROUP BY."""
    archive.ensure(db)
    where, args = _base_where(f)
    st, st_args = archive_state.state_sql("a.")
    sql = (f"SELECT a.program, a.program_name, a.kind, a.year, a.round, {st} AS st, a.status = 'folder', COUNT(*), SUM(a.size)"
           f" FROM archive_files a{where} GROUP BY 1, 2, 3, 4, 5, 6, 7")
    with db._conn() as conn:
        rows = conn.execute(sql, st_args + args).fetchall()
    keys = ("program", "program_name", "kind", "year", "round", "state", "folder", "n", "bytes")
    return [dict(zip(keys, r)) for r in rows]


def ext_options(db) -> list[dict]:
    """형식 선택지 — 원본 문서 형식 갈래(app/vocab.FORMAT_GROUPS: 문서·표·발표·웹 문서·메일·그림·도면)의 확장자만 많은 순으로,
    나머지(프로그램·시스템·웹·백업 파일 등)는 「그 밖의 형식」 하나로."""
    archive.ensure(db)
    with db._conn() as conn:
        orig, orig_args = archive_state.original_sql()
        rows = conn.execute(f"SELECT LOWER(ext), COUNT(*) FROM archive_files WHERE removed_at = '' AND {orig} GROUP BY LOWER(ext)",
                            orig_args).fetchall()
    tally: dict[str, int] = defaultdict(int)
    other = 0
    for e, n in rows:
        e = ext_of(str(e or ""))
        if e and vocab.format_group(e):
            tally[e] += int(n)
        else:
            other += int(n)
    out = [{"value": e, "label": ext_label(e), "n": n} for e, n in sorted(tally.items(), key=lambda t: -t[1])]
    if other:
        out.append({"value": vocab.OTHER_FORMAT, "label": vocab.OTHER_FORMAT_LABEL, "n": other})
    return out


def _match(r: dict, f: dict, prog_of: dict[str, str], skip: str = "") -> bool:
    if skip != "program" and f["program"]:
        if (prog_of.of(r["program"]) or NONE) != f["program"]:
            return False
    if skip != "year":
        if f["year"] == NONE and r["year"]:
            return False
        if f["year"] not in ("", NONE) and r["year"] != f["year"]:
            return False
    if skip != "year" and f["round"] and r["round"] != f["round"]:
        return False
    if skip != "kind" and f["kind"]:
        if (r["kind"] or NONE) != f["kind"]:
            return False
    if skip != "state" and f["state"] and r["state"] != f["state"]:
        return False
    return True


def program_years(rows: list[dict], prog_of: dict[str, str]) -> list[dict]:
    """연도·연차는 사업 연도다 — 사업으로 분류된 파일의 그럴듯한 연도(graph.programs.plausible_year)만 남기고 나머지는 연도 미상.
    사업이 없는 파일의 연도는 파일 이름 속 숫자(서식 번호 「…_2000.rdf」 등)라서 사업 연도로 쓰지 않는다."""
    from zzaimy.graph.programs import plausible_year
    for r in rows:
        if not prog_of.of(r["program"]) or not plausible_year(r["year"]):
            r["year"] = None
        if not prog_of.of(r["program"]):
            r["round"] = None
    return rows


def _dated_keys(prog_of: dict[str, str]) -> list[str]:
    return sorted(k for k, g in prog_of.by_key.items() if k and g)


def browse(rows: list[dict], f: dict, prog_of: dict[str, str], labels: dict[str, str]) -> dict:
    """단계마다 건수 — 사업(다른 조건 적용), 연도·연차(사업 등 적용), 문서 종류, 처리 상태. 고른 조건은 서로 겹친다."""
    progs: dict[str, dict] = {}
    years: dict[tuple, int] = defaultdict(int)
    kinds: dict[str, int] = defaultdict(int)
    states: dict[str, int] = defaultdict(int)
    total = registered = folder = 0
    size = 0
    for r in rows:
        if _match(r, f, prog_of, "program"):
            name = prog_of.of(r["program"]) or NONE
            p = progs.setdefault(name, {"value": name, "name": prog_of.name.get(name, NO_PROGRAM), "n": 0, "registered": 0,
                                        "kinds": defaultdict(int), "keys": set()})
            p["n"] += r["n"]
            p["registered"] += r["n"] if r["state"] == "registered" else 0
            p["kinds"][r["kind"] or ""] += r["n"]
            if r["program"]:
                p["keys"].add(r["program"])
        if _match(r, f, prog_of, "year"):
            years[(r["year"] or None, r["round"] or None)] += r["n"]
        if _match(r, f, prog_of, "kind"):
            kinds[r["kind"] or ""] += r["n"]
        if _match(r, f, prog_of, "state"):
            states[r["state"]] += r["n"]
        if _match(r, f, prog_of):
            total += r["n"]
            size += int(r["bytes"] or 0)
            registered += r["n"] if r["state"] == "registered" else 0
            folder += r["n"] if r["folder"] else 0
    order = sorted((p for p in progs.values() if p["value"] != NONE), key=lambda p: -p["n"])
    for p in order + [progs[NONE]] if NONE in progs else order:
        named = sorted(((kind_label(k, labels), n) for k, n in p["kinds"].items() if k), key=lambda t: -t[1])
        p["kinds"] = named[:3]
        p["keys"] = sorted(p["keys"])
    # 연도(연차) — 연도가 있으면 연도별(연차는 함께 적는다), 연도 없이 연차만 있으면 연차별, 둘 다 없으면 연도 미상
    by_year: dict = defaultdict(lambda: {"n": 0, "rounds": set()})
    for (y, rd), n in years.items():
        key = ("y", y) if y else (("r", rd) if rd else ("u", 0))
        by_year[key]["n"] += n
        if y and rd:
            by_year[key]["rounds"].add(rd)
    year_items = []
    for (kind, v), d in sorted(by_year.items(), key=lambda t: ({"y": 0, "r": 1, "u": 2}[t[0][0]], -(t[0][1] or 0))):
        if kind == "y":
            rounds = sorted(d["rounds"])
            label = f"{v}년" + (f" · {'·'.join(map(str, rounds))}차년도" if rounds and len(rounds) <= 2 else "")
            year_items.append({"label": label, "n": d["n"], "year": v, "round": "", "future": v > date.today().year})
        elif kind == "r":
            year_items.append({"label": f"{v}차년도(연도 미상)", "n": d["n"], "year": "", "round": v, "future": False})
        else:
            year_items.append({"label": "연도 미상", "n": d["n"], "year": NONE, "round": "", "future": False})
    kind_items = sorted(({"value": k or NONE, "label": kind_label(k, labels), "n": n} for k, n in kinds.items()),
                        key=lambda t: (t["value"] == NONE, -t["n"]))           # 분류된 종류 먼저, 분류 전은 끝
    state_items = [{"value": k, "label": v, "n": states.get(k, 0)} for k, v in archive_state.STATES.items()]
    return {"programs": order, "unclassified": progs.get(NONE), "years": year_items, "kinds": kind_items,
            "states": state_items, "total": total, "registered": registered, "folder": folder, "gb": round(size / 1e9, 1)}


def list_page(db, f: dict, keys: list[str] | None, total: int,
              prog_of: dict[str, str], labels: dict[str, str], mask) -> dict:
    """조건에 맞는 한 쪽(PAGE_SIZE개). keys 는 고른 사업 묶음의 장부 열쇠(None 이면 사업 조건 없음)."""
    from zzaimy.app.storage import title_of
    from zzaimy.graph.programs import plausible_year

    where, args = _base_where(f)
    if keys is not None:
        where += (" AND a.program IN (" + ",".join("?" * len(keys)) + ")") if keys else " AND 1 = 0"
        args += keys
    import datetime as _dt
    dated = _dated_keys(prog_of)                 # 사업 연도가 있을 수 있는 열쇠(program_years 와 같은 기준)
    in_dated = ("a.program IN (" + ",".join("?" * len(dated)) + ")") if dated else "1 = 0"
    lo, hi = 2000, _dt.date.today().year + 2     # graph.programs.plausible_year 와 같은 범위
    if f["year"] == NONE:
        where += f" AND (a.year IS NULL OR a.year < ? OR a.year > ? OR NOT {in_dated})"
        args += [lo, hi, *dated]
    elif f["year"]:
        where += f" AND a.year = ? AND {in_dated}"
        args += [f["year"], *dated]
    if f["round"]:
        where += f" AND a.round = ? AND {in_dated}"
        args += [f["round"], *dated]
    if f["kind"]:
        where += " AND a.kind = ?"
        args.append("" if f["kind"] == NONE else f["kind"])
    st, st_args = archive_state.state_sql("a.")
    if f["state"]:
        where += f" AND {st} = ?"
        args += st_args + [f["state"]]
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(f["page"], pages)
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT a.rel, a.size, a.ext, a.program, a.program_name, a.kind, a.year, a.round, a.doc_id, a.status, a.dup_of,"
            f" a.analysis, d.filename, {st}, a.seen_at,"
            " EXISTS (SELECT 1 FROM archive_cleanup ac WHERE ac.rel = a.rel)"
            " FROM archive_files a LEFT JOIN documents d ON d.id = a.doc_id"
            + where + " ORDER BY " + SORTS[f["sort"]][1] + " LIMIT ? OFFSET ?",
            st_args + args + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
    cols = ("rel", "size", "ext", "program", "program_name", "kind", "year", "round", "doc_id", "status", "dup_of", "analysis",
            "doc_name", "state", "seen_at", "cleaned")
    items = [dict(zip(cols, r)) for r in rows]
    surf = prog_of.surfaces
    for it in items:
        name = it["rel"].rsplit("/", 1)[-1]
        prog = prog_of.label(it["program"])
        e = ext_of(it["ext"] or "", it["rel"])
        title = title_of(it["doc_name"] or name)
        it.update(
            title=mask(title) if mask else title,
            path=mask(it["rel"]) if mask else it["rel"],
            ext_label=e.upper() or "파일", ext=e, size_label=_size(it["size"]),
            kind_label=kind_label(it["kind"] or "", labels),
            program_label=prog or NO_PROGRAM,
            # 분류가 폴더 위치에서 왔는가 — 상위 폴더 물려받기(status folder)이거나 파일 이름이 그 사업을 부르지 않을 때
            by_folder=bool(prog) and (it["status"] == "folder" or not named_in_file(name, prog, surf.get(it["program"], []))),
            state_label=archive_state.STATES.get(it["state"], "").split("(")[0],
            why=(archive_state.exclusion_reason(it) if it["state"] == "excluded"
                 else (it["analysis"] or "")[len(archive_state.FAILED_MARK) + 2:] if it["state"] == "failed" else ""),
            # 사업 연도 — 파일 이름·경로에 그 연도가 없으면 연차를 사업 기간으로 환산한 값이다(scripts/165·170)
            year_from_round=bool(it["year"] and it["round"] and str(it["year"]) not in it["rel"]),
            show_year=bool(prog) and plausible_year(it["year"]), show_round=bool(prog) and bool(it["round"]),
            can_open=not it["rel"].startswith(archive.UPLOAD_PREFIX))
        it["short_path"] = short_path(it["path"])
        # 장부 값 그대로 — 펼치면 경로와 함께 보인다(옛 데이터 현황 「원본 장부」 탭의 원값 열을 여기로 옮김, 10-27)
        an = str(it["analysis"] or "")
        it.update(original=archive_state.is_original(it["rel"]), cleaned=bool(it["cleaned"]),
                  status_label=ARCHIVE_STATUS.get(it["status"] or "", it["status"] or ""),
                  analysis_note=next((v for k, v in ANALYSIS_NOTES.items() if an.startswith(k)), ""),
                  seen_label=(it["seen_at"] or "")[:16].replace("T", " "))
    first = (page - 1) * PAGE_SIZE + 1 if items else 0
    return {"found": items, "hits": total, "page": page, "pages": pages, "first": first, "last": first + len(items) - 1 if items else 0}


def page_url(f: dict, **change) -> str:
    """지금 조건에서 일부만 바꾼 주소 — 쪽 넘김·조건 지우기·공유용."""
    params = {k: f[k] for k in FILTER_KEYS}
    params.update(sort="" if f["sort"] == "name" else f["sort"], page=f["page"],
                  view="" if f.get("view", VIEWS[0]) == VIEWS[0] else f["view"])
    params.update(change)
    if "page" not in change:
        params["page"] = ""                  # 조건을 바꾸면 첫 쪽부터
    if params["page"] in (1, "1"):
        params["page"] = ""
    params = {k: v for k, v in params.items() if v not in ("", None)}
    return PAGE + ("?" + urlencode(params) if params else "") + "#archive-list"


def chips(f: dict, labels: dict[str, str], ext_labels: dict[str, str], progs: "Programs | None" = None) -> list[dict]:
    """걸린 조건 — 하나씩 지울 수 있게 그 조건만 뺀 주소를 붙인다."""
    names = progs.name if progs else {}
    label = {"scope": lambda v: SCOPES.get(v, v), "q": lambda v: f"‘{v}’", "program": lambda v: NO_PROGRAM if v == NONE else names.get(v, v),
             "year": lambda v: "연도 미상" if v == NONE else f"{v}년", "round": lambda v: f"{v}차년도",
             "kind": lambda v: UNSORTED if v == NONE else kind_label(v, labels), "ext": lambda v: ext_labels.get(v, v.upper()),
             "state": lambda v: archive_state.STATES.get(v, v)}
    return [{"key": k, "label": label[k](f[k]), "url": page_url(f, **{k: ""})} for k in FILTER_KEYS if f[k]]


def rail_tree(programs: list[dict]) -> list[dict]:
    """왼쪽 사업 목록의 대·중·소 분류 — 라이브러리 「사업별로 보기」·그래프 탐색과 같은 나무(app.program_tree, 외부 확인 장부 기준).
    programs 는 browse 의 사업 묶음(사업 미분류 제외). 항목의 value 는 묶음 열쇠라 고르기·거르기는 그대로다."""
    from zzaimy.app import program_tree
    items = [{"label": p["name"], "n": p["n"], "value": p["value"], "registered": p["registered"]} for p in programs]
    return program_tree.build(items, _ledger())


def picked_program(f: dict, view: dict, tree: list[dict]) -> dict | None:
    """고른 사업 — 목록 머리에 이름·건수·동작(지난 사업에서 찾기·그래프)을 보인다. 지금 조건에 원본이 없으면 None."""
    if f["program"] == NONE:
        u = view.get("unclassified")
        return {"value": NONE, "label": NO_PROGRAM, "raw_label": NO_PROGRAM, "n": u["n"], "registered": u["registered"]} if u else None
    return next((p for t in tree for g in t["groups"] for p in g["progs"] if p["value"] == f["program"]), None)


def resolve_program(value: str, prog_of: "Programs") -> str:
    return prog_of.resolve(value)


def _dev(request: Request) -> None:
    if getattr(request.state, "role", "") != "dev":
        raise HTTPException(403, "원본 관리는 관리자만 합니다")


@router.get("/dev/archive/cleanup")
def archive_cleanup_summary(request: Request):
    """정리 — 처리하지 않는 원본의 갈래별 건수·용량과 정리함에 든 수(관리자)."""
    from zzaimy.app import archive_cleanup
    _dev(request)
    return JSONResponse(archive_cleanup.summary(request.app.state.db))


@router.get("/dev/archive/cleanup/sample")
def archive_cleanup_sample(request: Request, key: str):
    from zzaimy.app import archive_cleanup
    _dev(request)
    return JSONResponse({"key": key, "rels": archive_cleanup.sample(request.app.state.db, key)})


def _keys(body) -> list[str]:
    """요청의 갈래 — {"keys": [...]}(선택한 줄 여럿) 또는 {"key": "..."}."""
    if not isinstance(body, dict):
        return []
    keys = body.get("keys") if isinstance(body.get("keys"), list) else [body.get("key")]
    return [str(k) for k in keys if k][:20]


@router.post("/dev/archive/cleanup/move")
async def archive_cleanup_move(request: Request):
    """선택한 갈래를 정리함으로."""
    from zzaimy.app import archive_cleanup
    _dev(request)
    by = getattr(request.state, "user", "") or ""
    n = sum(archive_cleanup.move(request.app.state.db, k, by=by) for k in _keys(await request.json()))
    return JSONResponse({"moved": n, **archive_cleanup.summary(request.app.state.db, force=True)})


@router.post("/dev/archive/cleanup/keep")
async def archive_cleanup_keep(request: Request):
    """선택한 갈래를 남기기 — 정리 목록에서만 빠지고 보관소에는 그대로."""
    from zzaimy.app import archive_cleanup
    _dev(request)
    by = getattr(request.state, "user", "") or ""
    n = sum(archive_cleanup.keep(request.app.state.db, k, by=by) for k in _keys(await request.json()))
    return JSONResponse({"kept": n, **archive_cleanup.summary(request.app.state.db, force=True)})


@router.post("/dev/archive/cleanup/restore")
async def archive_cleanup_restore(request: Request):
    from zzaimy.app import archive_cleanup
    _dev(request)
    body = await request.json()
    key = str((body.get("key") if isinstance(body, dict) else "") or "")
    n = archive_cleanup.restore(request.app.state.db, key)
    return JSONResponse({"restored": n, **archive_cleanup.summary(request.app.state.db, force=True)})


@router.get("/dev/archive/cleanup/failed")
def archive_cleanup_failed(request: Request, q: str = "", page: int = 1):
    """정리함 「처리 실패」 원본 — 사유와 함께 찾기·쪽 나눔(관리자). 다시 시도 장부에 적힌 수도 준다."""
    from zzaimy.app import read_failure
    _dev(request)
    return JSONResponse(read_failure.archive_failed(request.app.state.db, q=q[:100], page=page))


@router.post("/dev/archive/cleanup/retry")
async def archive_cleanup_retry(request: Request):
    """고른 「처리 실패」 원본을 정리함에서 빼고 다음 동기화에 다시 읽게 — {"rels": [...], "mode": "retry"|"ocr"}.
    글자 읽기 맡기기(ocr)는 판독 모델이 닿을 때 읽는다."""
    from zzaimy.app import archive_cleanup, read_failure
    _dev(request)
    body = await request.json()
    rels = body.get("rels") if isinstance(body, dict) and isinstance(body.get("rels"), list) else []
    mode = str(body.get("mode") or "retry") if isinstance(body, dict) else "retry"
    db = request.app.state.db
    n = read_failure.archive_retry(db, [str(r) for r in rels][:500], mode=mode, by=getattr(request.state, "user", "") or "")
    return JSONResponse({"retried": n, **archive_cleanup.summary(db, force=True)})


@router.get(PAGE, response_class=HTMLResponse)
def archive_page(request: Request, program: str = "", kind: str = "", q: str = "", year: str = "", round: str = "",
                 ext: str = "", state: str = "", sort: str = "name", page: str = "1", view: str = "", scope: str = "",
                 partial: str = ""):
    _dev(request)
    st = request.app.state
    db = st.db
    f = parse_filters(q, program, year, round, kind, ext, state, sort, page, view, scope)
    labels = kind_labels()
    rows = facet_rows(db, f)
    names, prog_of = load_programs(db)
    program_years(rows, prog_of)
    f["program"] = resolve_program(f["program"], prog_of)
    view = browse(rows, f, prog_of, labels)
    keys = None
    if f["program"]:
        keys = prog_of.keys_of(f["program"])
    exts = ext_options(db)
    ext_names = {e["value"]: e["label"] for e in exts}
    data = {"f": f, "view": view, "sorts": SORTS, "scopes": SCOPES, "states": archive_state.STATES, "exts": exts, "page_size": PAGE_SIZE,
            "chips": chips(f, labels, ext_names, prog_of),
            "program_label": NO_PROGRAM if f["program"] == NONE else prog_of.name.get(f["program"], f["program"]),
            "program_choices": prog_of.choices, "active": any(f[k] for k in FILTER_KEYS), "nokey": NONE,
            "no_program": NO_PROGRAM, "this_year": date.today().year, "prev_url": "", "next_url": "",
            "url": lambda **kw: page_url(f, **kw)}
    data["rail_tree"] = rail_tree(view["programs"])
    data["pick"] = picked_program(f, view, data["rail_tree"])
    data.update(list_page(db, f, keys, view["total"], prog_of, labels, masker(db)))
    if data["page"] > 1:
        data["prev_url"] = page_url(f, page=data["page"] - 1)
    if data["page"] < data["pages"]:
        data["next_url"] = page_url(f, page=data["page"] + 1)
    # 원본 수의 이름·정의는 지표 모듈에서(archive_originals) — 처리 상태는 왼쪽 「처리 상태」 한 곳에만 보인다
    data["head_metric"] = "archive_originals"
    if partial:                              # 조건을 바꿀 때마다 왼쪽 분류와 원본 목록만 다시 그린다
        return st.templates.TemplateResponse(request, "_archive_results.html", data)
    overall = view if not data["active"] else browse(rows if not (f["q"] or f["ext"] or f["scope"]) else program_years(facet_rows(
        db, parse_filters()), prog_of), parse_filters(), prog_of, labels)
    data["overall"] = overall
    return st.templates.TemplateResponse(request, "archive.html", st.page_ctx(request, data))


@router.get(PAGE + "/doc/{did}", response_class=HTMLResponse)
def archive_doc_result(request: Request, did: int):
    """반입 결과 보기(관리자) — 문서 하나의 개요·마스킹 기록·추출·검색 자료(색인 유무)·그래프 연관(app/data_explorer).
    원본 관리 줄의 「반입 결과」 와 문서 화면 「관리」 에서 연다. 옛 데이터 현황 「문서」 탭의 자리(10-27)."""
    from zzaimy.app import data_explorer as dx

    _dev(request)
    st = request.app.state
    db = st.db
    doc = db.get_document(did)
    if not doc:
        raise HTTPException(404, "문서를 찾지 못했습니다")
    index = dx.embedding_index()
    scope = {"dept": getattr(request.state, "dept", "") or None, "user": request.state.user, "role": request.state.role}
    view = dx.doc_result(db, doc, index=index, related_fn=lambda d: dx.related_from_kg(db, d, scope))
    return st.templates.TemplateResponse(request, "dev_doc_result.html", st.page_ctx(request, {
        "s": view, "index": index, "extract_page": dx.EXTRACT_PAGE}))


@router.get(PAGE + "/original")
def archive_original(request: Request, rel: str):
    """원본 파일 한 건(관리자) — 교직원은 문서의 「원본」(/doc/{id}/original)으로 연다."""
    from pathlib import PurePosixPath

    from zzaimy.app import archive_original as original

    _dev(request)
    db = request.app.state.db
    archive.ensure(db)
    with db._conn() as conn:
        row = conn.execute(
            "SELECT rel, size, mtime, doc_id FROM archive_files WHERE rel = ? AND removed_at = ''",
            (rel,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "원본 목록에서 파일을 찾을 수 없습니다.")
    record = dict(zip(("rel", "size", "mtime", "doc_id"), row))
    try:
        file = original.fetch(record)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    filename = PurePosixPath(rel).name
    inline_types = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
    mime = inline_types.get(PurePosixPath(rel).suffix.lower())
    return FileResponse(file, filename=filename, media_type=mime or "application/octet-stream",
                        content_disposition_type="inline" if mime else "attachment",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "sandbox"},
                        background=BackgroundTask(file.unlink, missing_ok=True))
