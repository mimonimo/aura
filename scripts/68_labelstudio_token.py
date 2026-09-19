"""Label Studio 고정 토큰 프로비저닝 보조 — scripts/68_labelstudio_token.sh 가 부른다.

ls-venv 의 python 으로 실행한다(label-studio·Django 가 거기 있다). 토큰은 인자가
아니라 환경변수 LS_TOKEN 으로 받고(ps 에 노출되지 않게), 어떤 하위 명령도 토큰을
화면에 찍지 않는다. unit-get 만 토큰을 stdout 으로 내보내는데, 셸이 변수로 바로
받는 용도다 — 터미널에서 직접 실행하지 않는다.

하위 명령:
  unit-port <unit>   ExecStart 의 --port 값(없으면 8080)
  unit-get  <unit>   유닛에 기록된 LABEL_STUDIO_USER_TOKEN(없으면 빈 줄)
  unit-set  <unit>   LABEL_STUDIO_USER_TOKEN=$LS_TOKEN 과
                     LABEL_STUDIO_ENABLE_LEGACY_API_TOKEN=true 를 Environment= 로 기록.
                     바뀌면 'changed', 이미 같으면 'unchanged' 출력
  apply     <unit>   유닛과 같은 환경으로 Django 를 띄워 부트스트랩 사용자의 API 토큰을
                     LS_TOKEN 으로 맞추고, 조직의 레거시 토큰 인증을 켠다
  user-get  <unit>   부트스트랩 사용자(아이디·이메일)를 출력 — 화면 표시용
  set-password <unit>  환경변수 LS_PASSWORD 로 받은 새 비밀번호를 그 사용자에게 설정하고
                     유닛의 LABEL_STUDIO_PASSWORD 도 같이 갱신(새 DB 부트스트랩용).
                     비밀번호는 어디에도 찍지 않는다

왜 ORM 까지 가나 — label-studio 1.23.0 설치본 소스로 확인한 사실:
  - --user-token / LABEL_STUDIO_USER_TOKEN 은 사용자를 처음 만들 때만 적용된다
    (server.py _create_user: create_user 성공 직후에만 Token 을 바꾼다). 이미 있는
    사용자에게는 'already exists' 만 찍고 무시한다.
  - 1.17 이후 `Authorization: Token …`(레거시) 인증은 조직 설정
    legacy_api_tokens_enabled 가 꺼져 있으면 401 이다(jwt_auth/auth.py). 새 조직의
    기본값은 False.
  그래서 유닛의 env 는 DB 를 새로 만들 때를 위한 보험이고, 지금 있는 사용자·조직은
  apply 가 직접 맞춘다. 다른 버전은 미확인 — import 가 실패하면 그대로 알리고 멈춘다.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

ENV_RE = re.compile(r"^\s*Environment\s*=\s*(.*?)\s*$")
ENVFILE_RE = re.compile(r"^\s*EnvironmentFile\s*=\s*-?(.*?)\s*$")
EXEC_RE = re.compile(r"^\s*ExecStart\s*=\s*(.*?)\s*$")
TOKEN_KEY = "LABEL_STUDIO_USER_TOKEN"
LEGACY_KEY = "LABEL_STUDIO_ENABLE_LEGACY_API_TOKEN"
DEFAULT_USERNAME = "default_user@localhost"  # label-studio 의 기본 계정명

# ExecStart 옵션 중 우리가 읽는 것 (label-studio 1.23 argparser 기준)
_FLAGS = {
    "--port": "port", "-p": "port",
    "--data-dir": "data_dir", "--database": "database",
    "--username": "username", "--user-token": "user_token",
}


def _pairs(spec: str) -> list[tuple[str, str]]:
    """Environment= 뒤의 'K=V' "K=V" 묶음 → [(K, V)]. 따옴표는 systemd 처럼 벗긴다."""
    out = []
    for item in shlex.split(spec):
        if "=" in item:
            k, v = item.split("=", 1)
            out.append((k, v))
    return out


def unit_env(text: str, unit_path: Path | None = None) -> dict[str, str]:
    """유닛의 Environment=·EnvironmentFile= 을 읽어 환경 사전으로."""
    env: dict[str, str] = {}
    for line in text.splitlines():
        m = ENVFILE_RE.match(line)
        if m:
            p = Path(os.path.expanduser(m.group(1)))
            if not p.is_absolute() and unit_path is not None:
                p = unit_path.parent / p
            try:
                raw_lines = p.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for raw in raw_lines:
                raw = raw.strip()
                if raw and not raw.startswith("#") and "=" in raw:
                    k, v = raw.split("=", 1)
                    env[k.strip()] = v.strip().strip("'\"")
            continue
        m = ENV_RE.match(line)
        if m:
            for k, v in _pairs(m.group(1)):
                env[k] = v
    return env


def exec_flags(text: str) -> dict[str, str]:
    """ExecStart 의 label-studio 옵션(--port·--data-dir·--database·--username·--user-token)."""
    flags: dict[str, str] = {}
    for line in text.splitlines():
        m = EXEC_RE.match(line)
        if not m:
            continue
        argv = shlex.split(m.group(1))
        i = 0
        while i < len(argv):
            a = argv[i]
            name, eq, val = a.partition("=")
            if eq and name in _FLAGS:
                flags[_FLAGS[name]] = val
            elif a in _FLAGS and i + 1 < len(argv):
                flags[_FLAGS[a]] = argv[i + 1]
                i += 1
            i += 1
    return flags


def _token_from_env() -> str:
    token = os.environ.get("LS_TOKEN", "").strip()
    # DRF Token 키는 40자 이하, label-studio 는 6자 이상만 받는다
    if not (6 <= len(token) <= 40) or not re.fullmatch(r"[A-Za-z0-9._-]+", token):
        sys.exit("FAIL: LS_TOKEN 이 없거나 형식이 맞지 않습니다 (6~40자 영숫자)")
    return token


def _write_private(path: Path, text: str) -> None:
    """같은 디렉터리에 임시 파일로 쓰고 바꿔치기 — 비밀이 든 파일이라 0600."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def cmd_unit_port(unit: Path) -> int:
    print(exec_flags(unit.read_text(encoding="utf-8")).get("port") or "8080")
    return 0


def cmd_unit_get(unit: Path) -> int:
    text = unit.read_text(encoding="utf-8")
    print(unit_env(text, unit).get(TOKEN_KEY) or exec_flags(text).get("user_token") or "")
    return 0


def cmd_unit_set(unit: Path) -> int:
    token = _token_from_env()
    want = {TOKEN_KEY: token, LEGACY_KEY: "true"}
    text = unit.read_text(encoding="utf-8")
    out: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines(keepends=True):
        if ENV_RE.match(line):
            # 있는 줄은 값만 바꾼다 — 같은 줄의 다른 변수(비밀번호 등)는 손대지 않는다
            for key, value in want.items():
                pat = re.compile(rf"(?<![\w-]){re.escape(key)}=[^\s\"']*")
                if pat.search(line):
                    line = pat.sub(f"{key}={value}", line, count=1)
                    seen.add(key)
        out.append(line)
    missing = [k for k in want if k not in seen]
    if missing:
        # 없는 변수는 ExecStart 바로 앞에 끼워 넣는다 (같은 [Service] 절)
        idx = next((i for i, ln in enumerate(out) if EXEC_RE.match(ln)), len(out))
        if idx > 0 and not out[idx - 1].endswith("\n"):
            out[idx - 1] += "\n"
        for key in missing:
            out.insert(idx, f"Environment={key}={want[key]}\n")
            idx += 1
    new = "".join(out)
    if new == text:
        print("unchanged")
        return 0
    _write_private(unit, new)
    print("changed")
    return 0


def _unit_username(unit: Path) -> str:
    """유닛(ExecStart --username 또는 LABEL_STUDIO_USERNAME/HEARTEX_USERNAME)의 부트스트랩 계정."""
    text = unit.read_text(encoding="utf-8")
    env = unit_env(text, unit)
    return (exec_flags(text).get("username") or env.get("LABEL_STUDIO_USERNAME")
            or env.get("HEARTEX_USERNAME") or DEFAULT_USERNAME)


def _boot_django(unit: Path):
    """서비스와 같은 환경으로 Label Studio 의 Django 를 띄우고 부트스트랩 사용자를 돌려준다.

    반환 (user, username, models) — 실패하면 FAIL 메시지를 찍고 None. 데이터 디렉터리·
    DB 경로가 다르면 엉뚱한 DB 를 만지므로 유닛의 값을 그대로 쓴다.
    """
    text = unit.read_text(encoding="utf-8")
    flags = exec_flags(text)
    for k, v in unit_env(text, unit).items():
        os.environ.setdefault(k, v)
    if flags.get("data_dir"):
        os.environ.setdefault(
            "LABEL_STUDIO_BASE_DATA_DIR",
            str(Path(flags["data_dir"]).expanduser().absolute()))
    if flags.get("database"):
        os.environ.setdefault(
            "DATABASE_NAME", str(Path(flags["database"]).expanduser().absolute()))
    username = _unit_username(unit)

    try:
        import label_studio
    except ImportError:
        print("FAIL: 이 python 에 label_studio 가 없습니다 — ~/ls-venv/bin/python 으로 실행")
        return None
    # label_studio/server.py _setup_env 와 같은 부팅 순서. LOG_LEVEL 은 CLI 기본값(WARNING)과
    # 맞춘다 — 없으면 DEBUG 로 떠들어 작업 결과가 묻힌다
    sys.path.insert(0, str(Path(label_studio.__file__).parent.absolute()))
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    # 초기화 중 PyPI 최신판 확인(10초 제한)을 끈다 — VM 은 아웃바운드가 막혀 있어 기다릴 이유가 없다
    os.environ.setdefault("LABEL_STUDIO_LATEST_VERSION_CHECK", "false")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "label_studio.core.settings.label_studio")
    # 초기화 중 Label Studio 앱이 내는 RuntimeWarning(APPS_NOT_READY 등)은 결과와 무관하다 — 표준 오류를 비운다
    import warnings

    warnings.filterwarnings("ignore", category=RuntimeWarning)
    try:
        from django.core.wsgi import get_wsgi_application

        get_wsgi_application()
        from django.db import IntegrityError, transaction
        from rest_framework.authtoken.models import Token
        from users.models import User
    except Exception as e:  # 버전 차이는 미확인 — 있는 그대로 알린다
        print(f"FAIL: Label Studio Django 초기화 실패 ({type(e).__name__}: {e})")
        return None

    user = User.objects.filter(email=username).first()
    if user is None:
        print(f"FAIL: Label Studio 사용자 {username} 이(가) 없습니다 — "
              "서비스가 한 번 기동해 계정을 만든 뒤 다시 실행")
        return None
    return user, username, {"IntegrityError": IntegrityError, "transaction": transaction,
                            "Token": Token}


def cmd_user_get(unit: Path) -> int:
    print(_unit_username(unit))
    return 0


def cmd_set_password(unit: Path) -> int:
    pw = os.environ.get("LS_PASSWORD", "")
    if len(pw) < 8:
        print("FAIL: LS_PASSWORD 가 없거나 8자 미만")
        return 2
    booted = _boot_django(unit)
    if booted is None:
        return 2
    user, username, _m = booted
    user.set_password(pw)          # Django 해시 저장 — 평문은 어디에도 남지 않는다
    user.save(update_fields=["password"])
    # 새 DB 를 부트스트랩할 때 쓰는 유닛 값도 맞춘다(있을 때만; 값은 찍지 않는다)
    text = unit.read_text(encoding="utf-8")
    pat = re.compile(r"(?<![\w-])LABEL_STUDIO_PASSWORD=[^\s\"']*")
    if pat.search(text):
        _write_private(unit, pat.sub("LABEL_STUDIO_PASSWORD=" + shlex.quote(pw), text, count=1))
        unit_note = "유닛 갱신"
    else:
        unit_note = "유닛에 비밀번호 항목 없음(생략)"
    print(f"PASS: Label Studio 사용자 {username} 비밀번호 재설정, {unit_note}")
    return 0


def cmd_apply(unit: Path) -> int:
    token = _token_from_env()
    booted = _boot_django(unit)
    if booted is None:
        return 2
    user, username, m = booted
    IntegrityError, transaction, Token = m["IntegrityError"], m["transaction"], m["Token"]
    try:
        with transaction.atomic():
            cur = Token.objects.filter(user=user).first()
            if cur is None:
                Token.objects.create(user=user, key=token)
                state = "발급"
            elif cur.key != token:
                # 키가 기본키라 save() 는 새 행을 만든다 — update() 로 제자리 갱신
                Token.objects.filter(user=user).update(key=token)
                state = "갱신"
            else:
                state = "유지"
    except IntegrityError:
        print("FAIL: 같은 토큰을 가진 다른 사용자가 있습니다 — 유닛의 "
              f"{TOKEN_KEY} 줄을 지우고 다시 실행하면 새 토큰을 만듭니다")
        return 2

    # 레거시 토큰 인증 허용 (1.17+ 조직 설정). 그 설정이 없는 버전이면 건너뛴다.
    try:
        from organizations.models import Organization

        n = 0
        for org in Organization.objects.all():
            jwt = getattr(org, "jwt", None)
            if jwt is not None and getattr(jwt, "legacy_api_tokens_enabled", True) is False:
                jwt.legacy_api_tokens_enabled = True
                jwt.save()
                n += 1
        legacy = f"조직 {n}건 켬"
    except Exception as e:
        legacy = f"생략 ({type(e).__name__})"
    print(f"Label Studio 사용자 {username}: 토큰 {state}, 레거시 토큰 인증 {legacy}")
    return 0


COMMANDS = {
    "unit-port": cmd_unit_port,
    "unit-get": cmd_unit_get,
    "unit-set": cmd_unit_set,
    "apply": cmd_apply,
    "user-get": cmd_user_get,
    "set-password": cmd_set_password,
}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in COMMANDS:
        print(__doc__)
        return 2
    unit = Path(argv[1]).expanduser()
    if not unit.is_file():
        print(f"FAIL: 유닛 파일이 없습니다: {unit}")
        return 2
    return COMMANDS[argv[0]](unit)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
