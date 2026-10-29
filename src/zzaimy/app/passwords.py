"""비밀번호 규칙 한 곳 — 본인 변경(/account/password)·관리자 임시 비밀번호(조직 관리 /team)·화면 실시간 체크가 같은 값을 쓴다.

화면(settings.html·_user_edit.html)은 `password_rules` 전역으로 같은 값을 받아 입력하는 동안 체크 표시만 하고,
받아들일지는 언제나 서버가 problem() 으로 정한다. 비밀번호 값은 돌려주는 글·로그에 넣지 않는다.
"""

from __future__ import annotations

import hashlib
import secrets

MIN_LENGTH = 8
HASH_ROUNDS = 200_000


def hash_pw(pw: str, salt: bytes | None = None) -> tuple[str, str]:
    """비밀번호 해시(pbkdf2-sha256, 개별 salt) — (해시 hex, salt hex). 앱 로그인과 계정 만들기 스크립트(218)가 같은 함수를 쓴다."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, HASH_ROUNDS)
    return digest.hex(), salt.hex()


def problem(new_pw: str, current_pw: str | None = None) -> str:
    """규칙에 어긋나면 사람이 읽을 까닭 한 줄, 맞으면 빈 문자열."""
    if len(new_pw or "") < MIN_LENGTH:
        return f"새 비밀번호는 {MIN_LENGTH}자 이상이어야 합니다"
    if current_pw is not None and new_pw == current_pw:
        return "새 비밀번호가 현재 비밀번호와 같습니다. 다른 비밀번호를 정해 주세요"
    return ""


def install(env) -> None:
    """화면 규칙 표시용 값 — 서버 규칙과 같은 숫자를 쓰게 Jinja 전역으로 단다."""
    env.globals["password_rules"] = {"min_length": MIN_LENGTH}


__all__ = ["MIN_LENGTH", "hash_pw", "install", "problem"]
