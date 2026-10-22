"""프로젝트 화면 작업 문서 — 한글 문서(웹 한글 기안기)와 구글 문서를 한 목록에, 줄마다 그 편집기로 여는 단추.
한글 파일은 보는 사람이 열 수 있는 부서의 것만. 공유 창은 「공유 안 함 / 공유 중 · N」 단추와 찾아 더하기."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from tests.test_webhwp import _hwpx, _login, _setup

from zzaimy.app import project_access as pa
from zzaimy.app import webhwp
from zzaimy.app import webhwp_agent as wa


def _world(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)                  # kim(기획처) · lee(산학협력단)
    db, base = app.state.db, Path(app.state.data_dir)
    pid = db.create_project("grant", "AI 사업", owner="kim")
    sid = db.create_chat_session("초안", project_id=pid, owner="kim")
    bound = webhwp.add(db, base, "기획처", "계획서 작업본.hwpx", _hwpx(), owner="kim", folder="작업/AI 사업")
    wa.attach(db, sid, bound)
    loose = webhwp.add(db, base, "기획처", "예산표.hwpx", _hwpx(), owner="kim", folder="작업/AI 사업/붙임")
    other = webhwp.add(db, base, "산학협력단", "남의 부서.hwpx", _hwpx(), owner="lee", folder="작업/AI 사업")
    unrelated = webhwp.add(db, base, "기획처", "다른 일.hwpx", _hwpx(), owner="kim", folder="작업/다른 사업")
    db.set_setting(f"chat_google_doc:{sid}", json.dumps({"doc": "GDOC_abc-1", "account": "kim@ync.ac.kr"}))
    m = webhwp.meta(db, loose)
    m["drive_out"] = {"id": "U1", "url": "https://drive.google.com/file/d/U1/view", "account": "kim@ync.ac.kr"}
    db.set_setting(f"webhwp_meta:{loose}", json.dumps(m, ensure_ascii=False))
    return app, pid, sid, {"bound": bound, "loose": loose, "other": other, "unrelated": unrelated}


def test_work_docs_list_hangul_and_google_with_their_own_open_buttons(tmp_path, monkeypatch):
    app, pid, sid, f = _world(tmp_path, monkeypatch)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    page = kim.get(f"/project/{pid}").text
    work = page.split('id="workDocs"', 1)[1].split('id="workDocsStatus"', 1)[0]
    assert f'/webhwpctrl/aura-edit?id={f["bound"]}' in work and "계획서 작업본" in work      # 대화에 이은 한글 파일
    assert f'/webhwpctrl/aura-edit?id={f["loose"]}' in work                                  # 「작업/AI 사업」 아래 폴더의 파일
    assert f'/webhwpctrl/aura-edit?id={f["other"]}' not in work                              # 못 여는 부서의 파일은 싣지 않는다
    assert f'/webhwpctrl/aura-edit?id={f["unrelated"]}' not in work                          # 다른 프로젝트 폴더
    assert "https://docs.google.com/document/d/GDOC_abc-1/edit" in work and "구글 문서 <span" in work
    assert "한글 문서 <span" in work and 'data-kind="hwp"' in work and 'data-kind="gdoc"' in work
    assert "https://drive.google.com/file/d/U1/view" in work                                 # 드라이브에 올린 한글 파일은 두 곳 다
    assert f'href="/chat/{sid}"' in work
    assert 'id="workDocCount">3<' in page


def test_hangul_work_docs_follow_viewer_departments(tmp_path, monkeypatch):
    app, pid, sid, f = _world(tmp_path, monkeypatch)
    db = app.state.db
    pa.set_shares(db, pid, "shared", [{"to": "user:lee", "level": "edit"}])
    lee = TestClient(app)
    assert _login(lee, "lee", "pass-12345678")
    page = lee.get(f"/project/{pid}").text
    assert f'aura-edit?id={f["other"]}' in page                    # 내 부서(산학협력단) 작업 폴더
    assert f'aura-edit?id={f["bound"]}' not in page and f'aura-edit?id={f["loose"]}' not in page
    assert "https://drive.google.com/file/d/U1/view" not in page   # 드라이브 최종본은 담당자 화면에만


def test_share_button_and_dialog_wording(tmp_path, monkeypatch):
    app, pid, sid, f = _world(tmp_path, monkeypatch)
    db = app.state.db
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    kim.post(f"/project/{pid}/shares", data={"mode": "private"}, follow_redirects=False)
    page = kim.get(f"/project/{pid}").text
    assert "공유 안 함</button>" in page and "공유 · 나만" not in page
    assert "나만 보기" in page and "함께 쓰기" in page
    assert "참조: 읽기 전용 — 공개 대화와 작업 문서를 읽습니다" in page
    assert "함께 작업: 읽기·쓰기 — 대화를 열고 작업 문서를 고칩니다" in page
    assert 'class="share-owner"' in page and ">소유<" in page
    assert "비공개 대화는 공유해도 다른 사람에게 보이지 않습니다" in page
    assert 'data-share-quick="dept:기획처"' in page                    # 우리 부서 전체 바로 더하기
    choices = json.loads(page.split("data-share-choices>", 1)[1].split("</script>", 1)[0])
    assert {"to": "user:lee", "label": "lee", "hint": "산학협력단"} in choices
    assert any(c["to"] == "dept:산학협력단" for c in choices)
    kim.post(f"/project/{pid}/shares", data={"mode": "shared", "to": ["dept:기획처", "user:lee"], "level": ["view", "edit"]},
             follow_redirects=False)
    page = kim.get(f"/project/{pid}").text
    assert "공유 중 · 2</button>" in page
    assert pa.shares(db, db.get_project(pid))["items"] == [{"to": "dept:기획처", "level": "view"}, {"to": "user:lee", "level": "edit"}]
    rows = page.split("data-share-rows>", 1)[1].split("data-share-empty", 1)[0]
    assert 'value="dept:기획처"' in rows and 'value="user:lee"' in rows and rows.count('<option value="edit" selected') == 1


def test_project_documents_are_one_line_rows_without_more_toggle(tmp_path, monkeypatch):
    app, pid, sid, f = _world(tmp_path, monkeypatch)
    db = app.state.db
    for i in range(9):
        db.add_document(f"붙임 {i}.pdf", f"/tmp/a{i}.pdf", doc_type="grant", project_id=pid)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    page = kim.get(f"/project/{pid}").text
    block = page.split('data-doc-group="attached"', 1)[1].split("</section>", 1)[0]
    assert block.count('class="pw-row"') == 9 and "더 보기" not in block and "chip-toggle" not in block
    assert 'class="pw-list"' in block
