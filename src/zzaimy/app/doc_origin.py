"""문서 검토 화면의 길 안내 — 돌아갈 곳과 원본 보관소 위치.

돌아갈 곳: 진행 중인 프로젝트에 든 문서면 그 프로젝트, 아니면 들어온 곳(쿼리 from= 또는 같은 사이트의 Referer),
그것도 없으면 보관된 사업 묶음 · 기준 문서 · 라이브러리 순. 원본 위치는 원본 보관소 장부(archive_files)의
사업·연도로 짧게 보이고, 전체 경로는 툴팁·복사로만 둔다.
"""
from __future__ import annotations

from urllib.parse import urlencode, urlsplit

from zzaimy.app.program_names import display_name
from zzaimy.graph.programs import plausible_year

# 들어온 곳 — 열쇠 → (이름, 기본 주소). 주소 앞부분으로 Referer 를 이 열쇠로 바꾼다
ORIGINS: dict[str, tuple[str, str]] = {
    "library": ("라이브러리", "/?type=all"),
    "archive": ("원본 보관소", "/archive"),
    "search": ("검색", "/search"),
    "criteria": ("기준 문서", "/?type=all&use=criteria"),     # 옛 /criteria 탭은 라이브러리 기준 칩(ADR-0062)
    "graph": ("그래프 탐색", "/graph/explore"),
}
_PREFIX = (("/archive", "archive"), ("/search", "search"), ("/criteria", "criteria"), ("/graph", "graph"),
           ("/inbox", "library"))


def origin_of_path(path: str) -> str:
    """주소 경로 → 들어온 곳 열쇠. 모르는 화면이면 빈 문자열."""
    if path == "/":
        return "library"
    for prefix, key in _PREFIX:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
            return key
    return ""


def came_from(from_: str = "", referer: str = "", host: str = "") -> dict | None:
    """들어온 곳 — {key, label, href}. from= 열쇠가 먼저, 없으면 같은 사이트 Referer(조건이 붙은 주소 그대로)."""
    key = (from_ or "").strip()
    if key in ORIGINS:
        label, href = ORIGINS[key]
        return {"key": key, "label": label, "href": href}
    if not referer:
        return None
    try:
        parts = urlsplit(referer)
    except ValueError:
        return None
    if parts.netloc and host and parts.netloc != host:      # 다른 사이트에서 온 주소는 따르지 않는다
        return None
    key = origin_of_path(parts.path or "")
    if not key:
        return None
    href = parts.path + ("?" + parts.query if parts.query else "")
    if not href.startswith("/") or href.startswith("//"):
        return None
    return {"key": key, "label": ORIGINS[key][0], "href": href}


def with_ro(word: str) -> str:
    """「~로/~으로」 — 끝 글자 받침으로 고른다(ㄹ 받침은 「로」)."""
    last = word[-1:] if word else ""
    if "가" <= last <= "힣":
        jong = (ord(last) - 0xAC00) % 28
        return word + ("으로" if jong and jong != 8 else "로")
    return word + "로"


def back_link(doc: dict, project: dict | None, from_: str = "", referer: str = "", host: str = "") -> dict:
    """「←」 링크 — {label, href, title}."""
    if project and not project.get("archived"):
        return {"label": "프로젝트로 돌아가기", "href": f"/project/{project['id']}", "title": str(project.get("name") or "")}
    origin = came_from(from_, referer, host)
    if origin:
        return {"label": with_ro(origin["label"]), "href": origin["href"], "title": ""}
    if project:
        return {"label": "지난 사업으로", "href": f"/project/{project['id']}", "title": str(project.get("name") or "")}
    if doc.get("doc_type") == "regulation":
        return {"label": "기준 문서로", "href": ORIGINS["criteria"][1], "title": ""}
    if str(doc.get("stored_path") or "").startswith("dgx://"):
        return {"label": "원본 보관소로", "href": "/archive", "title": ""}
    return {"label": "라이브러리로", "href": "/?type=all", "title": ""}


def archive_location(rel: str, row: dict | None) -> dict:
    """원본 보관소 짧은 위치 — {label, href, path}. row 는 장부 줄(program·program_name·year), 없으면 폴더 이름으로."""
    rel = rel or ""
    name = rel.rsplit("/", 1)[-1]
    row = row or {}
    program = str(row.get("program") or "")
    program_label = display_name(str(row.get("program_name") or ""))
    year = str(row.get("year") or "").strip()
    year = int(year) if year.isdigit() and plausible_year(year) else None
    parts = ["원본 보관소"]
    if program_label:
        parts.append(program_label + (f" › {year}년" if year else ""))
    else:
        folders = [f for f in rel.split("/")[:-1] if f]
        if folders:
            parts.append(" › ".join(folders[-2:]))
    params = {}
    if program and program_label:
        params["program"] = program
    if year and program:
        params["year"] = year
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if stem:
        params["q"] = stem[:200]
    return {"label": " · ".join(parts), "href": "/archive?" + urlencode(params) + "#archive-list", "path": rel}
