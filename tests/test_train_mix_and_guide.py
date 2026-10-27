"""데이터·학습 — 학습셋 구성 표·갈래 표본, 학습셋 상태 표(운영 판단 포함), 검수 가이드 화면(10-27)."""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import dev_train_routes
from zzaimy.app.main import create_app
from zzaimy.export import bundle


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter())
    return TestClient(app)


def _sets(tmp_path, monkeypatch, *, index=True):
    d = tmp_path / "trainsets" / "set-a"
    d.mkdir(parents=True)
    rows, idx = [], []
    for i in range(6):
        track = "institutional_policy" if i % 2 else "program_retrieval"
        rows.append({"prompt": [{"role": "system", "content": "지시"}, {"role": "user", "content": f"질문 {i}"}],
                     "completion": [{"role": "assistant", "content": f"답 {i}"}]})
        idx.append({"job": f"j{i}", "track": track, "turn": 1})
    (d / "train.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    if index:
        (d / "train-index.jsonl").write_text("\n".join(json.dumps(r) for r in idx) + "\n", encoding="utf-8")
    items = [
        {"kind": "release", "name": "writer-v1-approval", "current": True, "status": "sft_approved", "training": "stopped",
         "counts": {"train": 6}},
        {"kind": "staging", "name": "set-a", "current": True, "status": "candidate_not_sft_approved", "approved_by": "writer-v1-approval",
         "at": "2026-10-20 10:00", "mirrored": ["train.jsonl", "train-index.jsonl"],
         "splits": {"train": {"rows": 6, "tracks": {"institutional_policy": 3, "program_retrieval": 3}},
                    "eval": {"rows": 2, "tracks": {"institutional_policy": 1, "program_retrieval": 1}},
                    "test": {"rows": 1, "tracks": {"program_retrieval": 1}}}},
        {"kind": "staging", "name": "set-b", "current": True, "status": "candidate_not_sft_approved",
         "splits": {"train": {"rows": 4, "tracks": {}}}},
    ]
    (tmp_path / "trainsets.json").write_text(json.dumps({"at": "10-27 09:00", "items": items}), encoding="utf-8")
    monkeypatch.setattr(bundle, "TRAINSETS", tmp_path / "trainsets.json")
    monkeypatch.setattr(bundle, "TRAINSET_DIR", tmp_path / "trainsets")


def test_set_state_follows_release_and_decisions(tmp_path, monkeypatch):
    _sets(tmp_path, monkeypatch)
    sets = bundle.trainsets()
    by = {t["name"]: t for t in sets}
    dec = {"writer-v1": {"label": "학습 완료 · 운영 미채택", "adr": "ADR-0069", "href": "/dev/quality#model-compare"}}
    assert bundle.set_state(by["set-a"], sets, dec)["label"] == "학습 완료 · 운영 미채택"      # 승인 기록의 결정을 따른다
    assert bundle.set_state(by["set-a"], sets, {})["label"] == "학습 멈춤"
    assert bundle.set_state(by["set-b"], sets, dec)["label"] == "후보"
    assert by["set-a"]["split_tracks"]["program_retrieval"] == {"train": 3, "eval": 1, "test": 1}
    assert by["set-a"]["counts"] == {"train": 6, "eval": 2, "test": 1}


def test_track_samples_use_index_or_fall_back(tmp_path, monkeypatch):
    _sets(tmp_path, monkeypatch)
    got = bundle.trainset_track_samples("set-a", per=2)
    assert set(got) == {"institutional_policy", "program_retrieval"}
    assert got["institutional_policy"] == [{"q": "질문 1", "a": "답 1"}, {"q": "질문 3", "a": "답 3"}]
    (tmp_path / "trainsets" / "set-a" / "train-index.jsonl").unlink()
    assert list(bundle.trainset_track_samples("set-a", per=2)) == ["전체"]
    assert bundle.trainset_track_samples("../set-a") == {}


def test_train_page_shows_mix_and_one_set_table(client, tmp_path, monkeypatch):
    _sets(tmp_path, monkeypatch)
    page = client.get("/dev/train").text
    mix = page.split('id="trainset-mix"', 1)[1].split("</section>", 1)[0]
    assert "규정·제도" in mix and "사업 문서 검색 활용" in mix and "표본 3건" in mix and "질문 1" in mix
    assert re.search(r"합계</td>.*?>6<.*?>2<.*?>1<.*?>9<", mix, re.S)
    table = page.split('id="trainsetTable"', 1)[1].split("</table>", 1)[0]
    assert table.count("<tr>") == 1 + 3                            # 머리 + 세 줄(승인 기록·학습셋 둘)
    assert 'href="/dev/quality#model-compare">학습 완료 · 운영 미채택(ADR-0069)</a>' in table
    assert 'href="/dev/train/trainset/set-a/train.jsonl"' in table and "train-index" not in table
    outputs = page.split('id="approved-data"', 1)[1].split('id="training-tools"', 1)[0]
    assert "trainset-list" not in outputs                         # 같은 목록을 두 곳에 두지 않는다


def test_review_guide_renders_markdown_and_serves_only_its_images(client, tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    guide = docs / "guides" / "sft-review-guide.md"
    imgs = docs / "images" / "guides" / "sft-review"
    imgs.mkdir(parents=True)
    guide.parent.mkdir(parents=True)
    (imgs / "01-list.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (docs / "secret.png").write_bytes(b"x")
    guide.write_text("# SFT 검수 가이드\n\n## 1. 프로젝트 열기\n\n목록에서 **ZZAIMY SFT 5000 검수** 를 연다.\n\n"
                     "![프로젝트 목록](../images/guides/sft-review/01-list.png)\n\n![밖](../secret.png)\n\n"
                     "<script>alert(1)</script>\n", encoding="utf-8")
    monkeypatch.setattr(dev_train_routes, "GUIDE_PATH", guide)
    monkeypatch.setattr(dev_train_routes, "GUIDE_IMG_DIR", imgs)
    page = client.get("/dev/train/review-guide").text
    assert "SFT 검수 가이드" in page and "<b>ZZAIMY SFT 5000 검수</b>" in page
    assert '<img class="guide-img" src="/dev/train/review-guide/img/01-list.png" alt="프로젝트 목록"' in page
    assert "![밖](../secret.png)" in page and "<script>alert(1)</script>" not in page   # 폴더 밖 그림은 그리지 않고, 글은 이스케이프
    assert re.findall(r'<a[^>]*href="([^"]+)"[^>]*class="active"', page) == ["/dev/train"]
    r = client.get("/dev/train/review-guide/img/01-list.png")
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    for bad in ("..%2Fsecret.png", "secret.png", ".hidden.png", "01-list.txt"):
        assert client.get(f"/dev/train/review-guide/img/{bad}").status_code == 404
    guide.unlink()
    assert "아직 없습니다" in client.get("/dev/train/review-guide").text


def test_review_guide_is_dev_only(tmp_path):
    (tmp_path / "platform").mkdir()
    (tmp_path / "platform" / "accounts.json").write_text(json.dumps({"kim": {"pw": "kim-pass", "role": "staff", "dept": "학생처"}}))
    app = create_app(db_path=tmp_path / "platform" / "platform.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), password="boot-pass-1")
    c = TestClient(app)
    assert c.post("/login", data={"username": "kim", "pw": "kim-pass"}, follow_redirects=False).status_code == 303
    assert c.get("/dev/train/review-guide").status_code == 403
    assert c.get("/dev/train/review-guide/img/01-list.png").status_code == 403
