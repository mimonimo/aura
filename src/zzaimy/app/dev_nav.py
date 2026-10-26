"""개발 화면 이름표 — 주소 하나에 이름 하나. 사이드바·화면 머리·탭 제목이 모두 여기서 읽는다.

같은 화면이 사이드바에서는 「데이터 현황」, 머리에서는 「데이터 열람 (개발자)」처럼 달리 불리면
찾아 들어간 화면이 맞는지 헷갈린다. 이름을 바꿀 때는 이 표만 고친다.
"""

from __future__ import annotations

# (주소, 이름) — 사이드바 순서대로, 하는 일 묶음별로(문서가 들어와 → 검색·지식이 되고 → 모델이 쓰고 → 점검·관리)
GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    # 묶음은 개발 현황 「구축 현황」 의 여섯 영역·처리 흐름 단계와 같은 지도 — 어느 화면에서든 같은 자리에서 찾게
    ("개요", [("/dev", "개발 현황"), ("/dev/flow", "처리 흐름"), ("/dev/docs", "논문·설계 문서")]),
    ("문서 반입·읽기", [("/dev/db", "데이터 현황")]),
    ("검색·지식", [("/dev/rag", "RAG 공간"), ("/graph/explore", "그래프 탐색"), ("/dev/quality", "품질·성능")]),
    ("문서 작성", [("/dev/coedit-demo", "공동 편집 시연")]),
    ("모델 학습", [("/dev/train", "데이터·학습"), ("/dev/collect", "수집 자료"), ("/dev/tools", "도구·모델 연결")]),
    ("보안·관리", [("/dev/users", "사용자·계정"), ("/dev/pii", "개인정보·반출 점검")]),
]

# 사이드바에 없는 개발 화면
EXTRA = {"/dev/history": "변경 이력",                              # 개발 현황 머리의 「변경 이력」
         "/dev/intake": "반입 점검",                               # 처리 흐름 화면의 한 칸(옛 「문서 가져오기」 — 주소는 그리로 넘긴다)
         "/dev/models/compare": "모델 비교 결과"}                     # 개발 현황 모델 표의 Writer 줄에서 들어간다

NAMES: dict[str, str] = {href: name for _, links in GROUPS for href, name in links} | EXTRA


def page_name(href: str) -> str:
    """주소의 화면 이름. 표에 없으면 KeyError — 새 화면은 표에 먼저 넣는다."""
    return NAMES[href]


def is_active(href: str, path: str, train_tab: str) -> bool:
    """사이드바에서 지금 화면을 고른다. 「데이터·학습」은 /dev/train, 「도구·모델 연결」은 /dev/tools."""
    if href == "/dev/train":
        return path == "/dev/train" or path.startswith("/dev/models/")
    if href == "/dev/docs":                        # 문서 보기·논문 자료·주간 보고는 논문·설계 문서의 하위 화면
        return path.startswith(("/dev/doc", "/dev/paper", "/dev/weekly"))
    if href == "/dev":                             # 변경 이력은 개발 현황의 하위 화면
        return path in ("/dev", "/dev/history")
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
