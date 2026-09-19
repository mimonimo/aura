"""Label Studio 연동 흐름 — 사람이 토큰·파일을 만지지 않는 왕복.

- 연결 상태는 서버가 짧은 시간 제한(2초)으로 한 번 묻고 30초 캐시한다.
- 미연결이면 붙여넣기 칸 없이 고치는 법(scripts/68_labelstudio_token.sh) 한 문장만 보인다.
- 되받기(ls-pull)는 그대로 동작하고, 수동 파일 교환·토큰 붙여넣기 라우트는 없다.
- /dev/train 의 Label Studio 카드는 읽기 전용(주소 편집 경로 없음).
네트워크는 requests.request 를 가짜로 바꿔 흉내 낸다 — 실제 소켓은 열지 않는다.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

from zzaimy.app.main import create_app
from zzaimy.dataset import ls_client as lsc

PROJECT = "ZZAIMY 검수"
LS_URL = "http://ls.test:8080"
PAIR = {
    "conversations": [{"from": "human", "value": "근거: 총액 5,000,000원"},
                      {"from": "gpt", "value": "총액은 5,000,000원이다."}],
    "meta": {"source": "draft", "doc_id": 1, "reviewed": True, "decision": "채택"},
}
PROJECT_LIST = {"count": 1, "results": [
    {"id": 3, "title": PROJECT, "task_number": 5, "num_tasks_with_annotations": 2},
]}


class _Processor:
    """앱 생성에만 필요한 자리표시 — 이 흐름에서는 호출되지 않는다."""

    def process(self, db, doc_id, file_path):
        pass

    def reprocess(self, db, doc_id):
        pass

    def extract_text(self, file_path):
        return ""

    def analyze(self, db, doc_id):
        pass


class _Drafter:
    def generate(self, db, doc_id):
        pass


@pytest.fixture()
def client(tmp_path):
    app = create_app(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=_Processor(), drafter=_Drafter(),
    )
    return TestClient(app)


def _configure(client):
    db = client.app.state.db
    db.set_setting("labelstudio_url", LS_URL)
    db.set_setting("labelstudio_token", "f" * 40)
    return db


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.ok = status < 400
        self.content = json.dumps(payload).encode() if payload is not None else b""
        self.text = self.content.decode()

    def json(self):
        return json.loads(self.content)


def _fake_requests(monkeypatch, routes=None, exc=None):
    """requests.request 를 가짜로 — 경로별 (status, payload) 또는 예외. 호출 기록을 돌려준다."""
    calls = []

    def request(method, url, headers=None, timeout=None, **kw):
        calls.append({"method": method, "url": url, "timeout": timeout, "headers": headers or {}})
        if exc is not None:
            raise exc
        path = "/" + url.split("//", 1)[1].split("/", 1)[1].split("?", 1)[0]
        status, payload = (routes or {}).get(path, (404, {}))
        return _Resp(status, payload)

    monkeypatch.setattr(lsc.requests, "request", request)
    return calls


# ---- 클라이언트: 상태 조회는 짧고, 한 번만 두드린다 ----

def test_status_unreachable_is_short_and_time_bounded(monkeypatch):
    calls = _fake_requests(monkeypatch, exc=requests.ConnectTimeout("timed out"))
    st = lsc.LabelStudioClient(LS_URL, "tok").status(PROJECT)
    assert st["ok"] is False and st["error"] == "응답 없음"
    assert st["project_id"] is None and (st["total"], st["done"], st["pending"]) == (0, 0, 0)
    assert len(calls) == 1 and calls[0]["timeout"] <= 2


def test_status_reads_progress_from_project_list(monkeypatch):
    calls = _fake_requests(monkeypatch, {"/api/projects": (200, PROJECT_LIST)})
    st = lsc.LabelStudioClient(LS_URL, "tok").status(PROJECT)
    assert st["ok"] is True and st["project_id"] == 3 and st["project"] == PROJECT
    assert (st["total"], st["done"], st["pending"]) == (5, 2, 3)
    assert len(calls) == 1  # 목록 한 번으로 끝 — 화면마다 두 번 두드리지 않는다


def test_status_bad_token_and_missing_project(monkeypatch):
    _fake_requests(monkeypatch, {"/api/projects": (401, {"detail": "Invalid token."})})
    st = lsc.LabelStudioClient(LS_URL, "tok").status(PROJECT)
    assert st["ok"] is False and st["error"] == "토큰 불일치"

    _fake_requests(monkeypatch, {"/api/projects": (200, {"count": 0, "results": []})})
    st = lsc.LabelStudioClient(LS_URL, "tok").status(PROJECT)
    assert st["ok"] is True and st["project_id"] is None and st["total"] == 0


# ---- 데이터 공방 ② — 상태 줄과 버튼, 붙여넣기 칸 없음 ----

def test_page_unreachable_shows_fix_sentence_without_paste_box(client, monkeypatch):
    _configure(client)
    _fake_requests(monkeypatch, exc=requests.ConnectionError("refused"))
    r = client.get("/dev/data")
    assert r.status_code == 200
    assert "미연결" in r.text and "68_labelstudio_token.sh" in r.text
    assert 'name="token"' not in r.text          # 토큰 붙여넣기 칸 없음
    assert "label-export" not in r.text and "label-import" not in r.text  # 수동 파일 교환 없음


def test_page_connected_shows_progress_and_buttons(client, monkeypatch):
    _configure(client)
    _fake_requests(monkeypatch, {"/api/projects": (200, PROJECT_LIST)})
    r = client.get("/dev/data")
    assert r.status_code == 200
    assert "연결됨" in r.text
    assert "전체 5" in r.text and "완료 2" in r.text and "대기 3" in r.text
    assert "Label Studio에서 검수" in r.text and 'action="/dev/data/ls-pull"' in r.text
    assert f'href="{LS_URL}"' in r.text


def test_page_without_settings_never_touches_network(client, monkeypatch):
    _fake_requests(monkeypatch, exc=AssertionError("설정이 없으면 네트워크에 나가면 안 된다"))
    r = client.get("/dev/data")
    assert r.status_code == 200
    assert "미연결" in r.text and "68_labelstudio_token.sh" in r.text


def test_status_is_cached_between_page_loads(client, monkeypatch):
    _configure(client)
    calls = _fake_requests(monkeypatch, {"/api/projects": (200, PROJECT_LIST)})
    client.get("/dev/data")
    client.get("/dev/data")
    client.get("/dev/train")
    assert len(calls) == 1


# ---- 되받기는 그대로, 수동 라우트는 사라졌다 ----

def test_ls_pull_still_confirms_reviewed_pairs(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)   # data/interim/sft 산출물이 저장소 data/ 에 닿지 않게
    _configure(client)
    monkeypatch.setattr(lsc.LabelStudioClient, "ensure_project", lambda self, title: 3)
    monkeypatch.setattr(lsc.LabelStudioClient, "pull_reviewed", lambda self, pid: [PAIR])
    r = client.post("/dev/data/ls-pull", data={"name": "검수완료"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("ls_pulled=1")
    ds = client.app.state.db.list_datasets()[0]
    assert ds["sources"] == "labelstudio" and ds["n_pairs"] == 1
    assert Path(ds["path"]).exists()


@pytest.mark.parametrize("method,path", [
    ("post", "/dev/data/label-export"),
    ("get", "/dev/data/label-config"),
    ("post", "/dev/data/label-import"),
    ("post", "/dev/data/ls-token"),
])
def test_manual_exchange_routes_are_gone(client, method, path):
    assert getattr(client, method)(path).status_code == 404


# ---- /dev/train — Label Studio 카드는 읽기 전용 ----

def test_dev_train_labelstudio_card_is_read_only(client, monkeypatch):
    _configure(client)
    _fake_requests(monkeypatch, exc=requests.ConnectionError("refused"))
    r = client.get("/dev/train")
    assert r.status_code == 200
    assert "미연결" in r.text and "/dev/accounts" not in r.text
    assert 'href="/dev/data"' not in r.text               # 데이터 공방 진입은 허브 카드 하나 — 여기엔 없다
    assert "toolModal-tensorboard" in r.text and "toolModal-labelstudio" in r.text   # 도구 설정 창은 이 화면에
    assert 'name="url"' in r.text and "labelstudio_url" not in r.text   # LS 주소 입력란은 없다
    r = client.post("/dev/train/url", data={"setting": "labelstudio_url", "url": "http://x"})
    assert r.status_code == 400                          # Label Studio 주소는 화면에서 못 바꾼다


# ---- 프로비저닝 보조 스크립트 — 유닛 편집은 멱등이고 토큰을 찍지 않는다 ----

_HELPER = Path(__file__).resolve().parents[1] / "scripts" / "68_labelstudio_token.py"
_UNIT = """[Unit]
Description=Label Studio

[Service]
Environment=LABEL_STUDIO_USERNAME=admin@ync.ac.kr
Environment="LABEL_STUDIO_PASSWORD=p w#1"
ExecStart=%h/ls-venv/bin/label-studio start --host 0.0.0.0 --port 8081 --no-browser
Restart=on-failure

[Install]
WantedBy=default.target
"""


def _helper():
    spec = importlib.util.spec_from_file_location("ls_token_helper", _HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_helper_unit_set_is_idempotent_and_silent(tmp_path, monkeypatch, capsys):
    h = _helper()
    unit = tmp_path / "label-studio.service"
    unit.write_text(_UNIT, encoding="utf-8")
    token = "a1" * 20
    monkeypatch.setenv("LS_TOKEN", token)

    assert h.cmd_unit_set(unit) == 0
    assert capsys.readouterr().out.strip() == "changed"
    text = unit.read_text(encoding="utf-8")
    env = h.unit_env(text, unit)
    assert env["LABEL_STUDIO_USER_TOKEN"] == token
    assert env["LABEL_STUDIO_ENABLE_LEGACY_API_TOKEN"] == "true"
    assert env["LABEL_STUDIO_PASSWORD"] == "p w#1"          # 기존 줄은 손대지 않는다
    assert text.index("LABEL_STUDIO_USER_TOKEN") < text.index("ExecStart=")
    assert unit.stat().st_mode & 0o777 == 0o600
    assert h.exec_flags(text)["port"] == "8081"

    # 두 번째 실행 — 변경 없음, 토큰은 출력되지 않는다
    assert h.cmd_unit_set(unit) == 0
    out = capsys.readouterr().out
    assert out.strip() == "unchanged" and token not in out

    # 토큰 교체 — 줄이 늘지 않고 값만 바뀐다
    monkeypatch.setenv("LS_TOKEN", "b2" * 20)
    assert h.cmd_unit_set(unit) == 0
    capsys.readouterr()
    text2 = unit.read_text(encoding="utf-8")
    assert text2.count("LABEL_STUDIO_USER_TOKEN=") == 1 and "b2" * 20 in text2
    assert text2.count("LABEL_STUDIO_ENABLE_LEGACY_API_TOKEN=") == 1


def test_helper_unit_get_reads_env_or_flag(tmp_path, capsys):
    h = _helper()
    unit = tmp_path / "ls.service"
    unit.write_text(_UNIT, encoding="utf-8")
    assert h.cmd_unit_get(unit) == 0 and capsys.readouterr().out.strip() == ""
    unit.write_text(_UNIT.replace("--no-browser", "--no-browser --user-token c3c3c3c3"),
                    encoding="utf-8")
    assert h.cmd_unit_get(unit) == 0 and capsys.readouterr().out.strip() == "c3c3c3c3"
    assert h.cmd_unit_port(unit) == 0 and capsys.readouterr().out.strip() == "8081"
