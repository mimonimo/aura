"""한글 에이전트 배포 번들 조립 (맥에서 실행 — 인터넷 필요).

Windows에서 설치 없이 도는 자급자족 번들을 만든다:
  파이썬 내장 배포판(embeddable, 설치 불필요) + comtypes(순수 파이썬 COM)
  + hwp_agent.py + 시작.bat

산출물: data/dist/hwp-agent-base.zip (git 제외 — 완성 후 VM data/dist/로 scp).
플랫폼의 /dev/hwp/agent.zip 라우트가 이 베이스에 접속 정보(config.json)와
서버 인증서를 심어 개인화 zip으로 내려준다.

실행: .venv/bin/python scripts/71_build_hwp_agent_bundle.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PY_EMBED_URL = (
    "https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip"
)
ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "tools" / "hwp-agent"
OUT = ROOT / "data" / "dist" / "hwp-agent-base.zip"

_BAT = "\r\n".join([
    "@echo off",
    "chcp 65001 >nul",
    "cd /d %~dp0",
    "echo ZZAIMY 한글 에이전트를 시작합니다. 이 창을 닫으면 연결이 끊어집니다.",
    "python\\python.exe hwp_agent.py",
    "pause",
]) + "\r\n"

_README = """ZZAIMY 한글 에이전트

1. 이 압축을 아무 폴더에나 풉니다 (설치 불필요).
2. 한글(한컴오피스)을 실행해 둡니다.
3. 시작.bat 을 더블클릭합니다.

접속 주소·토큰·서버 인증서는 config.json 에 이미 들어 있습니다
(플랫폼에서 내려받을 때 자동으로 채워짐). 토큰을 재발급했다면
플랫폼에서 zip을 다시 내려받으세요.
"""


def main() -> int:
    work = ROOT / "data" / "dist" / "_bundle_work"
    if work.exists():
        shutil.rmtree(work)
    (work / "python").mkdir(parents=True)

    print("1/4 파이썬 내장 배포판 내려받기")
    # urllib 대신 curl — 맥 파이썬의 CA 번들 부재를 우회 (시스템 CA 사용)
    embed_zip = work / "_embed.zip"
    subprocess.run(
        ["curl", "-fsSL", "-o", str(embed_zip), PY_EMBED_URL], check=True
    )
    with zipfile.ZipFile(embed_zip) as z:
        z.extractall(work / "python")
    embed_zip.unlink()
    # 격리 경로에 번들 루트(..)를 추가 — hwp_agent.py·comtypes를 찾게 한다
    pth = next((work / "python").glob("python*._pth"))
    pth.write_text(pth.read_text() + "..\n", encoding="ascii")

    print("2/4 comtypes(순수 파이썬 COM) 내려받기")
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "comtypes", "--no-deps",
         "-d", str(work / "_wheels"), "--quiet"],
        check=True,
    )
    wheel = next((work / "_wheels").glob("comtypes-*.whl"))
    with zipfile.ZipFile(wheel) as z:
        for name in z.namelist():
            if name.startswith("comtypes/"):
                z.extract(name, work)
    shutil.rmtree(work / "_wheels")

    print("3/4 에이전트 파일 복사")
    for f in ("hwp_agent.py", "protocol.md"):
        shutil.copy2(AGENT_DIR / f, work / f)
    (work / "시작.bat").write_bytes(_BAT.encode("utf-8"))
    (work / "README.txt").write_text(_README, encoding="utf-8")

    print("4/4 베이스 zip 조립")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(work.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(work).as_posix())
    shutil.rmtree(work)
    print(f"완료: {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")
    print("VM 반영: scp 로 서버 data/dist/ 에 올리면 /dev/hwp 에 다운로드가 뜬다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
