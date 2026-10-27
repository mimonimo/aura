"""사람 이름 한 곳 — 화면에 사람(계정)을 보일 때 쓰는 이름.

순서: 계정 장부(accounts.json)의 이름 → 내 설정의 성명(DB 프로필, 장부에 이름이 없는 계정만) → 계정 아이디.
사이드바·사용자 메뉴·설정 머리·사람 칩·편집 잠금 안내처럼 사람을 보이는 곳은 이 함수 하나를 쓴다
(템플릿은 전역 person_name(uid), 라우터는 request.app.state.person_name(uid)).

장부 이름은 관리자가 정한다(조직 관리 /team 계정 목록). 장부에 이름이 있으면 본인이 설정에서 바꿀 수 없다 — name_locked.
"""

from __future__ import annotations


def account_name(uid: str | None, accounts: dict | None) -> str:
    """계정 장부의 이름(관리자가 정한 이름) — 없으면 빈 값."""
    if not uid or not accounts:
        return ""
    return " ".join(str((accounts.get(uid) or {}).get("name") or "").split())


def name_locked(uid: str | None, accounts: dict | None) -> bool:
    """설정의 성명을 본인이 고칠 수 없는가 — 장부에 관리자가 정한 이름이 있으면 그 이름을 쓴다."""
    return bool(account_name(uid, accounts))


def display_name(uid: str | None, accounts: dict | None = None, db=None, *, fallback: str | None = None) -> str:
    """사람을 보일 이름 — 장부 이름 → DB 프로필 성명 → 아이디(fallback 을 주면 그 값)."""
    name = account_name(uid, accounts)
    if not name and db is not None and uid:
        try:
            # 그 계정 몫만 — profile_for 는 계정 값이 없으면 플랫폼 공통 값을 주어 남의 이름처럼 보일 수 있다
            name = " ".join(str(db.get_setting(f"user:{uid}:name", "") or "").split())
        except Exception:                                # 프로필을 못 읽어도 화면은 그린다
            name = ""
    if name:
        return name
    return (uid or "") if fallback is None else fallback
