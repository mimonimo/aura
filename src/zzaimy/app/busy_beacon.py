"""진행 중인 모델 호출 표시 — 배포 스크립트(99_deploy.sh --restart)가 재시작 전에 읽고, 담당자 요청이 끝날 때까지 기다린다.

11-10: 배포 재시작이 문서 작성 도중의 요청 세 건을 끊어 담당자에게는 「서버가 죽은」 것처럼 보였다.
HTTP 주소로 내면 인증 밖 길이 생기므로, 앱이 몇 초마다 데이터 폴더에 작은 파일(.busy.json)로 남긴다(운영 VM 안에서만 읽음)."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_started = {"on": False}


def snapshot(last: float) -> dict:
    from zzaimy.generate import llm_gate
    run, wait = llm_gate.running(), llm_gate.waiting()
    now = time.time()
    if run or wait:
        last = now
    return {"running": run, "waiting": wait, "last_active": last, "at": now}


def start(data_dir: Path, every: float = 3.0) -> None:
    """한 프로세스에 한 번 — 데몬 스레드가 every 초마다 파일을 바꿔 쓴다(쓰기는 임시 파일 뒤 이름 바꾸기)."""
    if _started["on"] or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    _started["on"] = True
    path = Path(data_dir) / ".busy.json"

    def loop() -> None:
        last = 0.0
        while True:
            try:
                s = snapshot(last)
                last = s["last_active"]
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(s), encoding="utf-8")
                tmp.replace(path)
            except Exception:
                pass
            time.sleep(every)
    threading.Thread(target=loop, name="busy-beacon", daemon=True).start()
