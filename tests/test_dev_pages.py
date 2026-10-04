"""개발자 영역 화면 스모크 — 2026-09-14 전수 검수에서 잡힌 렌더 결함의 재발 방지."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(
        db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(),
    )
    return TestClient(app)


def test_paper_nav_renders_labels_not_dicts(client):
    r = client.get("/dev/paper/논문-원재료.md")
    assert r.status_code == 200
    assert "&#39;file&#39;" not in r.text and "{'file'" not in r.text
    assert 'href="/dev/docs"' in r.text                 # 목록으로 돌아가는 링크 하나뿐(알약 목록 없음)
    assert 'class="pill' not in r.text.split("<div class=\"card reveal\">")[0]
    assert "export.hwpx" in r.text                      # 논문 자료는 내보내기 제공


def test_settings_reuses_connection_probe(client, monkeypatch):
    from zzaimy.app import main
    from zzaimy.generate import llm_connections as lc, model_config
    conn = lc.add('probe-test', 'vllm', 'http://127.0.0.1:1/v1', 'test', '')
    calls = []
    def live(cid):
        calls.append(cid)
        return {'ok': False, 'models': [], 'error': '연결되지 않음'}
    monkeypatch.setattr(main, '_live_models_cached', live)
    monkeypatch.setattr(model_config, 'current', lambda: {'configured': True, 'connection_id': conn['id'],
        'base_url': conn['base_url'], 'model': 'test', 'external': False})
    def duplicate_probe(*args, **kwargs):
        raise AssertionError('default connection must reuse existing probe')
    monkeypatch.setattr(model_config, 'probe', duplicate_probe)
    response = client.get('/dev/train?tab=settings')
    assert response.status_code == 200
    assert calls.count(conn['id']) == 1


def test_overview_uses_responsive_roles(client):
    response = client.get('/dev')
    assert 'system-roles' in response.text
    assert '상세 구성도' in response.text
    assert '<img src="/static/topology.svg"' not in response.text


@pytest.mark.parametrize('path', ['/dev/train', '/dev/train?tab=data', '/dev/data'])
def test_training_navigation_does_not_scan_hidden_legacy_documents(client, monkeypatch, path):
    from zzaimy.dataset import build
    def forbidden(*args, **kwargs):
        raise AssertionError('hidden legacy data must not scan all documents')
    monkeypatch.setattr(build, 'rag_status', forbidden)
    monkeypatch.setattr(build, 'preview_sources', forbidden)
    response = client.get(path)
    assert response.status_code == 200
    assert '문답 검수' in response.text


def test_training_legacy_keeps_requested_previews(client, monkeypatch):
    from zzaimy.dataset import build
    calls = []
    monkeypatch.setattr(build, 'rag_status', lambda db: calls.append('rag') or [])
    monkeypatch.setattr(build, 'preview_sources', lambda db: calls.append('source') or [])
    response = client.get('/dev/train?view=legacy')
    assert response.status_code == 200
    assert sorted(calls) == ['rag', 'source']


def test_design_doc_has_no_export_buttons(client):
    r = client.get("/dev/doc/quality-system.md")
    assert r.status_code == 200
    assert "export.hwpx" not in r.text                  # /dev/doc 내보내기는 항상 404였음
    assert "설계·기술 문서" in r.text
    for name in ("rerank-baseline.md", "retrieval-weight-sweep.md", "HANDOFF.md"):
        assert client.get(f"/dev/doc/{name}").status_code == 200


def test_db_browser_shortens_long_names_and_keeps_full_title(client):
    long = "가" * 200
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": (long + ".pdf", b"%PDF", "application/pdf")})
    r = client.get("/dev/db?table=documents")          # 예전 표 주소도 문서 탭으로
    assert r.status_code == 200
    assert f'title="{long}.pdf"' in r.text             # 목록은 줄여 보이고 전체 이름은 툴팁에
    assert "n_hidden" not in r.text
    r2 = client.get("/dev/db?tab=docs&q=가&doc=1")
    assert r2.status_code == 200 and "추출·검색 자료" in r2.text


def test_hwp_console_hides_internal_op(client):
    r = client.get("/dev/hwp")
    assert '플랫폼 연동 미완료' in r.text and '에이전트 앱 받기' not in r.text
    r = client.get("/dev/hwp?legacy=1")
    assert r.status_code == 200
    assert '<option value="open_bytes">' not in r.text
    assert '<option value="find">' in r.text


def test_dev_dashboard_labels(client):
    r = client.get("/dev")
    assert r.status_code == 200
    assert "접수 문서" in r.text and "규정 문단" in r.text   # 규모 타일 — /dev/db 와 같은 이름
    assert "구축 현황" in r.text and "영역별 완료 비율" in r.text and "진행 현황" in r.text
    # 허브 — 상세 카드는 전용 페이지로 갔고, 바로가기 카드만 남는다
    for link in ("/dev/quality", "/dev/docs", "/dev/history", "/dev/train", "/dev/pii"):
        assert f'href="{link}"' in r.text
    q = client.get("/dev/quality").text
    assert "규정 검색 품질" in q and "추출 품질 백로그" in q
    d = client.get("/dev/docs").text
    assert "논문 자료" in d and "설계 결정" in d and "현황·설계" in d
    records = client.get("/dev/docs?view=records").text
    assert "측정 기록·모델 카드" in records and "작업 메모" in records
    assert "측정 기록·모델 카드" not in d
    hpage = client.get("/dev/history").text
    assert "전체 변경 목록" in hpage and "/dev/docs#weekly" in hpage     # 주간 보고서는 논문 자료 칸으로
    r2 = client.get("/dev/egress")
    assert r2.status_code == 200 and "외부 참조 AI · 구독 연결 확인" in r2.text
    r3 = client.get("/dev/corpus")
    assert r3.status_code == 200
    assert '>문서 추출</h2>' not in r.text
    assert '>문서 가져오기</h2>' not in r.text
    assert 'href="/dev/egress"' not in r.text


def test_developer_work_pages_have_actions_not_duplicate_plans(client):
    page = client.get('/dev/train?tab=data').text
    assert '완료한 검수 결과 반영' in page
    assert 'action="/dev/data/grounded-review-config"' not in page
    assert 'id="datasetBuildForm"' not in page and '<details' not in page
    legacy = client.get('/dev/train?tab=data&view=legacy').text
    assert 'id="datasetBuildForm"' in legacy and '데이터 묶음 이력' in legacy
    settings = client.get('/dev/train?tab=settings').text
    assert 'action="/dev/data/grounded-review-config"' not in settings
    for url in ['/dev/docs', '/dev/quality', '/dev/pii']:
        page = client.get(url).text
        assert '← 개발 현황' not in page


def test_privacy_tabs_and_subscription_check(client, monkeypatch):
    from zzaimy.app import subscription_status
    monkeypatch.setattr(subscription_status, 'probe', lambda p: {'provider': p, 'installed': False, 'state': 'missing'})
    response = client.post('/dev/pii/subscriptions/check')
    assert response.status_code == 200
    assert 'CLI 미설치' in response.text
    assert '아스트라 · Codex' in response.text and 'Claude Code' in response.text
    assert '기존 외부 API' not in response.text
    assert 'API 호출 미사용' not in response.text
    assert 'action="/dev/egress/submit"' not in response.text
    assert '마스킹 기록 —' not in response.text
    invalid = client.get('/dev/pii?view=invalid').text
    assert '내부 마스킹 설정' in invalid
    assert '<details' not in invalid


def test_export_selection_is_honored(client):
    import io
    import zipfile

    r = client.get("/dev/train/export.zip?rag=0&datasets=0&model=0", follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]   # 전부 해제 → 안내
    page = client.get(r.headers["location"])
    assert page.status_code == 200 and "하나 이상 선택" in page.text   # err 가 실제로 렌더된다
    r = client.get("/dev/train/export.zip?rag=0&datasets=1&model=0")
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert not any(n.startswith("rag/") for n in names)              # 해제한 항목은 빠진다
    page = client.get("/dev/train?tab=exports")
    assert 'type="hidden" name="rag" value="0"' in page.text


def test_train_page_holds_tool_accounts_once(client):
    page = client.get("/dev/train?tab=settings").text
    assert "toolModal-labelstudio" in page and "/dev/accounts" not in page
    # 비밀번호는 같은 창 안에서 화면만 바꿔 받는다 — 접이식(details)으로 펼치지 않는다
    assert "비밀번호 변경" in page and "data-pw-open" in page and "<details" not in page
    assert 'href="/dev/train"' in page
    assert 'grounded-review-config' not in page
    assert 'tool-status-row' in page
    assert "준비된 학습 데이터" not in page                 # 목록은 데이터 공방이 원본


def test_training_workflow_is_one_page(client):
    for url in ("/dev/train", "/dev/train?tab=data", "/dev/train?tab=models", "/dev/train?tab=exports"):
        response = client.get(url)
        assert response.status_code == 200
        page = response.text
        assert '<title>데이터·학습' in page
        assert 'action="/dev/data/grounded-pull"' in page
        assert 'id="approved-data"' in page and 'id="training-tools"' in page
        assert 'action="/dev/train/export.zip"' in page
        assert page.count('>데이터·학습</a>') == 1
        assert '>승인 산출물</a>' not in page
        assert '데이터는 자동 전달되지 않으며' in page
    overview = client.get('/dev').text
    assert 'data-model-progress' in overview and 'data-learning-sequence' in overview
    assert '모델 계획 읽기' not in overview


def test_training_actions_and_empty_outputs_are_compact(client, monkeypatch):
    from html.parser import HTMLParser
    class Tags(HTMLParser):
        def __init__(self, text):
            super().__init__()
            self.tags = []
            self.feed(text)
        def handle_starttag(self, tag, attrs):
            self.tags.append((tag, dict(attrs)))
    from zzaimy.dataset.ls_client import LabelStudioClient
    client.app.state.db.set_setting('labelstudio_url', 'http://127.0.0.1:9')
    client.app.state.db.set_setting('labelstudio_token', 'test-placeholder')
    monkeypatch.setattr(LabelStudioClient, 'status', lambda self, project: {
        'ok': True, 'project_id': 1, 'total': 12, 'done': 0, 'pending': 12,
    })
    text = client.get('/dev/train').text
    actions = text.split('<div class="review-actions">', 1)[1].split('</div>', 1)[0]
    assert 'review-primary' in actions and 'action="/dev/data/grounded-pull"' in actions
    for row in text.split('<div class="training-output-row">')[1:]:
        row = row.split('</div>\n    ', 1)[0]
        if 'output-empty' in row:
            assert 'type="checkbox"' not in row
    tags = Tags(text).tags
    for tag, attrs in tags:
        if tag == 'a' and attrs.get('href', '').startswith('/dev/train/export/file?'):
            assert '다운로드' in attrs['aria-label']
    overview = client.get('/dev').text
    assert overview.count('class="model-comparison-row"') == 4
    assert overview.split('class="learning-flow"', 1)[1].split('</ol>', 1)[0].count('<li>') == 3


def test_receipt_no_never_reused_after_delete(tmp_path):
    from zzaimy.app.db import Database

    db = Database(tmp_path / "t.db")
    a = db.add_document(filename="a.pdf", stored_path="/x", doc_type="recruit")
    b = db.add_document(filename="b.pdf", stored_path="/x", doc_type="recruit")
    nb = db.get_document(b)["receipt_no"]
    db.delete_document(a)                                  # 앞 문서를 지워도
    c = db.add_document(filename="c.pdf", stored_path="/x", doc_type="recruit")
    nc = db.get_document(c)["receipt_no"]
    assert nc != nb and nc.endswith("-0003")               # 번호는 이어지고 겹치지 않는다
    assert nc.split("-")[1] == nb.split("-")[1]            # 같은 유형 코드


# ── 문서 열람 화면 가독성 (2026-09-15) — 목록의 한 줄 정보·본문 손질·이력 묶음은 전부 실제 파일에서 나온다

_DOCS = __import__("pathlib").Path(__file__).resolve().parents[1] / "docs"


def test_docs_list_pulls_status_and_date_from_adr_files(client):
    import re

    page = client.get("/dev/docs?view=decisions&q=0001").text
    adr = sorted(p for p in (_DOCS / "decisions").glob("0*.md") if p.name != "0000-template.md")[0]
    head = adr.read_text(encoding="utf-8").splitlines()[:8]
    status = next(re.match(r"-\s*\**상태\**\s*:\s*(.+)", ln.strip()).group(1)
                  for ln in head if "상태" in ln)
    status = re.split(r"[\s(（]", status)[0]
    date = next(re.search(r"\d{4}-\d{2}-\d{2}", ln).group(0) for ln in head if "날짜" in ln)
    row = page.split(f'href="/dev/doc/decisions/{adr.name}"', 1)[1].split("</a>", 1)[0]
    assert f'<span class="doc-num">{adr.name[:4]}</span>' in row      # ADR 번호는 따로 떼어 보인다
    assert status in row and date in row                               # 상태·날짜는 파일의 머리 줄에서
    page = client.get("/dev/docs").text + client.get("/dev/docs?view=records").text
    assert "측정 기록" in page and "설계·계획" in page and "먼저 볼 것" in page
    for f in ("retrieval-baseline-mini.md", "embed-v0-report.md", "model-plan.md", "quality-system.md"):
        assert f'href="/dev/doc/{f}"' in page                          # dev_doc 이 여는 파일은 목록에도 있다


def test_docs_decisions_supersession_and_paging(client):
    page = client.get('/dev/docs?view=decisions&q=0014').text
    row = page.split('href="/dev/doc/decisions/0014-', 1)[1].split('</a>', 1)[0]
    assert '대체됨' in row and '확정' not in row
    page = client.get('/dev/docs?view=decisions').text
    assert page.count('class="doc-row"') <= 12
    assert '다음</a>' in page
    assert '검색 결과가 없습니다' in client.get('/dev/docs?view=decisions&q=no-such-document').text
    assert '논문 자료' in client.get('/dev/docs?view=invalid').text


def test_doc_view_joins_wrapped_lines_and_builds_toc(client):
    import re

    src = (_DOCS / "paper" / "논문-원재료.md").read_text(encoding="utf-8").splitlines()
    page = client.get("/dev/paper/논문-원재료.md").text
    body = page.split('<article class="doc-body">', 1)[1].split("</article>", 1)[0]
    # 줄바꿈으로 감싼 문단 — 앞줄 끝 낱말과 뒷줄 첫 낱말이 같은 <p> 안에 붙는다
    block = re.compile(r"^(\s*[-*+]\s|\s*\d+[.)]\s|#{1,6}\s|\s*\||>|```)")
    pair, n_h2, fence = None, 0, False
    for i, a in enumerate(src):
        if a.lstrip().startswith("```"):
            fence = not fence
        if not fence and a.startswith("## "):
            n_h2 += 1
        b = src[i + 1] if i + 1 < len(src) else ""
        if fence or pair or not (a.strip() and b.strip()) or block.match(a) or block.match(b):
            continue
        t1, t2 = a.split()[-1], b.split()[0]
        if not any(ch in t1 + t2 for ch in "`*<>&\"'"):
            pair = f"{t1} {t2}"
    if pair is None:
        pytest.skip("감싼 문단이 없는 문서")
    assert pair in body
    assert 'class="doc-toc"' in page and body.count('<h4 id="s') == n_h2   # 목차는 ##·### 제목에서
    title = next(ln[2:].strip() for ln in src if ln.startswith("# "))
    assert title in page.split("<article", 1)[0] and "<h3" not in body      # 제목은 머리에 한 번만
    assert page.count('href="/dev/docs"') == 2  # sidebar + document-list return


def test_doc_view_renders_quote_code_and_rule(client):
    plan = client.get("/dev/doc/model-plan.md").text
    if any(ln.startswith("> ") for ln in (_DOCS / "model-plan.md").read_text(encoding="utf-8").splitlines()):
        assert "<blockquote>" in plan and "&gt; " not in plan.split('<article', 1)[1]
    hand = client.get("/dev/doc/HANDOFF.md").text
    src = (_DOCS / "HANDOFF.md").read_text(encoding="utf-8")
    if "`" in src:
        assert "<code>" in hand and "`" not in hand.split('<article', 1)[1].split("<pre", 1)[0]
    if "\n---\n" in src:
        assert "<hr>" in hand and ">---</p>" not in hand
    assert "export.hwpx" not in hand                                       # 설계 문서는 내보내기 없음


def test_history_groups_by_day_with_real_counts(client):
    import re

    page = client.get("/dev/history").text
    now = (_DOCS / "dev-now.md").read_text(encoding="utf-8").partition("\n## 최근 작업")[2]
    n_days = sum(1 for ln in now.splitlines() if ln.startswith("### "))
    assert page.count('class="tl-day"') == n_days                         # 작업 기록은 날짜별 구역
    import subprocess
    log = subprocess.run(["git", "log", "--no-merges", "--format=%ad", "--date=short"],
                         capture_output=True, text=True, cwd=str(_DOCS.parent)).stdout.split()
    days = set(log)
    assert f'<span class="cl-count">{len(days)}일 · {len(log)}건</span>' in page   # 깃 커밋이 정본
    assert page.count('class="cl-day"') == len(days)
    assert '<details class="cl" open>' in page and "cl-chev" in page       # 펼친 채로 — 이 화면의 본문이다
    assert 'class="cl-tag"' in page and "깃 커밋" in page                  # 커밋마다 영역 표식
    assert 'class="wk-label"' not in page                               # 주간 보고 요약 블록은 논문 자료 칸으로(2026-09-22)
    assert 'href="/dev/docs#weekly"' in page


def test_connection_page_shows_what_is_in_use_without_popups(client, monkeypatch, tmp_path):
    """연결 화면의 첫 정보는 '지금 무엇에 무엇이 쓰이는가'다 — 고르는 칸·팝업은 그 다음."""
    from zzaimy.app import main as app_main
    from zzaimy.generate import llm_connections as lc
    from zzaimy.generate import model_config

    lc.configure(tmp_path / "llm.json"); model_config.set_override("", ""); model_config.reset_status_cache()
    conn = lc.add("교내 DGX", "vllm", "http://dgx:11434/v1", "qwen3.6:35b", "")
    lc.activate(conn["id"])
    monkeypatch.setattr(app_main, "_live_models_cached", lambda cid, ttl=0: {
        "ok": True, "models": [{"id": "qwen3.8:27b"}, {"id": "qwen3.6:35b"}], "error": ""})
    page = client.get("/dev/train?tab=settings").text
    # 서버가 지금 내어 주는 모델을 고를 수 있다 — 어제 받은 모델이 안 보이던 문제
    assert "qwen3.8:27b" in page
    # 연결(서버) 관리와 단계별 모델 선택은 나뉘어 있다
    assert "LLM 연결" in page and "단계별 모델" in page
    # 단계마다 지금 쓰는 서버·모델이 값으로 보이고, 물려받은 줄에는 '기본' 표시만 붙는다
    assert 'class="pill use-tag">기본<' in page and "교내 DGX" in page
    # 목록·열람용 팝업을 따로 두지 않는다 — 서버가 내어 주는 모델 전체는 설정(톱니) 안에 있다
    assert 'id="llmModels-' not in page and 'id="llmSet-' in page
    lc.configure(tmp_path / "none.json"); model_config.set_override("", ""); model_config.reset_status_cache()


def test_search_stages_show_where_they_run_not_a_picker(client, monkeypatch):
    """검색 단계(임베딩·리랭킹)는 고르는 칸이 아니라 지금 어디서 도는지를 보여 준다."""
    from zzaimy.app import search_serving

    search_serving.clear_cache()
    monkeypatch.setenv("ZZAIMY_EMBED_URL", "http://211.170.162.121:8014/embed")
    monkeypatch.delenv("ZZAIMY_RERANK_URL", raising=False)
    monkeypatch.setattr(search_serving, "_health",
                        lambda url: {"ok": True, "model": "/models/KURE-v1", "detail": ""})
    got = {p["key"]: p for p in search_serving.status(ttl=0)}
    assert got["embed"]["remote"] and got["embed"]["model"] == "KURE-v1"
    assert "211.170.162.121:8014" in got["embed"]["where"]
    assert not got["rerank"]["remote"] and "VM" in got["rerank"]["where"]
    page = client.get("/dev/train?tab=settings").text
    # 계획서 모델 4종이 지금 어디서 도는지 한 표에서 보인다
    assert "계획 모델과 현재 서빙 연결" in page and "②ZZAIMY-Rerank" in page and "계획 KURE-v1" in page
    # 쓰이지 않는 단계를 고르게 두지 않는다 — 학습 서버 지정 칸은 없다
    assert 'value="train"' not in page
    search_serving.clear_cache()


def test_stage_models_save_once_atomically(client, monkeypatch, tmp_path):
    """단계별 모델은 줄마다 저장하지 않고 한 번에 저장한다 — 중간 실패로 절반만 바뀌지 않게."""
    from zzaimy.app import main as app_main
    from zzaimy.generate import llm_connections as lc
    from zzaimy.generate import model_config

    lc.configure(tmp_path / "llm.json"); model_config.set_override("", ""); model_config.reset_status_cache()
    a = lc.add("교내 DGX", "vllm", "http://dgx:11434/v1", "qwen3.6:35b", "")
    b = lc.add("토르 03", "vllm", "http://thor:11434/v1", "qwen3:30b", "")
    lc.activate(a["id"])
    monkeypatch.setattr(app_main, "_live_models_cached", lambda cid, ttl=0: {
        "ok": True, "models": [{"id": "qwen3.8:27b"}, {"id": "qwen3.6:35b"}], "error": ""})

    page = client.get("/dev/train?tab=settings").text
    assert 'action="/dev/llm/roles"' in page and 'id="useSave"' in page
    assert page.count('name="role"') == len(lc.ROLES)      # 단계마다 한 줄, 저장 버튼은 하나
    # 고르는 칸은 선택창이 아니라 칩이다 — 목록이 OS 팝업으로 떠 스크롤을 가로채지 않게
    assert "data-role-pick" in page and 'class="chips' in page
    assert "data-role-server" not in page and "data-role-model" not in page
    assert 'data-server="' in page and 'data-model="qwen3.8:27b"' in page

    # 한 번의 제출로 두 단계를 정한다 (브라우저와 같은 폼 인코딩 — 같은 이름이 반복된다)
    def submit(items):
        from urllib.parse import urlencode

        return client.post("/dev/llm/roles", content=urlencode(items),
                           headers={"Content-Type": "application/x-www-form-urlencoded"},
                           follow_redirects=False)

    r = submit([("role", "answer"), ("pick", a["id"] + "|qwen3.8:27b"),
                ("role", "vision"), ("pick", b["id"] + "|")])
    assert "ok=" in r.headers["location"]
    got = {x["role"]: x for x in lc.roles_public()}
    assert got["answer"]["id"] == a["id"] and got["answer"]["model"] == "qwen3.8:27b"
    assert got["vision"]["id"] == b["id"]

    # 없는 연결이 섞이면 아무것도 바뀌지 않는다
    r = submit([("role", "answer"), ("pick", "없음|"),
                ("role", "vision"), ("pick", "")])
    assert "err=" in r.headers["location"]
    got = {x["role"]: x for x in lc.roles_public()}
    assert got["answer"]["id"] == a["id"] and got["vision"]["id"] == b["id"]   # 그대로다
    lc.configure(tmp_path / "none.json"); model_config.set_override("", ""); model_config.reset_status_cache()


def test_weekly_report_card_and_template(client, tmp_path):
    """논문 자료 화면에 주간 보고서 칸 — 이번 주 만들기·지난 보고 목록. 양식 파일이 있으면 그 구성을 쓴다."""
    page = client.get("/dev/docs").text
    assert "주간 보고서" in page and "보고서 생성" in page
    assert "/dev/weekly.docx" not in page and "다시 만들기" not in page
    assert "/dev/weekly/generate" in page and "지난 보고서" in page
    assert 'name="feedback"' not in page and "docs/weekly/양식.md 적용" not in page
    st = client.get("/dev/weekly/status").json()
    assert set(st) == {"running", "error", "exists"}
    r = client.get("/dev/weekly.md")
    assert r.status_code == 200 and "주간업무보고" in r.text


def test_weekly_feedback_is_saved_and_used(client, tmp_path, monkeypatch):
    """지난 보고서의 피드백은 그 보고서에만 적용하고 이전 본문을 보관한다."""
    from types import SimpleNamespace as NS
    import time
    from zzaimy.app import storage
    from zzaimy.generate import client as llm
    folder = storage.report_dir(tmp_path, "주간")
    report = folder / "2026-09-21.md"
    report.write_text("# 지난 보고서\n\n기존 성과", encoding="utf-8")
    prompts = []
    def create(**kw):
        prompts.append(kw["messages"][0]["content"])
        return NS(choices=[NS(message=NS(content="# 지난 보고서\n\n개선된 성과"))])
    monkeypatch.setattr(llm, "VllmClient", lambda **kw: NS(model="fake", client=NS(chat=NS(completions=NS(create=create)))))
    r = client.post("/dev/weekly/feedback", data={"stem": "2026-09-21", "feedback": "성과를 간결하게"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/dev/weekly/2026-09-21"
    for _ in range(100):
        if not client.get("/dev/weekly/status").json()["running"]:
            break
        time.sleep(.01)
    assert "개선된 성과" in report.read_text()
    assert "기존 성과" in prompts[0] and "성과를 간결하게" in prompts[0]
    assert len(list((folder / "history" / "2026-09-21").glob("*.md"))) == 1
    page = client.get("/dev/docs").text
    assert "2026-09-21.feedback" not in page and "성과를 간결하게" not in page
    detail = client.get("/dev/weekly/2026-09-21").text
    assert "보고서 다듬기" in detail and "성과를 간결하게" in detail
    assert client.get("/dev/weekly/2026-09-21.feedback").status_code == 404


def test_weekly_schedule_korean_monday():
    from datetime import datetime
    from zzaimy.app.weekly_schedule import scheduled_week
    assert scheduled_week(datetime.fromisoformat("2026-09-28T00:00:00+00:00")) == "2026-09-28"
    assert scheduled_week(datetime.fromisoformat("2026-09-27T23:59:00+00:00")) is None
    assert scheduled_week(datetime.fromisoformat("2026-09-29T00:00:00+00:00")) is None


def test_notification_grant_has_no_decision_alert(client):
    import re
    client.post('/upload', data={'doc_type': 'grant'},
                files={'file': ('notification-test.pdf', b'%PDF', 'application/pdf')})
    page = client.get('/dev/docs').text
    bell = page.split('id="bellPop"', 1)[1].split('id="userPop"', 1)[0]
    assert '판정' not in bell
    assert '최근 검토 완료' in bell
    button = page.split('data-pop="bellPop"', 1)[1].split('</button>', 1)[0]
    assert 'badge-n' not in button
    link = re.search(r'href="(/doc/\d+)" data-force-navigation', bell)
    assert link
    assert client.get(link.group(1)).status_code == 200


def test_weekly_failure_keeps_existing_report(client, tmp_path, monkeypatch):
    import time
    from zzaimy.app import storage
    from zzaimy.generate import client as llm
    folder = storage.report_dir(tmp_path, "주간")
    report = folder / "2026-09-21.md"
    report.write_text("# 기존 보고\n\n유지할 내용", encoding="utf-8")
    def fail(**kw):
        raise RuntimeError("test unavailable")
    monkeypatch.setattr(llm, "VllmClient", fail)
    client.post("/dev/weekly/feedback", data={"stem": "2026-09-21", "feedback": "수정"}, follow_redirects=False)
    for _ in range(100):
        if not client.get("/dev/weekly/status").json()["running"]:
            break
        time.sleep(.01)
    assert "유지할 내용" in report.read_text()
    assert "기존 보고서는 유지됩니다" in client.get("/dev/weekly/2026-09-21").text


def test_weekly_markdown_is_tidied_for_screen_and_export():
    """연번 항목 앞 빈 줄·세부 줄 '   - ' 로 골라야 화면(마크다운)과 내보내기(docx·hwpx)가 1,2,3 을 항목으로 본다."""
    from zzaimy.app.draft_export import parse_draft
    from zzaimy.app.main import _tidy_weekly_md

    md = "【이번 주 한 일】\n1. 장비 구성\n- 토르 2대\n2. 문서 반입\n• 194건\n【다음 주 계획】\n1. 파일럿"
    assert len([b for s in parse_draft(md) for b in s["blocks"]]) == 1          # 정리 전: 한 문단에 뭉침
    tidy = _tidy_weekly_md(md)
    assert "\n\n2) 문서 반입\n   - 194건" in tidy and tidy.startswith("【이번 주 한 일】\n\n1) 장비 구성\n   - 토르 2대")
    blocks = [b for s in parse_draft(tidy) for b in s["blocks"]]
    assert [b["kind"] for b in blocks] == ["p", "list", "list", "p", "list"]
