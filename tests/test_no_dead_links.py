"""잔재 점검 — 화면(템플릿·정적 스크립트)과 안내 자료(platform_guide·process_flow·dev_nav·actions)가 가리키는 내부 주소가
지금 앱에 있는 경로인지 본다. 기능을 지우거나 주소를 옮겼는데 옛 링크가 남으면 여기서 막힌다(10-27 사용자 「잔재들 항상 점검」)."""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app

APP = Path(__file__).resolve().parents[1] / "src" / "zzaimy" / "app"
# 앱 경로가 아닌 것(정적 파일·기안기 서버 정적 자원·바깥 주소)
SKIP = ("/static/", "/webhwpctrl/js", "/webhwpctrl/css", "/webhwpctrl/img", "/webhwpctrl/", "//")


class _Prefixed:
    def __init__(self, r, prefix: str):
        self.original_router = getattr(r, "original_router", None)
        self.include_context = getattr(r, "include_context", None)
        self.path = prefix + getattr(r, "path", "") if getattr(r, "path", "") else ""
        self.routes = getattr(r, "routes", None)


def _routes(app) -> list[tuple[str, re.Pattern]]:
    pats = []
    flat, todo = [], list(app.routes)
    while todo:                                           # 새 FastAPI 는 포함한 라우터를 감싸 둔다(_IncludedRouter.original_router)
        r = todo.pop()
        sub = getattr(r, "original_router", None)
        if sub is not None:
            prefix = getattr(getattr(r, "include_context", None), "prefix", "") or ""
            todo.extend(_Prefixed(x, prefix) for x in sub.routes)
            continue
        flat.append(r)
    for r in flat:
        p = getattr(r, "path", "")
        if not p:
            continue
        parts = re.split(r"\{[^}]+\}", p)
        pats.append((p, re.compile("^" + "[^/]+".join(re.escape(x) for x in parts) + "/?$")))
    return pats


def _links() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    files = list((APP / "templates").glob("*.html")) + list((APP / "static").glob("*.js"))
    files += [APP / n for n in ("platform_guide.py", "process_flow.py", "dev_nav.py", "actions.py")]
    for f in files:
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r'''(?:href|action)=["'](/[^"'{}<>\s#?]*)''', text):
            found.setdefault(m.group(1), []).append(f.name)
        if f.suffix == ".py":
            text = "\n".join(ln for ln in text.splitlines() if "startswith(" not in ln)   # 주소 앞머리 견주기(메뉴 켜짐 표시)는 링크가 아니다
            for m in re.finditer(r'''["'](/(?:dev|team|hwp|project|archive|settings|chat|graph|doc|programs)[^"'{}\s#?]*)["']''', text):
                found.setdefault(m.group(1), []).append(f.name)
    return found


def test_internal_links_point_to_live_routes(tmp_path):
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(), drafter=FakeDrafter())
    pats = _routes(app)
    dead = {}
    for link, where in _links().items():
        if link in ("/", "") or link.startswith(SKIP) or "." in link.rsplit("/", 1)[-1]:
            continue
        # 「/doc/」 처럼 뒤에 번호를 붙여 쓰는 앞머리는 그 앞머리로 시작하는 경로가 있으면 된다
        if link.endswith("/") and any(raw.startswith(link) for raw, _ in pats):
            continue
        if not any(rx.match(link) for _, rx in pats):
            dead[link] = sorted(set(where))
    assert not dead, "없는 주소를 가리키는 링크(지운 기능·옮긴 주소의 잔재): " + "; ".join(f"{k} ← {', '.join(v)}" for k, v in sorted(dead.items()))
