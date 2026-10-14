"""개발 화면 이름표 — 주소 하나에 이름 하나. 사이드바·화면 머리·탭 제목이 모두 여기서 읽는다.

같은 화면이 사이드바에서는 「원천 데이터」, 머리에서는 「데이터 열람 (개발자)」처럼 달리 불리면
찾아 들어간 화면이 맞는지 헷갈린다. 이름을 바꿀 때는 이 표만 고친다.
"""

from __future__ import annotations

# (주소, 이름) — 사이드바 순서대로, 하는 일 묶음별로(문서가 들어와 → 검색·지식이 되고 → 모델이 쓰고 → 점검·관리)
GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("개요", [("/dev", "개발 현황"), ("/dev/flow", "처리 흐름")]),
    ("문서·데이터", [("/dev/intake", "문서 반입"), ("/dev/db", "원천 데이터")]),
    ("검색·지식", [("/dev/rag", "RAG 공간"), ("/graph/explore", "그래프 탐색")]),
    ("모델", [("/dev/train", "데이터·학습"), ("/dev/tools", "도구·모델 연결")]),
    ("점검", [("/dev/quality", "품질·성능"), ("/dev/pii", "개인정보·반출 점검")]),
    ("관리·기록", [("/dev/users", "사용자·계정"), ("/dev/docs", "논문·설계 문서")]),
]

# 사이드바에 없는 개발 화면
EXTRA = {"/dev/history": "변경 이력", "/dev/nas": "문서 반입",     # Google Drive 가져오기는 문서 반입의 탭
         "/dev/train?tab=settings": "도구·모델 연결"}                 # 옛 주소 — /dev/tools 로 넘긴다

NAMES: dict[str, str] = {href: name for _, links in GROUPS for href, name in links} | EXTRA


def page_name(href: str) -> str:
    """주소의 화면 이름. 표에 없으면 KeyError — 새 화면은 표에 먼저 넣는다."""
    return NAMES[href]


def is_active(href: str, path: str, train_tab: str) -> bool:
    """사이드바에서 지금 화면을 고른다. 「데이터·학습」은 /dev/train·/dev/data, 「도구·모델 연결」은 /dev/tools."""
    if href == "/dev/train":
        return path in ("/dev/data", "/dev/train")
    if href == "/dev/docs":
        return path.startswith("/dev/doc")
    if href == "/dev/intake":
        return path in ("/dev/intake", "/dev/nas")
    return path == href


def in_developer_space(path: str, role: str) -> bool:
    """개발 사이드바를 보일 화면인가 — 개발자 역할이고, /dev 아래이거나 개발 메뉴에 올린 다른 주소(그래프 탐색 등)."""
    if role != "dev":
        return False
    return path.startswith("/dev") or path in {href.split("?", 1)[0] for href in NAMES}


def install(env) -> None:
    env.globals["dev_space"] = in_developer_space
    env.globals["dev_page_name"] = page_name
    env.globals["dev_nav_groups"] = GROUPS
    env.globals["dev_nav_active"] = is_active
