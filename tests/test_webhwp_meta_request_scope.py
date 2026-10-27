import json

from zzaimy.app import webhwp
from zzaimy.app.db import Database


def test_bulk_after_threshold_and_writes_stay_visible(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    for i in range(60):
        db.set_setting(f"webhwp_meta:{i}", json.dumps({"source": f"s{i}"}))
    calls = {"one": 0, "bulk": 0}
    real_get, real_bulk = db.get_setting, db.settings_with_prefix
    monkeypatch.setattr(db, "get_setting", lambda *a, **k: (calls.__setitem__("one", calls["one"] + 1), real_get(*a, **k))[1])
    monkeypatch.setattr(db, "settings_with_prefix", lambda *a, **k: (calls.__setitem__("bulk", calls["bulk"] + 1), real_bulk(*a, **k))[1])
    with webhwp.request_scope():
        got = [webhwp.meta(db, i)["source"] for i in range(60)]
        assert got == [f"s{i}" for i in range(60)]
        assert calls["one"] == webhwp.BULK_AFTER and calls["bulk"] == 1
        db.set_setting("webhwp_meta:5", json.dumps({"source": "바뀜"}))     # 쓰기는 한 번에 읽은 값에도
        assert webhwp.meta(db, 5)["source"] == "바뀜"
        db.delete_setting("webhwp_meta:6")
        assert webhwp.meta(db, 6) == {}
    calls["one"] = 0
    assert webhwp.meta(db, 7)["source"] == "s7" and calls["one"] == 1      # 요청 밖은 지금처럼 하나씩
