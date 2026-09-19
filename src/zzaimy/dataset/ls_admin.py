"""Label Studio 계정 관리 — 화면(/dev/train)에서 비밀번호를 재설정한다.

Label Studio 는 비밀번호를 해시로만 저장하므로 '조회'는 불가능하고 재설정만 된다.
실제 변경은 scripts/68_labelstudio_token.py 를 ls-venv 의 python 으로 실행해 Django
ORM 으로 한다(값은 환경변수로만 전달 — 인자·로그·화면에 남지 않는다).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

HELPER = Path(__file__).resolve().parents[3] / "scripts" / "68_labelstudio_token.py"


def unit_path() -> Path:
    return Path(os.environ.get(
        "ZZAIMY_LS_UNIT", Path.home() / ".config/systemd/user/label-studio.service"))


def ls_python() -> Path:
    return Path(os.environ.get("ZZAIMY_LS_VENV", Path.home() / "ls-venv")) / "bin" / "python"


def available() -> bool:
    """이 서버에 Label Studio 프로비저닝(유닛·ls-venv·헬퍼)이 있는가."""
    return unit_path().is_file() and ls_python().is_file() and HELPER.is_file()


def set_password(new_pw: str) -> tuple[bool, str]:
    """비밀번호 재설정. 반환 (성공 여부, 사람이 읽을 한 줄)."""
    if not available():
        return False, "이 서버에는 Label Studio 프로비저닝(유닛·ls-venv)이 없습니다"
    try:
        r = subprocess.run(
            [str(ls_python()), str(HELPER), "set-password", str(unit_path())],
            env={**os.environ, "LS_PASSWORD": new_pw},
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, "Label Studio 응답 없음(시간 초과)"
    # 결과 판정은 표준 출력의 마지막 줄(PASS/FAIL 규약)로만 한다. 표준 오류에는 Label Studio 의
    # Django 가 초기화 중 찍는 경고(APPS_NOT_READY RuntimeWarning 등)가 섞이므로 실패 사유로만 쓴다.
    out = [ln for ln in r.stdout.splitlines() if ln.strip()]
    last = out[-1] if out else ""
    if r.returncode != 0 or not last.startswith("PASS"):
        err = [ln.strip() for ln in r.stderr.splitlines()
               if ln.strip() and "warn" not in ln.lower()]
        detail = last if last.startswith("FAIL") else (err[-1] if err else last)
        return False, (detail or f"헬퍼 종료 코드 {r.returncode}")[:160]
    # 유닛 파일이 바뀌었을 수 있다 — 다음 재시작을 위해 반영(실패해도 비밀번호는 이미 바뀜)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=30)
    return True, last[:160]
