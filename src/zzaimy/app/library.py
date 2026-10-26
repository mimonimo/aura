"""라이브러리(등록 문서 목록) — 판본 묶음, 쪽 나눔, 공통 양식 바로가기.

판본 묶음. 같은 문서가 여러 번 들어오면(같은 이름의 재반입, `_ver3`·`(최종)`·`(1)` 꼬리만 다른 판) 목록에
같은 제목이 줄지어 선다. 목록에서는 판 꼬리를 뗀 제목 열쇠(version_key)로 묶어 가장 최근에 등록된 판
하나만 보이고 나머지는 「판본 n개」로 펼친다. 저장된 family 는 바꾸지 않는다 — 반입 중복 판정(doc_family.judge)이
판본을 같은 내용으로 막지 않게 하려는 것이고, 여기 묶음은 화면 표시만을 위한 것이다.
"""

from __future__ import annotations

import re

from zzaimy.app.doc_family import family_key

_EXT = re.compile(r"\.(hwpx?|pdf|docx?|xlsx?|pptx?|jpe?g|png|txt|md|zip)$", re.I)
# 판을 가리키는 꼬리 — 이름 끝에서만 뗀다. 낱말 꼬리 가운데 뜻이 넓은 것(수정·초안)은 구분 기호 뒤에 올 때만.
_VERSION_TAIL = re.compile(
    r"(?:"
    r"[\s_\-.]*[(\[]?\s*(?:ver|rev)\.?\s*\d+(?:[._]\d+)*\s*[)\]]?"            # _ver3.1 · (rev 4)
    r"|(?:[\s_\-.]+|[(\[])\s*v\.?\s*\d+(?:[._]\d+)*\s*[)\]]?"                  # _v2 — 「IPv6」 처럼 붙은 v 는 판이 아니다
    r"|[\s_\-.]*[(\[]?\s*(?:최최종|최종본?|수정본|수정안|제출본|검토본|확정본)\s*\d*\s*[)\]]?"   # (최종) · _수정본2
    r"|[_\-]+(?:수정|초안)\s*\d*|[(\[]\s*(?:수정|초안)\s*\d*\s*[)\]]"              # _수정 · (초안)
    r"|\s*\([^()]{0,12}\s수정\)"                                                # (홍길동 수정)
    r"|\s+\(\d{1,2}\)"                                                         # 내려받기 사본 표시 「이름 (1)」 — 붙은 「회의록(2)」는 번호라 두지 않는다
    r")\s*$",
    re.I,
)


def version_key(filename: str) -> str:
    """판본 묶음 열쇠 — 확장자와 판 꼬리를 뗀 뒤 family_key 로 정규화. 다 떼어 비면 원래 이름의 열쇠."""
    base = _EXT.sub("", (filename or "").strip())
    # 이름 뒤의 「 · …」(옛 이름 규칙이 붙인 구분 말·접수번호, 규정의 기준 날짜)는 판을 가르는 꼬리다
    base = base.split(" · ", 1)[0].strip() or base
    prev = None
    s = base
    while prev != s:
        prev = s
        s = _VERSION_TAIL.sub("", s).rstrip(" _-.")
    return family_key(s) or family_key(base)


def group_ids(rows: list[tuple]) -> list[list[int]]:
    """(id, doc_type, filename) 줄(최근 id 먼저)을 판본 묶음으로 — 묶음 안도 최근 먼저, 묶음 순서는 최신 판 기준."""
    order: list[list[int]] = []
    where: dict[tuple, int] = {}
    for did, doc_type, filename in rows:
        k = version_key(filename or "")
        key = (doc_type or "", k) if k else ("#", str(did))
        i = where.get(key)
        if i is None:
            where[key] = len(order)
            order.append([int(did)])
        else:
            order[i].append(int(did))
    return order


ORDERS = {"new": "d.id DESC", "old": "d.id ASC", "name": "d.filename ASC, d.id DESC"}


def grouped(conn, where: str, args: list, sort: str = "new") -> list[list[int]]:
    """조건(where 는 ' WHERE …', 표 별칭 d, 열람 조건 포함)에 맞는 문서를 판본 묶음 목록으로. sort: 최근 등록·오래된 순·이름순.

    요청마다 새로 계산한다 — 사용자·권한 사이에 결과를 나눠 쓰지 않는다(열람 등급이 바뀌면 바로 반영)."""
    rows = [tuple(r) for r in conn.execute(
        f"SELECT d.id, d.doc_type, d.filename FROM documents d{where} ORDER BY {ORDERS.get(sort, ORDERS['new'])}", args).fetchall()]
    return group_ids(rows)


def page_numbers(page: int, pages: int, around: int = 2) -> list[int | None]:
    """쪽 번호 줄 — 처음·끝과 지금 쪽 앞뒤. 건너뛴 자리는 None(…)."""
    if pages <= 1:
        return [1] if pages == 1 else []
    keep = {1, pages, *range(max(1, page - around), min(pages, page + around) + 1)}
    out: list[int | None] = []
    last = 0
    for n in sorted(keep):
        if n - last > 1:
            out.append(None)
        out.append(n)
        last = n
    return out


_GDOC = re.compile(r"^(https://docs\.google\.com/(?:document|spreadsheets|presentation)/d/[\w-]+)(?:/[^?#]*)?(?:[?#].*)?$")


def copy_url(url: str) -> str:
    """구글 문서 주소 → 사본 만들기 주소. 공용 원본을 편집 모드로 열지 않는다. 구글 문서가 아니면 빈 문자열."""
    m = _GDOC.match((url or "").strip())
    return f"{m.group(1)}/copy" if m else ""


# ---- 쓰임새 칩과 업무 — 한 목록(ADR-0062) ----
#
# 라이브러리는 문서 목록 하나다. 「기준」 은 따로 쌓아 둔 탭이 아니라 쓰임새로 고르는 칩이다 — 서류 갈래(kind)가
# 공고·지침·규정·심사 기준·기본계획이거나(project_documents.CRITERIA_KINDS, 프로젝트 기준 자동 연결과 같은 집합)
# 규정 색인에 든 문서(doc_type='regulation'). 나머지는 「작성·결과」(계획서·보고서·양식·현황표·분류 전 …).
# 데이터는 바꾸지 않는다 — 칩은 목록을 거르기만 하고, 규정 색인 반입은 기존 경로(/criteria/upload)만 쓴다.

USES = (("all", "전체"), ("criteria", "기준"), ("work", "작성·결과"))
USE_METRICS = {"all": "documents_visible", "criteria": "documents_use_criteria", "work": "documents_use_work"}


def _criteria_kinds() -> list[str]:
    from zzaimy.app.project_documents import CRITERIA_KINDS

    return sorted(CRITERIA_KINDS)


def use_of(doc_type: str | None, kind: str | None) -> str:
    """문서 하나의 쓰임새 — criteria | work. use_sql 과 같은 규칙(테스트가 대조한다)."""
    return "criteria" if (doc_type or "") == "regulation" or (kind or "") in _criteria_kinds() else "work"


def use_sql(use: str) -> tuple[str, list]:
    """쓰임새 칩의 SQL 조건(표 별칭 d) — (조건, 인자). all 이나 모르는 값이면 빈 조건."""
    kinds = _criteria_kinds()
    marks = ",".join("?" * len(kinds))
    crit = f"(COALESCE(d.doc_type, '') = 'regulation' OR COALESCE(d.kind, '') IN ({marks}))"
    if use == "criteria":
        return crit, list(kinds)
    if use == "work":
        return f"NOT {crit}", list(kinds)
    return "", []


def use_case_sql() -> tuple[str, list]:
    """쓰임새를 한 열로 — 칩마다 수를 한 번에 센다."""
    kinds = _criteria_kinds()
    marks = ",".join("?" * len(kinds))
    return (f"CASE WHEN COALESCE(d.doc_type, '') = 'regulation' OR COALESCE(d.kind, '') IN ({marks})"
            " THEN 'criteria' ELSE 'work' END"), list(kinds)


def business_sql(businesses) -> str:
    """문서의 업무 열(표 별칭 d) — 접수 문서는 처리 갈래(doc_type), 기준·추출 문서는 업무 영역(sector), 없으면 공통."""
    keys = ",".join(f"'{k}'" for k in businesses)     # 화면 상수(INBOX_TYPES)의 열쇠만 — 사용자 값이 아니다
    return (f"CASE WHEN COALESCE(d.doc_type, '') IN ({keys}) THEN d.doc_type"
            f" WHEN COALESCE(d.sector, '') IN ({keys}) THEN d.sector ELSE 'common' END")


def business_filter_sql(business: str, businesses) -> tuple[str, list]:
    """고른 업무의 문서 — 그 업무 문서와 공통(모든 업무에 적용하는 기준·공통 문서)."""
    return f"{business_sql(businesses)} IN (?, 'common')", [business]


def show_business_tabs(counts: dict[str, int], businesses) -> bool:
    """업무 탭 줄은 문서가 있는 업무가 둘 이상일 때만 — 하나뿐이면 탭은 고를 것이 없다(선택 상자에서 고른다)."""
    return sum(1 for k in businesses if counts.get(k)) >= 2


# ---- 사업별로 보기 — 왼쪽 사업 목록(문서가 속한 프로젝트의 사업 열쇠로 묶는다) ----
# 묶음 열쇠: 「prog:<사업 id>」(지난 사업 보관 묶음의 사업), 「work」(담당자가 만든 진행 중 프로젝트),
# 「nonprogram」(기관 일반 업무, 사업 아님), 「none」(사업 미분류·프로젝트 없음)
RAIL_FIXED = {"work": "진행 중 프로젝트", "nonprogram": "기관 일반 업무 (사업 아님)", "none": "사업 미분류"}


def rail_key(program: str | None, archived, has_project: bool) -> tuple[str, str]:
    """(묶음 열쇠, 연도 꼬리) — 프로젝트의 program 값(「program:linc30|2024」)에서."""
    if not has_project:
        return "none", ""
    p = program or ""
    base, _, tail = p.partition("|")
    if base.startswith("program:"):
        return "prog:" + base, tail
    if base == "nonprogram":
        return "nonprogram", ""
    if not p and not archived:
        return "work", ""
    return "none", ""


def _bundle_name(name: str) -> str:
    """보관 묶음 이름(「2024년 ○○ 사업 (2차년도)」)에서 사업 이름만."""
    import re
    name = re.sub(r"^\s*(?:19|20)\d{2}\s*년\s*", "", name or "")
    return re.sub(r"\s*\((?:\d+차년도|연도 미상)\)\s*$|\s*\d+차년도\s*$", "", name).strip()


def year_label(tail: str) -> str:
    if tail == "*":
        return "전체 기간"                         # 연도로 나누지 않은 묶음(기관 일반 업무 등)
    if not tail or tail == "?":
        return "연도 미상"
    if tail.startswith("r") and tail[1:].isdigit():
        return f"{tail[1:]}차년도"
    return f"{tail}년" if tail.isdigit() else tail


def rail_sql(key: str, tail: str = "", members: dict[str, list[str]] | None = None) -> tuple[str, list]:
    """고른 사업 묶음(과 연도)의 문서 조건(표 별칭 d). members: 대표 사업 id → 같은 사업으로 묶인 id 들(그래프의 이름 묶음)."""
    if key.startswith("prog:"):
        ids = (members or {}).get(key[5:]) or [key[5:]]
        if tail:
            return (f"d.project_id IN (SELECT id FROM projects WHERE program IN ({','.join('?' * len(ids))}))",
                    [f"{b}|{tail}" for b in ids])
        cond = " OR ".join("program = ? OR program LIKE ?" for _ in ids)
        return f"d.project_id IN (SELECT id FROM projects WHERE {cond})", [x for b in ids for x in (b, b + "|%")]
    if key == "nonprogram":
        return "d.project_id IN (SELECT id FROM projects WHERE program LIKE 'nonprogram%')", []
    if key == "work":
        return "d.project_id IN (SELECT id FROM projects WHERE COALESCE(program, '') = '' AND COALESCE(archived, 0) = 0)", []
    if key == "none":
        return ("(d.project_id IS NULL OR d.project_id IN (SELECT id FROM projects WHERE program LIKE 'none%'"
                " OR (COALESCE(program, '') = '' AND COALESCE(archived, 0) <> 0)))"), []
    return "", []


def rail(conn, where: str, args: list, rep: dict[str, str] | None = None) -> list[dict]:
    """조건에 맞는 문서를 사업 묶음으로 센다 — [{key, label, n, years:[{tail, label, n}]}], 사업은 문서 많은 순, 고정 묶음은 끝.
    rep: 사업 id → 대표 id(그래프 탐색 왼쪽 목록과 같은 이름 묶음, kg_explore.programs) — 같은 사업의 다른 열쇠를 한 줄로."""
    rows = conn.execute(
        "SELECT p.program, p.archived, d.project_id IS NOT NULL AND p.id IS NOT NULL, COUNT(*), MAX(p.name) FROM documents d"
        f" LEFT JOIN projects p ON p.id = d.project_id{where} GROUP BY 1, 2, 3", args).fetchall()
    out: dict[str, dict] = {}
    for program, archived, has_p, n, pname in rows:
        key, tail = rail_key(program, archived, bool(has_p))
        if key.startswith("prog:") and rep:
            key = "prog:" + rep.get(key[5:], key[5:])
        g = out.setdefault(key, {"key": key, "n": 0, "years": {}, "pname": _bundle_name(pname or "")})
        g["n"] += int(n)
        if key.startswith("prog:"):
            g["years"][tail] = g["years"].get(tail, 0) + int(n)
    ids = [k[5:] for k in out if k.startswith("prog:")]
    labels = {}
    try:                                                    # 그래프가 없으면 보관 묶음 이름에서
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            labels.update({r[0]: r[1] for r in conn.execute(
                f"SELECT id, label FROM kg_nodes WHERE id IN ({','.join('?' * len(part))})", part).fetchall()})
    except Exception:
        labels = {}
    res = []
    for g in out.values():
        k = g["key"]
        g["label"] = RAIL_FIXED.get(k) or labels.get(k[5:]) or g.pop("pname", "") or k[5:].split(":", 1)[-1]
        g.pop("pname", None)
        g["years"] = sorted(({"tail": t, "label": year_label(t), "n": n} for t, n in g["years"].items()),
                            key=lambda y: (not (y["tail"] or "").isdigit(), y["tail"]), reverse=False)
        res.append(g)
    progs = sorted((g for g in res if g["key"].startswith("prog:")), key=lambda g: (-g["n"], g["label"]))
    fixed = [g for k in ("work", "nonprogram", "none") for g in res if g["key"] == k]
    return progs + fixed


# ---- 사업 한눈에 보기 — 사업을 고르면 목록 위에 사업 머리·연도 × 서류 갈래 표·주요 문서 ----
# 지난 사업을 찾는 일이 라이브러리의 주된 쓰임이라, 사업을 고르면 문서 줄보다 먼저 사업의 모양(어느 해에 어떤 서류가 몇 건)을 보인다.
# 표는 SQL GROUP BY 한 번으로 센다(문서 수만 건을 파이썬으로 훑지 않는다). 사업 정보는 장부(kg_external.json)에 적힌 것만 쓴다.
MATRIX_COLS = 7                                  # 따로 세우는 갈래 열 수(문서 많은 순) — 나머지는 「그 밖」 한 열
KEY_KINDS = ("plan", "report")                   # 주요 문서 — 해마다 가장 최근에 등록된 계획서·결과보고서


def _year_order(tail: str) -> tuple:
    """표의 연도 줄 순서 — 최근 해 먼저, 그다음 n차년도, 연도 미상은 끝."""
    if tail.isdigit():
        return (0, -int(tail))
    if tail.startswith("r") and tail[1:].isdigit():
        return (1, int(tail[1:]))
    return (2, tail)


def matrix(conn, where: str, args: list, kind_labels: dict[str, str]) -> dict:
    """고른 사업의 연도 × 서류 갈래 수. where 는 ' WHERE …'(표 별칭 d, 열람·사업 조건 포함).
    → {years:[{tail,label,n,cells:{kind:n}}], cols:[{kind,label,n}], rest:bool, n, key_docs:{tail:{kind:id}}, span:(첫 해, 끝 해)|None}"""
    rows = conn.execute(
        "SELECT p.program, COALESCE(d.kind, ''), COUNT(*), MAX(d.id) FROM documents d"
        f" LEFT JOIN projects p ON p.id = d.project_id{where} GROUP BY 1, 2", args).fetchall()
    cells: dict[str, dict[str, int]] = {}
    latest: dict[str, dict[str, int]] = {}
    tot: dict[str, int] = {}
    for program, kind, n, last in rows:
        tail = str(program or "").partition("|")[2]
        cells.setdefault(tail, {})
        cells[tail][kind] = cells[tail].get(kind, 0) + int(n)
        tot[kind] = tot.get(kind, 0) + int(n)
        if kind in KEY_KINDS:
            got = latest.setdefault(tail, {})
            got[kind] = max(got.get(kind, 0), int(last))
    ranked = sorted(tot, key=lambda k: (-tot[k], k == "", k))
    shown, rest = ranked[:MATRIX_COLS], ranked[MATRIX_COLS:]
    cols = [{"kind": k, "label": kind_labels.get(k, "분류 전") if k else "분류 전", "n": tot[k]} for k in shown]
    years = []
    for tail in sorted(cells, key=_year_order):
        c = cells[tail]
        row = {"tail": tail, "label": year_label(tail), "n": sum(c.values()), "cells": {k: c.get(k, 0) for k in shown}}
        row["rest"] = sum(c.get(k, 0) for k in rest)
        years.append(row)
    nums = sorted(int(t) for t in cells if t.isdigit())
    return {"years": years, "cols": cols, "rest": bool(rest), "rest_n": sum(tot[k] for k in rest), "n": sum(tot.values()),
            "key_docs": latest, "span": (nums[0], nums[-1]) if nums else None}


def program_facts(entry: dict | None, categories: dict | None = None) -> dict:
    """장부 항목 → 사업 머리에 적을 사실(장부에 있는 것만). 항목이 없으면 빈 사실."""
    if not entry:
        return {}
    from zzaimy.app.program_tree import place

    l1, l2 = place(entry, categories)
    school = entry.get("school") or {}
    name = (entry.get("name") or "").strip()
    terms = []
    for t in [*(entry.get("terms") or []), *(school.get("names") or [])]:
        t = " ".join(str(t or "").split())
        if t and t != name and t not in terms:
            terms.append(t)
    return {"name": name, "aliases": terms[:8], "l1": l1, "l2": l2,
            "ministry": str(entry.get("ministry") or "").strip(), "agency": str(entry.get("agency") or "").strip(),
            "period": str(entry.get("period") or "").strip(),
            "depts": [d for d in (school.get("depts") or []) if str(d or "").strip()][:6],
            "school_years": [int(y) for y in (school.get("years") or []) if str(y).isdigit()]}


def find_programs(items: list[dict], text: str, limit: int = 30) -> list[dict]:
    """사업 찾기 — 이름·문서 속 이름·장부 별칭에 찾는 말(띄어쓰기·기호 무시)이 든 사업. 문서 많은 순."""
    from zzaimy.app.program_names import group_key

    want = group_key(text or "")
    if not want:
        return []
    hits = []
    for it in items:
        names = [it.get("label") or "", it.get("raw_label") or "", *(it.get("aliases") or [])]
        if any(want in group_key(n) for n in names if n):
            hits.append(it)
    hits.sort(key=lambda it: -int(it.get("n") or 0))
    return hits[:limit]


def year_span(years: list[dict]) -> str:
    """사업 목록 묶음의 연도 꼬리 → 「2019–2025」(숫자 연도만). 없으면 빈 문자열."""
    nums = sorted(int(y["tail"]) for y in years if str(y.get("tail") or "").isdigit())
    if not nums:
        return ""
    return str(nums[0]) if nums[0] == nums[-1] else f"{nums[0]}–{nums[-1]}"
