"""조직·계정 저장값을 쓰는 경로의 공용 잠금과 쓰기 기록.

계정 표(accounts.json), RAG 공간 설정(rag_spaces.json), 확정 위계(dept_parents.json), 사업 관리 저장값(program_admin.json)은
여러 화면이 읽고-고치고-쓴다. 앱은 한 프로세스라 모듈 전역 RLock 하나로 검사부터 쓰기·되돌리기까지를 한 번에 하나씩 돌린다
(조직도 org_routes, 사용자·계정 user_admin 의 계정 저장·부서 만들기/지우기·상위 부서·부서 사람 저장, program_admin.save_store).

쓰기 기록(journal): 잠금 안에서 파일을 쓴 함수는 note() 로 「쓴 직후 내용 해시」를 남긴다. 조직도의 되돌리기는 이 해시와
지금 내용이 같은 파일만 되돌린다 — 그 사이 다른 쪽이 쓴 파일은 덮지 않는다.
"""
from __future__ import annotations

import functools
import hashlib
import threading
from pathlib import Path

LOCK = threading.RLock()
_local = threading.local()


def serialized(fn):
    """라우트 함수 전체를 잠금 안에서 — FastAPI 는 inspect.signature 로 __wrapped__ 를 따라가 인자를 그대로 읽는다."""
    @functools.wraps(fn)
    def run(*a, **k):
        with LOCK:
            return fn(*a, **k)
    return run


def digest(path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def start_journal() -> dict:
    j: dict[str, str | None] = {}
    _local.journal = j
    return j


def stop_journal() -> None:
    _local.journal = None


def note(path) -> None:
    """방금 쓴 파일의 내용 해시를 지금 기록 중인 쓰기 묶음에 남긴다(기록 중이 아니면 아무것도 안 함)."""
    j = getattr(_local, "journal", None)
    if j is not None:
        j[str(Path(path).resolve())] = digest(path)


def save_accounts(st) -> None:
    """계정 표 저장 + 쓰기 기록 — 저장 함수(main.py)는 그대로 두고 여기서 감싼다."""
    with LOCK:
        st.save_accounts()
        dd = getattr(st, "data_dir", None)
        if dd is not None:
            note(Path(dd) / "accounts.json")
