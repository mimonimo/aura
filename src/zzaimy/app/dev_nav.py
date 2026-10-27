"""개발 화면 이름표 — 주소 하나에 이름 하나. 사이드바·화면 머리·탭 제목이 모두 여기서 읽는다.

같은 화면이 사이드바와 화면 머리에서 다른 이름으로 불리면
찾아 들어간 화면이 맞는지 헷갈린다. 이름을 바꿀 때는 이 표만 고친다.
"""

from __future__ import annotations

# (주소, 이름) — 사이드바 순서대로, 묶음별로
GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    # 세 묶음만(10-27) — 개발(진행·문서), 관리(자료·연결), 지표(측정). 업무 화면에 있는 것(그래프 탐색, 계정·부서·구글 연결 → 조직 관리)은 두지 않는다
    ("개발", [("/dev", "개발 현황"), ("/dev/flow", "처리 흐름"), ("/dev/docs", "논문·설계 문서"), ("/dev/coedit-demo", "공동 편집 시연")]),
    ("관리", [("/dev/archive", "원본 관리"), ("/dev/rag", "RAG 공간"), ("/dev/train", "데이터·학습"), ("/dev/tools", "도구·모델 연결"),
            ("/dev/pii", "개인정보·반출 점검")]),
    ("지표", [("/dev/quality", "품질·성능")]),
]

# 사이드바에 없는 개발 화면
EXTRA = {"/dev/history": "변경 이력",                              # 개발 현황 머리의 「변경 이력」
         "/dev/models/compare": "모델 비교",                         # 품질·성능 「모델 비교」 카드의 「자세히」
         "/dev/quality/regulation": "규정 검색 품질",                # 품질·성능 「규정 검색 품질」 카드의 「자세히」
         "/dev/quality/grant-search": "사업 문서 검색",              # 품질·성능 「사업 문서 검색」 카드의 「자세히」
         "/dev/archive/doc/": "반입 결과 보기"}                       # 원본 관리 줄·문서 화면 「관리」 에서 문서 하나(/dev/archive/doc/{id})

NAMES: dict[str, str] = {href: name for _, links in GROUPS for href, name in links} | EXTRA

# 하위 화면 → 사이드바의 어느 화면 안인가(주소 앞부분). 가장 긴 앞부분이 이긴다 —
# /dev/models/compare 는 품질·성능, 그 밖의 /dev/models/… 는 데이터·학습.
SUBPAGES: dict[str, str] = {
    "/dev/history": "/dev",                         # 변경 이력은 개발 현황 안
    "/dev/doc/": "/dev/docs", "/dev/paper/": "/dev/docs", "/dev/weekly/": "/dev/docs", "/dev/weekly.": "/dev/docs",   # 문서 보기·논문 자료·주간 보고
    "/dev/archive": "/dev/archive",                 # 원본 파일 한 건 열기도 원본 관리 안
    "/dev/models/": "/dev/train",
    "/dev/train/": "/dev/train",                    # 검수 가이드·학습 상태·학습셋 표본도 데이터·학습 안
    "/dev/models/compare": "/dev/quality",          # 모델 비교는 품질·성능 안
    "/dev/quality/": "/dev/quality",                # 규정 검색·사업 문서 검색 자세히도 품질·성능 안
}


def page_name(href: str) -> str:
    """주소의 화면 이름. 표에 없으면 KeyError — 새 화면은 표에 먼저 넣는다."""
    return NAMES[href]


def owner(path: str) -> str:
    """지금 주소가 속한 사이드바 화면 — 사이드바 주소 그대로면 그것, 아니면 SUBPAGES 의 가장 긴 앞부분."""
    if any(path == href for _, links in GROUPS for href, _ in links):
        return path
    hits = [pre for pre in SUBPAGES if path.startswith(pre)]
    return SUBPAGES[max(hits, key=len)] if hits else ""


def parent(path: str) -> tuple[str, str] | None:
    """하위 화면이면 (사이드바 화면 주소, 이름) — 화면 머리의 「품질·성능 › 모델 비교」 처럼 쓴다."""
    own = owner(path)
    return (own, NAMES[own]) if own and own != path else None


def is_active(href: str, path: str, train_tab: str = "") -> bool:
    """사이드바에서 지금 화면을 고른다(owner 와 같으면 켬)."""
    return owner(path) == href


def in_developer_space(path: str, role: str) -> bool:
    """개발 사이드바를 보일 화면인가 — 개발자 역할이고, /dev 아래이거나 개발 메뉴에 올린 다른 주소."""
    if role != "dev":
        return False
    return path.startswith("/dev") or path in {href.split("?", 1)[0] for href in NAMES}


def install(env) -> None:
    env.globals["dev_space"] = in_developer_space
    env.globals["dev_page_name"] = page_name
    env.globals["dev_nav_groups"] = GROUPS
    env.globals["dev_nav_active"] = is_active
    env.globals["dev_nav_parent"] = parent
