"""배포 재시작이 진행 중인 요청을 끊지 않게 — 앱이 모델 호출 수를 파일로 남기고 99_deploy.sh 가 읽어 기다린다(11-10)."""
from pathlib import Path

from zzaimy.app import busy_beacon
from zzaimy.generate import llm_gate


def test_snapshot_counts_running_calls_and_keeps_last_active():
    s = busy_beacon.snapshot(0.0)
    assert s["running"] == 0 and s["last_active"] == 0.0
    with llm_gate.slot():
        s = busy_beacon.snapshot(0.0)
        assert s["running"] == 1 and s["last_active"] == s["at"]
    assert busy_beacon.snapshot(123.0)["last_active"] == 123.0      # 끝난 뒤에는 마지막 호출 시각을 그대로


def test_deploy_waits_for_busy_file_before_restart():
    sh = (Path(__file__).resolve().parents[1] / "scripts/99_deploy.sh").read_text(encoding="utf-8")
    assert sh.index(".busy.json") < sh.index("systemctl --user restart zzaimy.service")
    assert "DEPLOY_WAIT_MAX" in sh and "FORCE" in sh
