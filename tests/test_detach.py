"""배경 작업 떼어 놓기 — systemd 서비스 안에서만 scope 로 감싼다."""
from zzaimy.app import detach


def test_plain_outside_systemd(monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    assert detach.detached(["echo", "x"], "t") == ["echo", "x"]


def test_scope_inside_systemd(monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "abc")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setattr(detach.shutil, "which", lambda n: "/usr/bin/systemd-run")
    out = detach.detached(["echo", "x"], "eval")
    assert out[:4] == ["systemd-run", "--user", "--scope", "--quiet"] and out[-2:] == ["echo", "x"] and any(a.startswith("--unit=zzaimy-eval-") for a in out)
