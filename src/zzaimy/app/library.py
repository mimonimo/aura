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


def grouped(conn, where: str, args: list) -> list[list[int]]:
    """조건(where 는 ' WHERE …', 표 별칭 d, 열람 조건 포함)에 맞는 문서를 판본 묶음 목록으로.

    요청마다 새로 계산한다 — 사용자·권한 사이에 결과를 나눠 쓰지 않는다(열람 등급이 바뀌면 바로 반영)."""
    rows = [tuple(r) for r in conn.execute(
        f"SELECT d.id, d.doc_type, d.filename FROM documents d{where} ORDER BY d.id DESC", args).fetchall()]
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
