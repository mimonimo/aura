"""앱이 띄우는 긴 배경 작업(재측정·재색인·반입 점검)을 서비스와 떼어 놓는다.

앱은 systemd 사용자 서비스로 돈다. 자식 프로세스는 서비스 묶음(cgroup)에 들어가 배포 때 서비스를 다시 켜면 함께 꺼진다
(10-18 규정 검색 재측정이 29,274건 중 4,900건에서 조용히 끊겼다). systemd-run --user --scope 로 띄우면 따로 묶여 살아남는다.
systemd 밖(시험·개발)에서는 명령을 그대로 돌린다. 프로세스 번호는 그대로라 기존 실행 중 확인(pid)도 그대로 쓴다.
"""
from __future__ import annotations

import os
import shutil
import time


def detached(cmd: list[str], name: str) -> list[str]:
    if os.environ.get("INVOCATION_ID") and os.environ.get("XDG_RUNTIME_DIR") and shutil.which("systemd-run"):
        unit = f"zzaimy-{name}-{time.strftime('%Y%m%d-%H%M%S')}"
        return ["systemd-run", "--user", "--scope", "--quiet", "--collect", f"--unit={unit}", *cmd]
    return cmd
