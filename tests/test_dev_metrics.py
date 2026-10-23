"""화면 지표 모듈·개발 화면 이름표·확인창·링크 — 같은 이름은 같은 숫자, 다른 숫자는 다른 이름."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import dev_nav, metrics
from zzaimy.app.main import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(tmp_path):
    metrics._cache.update(at=0.0, path=None, data=None)
    app = create_app(db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter())
    return TestClient(app)


# ---- 지표 정의 ----

def test_every_metric_has_distinct_label_per_source_and_a_definition():
    for m in metrics.DEFS.values():
        assert m.label and m.definition and m.source in metrics.SOURCE_LABEL
    # 다른 집합인데 이름이 같으면 화면에서 모순처럼 보인다 — 같은 이름은 같은 정의여야 한다
    by_label: dict[str, set[str]] = {}
    for m in metrics.DEFS.values():
        by_label.setdefault(m.label, set()).add(m.definition.split(" — ")[0])
    assert all(len(v) == 1 for v in by_label.values()), by_label


def test_label_html_escapes_and_carries_definition():
    html = str(metrics.label_html("archive_linked", "2026-01-02 03:04", "<b>연결</b>"))
    assert "&lt;b&gt;연결&lt;/b&gt;" in html
    assert "라이브러리 문서와 이어진 원본 파일" in html and "기준: 지금 DB 2026-01-02 03:04" in html


def test_label_html_is_info_icon_tooltip_not_underlined_abbr():
    """지표 이름은 밑줄 친 <abbr title> 이 아니라 이름 + 정보 아이콘 + 키보드로도 닿는 툴팁(aria-describedby)."""
    import re
    html = str(metrics.label_html("documents", "", "문서 <3>건"))
    assert "<abbr" not in html and "title=" not in html and "text-decoration" not in html
    m = re.fullmatch(r'<span class="metric" tabindex="0" aria-describedby="([^"]+)">문서 &lt;3&gt;건'
                     r'<iconify-icon class="metric-i" icon="solar:info-circle-linear" aria-hidden="true"></iconify-icon>'
                     r'<span class="metric-tip" id="([^"]+)" role="tooltip" hidden>([^<]+)</span></span>', html)
    assert m and m.group(1) == m.group(2) and m.group(3) == metrics.tip("documents")
    other = str(metrics.label_html("documents"))
    assert re.search(r'aria-describedby="([^"]+)"', other).group(1) != m.group(1)   # 한 화면에 여러 번 써도 id 가 겹치지 않는다
    assert metrics.DEFS["documents"].label in other


def test_live_counts_on_fresh_db_and_as_of(client, tmp_path):
    from zzaimy.app.db import Database
    db = Database(tmp_path / "test.db")
    got = metrics.live(db, force=True)
    assert set(got["values"]) >= {"archive_files", "documents", "grant_chunks", "graph_nodes", "embedding_index"}
    assert all(isinstance(v, int) for v in got["values"].values())
    assert got["values"]["documents_full"] == got["values"]["documents"] - got["values"]["documents_light"]
    one = metrics.get(db, "documents")
    assert one["value"] == got["values"]["documents"] and one["tip"].startswith("라이브러리에 등록된 문서 전부")


def test_audit_reads_pipeline_file_by_metric_name(tmp_path):
    assert metrics.audit(tmp_path) is None
    (tmp_path / "pipeline_audit.json").write_text(json.dumps(
        {"at": "2031-05-06 07:08", "targets": 12, "in_docbox": 7, "grant_docs": 5, "graph_doc_nodes": 6}), encoding="utf-8")
    got = metrics.audit(tmp_path)
    assert got["at"] == "2031-05-06 07:08"
    assert got["values"]["audit_targets"] == 12 and got["values"]["audit_in_library"] == 7
    assert got["values"]["audit_graph_docs"] == 5 and got["values"]["audit_missed"] == 0


@pytest.mark.parametrize("raw,want", [
    ("2031-03-04T13:00:49.821066+00:00", "2031-03-04 22:00"),   # UTC·마이크로초 → 한국시간
    ("2031-03-04T23:30:00+09:00", "2031-03-04 23:30"),
    ("2031-12-31T20:00:00Z", "2032-01-01 05:00"),
    ("2031-03-04 08:15:00", "2031-03-04 08:15"),                # 시간대 없음 → 그대로
    ("", ""), (None, ""),
])
def test_kst(raw, want):
    assert metrics.kst(raw) == want


@pytest.mark.parametrize("ms,want", [(850, "850ms"), (12_000, "12초"), (61_000, "1분 1초"), (120_000, "2분"),
                                     (12_931_001, "3시간 35분"), (7_200_000, "2시간"), ("x", "")])
def test_duration(ms, want):
    assert metrics.duration_ms(ms) == want


def test_num():
    assert metrics.num(1543190) == "1,543,190" and metrics.num("7") == "7" and metrics.num(None) == ""


# ---- 화면 배선 ----

def test_dev_pages_show_metric_definitions(client):
    page = client.get("/dev").text
    assert "등록된 문서 전부" in page                       # 접수 문서 타일의 정의 툴팁
    assert "문서에서 뽑은 개체" in page and "개체(그래프)" not in page
    assert "그래프 노드" in page and "지식그래프와 별개" in page
    assert "/graph\"" not in page                         # 404 였던 그래프 링크
    lib = client.get("/?type=all").text
    assert "지금 계정이 볼 수 있는 등록 문서" in lib


def test_intake_page_names_each_number(client, tmp_path):
    (tmp_path / "pipeline_audit.json").write_text(json.dumps(
        {"at": "2031-05-06 07:08", "archive_files": 99, "targets": 10, "in_docbox": 4, "failed": 1, "waiting": 2,
         "missed": 3, "grant_docs": 4, "graph_doc_nodes": 4, "grant_chunks": 40, "embedding_index": 40,
         "lexical_index": 40, "stuck_docs": 0, "composition": {}, "alerts": []}), encoding="utf-8")
    page = client.get("/dev/intake").text
    assert "처리 대상 문서 가운데 라이브러리 문서로 들어온 것" in page
    assert "그래프 대상 사업 문서 수" in page and "문서함" not in page
    assert 'data-confirm="지금 맞출까요?' in page and 'data-confirm="지금 다시 점검할까요?' in page


def test_heavy_dev_actions_ask_first(client):
    q = client.get("/dev/quality").text
    assert "/dev/eval/run" not in q or 'data-confirm="규정 검색 품질을 다시 잴까요?' in q


def test_shared_confirm_covers_any_form_with_data_confirm():
    js = (ROOT / "src/zzaimy/app/static/workspace-navigation.js").read_text(encoding="utf-8")
    assert "'confirm' in form.dataset" in js and "'ownConfirm' in form.dataset" in js
    # 확인창은 공용 대화상자 하나 — 브라우저 기본 confirm() 을 띄우지 않고, 구글 문서 화면도 따로 묻지 않는다
    assert "window.confirm(" not in js and "showModal()" in js
    assert "form[data-confirm]" not in (ROOT / "src/zzaimy/app/static/gdocs-work.js").read_text(encoding="utf-8")


# ---- 이름표 ----

def test_dev_titles_match_sidebar_names(client):
    for href, name in [("/dev/db", "데이터 현황"), ("/dev/users", "사용자·계정"), ("/dev/pii", "개인정보·반출 점검"),
                       ("/dev/intake", "문서 가져오기"), ("/dev", "개발 현황")]:
        page = client.get(href).text
        assert f"<title>{name}" in page, href
        sidebar = page.split("data-developer-navigation", 1)[1].split("</aside>", 1)[0]
        assert f'aria-current="page">{name}</a>' in sidebar, href


def test_every_nav_entry_has_a_name_and_page_name_rejects_unknown():
    for _, links in dev_nav.GROUPS:
        for href, name in links:
            assert dev_nav.page_name(href) == name
    with pytest.raises(KeyError):
        dev_nav.page_name("/dev/없는-화면")


@pytest.mark.parametrize("url", ["/dev/train?tab=models", "/dev/train?tab=exports", "/dev/train?tab=whatever"])
def test_train_accepts_only_tabs_it_shows(client, url):
    r = client.get(url + "&err=안내", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/dev/train?tab=data")
    assert "err=" in r.headers["location"]


# ---- 링크 ----

def test_progress_links_resolve(client):
    data = json.loads((ROOT / "docs/progress.json").read_text(encoding="utf-8"))
    hrefs = {lnk["href"] for f in data.get("features", []) for lnk in f.get("links", []) if lnk["href"].startswith("/")}
    assert hrefs
    for href in sorted(hrefs):
        if href.startswith("/dev/doc/") or href.startswith("/dev/paper/"):
            continue                                          # 문서 화면은 test_dev_pages 가 본다
        status = client.get(href, follow_redirects=False).status_code
        assert status != 404, href
    assert "updated" not in data                              # 기준일은 파일의 마지막 변경일에서 — 손으로 적지 않는다


# ---- ADR 번호 ----

def _adr_module():
    spec = importlib.util.spec_from_file_location("adr_index", ROOT / "scripts/127_adr_index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_new_adr_number_collisions():
    mod = _adr_module()
    assert mod.new_duplicates(mod.adr_files()) == {}


def test_adr_duplicate_detection_on_unseen_numbers(tmp_path):
    mod = _adr_module()
    for name in ("0001-a.md", "0002-b.md", "0002-c.md", "0099-d.md", "0000-template.md"):
        (tmp_path / name).write_text("# t\n", encoding="utf-8")
    files = mod.adr_files(tmp_path)
    assert mod.duplicates(files) == {"0002": ["0002-b.md", "0002-c.md"]}
    assert mod.next_number(files) == "0100"


# ---- 주간 보고 제목 ----

def test_weekly_titles_follow_project_week_rule(client):
    page = client.get("/dev/docs").text
    titles = re.findall(r"(\d+)주차 주간 보고 \((\d{2}-\d{2}) ~ (\d{2}-\d{2})\)", page)
    # 저장소 수기본 2026-09-08-w2.md — 실행 계획의 가용 기간 시작일(09-01)에서 7일 단위로 센다
    assert ("2", "09-08", "09-14") in titles


# ---- 사용자 연결 표시 ----

def test_user_status_names_fallback_account(monkeypatch, tmp_path):
    from zzaimy.app import user_admin
    from zzaimy.app.db import Database
    db = Database(tmp_path / "u.db")
    monkeypatch.setattr(user_admin, "connected_emails", lambda: {"shared@example.ac.kr": {}})
    monkeypatch.setattr(user_admin.gdrive_files, "account_for", lambda db, uid, dept: "shared@example.ac.kr")
    st = user_admin.user_status(db, "someone", None)
    assert st["bound"] == "" and st["effective"] == "shared@example.ac.kr" and st["via"] == "공용 계정 사용"


# ---- 무거운 개발 화면은 작업자를 붙잡지 않는다 ----

def test_legacy_data_view_returns_pending_instead_of_blocking(client, monkeypatch):
    import threading
    import time as _time

    from zzaimy.dataset import build
    gate = threading.Event()

    def slow_status(db):
        gate.wait(30)
        return [{"id": 1, "filename": "a.pdf", "doc_type": "grant", "status": "reviewed",
                 "n_chunks": 3, "indexed": None, "has_review": False, "has_draft": False}]

    monkeypatch.setattr(build, "rag_status", slow_status)
    t0 = _time.time()
    page = client.get("/dev/train?tab=data&view=legacy").text
    assert _time.time() - t0 < 15
    assert "문서별 상태를 세는 중입니다" in page and "등록된 문서가 없습니다" not in page
    gate.set()
    _time.sleep(0.3)
    page = client.get("/dev/train?tab=data&view=legacy").text      # 끝난 결과는 재사용
    assert "세는 중" not in page.split("변환 현황판", 1)[1][:600] and 'href="/doc/1"' in page


def test_project_tile_counts_active_projects_only(client, tmp_path):
    from zzaimy.app.db import Database
    db = Database(tmp_path / "test.db")
    with db._conn() as conn:
        conn.execute("INSERT INTO projects (sector, name, created_at, archived) VALUES ('grant', '진행', '2031-01-01', 0)")
        for i in range(3):
            conn.execute("INSERT INTO projects (sector, name, created_at, archived) VALUES ('grant', ?, '2031-01-01', 1)",
                         (f"묶음{i}",))
    metrics._cache.update(at=0.0, path=None, data=None)
    got = metrics.live(db, force=True)["values"]
    assert got["projects_active"] == 1 and got["projects_archived"] == 3
    page = client.get("/dev").text
    assert "프로젝트 1" in page and "지난 사업 묶음 3개는 별도" in page


def test_dev_role_label_comes_from_access_guard(client):
    from zzaimy.app.access_guard import ROLES
    page = client.get("/settings").text                 # 업무 화면 사이드바의 개발 현황 진입
    assert "개발 현황" in page and "(개발자)" not in page and f"개발 현황 ({ROLES['dev']})" not in page


def test_originals_exclude_platform_copies_and_byproducts(client, tmp_path):
    from zzaimy.app import archive
    from zzaimy.app.db import Database
    db = Database(tmp_path / "test.db")
    archive.ensure(db)
    rows = [("가상과/계획.hwp", "hwp", ""), ("가상과/계획_imgs/그림1.png", "png", ""), (archive.UPLOAD_PREFIX + "반입/원본.pdf", "pdf", ""),
            ("가상과/깨진.pdf", "pdf", "처리 실패: 손상"), ("가상과/사진.jpg", "jpg", "")]
    with db._conn() as conn:
        for rel, ext, analysis in rows:
            conn.execute("INSERT INTO archive_files (rel, ext, analysis, removed_at) VALUES (?, ?, ?, '')", (rel, ext, analysis))
    v = metrics.live(db, force=True)["values"]
    assert v["archive_files"] == 5 and v["archive_originals"] == 3 and v["archive_failed"] == 1
    assert metrics.DEFS["archive_files"].label != metrics.DEFS["archive_originals"].label
    page = client.get("/archive").text
    assert metrics.tip("archive_originals").split(" · ")[0] in page          # 보관소 머리 숫자도 같은 지표 정의


def test_shared_small_button_and_page_head_classes_are_defined_once_in_base():
    """동작 링크용 작은 보조 단추(.mini-btn, 카드 머리용 .h-link 같은 모양)와 머리 줄(.page-head)은 base.html 한 곳."""
    from pathlib import Path

    base = (Path(metrics.__file__).parent / "templates" / "base.html").read_text(encoding="utf-8")
    assert ".mini-btn, .h-link {" in base and ".page-head {" in base and ".page-head-actions {" in base
    assert ".metric-pop {" in base and 'closest(".metric[aria-describedby]")' in base
