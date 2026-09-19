"""NAS 수집 — 서버 경로 백엔드로 반입·중복·갱신·확장자·실패 처리와 화면을 검증한다 (NAS 쓰기 없음)."""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app
from zzaimy.ingest import nas_sync


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_NAS_AUTO", "0")
    nas = tmp_path / "nas"
    (nas / "규정" / "하위").mkdir(parents=True)
    (nas / "규정" / "학칙.pdf").write_bytes(b"%PDF-1.4 rule one")
    (nas / "규정" / "하위" / "장학규정.hwp").write_bytes(b"HWP rule two")
    (nas / "규정" / "메모.txt").write_bytes(b"not a target")
    (nas / "규정" / ".숨김.pdf").write_bytes(b"hidden")
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter())
    return app, TestClient(app), nas


def test_local_source_sync_dedup_and_update(env, tmp_path):
    app, client, nas = env
    db = app.state.db
    r = client.post("/dev/nas/add", data={"name": "규정 폴더", "backend": "local", "root": str(nas / "규정"),
                                          "target": "regulation", "sector": "common", "extensions": "",
                                          "recursive": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    src = nas_sync.list_sources()[0]
    assert oct((tmp_path / "nas_sources.json").stat().st_mode & 0o777) == "0o600"
    # 연결 확인 — 목록만
    pr = nas_sync.probe(src)
    assert pr["ok"] and pr["n_match"] == 2 and "학칙.pdf" in pr["sample"] and not any(".숨김" in p for p in pr["sample"])
    # 반입(동기) → 기준 문서 2건, txt 는 대상 아님
    from urllib.parse import unquote
    r = client.post(f"/dev/nas/{src['id']}/sync", data={"wait": "1"}, follow_redirects=False)
    assert "새로 2" in unquote(r.headers["location"])
    docs = db.list_documents("regulation")
    assert sorted(d["filename"] for d in docs) == ["장학규정.hwp", "학칙.pdf"]
    assert all(d["status"] == "reviewed" for d in docs)             # 처리 경로를 탔다
    assert all(pathlib.Path(d["stored_path"]).is_file() for d in docs)
    # 다시 반입 → 그대로
    res = nas_sync.sync(src, db, FakeProcessor(), tmp_path / "inbox")
    assert res["new"] == 0 and res["same"] == 2
    # 내용이 같은 다른 이름 → 중복, 내용이 바뀐 파일 → 새 문서
    (nas / "규정" / "학칙-사본.pdf").write_bytes(b"%PDF-1.4 rule one")
    p = nas / "규정" / "학칙.pdf"; p.write_bytes(b"%PDF-1.4 rule one v2")
    import os, time
    os.utime(p, (time.time() + 5, time.time() + 5))
    client.post(f"/dev/nas/{src['id']}/sync", data={"wait": "1"})
    res = nas_sync.run_status(src["id"])["last"]
    assert res["dup"] == 1 and res["new"] == 1 and res["failed"] == 0
    assert len(db.list_documents("regulation")) == 3
    # NAS 원본은 손대지 않았다
    assert {x.name for x in (nas / "규정").iterdir()} == {".숨김.pdf", "메모.txt", "하위", "학칙-사본.pdf", "학칙.pdf"}
    assert (nas / "규정" / "하위" / "장학규정.hwp").read_bytes() == b"HWP rule two"
    # 화면: 원천·마지막 반입 요약·결과 표, 비밀번호는 안 보인다
    page = client.get("/dev/nas").text
    assert "규정 폴더" in page and "새로 1" in page and "학칙-사본.pdf" in page
    assert client.get("/dev/nas?probe=" + src["id"]).status_code == 200


def test_smb_source_validation_and_missing_package(env, monkeypatch):
    app, client, nas = env
    r = client.post("/dev/nas/add", data={"name": "NAS", "backend": "smb", "root": "nas.example/공유",
                                          "target": "ocr"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    client.post("/dev/nas/add", data={"name": "NAS", "backend": "smb", "root": r"\\nas.example\문서\규정",
                                      "target": "ocr", "username": "reader", "password": "pw-secret"})
    src = nas_sync.list_sources()[-1]
    assert src["backend"] == "smb" and src["password"] == "pw-secret"
    page = client.get("/dev/nas").text
    assert "pw-secret" not in page and "reader" in page
    import builtins
    real_import = builtins.__import__
    def no_smb(name, *a, **k):
        if name == "smbclient":
            raise ImportError("no smb")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_smb)
    pr = nas_sync.probe(src)
    assert not pr["ok"] and "smbprotocol" in pr["error"]
    res = nas_sync.sync(src, app.state.db, FakeProcessor(), nas.parent / "inbox")
    assert res["error"] and res["new"] == 0


def test_processing_failure_is_recorded_not_fatal(env, tmp_path):
    app, client, nas = env

    class Boom:
        def process(self, db, doc_id, path):
            raise RuntimeError("파서 오류")

    src = nas_sync.add("규정", "local", str(nas / "규정"), "ocr")
    res = nas_sync.sync(src, app.state.db, Boom(), tmp_path / "inbox")
    assert res["new"] == 2 and res["failed"] == 0
    assert all(d["status"] == "failed" and "파서 오류" in (d["error"] or "") for d in app.state.db.list_documents("ocr"))


def test_add_form_chips_interval_and_draft_probe(env, tmp_path):
    app, client, nas = env
    d = client.post("/dev/nas/probe-draft", data={"backend": "local", "root": str(nas / "규정"),
                                                  "ext_group": [".pdf", ".hwp .hwpx"], "recursive": "1"}).json()
    assert d["ok"] and d["n_match"] == 2 and len(d["sample"]) == 2
    assert not client.post("/dev/nas/probe-draft", data={"backend": "local", "root": ""}).json()["ok"]
    r = client.post("/dev/nas/add", data={"name": "칩", "backend": "local", "root": str(nas / "규정"), "target": "ocr",
                                          "ext_group": [".pdf", ".hwp .hwpx"], "recursive": "1", "interval_min": "360"},
                    follow_redirects=False)
    assert "ok=" in r.headers["location"]
    src = nas_sync.list_sources()[-1]
    assert src["extensions"] == [".pdf", ".hwp", ".hwpx"] and src["auto"] and src["interval_min"] == 360
    client.post(f"/dev/nas/{src['id']}/update", data={"name": "", "root": "", "ext_group": [".pdf"], "interval_min": "0"})
    src = nas_sync.get(src["id"])
    assert src["extensions"] == [".pdf"] and not src["auto"]
    page = client.get("/dev/nas").text
    assert "<legend>연결</legend>" in page and "폴더 찾아보기" in page and "미리보기" in page and 'name="ext_group"' in page


def test_browse_plan_and_filters(env, tmp_path):
    app, client, nas = env
    (nas / "규정" / "백업").mkdir(); (nas / "규정" / "백업" / "옛규정.pdf").write_bytes(b"%PDF old")
    big = nas / "규정" / "큰파일.pdf"; big.write_bytes(b"%PDF " + b"0" * (2 * 1024 * 1024))
    base = {"backend": "local", "root": str(nas / "규정"), "recursive": "1"}
    d = client.post("/dev/nas/browse-draft", data=dict(base, rel="")).json()
    assert d["ok"] and [x["name"] for x in d["dirs"]] == ["백업", "하위"]
    assert d["counts"]["pdf"] == 3 and d["counts"]["hwp"] == 1 and d["other"] == 1   # txt 는 기타
    d1 = client.post("/dev/nas/browse-draft", data=dict(base, rel="하위")).json()
    assert d1["ok"] and d1["dirs"] == [] and d1["counts"]["hwp"] == 1 and d1["n_files"] == 1
    assert not client.post("/dev/nas/browse-draft", data=dict(base, rel="../x")).json()["ok"]
    # 필터: 제외 폴더·최대 크기 → 미리보기에서 제외로 잡힌다
    pl = client.post("/dev/nas/plan-draft", data=dict(base, ext_group=[".pdf", ".hwp .hwpx"], exclude="백업", max_mb="1")).json()
    assert pl["ok"] and pl["new"] == 2 and pl["skipped"] == 3 and pl["same"] == 0
    # 저장 + 반입 → 필터가 실제 반입에도 적용된다
    r = client.post("/dev/nas/add", data=dict(base, name="필터", target="ocr", ext_group=[".pdf", ".hwp .hwpx"],
                                            exclude="백업", max_mb="1", since="2000-01-01"), follow_redirects=False)
    assert "ok=" in r.headers["location"]
    src = nas_sync.list_sources()[-1]
    assert src["exclude"] == ["백업"] and src["max_mb"] == 1 and src["since"] == "2000-01-01"
    client.post(f"/dev/nas/{src['id']}/sync", data={"wait": "1"})
    res = nas_sync.run_status(src["id"])["last"]
    assert res["new"] == 2 and res["failed"] == 0
    assert sorted(d["filename"] for d in app.state.db.list_documents("ocr")) == ["장학규정.hwp", "학칙.pdf"]
    # 반입 뒤 미리보기: 그대로 2
    pl2 = client.post("/dev/nas/plan-draft", data=dict(base, ext_group=[".pdf", ".hwp .hwpx"], exclude="백업", max_mb="1", sid=src["id"])).json()
    assert pl2["new"] == 0 and pl2["same"] == 2
    page = client.get(f"/dev/nas?plan={src['id']}").text
    assert "미리보기" in page and "그대로 2" in page
