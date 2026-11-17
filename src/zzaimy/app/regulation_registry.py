"""규정 장부 — 같은 규정의 판을 묶고 현행·구판을 가린다(11-16 사용자: 규정 신규·구판 관리가 돼야 규정 설명이 정확하다).

규정 문서(doc_type=regulation)마다 본문 첫머리의 「학과장회 통과일자 : 2016년 3월 17일」, 조문의 「<개정 2012.2.23>」,
부칙의 「(2020. 3. 1.)」 같은 날짜를 읽어 가장 늦은 날짜를 그 판의 최종 개정일로 둔다. 규정 이름은 파일 이름·본문 첫 줄에서
규정 번호(「5-24-1」)·학교 이름·뒤에 붙은 날짜를 뗀 것이고, 같은 이름(공백 무시)이면 같은 규정의 판이다.
판 가운데 최종 개정일이 가장 늦은 것(같으면 나중에 들어온 것)이 현행, 나머지는 구판이다. 판정은 기계적으로 읽은 값뿐이라
본문에 날짜가 없으면 들어온 차례로만 가린다(「날짜 없음」 표시).
"""
from __future__ import annotations

import re
import threading

_CODE = re.compile(r"^\s*(\d{1,2}(?:-\d{1,3}){1,3})\s+")
_PASSED = re.compile(r"통과\s*일자\s*(?:년월일)?\s*[:：]?\s*(\d{4})\s*[년.\s]\s*(\d{1,2})\s*[월.\s]\s*(\d{1,2})")
_REV = re.compile(r"(?:개정|제정|일부개정|전부개정|시행)\s*[:：]?\s*(\d{4})\s*[.\-년]\s*(\d{1,2})\s*[.\-월]\s*(\d{1,2})")
_ANNEX = re.compile(r"부\s*칙\s*[<(（]?\s*(\d{4})\s*[.\-년]\s*(\d{1,2})\s*[.\-월]\s*(\d{1,2})")
_TAIL = re.compile(r"\s*(?:·\s*\d{4}년.*|\(\d{4}[-.]\d{1,2}[-.]\d{1,2}\)|\(\d{8}\))\s*$")


def _date(y, m, d) -> str:
    """YYYY-MM-DD — 오늘보다 늦은 날짜는 버린다(본문에 먼 미래 날짜를 적어 현행 판을 차지하지 못하게)."""
    import datetime as _dt
    try:
        v = _dt.date(int(y), int(m), int(d))
    except ValueError:
        return ""
    return v.isoformat() if _dt.date(1980, 1, 1) <= v <= _dt.date.today() else ""


def name_and_code(filename: str, text: str = "") -> tuple[str, str]:
    """(규정 이름, 규정 번호) — 파일 이름에서, 번호가 없으면 본문 첫 줄에서."""
    base = re.sub(r"\.(hwpx?|pdf|docx?)$", "", filename or "", flags=re.I).strip()
    base = _TAIL.sub("", base)
    code = ""
    m = _CODE.match(base)
    if m:
        code, base = m.group(1), base[m.end():]
    if not code:
        m = _CODE.match((text or "")[:200])
        code = m.group(1) if m else ""
    name = re.sub(r"^\s*영남이공대학교\s*", "", base).strip()
    return name or base, code


def key_of(name: str) -> str:
    return re.sub(r"[\s·()\[\]「」]", "", name or "").lower()


def dates_of(text: str) -> dict:
    """{passed, revisions[], latest} — 본문에서 읽은 날짜(YYYY-MM-DD)."""
    t = text or ""
    head = t[:600]
    p = _PASSED.search(head)
    passed = _date(*p.groups()) if p else ""
    revs = sorted({d for d in (_date(*m.groups()) for m in _REV.finditer(t)) if d} | {d for d in (_date(*m.groups()) for m in _ANNEX.finditer(t)) if d})
    latest = max([x for x in [passed, *revs] if x], default="")
    return {"passed": passed, "revisions": revs, "latest": latest}


_RULE_NAME = re.compile(r"(규정|학칙|세칙|규칙|지침|요령|내규|정관|기준)$")


def is_rule(name: str, code: str, d: dict) -> bool:
    """학교 규정인가 — 규정 번호가 있거나, 학과장회 통과일자가 있거나, 이름이 「…규정·학칙·세칙·지침」 으로 끝난다.
    기준 문서(doc_type=regulation)에는 공고·기본계획처럼 판을 따질 수 없는 문서도 들어 있어 장부에서는 뺀다(따로 센다)."""
    return bool(code or d.get("passed") or _RULE_NAME.search(re.sub(r"[\s)）\]]+$", "", name or "")))


OVERLAP_MIN = 0.3          # 새 판·이전 판 본문의 8글자 조각 겹침 하한 — 운영 실측: 같은 규정 개정판 0.37~0.72, 다른 규정 짝 p95 0.17(최대 0.46). 이름·개정 이력 상속이 주 판정이고 겹침은 보조


def _grams(text: str) -> set[str]:
    t = re.sub(r"\s+", "", text or "")[:30000]
    return {t[i:i + 8] for i in range(max(0, len(t) - 7))}


def _overlap(a: set, b: set) -> float:
    return len(a & b) / max(1, min(len(a), len(b)))


def _follows(new: dict, old: dict) -> bool:
    """새 판이 이전 판을 이은 것인가 — 개정 이력(「<개정 …>」·부칙 날짜)은 쌓이므로 이전 판의 개정 날짜를 모두 품고, 더 늦으며, 본문이
    크게 겹친다. 학과장회 통과일자는 판마다 새로 적혀 비교에서 뺀다."""
    return ((new["latest"] or "") > (old["latest"] or "") and old["_dates"] <= new["_dates"]
            and _overlap(new["_grams"], old["_grams"]) >= OVERLAP_MIN)


def _pick_current(vs: list[dict]) -> int:
    """늦은 판부터 보며 현행을 고른다(11-17 사용자: 사람 승인 없이 자동으로). 학교 원본(DGX)·관리자가 올린 판은 출처로 믿고,
    그 밖에 올라온 판(기준 등록·프로젝트 묶음)은 바로 아래 이전 판을 이은 것(_follows)일 때만 현행이 된다.
    이전 판이 없는 하나뿐인 판은 그대로 현행. 잇지 못한 더 늦은 판은 「확인 필요」 로 남아 현행을 차지하지 못한다."""
    for i, v in enumerate(vs):
        older = vs[i + 1:]
        if v["trusted"] or not older:
            return i
        anchor = next((o for o in older if o["trusted"]), older[0])
        if _follows(v, anchor):
            return i
    return len(vs) - 1


def build(rows: list[dict], others: list | None = None) -> list[dict]:
    """rows: [{id, filename, created_at, text}] → 규정마다 {name, code, current(판), versions(판 목록, 새것 먼저)}.
    판: {id, filename, latest, passed, n_rev, status(현행|구판), created_at}."""
    groups: dict[str, dict] = {}
    for r in rows:
        name, code = name_and_code(r.get("filename") or "", r.get("text") or "")
        d = dates_of(r.get("text") or "")
        if not is_rule(name, code, d):
            if others is not None:
                others.append({"id": int(r["id"]), "filename": r.get("filename") or ""})
            continue
        k = key_of(name)
        g = groups.setdefault(k, {"name": name, "code": code, "versions": []})
        if code and not g["code"]:
            g["code"] = code
        g["versions"].append({"id": int(r["id"]), "filename": r.get("filename") or "", "latest": d["latest"], "passed": d["passed"],
                              "n_rev": len(d["revisions"]), "created_at": str(r.get("created_at") or "")[:10],
                              "trusted": bool(r.get("trusted")), "_dates": set(d["revisions"]),
                              "_grams": _grams(r.get("text") or "")})
    out = []
    for g in groups.values():
        vs = sorted(g["versions"], key=lambda v: (v["latest"] or "", v["id"]), reverse=True)
        cur = _pick_current(vs)
        for i, v in enumerate(vs):
            v["status"] = "현행" if i == cur else ("확인 필요" if i < cur else "구판")
        for v in vs:
            v.pop("_dates", None)
            v.pop("_grams", None)
        g["versions"] = [vs[cur]] + [v for i, v in enumerate(vs) if i != cur]
        g["current"] = vs[cur]
        out.append(g)
    out.sort(key=lambda g: (g["code"] == "", [int(x) for x in g["code"].split("-")] if g["code"] else [], g["name"]))
    return out


_CACHE: dict = {}                                   # (규정 수·최근 id, 열람 조건) → 장부. 조건마다 따로 둔다
_LOCK = threading.Lock()


def registry(db, where: str = "", args: list | None = None) -> list[dict]:
    """열람 조건(where — access_policy.doc_sql 의 ' AND …', 별칭 d.) 안의 규정 장부. 규정 수·최근 id 가 같으면 지난 계산을 쓴다."""
    args = list(args or [])
    with db._conn() as conn:
        sig = conn.execute(f"SELECT COUNT(*), MAX(d.id) FROM documents d WHERE d.doc_type = 'regulation'{where}", args).fetchone()
        key = (tuple(sig), where, tuple(args))
        with _LOCK:
            if key in _CACHE:
                return _CACHE[key]
        tsql, targs = trusted_sql(db)
        rows = [dict(r) for r in conn.execute(
            f"SELECT d.id, d.filename, d.created_at, SUBSTR(COALESCE(d.masked_text, ''), 1, 200000) AS text, "
            f"CASE WHEN {tsql} THEN 1 ELSE 0 END AS trusted FROM documents d "
            f"WHERE d.doc_type = 'regulation'{where}", targs + args)]
    val = build(rows)
    with _LOCK:
        if len(_CACHE) > 32:
            _CACHE.clear()
        _CACHE[key] = val
    return val


# 판 정하기에 드는 문서 — 관리자 계정이 올린 공통 공개 규정만. 담당자가 프로젝트에 올린 파일이 이름·날짜만으로 「현행」 을
# 차지하거나, 부서 한정 판의 날짜가 그 판을 못 보는 사람의 답에 드러나지 않게 한다(11-17 보안 점검).
def shared_where(db) -> tuple[str, list]:
    import json
    from pathlib import Path
    try:
        acc = json.loads((Path(db.path).parent / "accounts.json").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, AttributeError, TypeError):
        acc = {}
    admins = sorted(u for u, v in acc.items() if isinstance(v, dict) and v.get("role") == "dev") or ["\x00"]
    return (" AND COALESCE(d.access_level, 'public') = 'public' AND COALESCE(d.dept, '공통') = '공통'"
            f" AND (d.owner IN ({','.join('?' * len(admins))}) OR d.stored_path LIKE 'dgx://%' OR d.stored_path LIKE '%/기준/%')"), admins


def trusted_sql(db) -> tuple[str, list]:
    """출처로 믿는 판 — 학교 원본 폴더(dgx://)에서 동기화된 것이나 관리자 계정이 올린 것. 사람이 꾸밀 수 없는 표시다."""
    import json
    from pathlib import Path
    try:
        acc = json.loads((Path(db.path).parent / "accounts.json").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, AttributeError, TypeError):
        acc = {}
    admins = sorted(u for u, v in acc.items() if isinstance(v, dict) and v.get("role") == "dev") or ["\x00"]
    return f"(d.stored_path LIKE 'dgx://%' OR d.owner IN ({','.join('?' * len(admins))}))", admins


def superseded(db, doc_id: int) -> dict | None:
    """이 규정 문서가 구판이면 현행 판 {id, filename, latest} — 답의 출처에 「구판」 을 밝히는 데 쓴다. 현행이거나 규정이 아니면 None.
    관리자가 올린 공통 규정 장부(shared_where) 안에서만 따진다."""
    where, args = shared_where(db)
    for g in registry(db, where, args):
        for v in g["versions"]:
            if v["id"] == int(doc_id):
                return None if v["status"] == "현행" else {**g["current"], "why": v["status"]}
    return None
