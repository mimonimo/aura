"""서버 설정을 화면에서 — 설치한 곳마다 다른 주소·키·경로를 서버 파일(.env.local)을 고치지 않고 개발 화면에서 정한다.

왜: 다른 기관에 설치하면 임베딩·리랭커 서버 주소, 웹 검색 키, 원본 폴더가 모두 다르다.
실행 파일 경로(kordoc 등)는 화면에서 정하지 않는다 — 화면 값이 실행할 프로그램을 바꾸게 되므로 설치 때 서버 파일로만(11-15 보안 검토).
지금까지는 서버에 접속해 .env.local 을 고치고 재시작해야 했다(11-15 설치본 준비). LLM 연결·단계별 모델은 이미 화면에 있다(llm_connections).

어떻게: 화면에서 정한 값은 설정 표에 「env:<이름>」 으로 두고, 앱이 뜰 때와 저장·지울 때(db.SETTING_HOOKS) 그 값을 프로세스 환경에 넣는다.
값을 읽는 쪽 코드는 그대로 os.environ 을 본다 — 읽는 곳마다 고치지 않아도 되고, 화면 값을 지우면 서버 파일 값(뜰 때의 값)으로 돌아간다.
앱 밖에서 도는 배치(scripts/170 원본 갱신 등)는 시작할 때 apply_all(db) 를 부른다.
비밀 값(키)은 화면에 다시 보이지 않는다 — 있다/없다와 끝 네 글자만.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

PREFIX = "env:"


@dataclass(frozen=True)
class Item:
    key: str                     # 환경 변수 이름
    label: str                   # 화면 이름(명사구)
    group: str                   # 묶음
    hint: str = ""               # 짧은 설명(명사구)
    kind: str = "text"           # text|url|choice|number|path|ssh
    secret: bool = False
    choices: tuple = field(default_factory=tuple)   # (값, 이름)
    restart: bool = False        # 앱 다시 시작해야 반영


ITEMS: tuple[Item, ...] = (
    Item("ZZAIMY_EMBED_URL", "임베딩 서버", "검색 서빙", "질의 임베딩 서비스 주소 · 비우면 앱 서버 CPU", "url"),
    Item("ZZAIMY_RERANK_URL", "리랭커 서버", "검색 서빙", "후보 재정렬 서비스 주소 · 비우면 앱 서버 CPU", "url"),
    Item("ZZAIMY_RERANK_MIN", "리랭크 하한", "검색 서빙", "이 점수 밑 후보는 뺌 · 모델을 바꾸면 다시 잼", "number"),
    Item("ZZAIMY_WEB_SEARCH", "웹 검색 제공자", "웹 검색", "자동이면 키가 있는 쪽, 없으면 DuckDuckGo", "choice",
         choices=(("", "자동"), ("google", "Google"), ("naver", "네이버"), ("duckduckgo", "DuckDuckGo"), ("off", "쓰지 않음"))),
    Item("ZZAIMY_GOOGLE_CSE_KEY", "Google 검색 API 키", "웹 검색", "Programmable Search API 키", secret=True),
    Item("ZZAIMY_GOOGLE_CSE_CX", "Google 검색 엔진 ID", "웹 검색", "검색 엔진 cx"),
    Item("ZZAIMY_NAVER_CLIENT_ID", "네이버 Client ID", "웹 검색", "네이버 검색 API"),
    Item("ZZAIMY_NAVER_CLIENT_SECRET", "네이버 Client Secret", "웹 검색", "네이버 검색 API", secret=True),
    Item("ZZAIMY_ARCHIVE_SOURCE", "원본 폴더", "원본 폴더", "서버 안 폴더(/mnt/nas/원본) 또는 계정@주소:경로 · 비우면 원본 서버", "source"),
    Item("ZZAIMY_DGX_SSH", "원본 서버 접속", "원본 폴더", "계정@주소 · 원본을 읽기 전용으로 받는 보관 서버", "ssh"),
    Item("ZZAIMY_DGX_SSH_PORT", "원본 서버 포트", "원본 폴더", "SSH 포트 · 비우면 22", "number"),
)
BY_KEY = {i.key: i for i in ITEMS}
GROUPS = tuple(dict.fromkeys(i.group for i in ITEMS))

_orig: dict[str, str | None] = {}          # 화면 값을 넣기 전 프로세스 환경(서버 파일 값) — 화면 값을 지우면 이것으로


def _remember(key: str) -> None:
    if key not in _orig:
        _orig[key] = os.environ.get(key)


def _put(key: str, value: str | None) -> None:
    _remember(key)
    if value is None:
        old = _orig.get(key)
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old
    else:
        os.environ[key] = value
    _after_change(key)


def _after_change(key: str) -> None:
    """값을 짧게 붙들어 두는 곳을 비운다 — 바꾼 주소가 다음 요청부터 바로 쓰이게."""
    if key in ("ZZAIMY_EMBED_URL", "ZZAIMY_RERANK_URL", "ZZAIMY_RERANK_MIN"):
        try:
            from zzaimy.app import search_serving
            search_serving._cache.clear()
        except Exception:
            pass


def apply_all(db) -> int:
    """설정 표의 화면 값을 모두 프로세스 환경에 넣는다 — 앱이 뜰 때·배치가 시작할 때. 넣은 수."""
    n = 0
    for k, v in db.settings_with_prefix(PREFIX).items():
        key = k[len(PREFIX):]
        if key in BY_KEY:
            _put(key, v)
            n += 1
    return n


def on_setting(db, key: str, value) -> None:
    """db.SETTING_HOOKS — 화면에서 저장·지우면 곧바로 프로세스 환경에."""
    if not str(key).startswith(PREFIX):
        return
    name = str(key)[len(PREFIX):]
    if name in BY_KEY:
        _put(name, None if value is None else str(value))


def install(db) -> None:
    from zzaimy.app import db as _dbmod
    if on_setting not in _dbmod.SETTING_HOOKS:
        _dbmod.SETTING_HOOKS.append(on_setting)
    apply_all(db)


def _mask(v: str) -> str:
    v = v or ""
    return ("•••• " + v[-4:]) if len(v) > 4 else ("••••" if v else "")


def check_value(item: Item, value: str) -> str:
    """값 꼴 검사 — 틀리면 까닭(화면에 그대로), 맞으면 「」."""
    v = (value or "").strip()
    if not v:
        return ""
    if item.kind == "url" and not re.match(r"^https?://[^\s/]+", v):
        return "http:// 또는 https:// 로 시작하는 주소를 넣으세요"
    if item.kind == "number" and not re.match(r"^-?\d+(\.\d+)?$", v):
        return "숫자를 넣으세요"
    if item.kind == "choice" and v not in {c[0] for c in item.choices}:
        return "고를 수 있는 값이 아닙니다"
    if item.kind == "ssh" and not re.match(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+$", v):
        return "계정@주소 꼴로 넣으세요"
    if item.kind == "source" and not (v.startswith("/") or re.match(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:", v)):
        return "/로 시작하는 서버 안 폴더 또는 계정@주소:경로 꼴로 넣으세요"
    if item.kind == "source" and ".." in re.split(r"[/:]", v):
        return "경로에 .. 은 쓸 수 없습니다"
    if item.kind == "source" and not re.fullmatch(r"[\w@./:~ ()가-힣·-]+", v):
        # rsync 가 SSH 원본 경로를 원격 셸에 넘긴다 — 셸 기호(; | & $ ` 따옴표 등)는 받지 않는다
        return "경로에는 글자·숫자·공백과 . / : ~ ( ) - 만 씁니다"
    if re.search(r"[\r\n]", v):
        return "한 줄로 넣으세요"
    return ""


def rows(db) -> list[dict]:
    """화면 줄 — 묶음 차례대로. source: screen(화면에서 정함)·file(서버 파일)·default(정하지 않음)."""
    saved = {k[len(PREFIX):]: v for k, v in db.settings_with_prefix(PREFIX).items()}
    out = []
    for it in ITEMS:
        if it.key in saved:
            src, val = "screen", saved[it.key]
        else:
            base = _orig[it.key] if it.key in _orig else os.environ.get(it.key)
            src, val = ("file", base) if base else ("default", "")
        shown = _mask(val) if it.secret else (dict(it.choices).get(val, val) if it.kind == "choice" else val)
        out.append({"key": it.key, "label": it.label, "group": it.group, "hint": it.hint, "kind": it.kind,
                    "secret": it.secret, "choices": list(it.choices), "source": src, "shown": shown or "",
                    "value": "" if it.secret else (val or ""), "set": bool(val), "restart": it.restart})
    return out


def save(db, key: str, value: str) -> str:
    """화면 값 저장 — 「」 이면 저장, 아니면 까닭. 비밀 값을 빈칸으로 내면 그대로 둔다(다시 보이지 않으므로)."""
    it = BY_KEY.get(key)
    if it is None:
        return "알 수 없는 설정입니다"
    v = (value or "").strip()
    if it.secret and not v:
        return ""
    why = check_value(it, v)
    if why:
        return why
    db.set_setting(PREFIX + key, v)
    return ""


def reset(db, key: str) -> None:
    """화면 값을 지워 서버 파일 값(없으면 기본)으로 돌아간다."""
    if key in BY_KEY:
        db.delete_setting(PREFIX + key)


def probe(key: str) -> dict:
    """연결 확인 — 검색 서빙은 /health, 웹 검색은 지금 고른 제공자, 그 밖은 꼴만. {ok, text}."""
    v = os.environ.get(key, "").strip()
    if key in ("ZZAIMY_EMBED_URL", "ZZAIMY_RERANK_URL"):
        if not v:
            return {"ok": True, "text": "앱 서버 CPU 로 계산"}
        from zzaimy.app import search_serving
        got = search_serving._health(v)
        return {"ok": got["ok"], "text": (f"응답함 · {got['model']}" if got["ok"] else f"응답 없음 ({got['detail']})")}
    if key.startswith("ZZAIMY_GOOGLE") or key.startswith("ZZAIMY_NAVER") or key == "ZZAIMY_WEB_SEARCH":
        from zzaimy.app import web_search
        return {"ok": True, "text": f"지금 제공자: {web_search.provider()}"}
    if key == "ZZAIMY_ARCHIVE_SOURCE":
        if not v:
            return {"ok": True, "text": "원본 서버(DGX)에서 받음"}
        if not v.startswith("/"):
            return {"ok": True, "text": "SSH 원본 · 다음 동기화 때 확인(접속 키는 서버의 ~/.ssh)"}
        from pathlib import Path
        p = Path(v)
        if not p.is_dir():
            return {"ok": False, "text": "폴더가 없거나 열 수 없습니다"}
        try:
            n = sum(1 for _ in zip(range(5000), p.rglob("*")))
        except OSError as e:
            return {"ok": False, "text": f"읽지 못함 ({type(e).__name__})"}
        return {"ok": True, "text": f"폴더 확인 · 항목 {n}{'개 넘음' if n >= 5000 else '개'}"}
    return {"ok": True, "text": "꼴 확인됨" if v else "정하지 않음"}
