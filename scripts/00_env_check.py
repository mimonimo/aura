"""DGX Spark 환경 점검 스크립트 (W1-W2 TASK-01).

OS·GPU·메모리·파이썬 환경을 수집해 마크다운 표로 출력한다.
확인할 수 없는 값은 지어내지 않고 "미확인"으로 표기한다 (브리프 0장).

사용:
    python scripts/00_env_check.py > docs/env-report.md
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone

UNKNOWN = "미확인"


def run(cmd: list[str]) -> str | None:
    """명령을 실행해 stdout을 돌려준다. 실패하면 None."""
    if shutil.which(cmd[0]) is None:
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def meminfo() -> dict[str, str]:
    """/proc/meminfo에서 총량/가용량(GiB)을 읽는다. 리눅스가 아니면 미확인."""
    result = {"total": UNKNOWN, "available": UNKNOWN}
    try:
        with open("/proc/meminfo") as f:
            lines = dict(
                (parts[0].rstrip(":"), parts[1])
                for parts in (line.split() for line in f)
                if len(parts) >= 2
            )
        for key, name in [("MemTotal", "total"), ("MemAvailable", "available")]:
            if key in lines:
                result[name] = f"{int(lines[key]) / 1024 / 1024:.1f} GiB"
    except OSError:
        pass
    return result


def nvidia_query(fields: str) -> str | None:
    return run(["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader"])


def main() -> None:
    mem = meminfo()
    driver_cuda = nvidia_query("driver_version,name") or UNKNOWN
    compute_cap = nvidia_query("compute_cap") or UNKNOWN
    smi_full = run(["nvidia-smi"])
    # CUDA 런타임 버전은 nvidia-smi 헤더에서만 확인 가능
    cuda_version = UNKNOWN
    if smi_full:
        for token in smi_full.splitlines():
            if "CUDA Version" in token:
                cuda_version = token.split("CUDA Version:")[1].strip(" |").strip()
                break
    nvcc = run(["nvcc", "--version"])
    nvcc_line = nvcc.splitlines()[-1].strip() if nvcc else f"{UNKNOWN} (nvcc 없음)"
    pip_version = run([sys.executable, "-m", "pip", "--version"]) or UNKNOWN

    rows = [
        ("점검 시각", datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")),
        ("호스트", platform.node()),
        ("OS", f"{platform.system()} {platform.release()}"),
        ("아키텍처", platform.machine()),
        ("커널", platform.version()),
        ("NVIDIA 드라이버 / GPU", driver_cuda),
        ("CUDA 버전 (nvidia-smi)", cuda_version),
        ("nvcc", nvcc_line),
        ("GPU compute capability", compute_cap),
        ("메모리 총량", mem["total"]),
        ("메모리 가용량", mem["available"]),
        ("Python", f"{platform.python_version()} ({sys.executable})"),
        ("pip", pip_version),
    ]

    print("# 환경 점검 리포트 (DGX Spark)")
    print()
    print("> `scripts/00_env_check.py` 자동 생성 — W1-W2 TASK-01")
    print()
    print("| 항목 | 값 |")
    print("|---|---|")
    for name, value in rows:
        print(f"| {name} | {value} |")
    print()
    if smi_full:
        print("## nvidia-smi 전체 출력")
        print()
        print("```")
        print(smi_full)
        print("```")
    else:
        print(f"nvidia-smi: {UNKNOWN} (실행 불가)")


if __name__ == "__main__":
    main()
