"""서버 이전 목록(scripts/209) — 가짜 트리에서 갈래·규칙 없음 보고·비밀값 표시를 본다."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("migration_manifest", Path(__file__).parents[1] / "scripts/209_migration_manifest.py")
mm = importlib.util.module_from_spec(spec)
sys.modules["migration_manifest"] = mm   # dataclass 가 모듈을 찾는다
spec.loader.exec_module(mm)

SECRET_TEXT = "TOP-SECRET-VALUE-123"


def _put(path: Path, size: int = 10, text: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        path.write_text(text, encoding="utf-8")
    else:
        path.write_bytes(b"x" * size)
    return path


@pytest.fixture
def tree(tmp_path):
    root, home = tmp_path / "repo", tmp_path / "home"
    plat = root / "data" / "platform"
    _put(root / ".env.local", text=f"ZZAIMY_DATABASE_URL=postgresql://u:{SECRET_TEXT}@/db\nZZAIMY_TLS_KEY={tmp_path}/certs/server.key\n"
                                   f"ZZAIMY_TLS_CERT={tmp_path}/certs/server.crt\nZZAIMY_SOFFICE=/usr/bin/true\n")
    _put(tmp_path / "certs" / "server.key", text=SECRET_TEXT)
    _put(tmp_path / "certs" / "server.crt", text="cert")
    _put(root / "src" / "zzaimy" / "app" / "main.py")
    _put(root / "src" / "zzaimy" / "app" / "__pycache__" / "main.cpython-312.pyc")
    _put(root / ".venv" / "bin" / "python")
    _put(plat / "documents" / "반입" / "2026" / "국고" / "0001 계획서" / "원본.hwpx", 100)
    _put(plat / "documents" / "기안기" / "공통" / "보고서.hwpx", 50)
    _put(plat / "accounts.json", text=SECRET_TEXT)
    _put(plat / "gdrive_tokens.json", text=SECRET_TEXT)
    _put(plat / ".session_secret", text=SECRET_TEXT)
    _put(plat / "accounts.json.bak-20261001", text=SECRET_TEXT)
    _put(plat / "kg_external.json")
    _put(plat / "dept_parents.json")
    _put(plat / "program_admin.json")
    _put(plat / "rag_spaces.json")
    _put(plat / "origins.jsonl")
    _put(plat / "platform.db")
    _put(plat / "platform.retired-c129.sqlite3", 30)
    _put(plat / "knowledge" / "index" / "chunk_embeddings.npz", 40)
    _put(plat / "knowledge" / "eval" / "retrieval-latest.json")
    _put(plat / "cache" / "lines" / "1.json")
    _put(plat / "backups" / "chunks-20261001.tar")
    _put(plat / "webhwp_forms" / "select.json")
    _put(root / "data" / "inbox" / "core" / "aid" / "a.pdf", 70)
    _put(root / "data" / "inbox" / "parsed" / "parsed-1.jsonl")
    _put(root / "data" / "inbox" / "parsed" / ".offsets.json")
    _put(root / "data" / "train" / "datasets" / "writer" / "v1" / "sft.jsonl")
    _put(root / "data" / "interim" / "sft" / "a.jsonl")
    _put(root / "data" / "eval" / "q.jsonl")
    _put(root / "data" / "mystery" / "big.bin", mm.LARGE + 1)
    _put(root / "data" / "mystery" / "small.txt", 5)
    _put(home / "opt" / "webhwp" / "conf" / "server.xml")
    _put(home / "opt" / "webhwp" / "README.txt")
    _put(home / "opt" / "webhwp" / "webapps" / "hwp.war", 60)
    _put(home / "opt" / "webhwp" / "logs" / "catalina.out")
    _put(home / "opt" / "webhwp" / "license" / "webhwp.lic", text=SECRET_TEXT)
    _put(home / ".ssh" / "id_ed25519_dgx_ro", text=SECRET_TEXT)
    _put(home / "ops" / "fix.sh")
    _put(home / "Documents" / "personal.txt", mm.LARGE + 1)   # 홈의 다른 곳은 훑지 않는다
    env = mm.read_env(root / ".env.local", include_os=False)
    layout = mm.Layout.from_env(root, home, env)
    return tmp_path, layout


def _decision(result, layout, shown):
    clf = result["_classifier"]
    p = Path(shown.replace("~/", str(layout.home) + "/", 1)) if shown.startswith("~/") else \
        (Path(shown) if shown.startswith("/") else layout.root / shown)
    rule = clf.classify(p)
    return (rule.decision, rule.secret) if rule else (mm.UNMATCHED, False)


def test_decisions(tree):
    _, layout = tree
    r = mm.build(layout)
    expect = {
        "data/platform/documents/반입/2026/국고/0001 계획서/원본.hwpx": mm.MOVE,
        "data/platform/documents/기안기/공통/보고서.hwpx": mm.MOVE,
        "data/platform/kg_external.json": mm.MOVE,
        "data/platform/dept_parents.json": mm.MOVE,
        "data/platform/program_admin.json": mm.MOVE,
        "data/platform/rag_spaces.json": mm.MOVE,
        "data/platform/origins.jsonl": mm.MOVE,
        "data/platform/platform.db": mm.MOVE,
        "data/inbox/parsed/.offsets.json": mm.MOVE,
        "data/platform/knowledge/index/chunk_embeddings.npz": mm.REGEN,
        "data/platform/cache/lines/1.json": mm.REGEN,
        "data/platform/webhwp_forms/select.json": mm.REGEN,
        "data/inbox/parsed/parsed-1.jsonl": mm.REGEN,
        "src/zzaimy/app/main.py": mm.REGEN,
        "~/opt/webhwp/webapps/hwp.war": mm.REGEN,
        "data/platform/accounts.json.bak-20261001": mm.SKIP,
        "data/platform/platform.retired-c129.sqlite3": mm.SKIP,
        "data/platform/knowledge/eval/retrieval-latest.json": mm.SKIP,
        "data/platform/backups/chunks-20261001.tar": mm.SKIP,
        "data/inbox/core/aid/a.pdf": mm.SKIP,
        "data/eval/q.jsonl": mm.SKIP,
        "src/zzaimy/app/__pycache__/main.cpython-312.pyc": mm.SKIP,
        ".venv/bin/python": mm.SKIP,
        "~/opt/webhwp/logs/catalina.out": mm.SKIP,
        "~/ops/fix.sh": mm.SKIP,
        "~/opt/webhwp/conf/server.xml": mm.MOVE,
        "~/opt/webhwp/README.txt": mm.MOVE,
        "data/train/datasets/writer/v1/sft.jsonl": mm.TRAIN,
        "data/interim/sft/a.jsonl": mm.TRAIN,
    }
    for shown, want in expect.items():
        assert _decision(r, layout, shown)[0] == want, shown
    t = r["totals"]
    assert sum(v["count"] for v in t.values()) >= len(expect)
    assert t[mm.TRAIN]["count"] == 2


def test_unmatched_reported_not_dropped(tree):
    _, layout = tree
    r = mm.build(layout)
    large = [u["path"] for u in r["unmatched_large"]]
    assert large == ["data/mystery/big.bin"]                      # 작은 것은 개수에만, 큰 것은 하나씩
    dirs = {u["dir"]: u for u in r["unmatched_dirs"]}
    assert dirs["data/mystery"]["count"] == 2
    assert r["totals"][mm.UNMATCHED]["count"] == 2
    assert not any("personal" in p for p in large)                  # 홈 전체는 훑지 않는다
    moved = {str(p) for p in r["_files"]["move"]} | {str(p) for p in r["_files"]["secret"]}
    assert not any("mystery" in p for p in moved)


def test_secrets_flagged_and_never_printed(tree, tmp_path, capsys):
    base, layout = tree
    r = mm.build(layout)
    secret_paths = {s["path"] for s in r["secrets"]}
    assert {".env.local", "data/platform/accounts.json", "data/platform/gdrive_tokens.json",
            "data/platform/.session_secret", "~/.ssh/id_ed25519_dgx_ro", "~/opt/webhwp/license/webhwp.lic"} <= secret_paths
    assert str((base / "certs" / "server.key").resolve()) in secret_paths          # 환경 변수가 가리키는 열쇠
    assert str((base / "certs" / "server.crt").resolve()) not in secret_paths      # 인증서는 옮기되 비밀값은 아님
    assert all(s["note"] == mm.SECRET_NOTE for s in r["secrets"])
    assert "data/platform/accounts.json.bak-20261001" not in secret_paths
    assert not any(p.startswith("/usr/") for p in secret_paths)        # 실행 파일 경로는 따라가지 않는다

    out = tmp_path / "out"
    j, m = mm.write_report(r, out)
    sh = mm.pack(r, tmp_path / "pack", dest="aura@new")
    texts = [j.read_text(encoding="utf-8"), m.read_text(encoding="utf-8"), sh.read_text(encoding="utf-8")]
    texts += [p.read_text(encoding="utf-8") for p in (tmp_path / "pack").glob("*.txt")]
    texts += [(tmp_path / "pack" / "pack.json").read_text(encoding="utf-8")]
    for t in texts:
        assert SECRET_TEXT not in t
    assert mm.SECRET_NOTE in m.read_text(encoding="utf-8")
    data = json.loads(j.read_text(encoding="utf-8"))
    assert {d for d in data["totals"]} == {*mm.DECISIONS, mm.UNMATCHED}


def test_pack_lists_and_commands(tree, tmp_path):
    base, layout = tree
    r = mm.build(layout)
    out = tmp_path / "pack"
    sh = mm.pack(r, out, dest="aura@new")
    repo = (out / "files-repo.txt").read_text(encoding="utf-8").splitlines()
    assert "data/platform/documents/기안기/공통/보고서.hwpx" in repo
    assert "data/platform/kg_external.json" in repo
    assert not any(x.startswith(("data/platform/cache", "data/inbox/core", "src/", ".venv", "data/train")) for x in repo)
    assert ".env.local" not in repo and "data/platform/accounts.json" not in repo   # 비밀값은 따로
    secrets = (out / "secrets-repo.txt").read_text(encoding="utf-8").splitlines()
    assert ".env.local" in secrets and "data/platform/accounts.json" in secrets
    assert (out / "secrets-repo.txt").stat().st_mode & 0o077 == 0
    home = (out / "files-home.txt").read_text(encoding="utf-8").splitlines()
    assert "opt/webhwp/conf/server.xml" in home and "opt/webhwp/webapps/hwp.war" not in home
    assert "opt/webhwp/license/webhwp.lic" in (out / "secrets-home.txt").read_text(encoding="utf-8")
    assert str((base / "certs" / "server.crt").resolve()).lstrip("/") in (out / "files-abs.txt").read_text(encoding="utf-8")
    cmd = sh.read_text(encoding="utf-8")
    assert f"--files-from={out / 'files-repo.txt'}" in cmd
    assert "aura@new:zzaimy-capstone/" in cmd
    assert "--chmod=F600" in cmd and "pg_dump" in cmd and '"$ZZAIMY_DATABASE_URL"' in cmd
    assert 0 <= cmd.index("crontab -l >") < cmd.index("crontab -r")        # cron 은 적어 둔 뒤에만 끈다


def test_db_check_flags_paths_outside_move(tree):
    _, layout = tree
    r = mm.build(layout)
    rows = [("documents", "data/platform/documents/기안기/공통/보고서.hwpx"),
            ("documents", "dgx://aid/2024/a.pdf"),
            ("documents", str(layout.root / "data/inbox/core/aid/a.pdf")),
            ("files", "data/platform/documents/없는파일.pdf"),
            ("documents", "")]
    db = mm.check_db(r, rows=rows)
    assert db["counts"][mm.MOVE] == 2
    assert db["counts"]["원격·빈 경로"] == 2
    assert db["missing_on_disk"] == 1
    assert [o["path"] for o in db["outside_move"]] == ["data/inbox/core/aid/a.pdf"]


def test_data_dir_override(tmp_path):
    root, home = tmp_path / "repo", tmp_path / "home"
    ext = tmp_path / "srv" / "platform"
    _put(ext / "kg_external.json")
    _put(ext / "cache" / "x.png")
    _put(root / "README.md")
    layout = mm.Layout.from_env(root, home, {"ZZAIMY_DATA_DIR": str(ext)})
    r = mm.build(layout)
    moved = {str(p) for p in r["_files"]["move"]}
    assert str(ext.resolve() / "kg_external.json") in moved
    assert r["totals"][mm.REGEN]["count"] >= 1


def test_main_report_writes_files(tree, tmp_path, monkeypatch):
    _, layout = tree
    for k in ("ZZAIMY_DATA_DIR", "ZZAIMY_TRAIN_DIR"):
        monkeypatch.delenv(k, raising=False)
    out = tmp_path / "rep"
    code = mm.main(["--root", str(layout.root), "--home", str(layout.home), "report", "--out", str(out), "--strict"])
    assert code == 1                                                    # 규칙 없는 큰 파일이 있다
    assert (out / "manifest.json").is_file() and (out / "manifest.md").is_file()
