"""장비 접속 정보는 환경 변수 → configs/hosts.env 순으로 읽고, 저장소에는 값이 없다."""
import re
import subprocess
from pathlib import Path

import pytest

from zzaimy import hosts

ROOT = Path(__file__).resolve().parents[1]
KEYS = ("ZZAIMY_VM_SSH", "ZZAIMY_THOR02_SSH", "ZZAIMY_THOR03_SSH", "THOR02_SSH", "THOR03_SSH",
        "ZZAIMY_THOR_SSH_PORT", "ZZAIMY_DGX_SSH", "ZZAIMY_DGX_SSH_PORT")


@pytest.fixture
def clean_env(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_file_then_env_precedence(clean_env, tmp_path):
    f = tmp_path / "hosts.env"
    f.write_text("# 주석\nZZAIMY_VM_SSH=ops@192.0.2.10\nZZAIMY_DGX_SSH_PORT='2222'\n\nBROKEN LINE\n")
    assert hosts.get("ZZAIMY_VM_SSH", path=f) == "ops@192.0.2.10"
    assert hosts.ssh_port_args("ZZAIMY_DGX_SSH_PORT", path=f) == ["-p", "2222"]
    clean_env.setenv("ZZAIMY_VM_SSH", "ops@vm.example.test")
    assert hosts.get("ZZAIMY_VM_SSH", path=f) == "ops@vm.example.test"


def test_legacy_names_and_missing_values(clean_env, tmp_path):
    missing = tmp_path / "none.env"
    clean_env.setenv("THOR03_SSH", "gpu@192.0.2.33")
    assert hosts.get("ZZAIMY_THOR03_SSH", path=missing) == "gpu@192.0.2.33"
    assert hosts.ssh_port_args("ZZAIMY_THOR_SSH_PORT", path=missing) == []
    with pytest.raises(SystemExit, match="configs/hosts.env"):
        hosts.require("ZZAIMY_DGX_SSH", path=missing)


def test_example_has_placeholders_only_and_real_file_is_ignored():
    example = (ROOT / "configs" / "hosts.env.example").read_text()
    assert not re.search(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", example)
    for key in ("ZZAIMY_VM_SSH", "ZZAIMY_THOR02_SSH", "ZZAIMY_THOR03_SSH", "ZZAIMY_DGX_SSH"):
        assert f"{key}=user@" in example
    assert "configs/hosts.env" in (ROOT / ".gitignore").read_text().splitlines()


def test_shell_loader_stops_with_guidance(tmp_path):
    script = tmp_path / "s.sh"
    script.write_text(f'. "{ROOT}/scripts/_hosts.sh"\nneed_host ZZAIMY_NOT_SET_FOR_TEST\necho reached\n')
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert r.returncode == 2 and "reached" not in r.stdout
    assert "configs/hosts.env" in r.stderr
