#!/usr/bin/env bash
# NAS(SMB) 수집용 smbprotocol 오프라인 설치 — Mac 에서 받은 휠을 서버 data/tmp/wheels 에 올린 뒤 실행한다.
#   Mac:    .venv/bin/pip download smbprotocol pyspnego --no-deps -d data/tmp/wheels
#           scp data/tmp/wheels/{smbprotocol,pyspnego}-*.whl aura@서버:~/zzaimy-capstone/data/tmp/wheels/
#   서버:   bash scripts/80_install_smb.sh
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/pip install --no-index --no-deps --find-links data/tmp/wheels pyspnego smbprotocol
.venv/bin/python -c "import smbclient; from importlib.metadata import version; print('smbprotocol', version('smbprotocol'))"
