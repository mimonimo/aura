"""화면 스크립트가 브라우저 내장 전역 이름을 덮지 않는가.

인라인 스크립트의 맨 바깥에서 `var status = …` 처럼 선언하면 window.status(문자열)와 같은 이름이라 요소 대신
문자열이 남고, 그 줄에서 스크립트가 멈춰 필터·버튼이 모두 죽는다(기준 문서 화면에서 실제로 났던 일).
화면 스크립트는 즉시 실행 함수나 모듈로 감싸 전역 이름을 만들지 않는 것이 원칙이고, 이 검사는 그중
내장 이름과 겹치는 맨 바깥 선언만 잡는다(템플릿·정적 JS 전부, 앞으로 더해질 화면도).
"""
from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "src" / "zzaimy" / "app"

# window 에 이미 있는 이름 — 맨 바깥 var/function 선언이 이 값과 부딪히거나(status·name 은 문자열로 바뀐다)
# 읽기 전용이라 조용히 무시된다(top·parent·self·length·frames …)
WINDOW_NAMES = {
    "status", "name", "top", "parent", "self", "window", "frames", "length", "opener", "closed", "origin",
    "history", "location", "navigator", "document", "screen", "event", "external", "close", "open", "stop",
    "focus", "blur", "print", "scroll", "find", "toolbar", "menubar", "statusbar", "locationbar", "personalbar",
    "scrollbars", "frameElement", "defaultStatus", "content", "performance", "crypto", "caches", "localStorage",
    "sessionStorage", "indexedDB", "visualViewport", "customElements", "devicePixelRatio", "innerWidth",
    "innerHeight", "outerWidth", "outerHeight", "screenX", "screenY", "scrollX", "scrollY", "fetch", "alert",
    "confirm", "prompt", "postMessage", "onload", "onerror", "onsubmit", "onchange", "oninput", "onclick",
}

_JINJA = re.compile(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}", re.S)
_SCRIPT = re.compile(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.S | re.I)
_DECL = re.compile(r"(?:^|[;\s{}])(?:var|let|const)\s+([A-Za-z_$][\w$]*)|(?:^|[;\s{}])function\s+([A-Za-z_$][\w$]*)")


def _top_level(code: str) -> str:
    """중괄호·괄호 바깥(맨 바깥)에 있는 글자만 남긴다. 문자열·주석 안의 괄호는 세지 않는다."""
    out, depth, i, n = [], 0, 0, len(code)
    while i < n:
        c = code[i]
        if code.startswith("//", i):
            j = code.find("\n", i)
            i = n if j < 0 else j
            continue
        if code.startswith("/*", i):
            j = code.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "'\"`":
            j = i + 1
            while j < n and code[j] != c:
                j += 2 if code[j] == "\\" else 1
            if depth == 0:
                out.append(" ")
            i = j + 1
            continue
        if c in "{([":
            depth += 1
        elif c in "})]":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(c)
        i += 1
    return "".join(out)


def _scripts():
    for path in sorted((APP / "templates").glob("*.html")):
        text = _JINJA.sub(" ", path.read_text(encoding="utf-8"))
        for m in _SCRIPT.finditer(text):
            attrs = m.group("attrs").lower()
            if "src=" in attrs or "module" in attrs or "json" in attrs:
                continue                        # 모듈은 전역을 만들지 않고, 외부 파일은 아래에서 본다
            yield f"{path.name} 인라인", m.group("body")
    for path in sorted((APP / "static").glob("*.js")):
        if path.name.endswith(".min.js") or path.name in {"cytoscape-fcose.js", "cose-base.js", "layout-base.js"}:
            continue                            # 묶어 받은 외부 라이브러리
        yield path.name, path.read_text(encoding="utf-8")


def colliding_globals(code: str) -> set[str]:
    names = set()
    for m in _DECL.finditer(_top_level(code)):
        names.add(m.group(1) or m.group(2))
    return names & WINDOW_NAMES


def test_detector_catches_known_collision():
    assert colliding_globals("var status = document.getElementById('s'); status.addEventListener('x', f);") == {"status"}
    assert colliding_globals("(function(){ var status = 1; })();") == set()
    assert colliding_globals("function name() {}\nlet top = 1;") == {"name", "top"}
    assert colliding_globals("var s = '{'; var parent = 2;") == {"parent"}
    assert colliding_globals("// var status\nvar ok = 1;") == set()


def test_page_scripts_do_not_shadow_window_globals():
    bad = {where: sorted(hit) for where, code in _scripts() if (hit := colliding_globals(code))}
    assert not bad, f"화면 스크립트가 브라우저 전역 이름을 덮습니다 — 함수로 감싸 주세요: {bad}"
