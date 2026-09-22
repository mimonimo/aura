from types import SimpleNamespace
from zzaimy.app import storage_status


def test_snapshot_uses_requested_filesystem(monkeypatch, tmp_path):
    seen = []
    def usage(path):
        seen.append(path)
        return SimpleNamespace(total=100 * 1024**3, used=40 * 1024**3, free=55 * 1024**3)
    monkeypatch.setattr(storage_status.shutil, 'disk_usage', usage)
    result = storage_status.snapshot(tmp_path)
    assert seen == [tmp_path]
    assert result['free_bytes'] == 55 * 1024**3
    assert result['used_percent'] == 40
    assert result['available'] and not result['low_space']


def test_snapshot_does_not_report_missing_path_as_zero(monkeypatch, tmp_path):
    def missing(path):
        raise FileNotFoundError(path)
    monkeypatch.setattr(storage_status.shutil, 'disk_usage', missing)
    result = storage_status.snapshot(tmp_path)
    assert result['available'] is False
    assert 'free_bytes' not in result
