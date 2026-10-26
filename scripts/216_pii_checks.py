"""개인정보 점검 자동 실행 — 매일 새벽 정리(zz_reconcile.sh)에서 부른다.

- 마스커 자가 점검(합성 표본, 몇 초): 매일.
- 저장 본문 잔여 검사(등록 문서 본문·조각 전부, 몇 시간): 일요일에만 — 그날 새벽 정리 뒤 혼자 돈다(VM 메모리, 무거운 작업은 하나씩).
결과는 지금처럼 설정 표에 남고 개인정보·반출 점검 화면이 읽는다. 화면에서 돌리던 단추는 뺐다(10-26 — 자동으로 도는 일에 수동 단추를 두지 않는다).
  PYTHONPATH=src python scripts/216_pii_checks.py [--scan 강제]
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    from zzaimy.app import pii_audit
    from zzaimy.app.db import Database
    db = Database(Path(os.environ.get("ZZAIMY_PLATFORM_SQLITE_PATH") or ROOT / "data/platform/platform.db"))
    pii_audit.run_selftest(db)
    print("PII_SELFTEST done")
    if "--scan" in sys.argv or dt.date.today().isoweekday() == 7:
        pii_audit.run_scan(db)
        print("PII_SCAN done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
