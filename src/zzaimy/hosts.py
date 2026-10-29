"""장비 접속 정보(주소·계정·포트) 읽기 — 공개 저장소에 값을 두지 않는다.

환경 변수(.env.local 로 들어온 것 포함)가 먼저이고, 없으면 저장소 밖 파일 configs/hosts.env 를 본다.
형식은 configs/hosts.env.example. 셸 쪽은 scripts/_hosts.sh 가 같은 규칙으로 읽는다.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOSTS_FILE = ROOT / "configs" / "hosts.env"
LEGACY = {"ZZAIMY_THOR02_SSH": "THOR02_SSH", "ZZAIMY_THOR03_SSH": "THOR03_SSH"}


def _file_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def get(name: str, default: str = "", *, path: Path | None = None) -> str:
    """접속 정보 한 값. 없으면 default."""
    value = os.environ.get(name) or (os.environ.get(LEGACY[name], "") if name in LEGACY else "")
    if value:
        return value
    return _file_values(path or HOSTS_FILE).get(name) or default


def require(name: str, *, path: Path | None = None) -> str:
    """꼭 있어야 하는 값. 없으면 어디에 넣을지 알려 주는 오류."""
    value = get(name, path=path)
    if not value:
        raise SystemExit(f"접속 정보 {name} 가 없습니다. configs/hosts.env 에 {name}=계정@주소 를 넣으십시오"
                         "(형식: configs/hosts.env.example).")
    return value


def ssh_port_args(name: str, *, path: Path | None = None) -> list[str]:
    """ssh 포트 인자. 값이 없으면 빈 목록(ssh 기본값·~/.ssh/config 를 따른다)."""
    port = get(name, path=path)
    return ["-p", port] if port else []
