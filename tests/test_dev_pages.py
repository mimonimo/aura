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
    assert r2.status_code == 200 and "RAG 조각" in r2.text


def test_hwp_console_hides_internal_op(client):
    r = client.get("/dev/hwp")
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
    assert "논문 자료" in d and "설계 결정" in d and "측정 기록·인수인계" in d
    hpage = client.get("/dev/history").text
    assert "전체 변경 목록" in hpage and "/dev/weekly.docx" in hpage
    r2 = client.get("/dev/egress")
    assert r2.status_code == 200 and "전송 실패" in r2.text
    r3 = client.get("/dev/corpus")
    assert r3.status_code == 200


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
    page = client.get("/dev/train")
    assert 'type="hidden" name="rag" value="0"' in page.text


def test_train_page_holds_tool_accounts_once(client):
    page = client.get("/dev/train").text
    assert "toolModal-labelstudio" in page and "/dev/accounts" not in page
    # 비밀번호는 같은 창 안에서 화면만 바꿔 받는다 — 접이식(details)으로 펼치지 않는다
    assert "비밀번호 변경" in page and "data-pw-open" in page and "<details" not in page
    assert 'href="/dev/data"' not in page                # 데이터 공방 진입은 허브에서만
    assert "준비된 학습 데이터" not in page                 # 목록은 데이터 공방이 원본


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

    page = client.get("/dev/docs").text
    adr = sorted(p for p in (_DOCS / "decisions").glob("0*.md") if p.name != "0000-template.md")[0]
    head = adr.read_text(encoding="utf-8").splitlines()[:8]
    status = next(re.match(r"-\s*\**상태\**\s*:\s*(.+)", ln.strip()).group(1)
                  for ln in head if "상태" in ln)
    status = re.split(r"[\s(（]", status)[0]
    date = next(re.search(r"\d{4}-\d{2}-\d{2}", ln).group(0) for ln in head if "날짜" in ln)
    row = page.split(f'href="/dev/doc/decisions/{adr.name}"', 1)[1].split("</a>", 1)[0]
    assert f'<span class="doc-num">{adr.name[:4]}</span>' in row      # ADR 번호는 따로 떼어 보인다
    assert status in row and date in row                               # 상태·날짜는 파일의 머리 줄에서
    assert "측정 기록" in page and "인수인계·계획" in page
    for f in ("retrieval-baseline-mini.md", "embed-v0-report.md", "model-plan.md", "quality-system.md"):
        assert f'href="/dev/doc/{f}"' in page                          # dev_doc 이 여는 파일은 목록에도 있다


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
    assert page.count('href="/dev/docs"') == 1                             # 뒤로가기 하나


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
    log = [ln for ln in (_DOCS / "dev-changelog.md").read_text(encoding="utf-8").splitlines()
           if re.match(r"- \d{2}-\d{2} ", ln)]
    days = {ln.split()[1] for ln in log}
    assert f'<span class="cl-count">{len(days)}일 · {len(log)}건</span>' in page
    assert page.count('class="cl-day"') == len(days)
    assert '<details class="cl">' in page and "cl-chev" in page            # 기본 삼각형 대신 펼침 버튼
    assert 'class="h-link"' in page and 'href="/dev/weekly.docx"' in page  # 주간 보고서는 머리 오른쪽 h-link
    assert "위 버튼으로 생성" not in page
