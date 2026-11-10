from pathlib import Path

from zzaimy.app import webhwp


def test_rel_parts_fast_and_fallback(tmp_path):
    r = webhwp.root(tmp_path)
    (r / "기획처" / "작업" / "사업").mkdir(parents=True)
    f = r / "기획처" / "작업" / "사업" / "a.hwpx"
    f.write_bytes(b"x")
    assert webhwp.dept_of_path(tmp_path, str(f)) == "기획처"
    assert webhwp.folder_of(tmp_path, str(f)) == "작업/사업"
    # 정규형이 아닌 경로(..)는 예전처럼 풀어서 판단
    odd = str(r / "기획처" / "작업" / ".." / "작업" / "사업" / "a.hwpx")
    assert webhwp.folder_of(tmp_path, odd) == "작업/사업"
    # 뿌리 밖
    out = tmp_path / "밖.hwpx"
    out.write_bytes(b"x")
    assert webhwp.dept_of_path(tmp_path, str(out)) == ""
    assert webhwp.folder_of(tmp_path, str(r / ".." / "밖.hwpx")) == ""
    # 이름 앞머리만 같은 옆 디렉터리는 밖
    side = Path(str(r) + "x") / "기획처" / "b.hwpx"
    side.parent.mkdir(parents=True)
    side.write_bytes(b"x")
    assert webhwp.dept_of_path(tmp_path, str(side)) == ""


def test_rel_parts_relative_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    base = Path("data")
    r = webhwp.root(base)
    (tmp_path / r / "기획처" / "작업").mkdir(parents=True)
    rel = str(r / "기획처" / "작업" / "a.hwpx")
    (tmp_path / rel).write_bytes(b"x")
    webhwp._ROOTS.clear()
    assert webhwp.dept_of_path(base, rel) == "기획처"
    assert webhwp.folder_of(base, rel) == "작업"
