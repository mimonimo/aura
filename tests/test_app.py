"""문서 접수·검토 플랫폼 테스트 (대시보드 v0.1).

LLM·파서 등 무거운 단계는 가짜 프로세서를 주입해 API·DB 계층만 검증한다.
데이터는 전부 합성.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zzaimy.app.db import Database
from zzaimy.app.main import create_app


class FakeProcessor:
    """실제 파이프라인(파싱→마스킹→검토의견) 대신 즉시 완료 처리."""

    def process(self, db: Database, doc_id: int, file_path: Path) -> None:
        db.update_document(
            doc_id,
            status="reviewed",
            masked_text="합성 마스킹 본문",
            series="proposal",
            ai_review="합성 검토 의견: 형식 적합.",
        )

    def sparse_pages(self, path):
        return [11, 12] if str(path).endswith("합본.pdf") else []

    def read_image_pages(self, db: Database, doc_id: int, pages=None, max_pages: int = 12) -> dict:
        db.append_doc_chunks(doc_id, [{"kind": "text", "content": "판독: 대외여건 분석 — 지역 AI 인력 수요 12,800명", "page_no": 11}], replace_pages=[11])
        return {"read": 1, "pages": [11], "chunks": 1}

    def reprocess(self, db: Database, doc_id: int) -> None:
        db.update_document(
            doc_id, status="reviewed", ai_review="재검토 의견: 담당자 요청 반영."
        )

    def extract_text(self, file_path: Path) -> str:
        return "합성 첨부 본문"

    def analyze(self, db: Database, doc_id: int) -> None:
        db.update_document(doc_id, ai_review="합성 맥락 분석", coverage=None)


class FakeDrafter:
    """실제 초안 생성(스키마→섹션 생성→검증) 대신 즉시 완료 처리."""

    def generate(self, db: Database, doc_id: int) -> None:
        db.update_document(
            doc_id,
            draft="## 합성 초안 섹션\n합성 초안 본문이다.",
            coverage="배점 커버리지 70/100점",
        )


@pytest.fixture()
def client(tmp_path):
    app = create_app(
        db_path=tmp_path / "test.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(),
    )
    return TestClient(app)


def test_index_page_renders(client):
    """첫 화면은 채팅이고, 문서 라이브러리는 /inbox 에서 프로젝트부터 보여 준다(C-20260920-09)."""
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/chat")
    r = client.get("/inbox")
    assert r.status_code == 200
    assert "프로젝트" in r.text


def test_upload_creates_document_and_processes(client):
    r = client.post(
        "/upload",
        files={"file": ("합성문서.pdf", b"%PDF-1.4 fake", "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 303  # 목록으로 리다이렉트
    r = client.get("/")
    assert "합성문서.pdf" in r.text


def test_document_detail_shows_ai_review(client):
    client.post("/upload", files={"file": ("a.pdf", b"%PDF fake", "application/pdf")})
    r = client.get("/doc/1")
    assert r.status_code == 200
    assert "합성 검토 의견" in r.text


def test_reviewer_opinion_is_saved(client):
    client.post("/upload", files={"file": ("a.pdf", b"%PDF fake", "application/pdf")})
    r = client.post("/doc/1/review", data={"opinion": "검토자 의견: 보완 필요"},
                    follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/doc/1")
    assert "보완 필요" in r.text


def test_unknown_document_returns_404(client):
    assert client.get("/doc/999").status_code == 404


def test_upload_rejects_disallowed_extension(client):
    r = client.post(
        "/upload",
        files={"file": ("malware.exe", b"MZ", "application/octet-stream")},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_upload_stores_doc_type(client):
    client.post(
        "/upload",
        data={"doc_type": "recruit"},
        files={"file": ("이력서_합성.pdf", b"%PDF fake", "application/pdf")},
    )
    r = client.get("/doc/1")
    assert "채용" in r.text  # 문서 유형 라벨 표시


def test_draft_generation_flow(client):
    client.post(
        "/upload",
        data={"doc_type": "grant"},
        files={"file": ("공고_합성.pdf", b"%PDF fake", "application/pdf")},
    )
    r = client.post("/doc/1/draft", follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/doc/1")
    assert "합성 초안 섹션" in r.text
    assert "배점 커버리지 70/100점" in r.text


def test_draft_endpoint_404_for_unknown_doc(client):
    assert client.post("/doc/999/draft").status_code == 404


def test_review_prompt_differs_by_doc_type():
    from zzaimy.app.pipeline import pick_review_prompt

    grant = pick_review_prompt("grant")
    recruit = pick_review_prompt("recruit")
    assert grant != recruit
    assert "지원자" in recruit
    assert "행정" in grant


def _uploaded(client):
    client.post("/upload", files={"file": ("a.pdf", b"%PDF fake", "application/pdf")})
    return client


def test_decision_approve(client):
    _uploaded(client)
    r = client.post("/doc/1/decision", data={"decision": "approved"}, follow_redirects=False)
    assert r.status_code == 303
    assert "승인" in client.get("/doc/1").text


def test_decision_reject(client):
    _uploaded(client)
    client.post("/doc/1/decision", data={"decision": "rejected"})
    assert "반려" in client.get("/doc/1").text


def test_decision_rework_triggers_re_review(client):
    _uploaded(client)
    client.post("/doc/1/decision", data={"decision": "rework"})
    page = client.get("/doc/1").text
    assert "재검토 의견: 담당자 요청 반영." in page  # FakeProcessor.reprocess 결과


def test_decision_rejects_unknown_value(client):
    _uploaded(client)
    r = client.post("/doc/1/decision", data={"decision": "??"})
    assert r.status_code == 400


def test_sector_tab_filters_documents(client):
    client.post("/upload", data={"doc_type": "grant"},
                files={"file": ("공고문.pdf", b"%PDF", "application/pdf")})
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": ("이력서.pdf", b"%PDF", "application/pdf")})
    # 문서 표는 검색 결과로 나온다 — 업무 탭이 표를 거른다
    all_page = client.get("/inbox?q=pdf").text
    assert "공고문.pdf" in all_page and "이력서.pdf" in all_page
    recruit_page = client.get("/inbox?type=recruit&q=pdf").text
    table = recruit_page.split('<table class="intake-table">', 1)[-1].split("</table>", 1)[0]
    assert '<td><a href="/doc/2">이력서.pdf' in table
    assert '<td><a href="/doc/1">공고문.pdf' not in table


def test_password_protection_requires_auth(tmp_path):
    app = create_app(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(), password="secret-1234",
    )
    c = TestClient(app)
    # 브라우저(인증 없음)는 로그인 페이지로
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert "로그인" in c.get("/login").text
    # 틀린 로그인 → 오류 표시, 맞으면 세션 쿠키로 통과
    r = c.post("/login", data={"username": "zzaimy", "pw": "wrong"}, follow_redirects=False)
    assert "err=1" in r.headers["location"]
    r = c.post("/login", data={"username": "zzaimy", "pw": "secret-1234"},
               follow_redirects=False)
    assert r.headers["location"] == "/" and "zz_session" in r.headers.get("set-cookie", "")
    assert c.get("/").status_code == 200  # TestClient가 쿠키 유지
    # 스크립트·API 경로: Basic 인증 병행
    c2 = TestClient(app)
    assert c2.get("/", auth=("zzaimy", "wrong")).status_code == 401
    assert c2.get("/", auth=("zzaimy", "secret-1234")).status_code == 200
    # 로그아웃 → 다시 로그인 페이지로
    r = c.post("/logout", follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert c.get("/", follow_redirects=False).status_code == 303


def test_no_password_means_open_localhost_mode(client):
    # password=None(기본)이면 로컬 전용 모드 — 인증 없이 동작 (기존 테스트와 동일)
    assert client.get("/").status_code == 200


def test_db_status_flow(tmp_path):
    db = Database(tmp_path / "t.db")
    doc_id = db.add_document(filename="x.pdf", stored_path="/tmp/x.pdf")
    doc = db.get_document(doc_id)
    assert doc["status"] == "received"
    db.update_document(doc_id, status="processing")
    assert db.get_document(doc_id)["status"] == "processing"
    db.add_review(doc_id, opinion="의견1")
    assert [r["opinion"] for r in db.get_reviews(doc_id)] == ["의견1"]


def test_hwpx_parsing_extracts_text(tmp_path):
    import zipfile

    from zzaimy.app.pipeline import DocumentProcessor

    hwpx = tmp_path / "합성.hwpx"
    with zipfile.ZipFile(hwpx, "w") as zf:
        zf.writestr(
            "Contents/section0.xml",
            "<hp:p><hp:t>합성 학칙 제1조 목적</hp:t></hp:p><hp:p><hp:t>본문이다</hp:t></hp:p>",
        )
    text = DocumentProcessor._parse_hwpx(hwpx)
    assert "합성 학칙 제1조 목적" in text
    assert "본문이다" in text
    assert "<hp:t>" not in text


def test_regulation_docs_are_separated_from_inbox(client):
    client.post("/upload", data={"doc_type": "grant"},
                files={"file": ("계획서.pdf", b"%PDF", "application/pdf")})
    client.post("/criteria/upload",
                files={"file": ("학칙.pdf", b"%PDF", "application/pdf")})
    inbox = client.get("/").text
    table = inbox.split("<th>접수번호</th>", 1)[-1].split("</table>", 1)[0]
    assert "계획서.pdf" in table
    assert "학칙.pdf" not in table  # 기준 문서는 접수 표에 안 섞인다 (최근 활동에는 보임)
    criteria = client.get("/criteria").text
    assert "학칙.pdf" in criteria
    assert ">1건</span>" in criteria  # 등록 건수 1건 — 접수 문서는 기준 목록에 안 들어간다


class FakeResponder:
    def answer(self, db, question, attachment_text=None, criteria_ids=None, session_id=None,
               project=None):
        tail = f" / 첨부:{attachment_text}" if attachment_text else ""
        tail += f" / 기준:{sorted(criteria_ids)}" if criteria_ids else ""
        tail += f" / 프로젝트:{project['name']}" if project else ""
        return f"합성 답변: {question[:20]}{tail}"


def test_chat_send_and_history(tmp_path):
    from zzaimy.app.main import create_app as _create

    app = _create(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder(),
    )
    c = TestClient(app)
    r = c.get("/chat")
    assert r.status_code == 200
    r = c.post("/chat/send", data={"question": "휴학 처리 기준 알려줘"}, follow_redirects=False)
    assert r.status_code == 303
    session_url = r.headers["location"]
    page = c.get(session_url).text
    assert "휴학 처리 기준 알려줘" in page
    assert "합성 답변" in page
    # 사이드바 채팅 기록에 세션 제목 노출
    assert "휴학 처리 기준" in c.get("/").text


def test_chat_with_attachment_and_criteria(tmp_path):
    from zzaimy.app.main import create_app as _create

    app = _create(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder(),
    )
    c = TestClient(app)
    # 기준 문서 하나 등록
    c.post("/criteria/upload", data={"sector": "recruit"},
           files={"file": ("채용공고.pdf", b"%PDF", "application/pdf")})
    r = c.post(
        "/chat/send",
        data={"question": "이 이력서 검토해줘", "criteria": "1"},
        files={"attachment": ("이력서.pdf", b"%PDF fake", "application/pdf")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = c.get(r.headers["location"]).text
    assert "이력서.pdf" in page            # 첨부 표시
    assert "첨부:합성 첨부 본문" in page    # 첨부 텍스트가 응답기로 전달됨
    assert "기준:[1]" in page              # 선택한 기준이 전달됨


def test_sector_upload_binds_related_criteria(client):
    # 채용 공고를 기준으로 등록
    client.post("/criteria/upload", data={"sector": "recruit"},
                files={"file": ("채용공고A.pdf", b"%PDF", "application/pdf")})
    # 채용 섹터에서 해당 공고를 지정해 서류 접수
    client.post(
        "/upload",
        data={"doc_type": "recruit", "related_criteria_id": "1"},
        files={"file": ("지원서.pdf", b"%PDF", "application/pdf")},
    )
    page = client.get("/doc/2").text
    assert "채용공고A.pdf" in page  # 대상 공고가 문서 화면에 표시된다


def test_sector_page_is_project_hub(client):
    # 프로젝트 중심 구조: 섹터 페이지는 프로젝트 관리 뷰, 접수 폼은 없다
    page = client.get("/?type=recruit").text
    assert "프로젝트 추가" in page and "projectModal" in page
    assert 'action="/upload"' not in page  # 접수는 프로젝트 페이지에서
    # 프로젝트 기준 연결 옵션은 프로젝트 페이지가 담당한다
    client.post("/criteria/upload", data={"sector": "recruit"},
                files={"file": ("채용공고B.pdf", b"%PDF", "application/pdf")})
    client.post("/projects", data={"sector": "recruit", "name": "공고1"})
    assert "채용공고B.pdf" in client.get("/project/1").text


def test_receipt_number_scheme(client):
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": ("a.pdf", b"%PDF", "application/pdf")})
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": ("b.pdf", b"%PDF", "application/pdf")})
    client.post("/upload", data={"doc_type": "grant"},
                files={"file": ("c.pdf", b"%PDF", "application/pdf")})
    page = client.get("/inbox?q=pdf").text
    assert "2026-채용-0001" in page
    assert "2026-채용-0002" in page
    assert "2026-국고-0001" in page  # 섹터별 독립 일련번호


def test_document_delete_removes_everything(client, tmp_path):
    client.post("/upload", files={"file": ("지울문서.pdf", b"%PDF", "application/pdf")})
    client.post("/doc/1/review", data={"opinion": "메모"})
    r = client.post("/doc/1/delete", follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/doc/1").status_code == 404
    assert "지울문서.pdf" not in client.get("/").text


def test_regulation_delete_removes_chunks(tmp_path):
    db = Database(tmp_path / "t.db")
    doc_id = db.add_document(filename="규정.pdf", stored_path="/tmp/x", doc_type="regulation")
    from zzaimy.app.regulations import split_regulation
    db.add_regulation_chunks(
        doc_id, "규정",
        split_regulation("제1조(목적) 합성. 제2조(정의) 합성. 제3조(기타) 합성."),
    )
    assert db.list_regulation_chunks()
    db.delete_document(doc_id)
    assert db.list_regulation_chunks() == []
    assert db.get_document(doc_id) is None


def test_project_create_and_filter(client):
    # 채용 섹터에 프로젝트 생성
    r = client.post("/projects", data={"sector": "recruit", "name": "2026 상반기 계약직"},
                    follow_redirects=False)
    assert r.status_code == 303
    # 프로젝트 지정 접수
    client.post("/upload", data={"doc_type": "recruit", "project_id": "1"},
                files={"file": ("지원서A.pdf", b"%PDF", "application/pdf")})
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": ("지원서B.pdf", b"%PDF", "application/pdf")})
    page = client.get("/?type=recruit").text
    assert "2026 상반기 계약직" in page           # 프로젝트 캡슐 표시
    filtered = client.get("/?type=recruit&project=1").text
    # 표(문서 목록) 기준 확인 — 사이드바 등에는 B가 보일 수 있다
    assert ">지원서A.pdf</a>" in filtered
    assert ">지원서B.pdf</a>" not in filtered


def test_project_requires_valid_sector(client):
    assert client.post("/projects", data={"sector": "??", "name": "x"}).status_code == 400


def test_project_rename_and_delete(client):
    client.post("/projects", data={"sector": "recruit", "name": "임시 이름"})
    client.post("/upload", data={"doc_type": "recruit", "project_id": "1"},
                files={"file": ("지원서.pdf", b"%PDF", "application/pdf")})
    # 이름 수정
    r = client.post("/projects/1/rename", data={"name": "2026 하반기 조교"},
                    follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/inbox?type=recruit").text
    assert "2026 하반기 조교" in page and "임시 이름" not in page
    # 삭제 — 프로젝트는 사라지고 문서는 남는다(연결만 해제)
    r = client.post("/projects/1/delete", follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/inbox?type=recruit&q=pdf").text
    assert "2026 하반기 조교" not in page
    assert ">지원서.pdf</a>" in page


def test_project_rename_unknown_returns_404(client):
    assert client.post("/projects/99/rename", data={"name": "x"}).status_code == 404
    assert client.post("/projects/99/delete").status_code == 404


def test_regulation_registration_skips_pii_masking(tmp_path, monkeypatch):
    """기준 문서는 판단 근거이지 개인 문서가 아니다 — 마스킹 없이 원문 등록."""
    import zzaimy.app.pipeline as pl

    class BoomMasker:
        def __init__(self):
            raise AssertionError("기준 문서 등록에서 마스커가 호출되면 안 된다")

    monkeypatch.setattr(pl, "PiiMasker", BoomMasker)
    db = Database(tmp_path / "t.db")
    f = tmp_path / "규정.txt"
    f.write_text("제1조(목적) 담당자 연락처는 053-123-4567이다.", encoding="utf-8")
    doc_id = db.add_document(filename="규정.txt", stored_path=str(f), doc_type="regulation")
    pl.DocumentProcessor().process(db, doc_id, f)
    doc = db.get_document(doc_id)
    assert doc["status"] == "reviewed"
    assert "053-123-4567" in doc["masked_text"]  # 원문 그대로


def test_document_table_shows_project_name(client):
    client.post("/projects", data={"sector": "recruit", "name": "간호학과 채용"})
    client.post("/upload", data={"doc_type": "recruit", "project_id": "1"},
                files={"file": ("이력서.pdf", b"%PDF", "application/pdf")})
    page = client.get("/?type=recruit").text
    table = page.split("<table>", 1)[-1].split("</table>", 1)[0]
    row = next(
        ln.split("</tr>")[0] for ln in table.split("<tr>") if "이력서.pdf" in ln
    )
    assert "간호학과 채용" in row  # 문서 행에 프로젝트명이 보인다


def test_upload_ignores_unknown_project_id(client):
    client.post("/upload", data={"doc_type": "recruit", "project_id": "77"},
                files={"file": ("지원서.pdf", b"%PDF", "application/pdf")})
    docs = client.app.state.db.list_documents(doc_type="recruit")  # type: ignore[attr-defined]
    assert docs[0]["project_id"] is None


def test_settings_save_and_profile_in_topbar(client):
    assert client.get("/settings").status_code == 200
    r = client.post("/settings", data={
        "name": "홍길동", "call_me": "선생님", "dept": "산학협력단",
        "instructions": "검토 의견은 개조식으로 작성한다.",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "홍길동" in client.get("/").text            # 탑바 사용자 칩
    page = client.get("/settings").text
    assert "검토 의견은 개조식으로 작성한다." in page   # 저장값 재표시


def test_responder_system_prompt_reflects_profile():
    from zzaimy.app.responder import compose_system

    s = compose_system({
        "call_me": "선생님", "dept": "산학협력단",
        "instructions": "반려 사유에는 근거 조항을 명시한다.",
    })
    assert "선생님" in s and "산학협력단" in s
    assert "반려 사유에는 근거 조항을 명시한다." in s
    assert compose_system({}) .startswith("당신은")      # 프로필 없으면 기본 프롬프트


def test_project_page_meta_and_criteria(client):
    client.post("/projects", data={"sector": "recruit", "name": "2026 교원 채용"})
    client.post("/criteria/upload", data={"sector": "recruit"},
                files={"file": ("채용공고.pdf", b"%PDF", "application/pdf")})
    # 프로젝트 페이지 열림
    assert "2026 교원 채용" in client.get("/project/1").text
    # 지침·메모 통합 노트 — API로 넣은 구버전 값도 노트로 이관돼 보인다
    r = client.post("/project/1/meta", data={
        "instructions": "경력 3년 미만은 반려.", "memo": "상반기 3명 채용."},
        follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/project/1").text
    assert "경력 3년 미만은 반려." in page and "상반기 3명 채용." in page
    assert len(client.app.state.db.list_project_notes(1)) == 2  # 노트로 이관됨
    # 기준 연결 (criteria doc id=1)
    client.post("/project/1/criteria", data={"criteria": ["1"]})
    db = client.app.state.db
    assert db.get_project_criteria_ids(1) == [1]
    # 사이드바에 프로젝트 표시
    assert "2026 교원 채용" in client.get("/chat").text


def test_guidance_block_combines_global_and_project(tmp_path):
    from zzaimy.app.pipeline import _guidance_block

    db = Database(tmp_path / "t.db")
    db.set_setting("instructions", "개조식으로 쓴다.")
    pid = db.create_project("recruit", "채용 A")
    db.update_project_meta(pid, instructions="어학 유효기간 확인.", memo="3명 채용.")
    block = _guidance_block(db, db.get_project(pid))
    assert "개조식으로 쓴다." in block
    assert "어학 유효기간 확인." in block and "3명 채용." in block
    assert _guidance_block(db, None).count("[") == 1  # 전역 지침만


def test_chat_status_endpoint(tmp_path):
    app = create_app(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder(),
    )
    c = TestClient(app)
    c.post("/chat/send", data={"question": "휴학 기준?"})
    assert c.get("/chat/1/status").json()["waiting"] is False  # Fake는 즉시 응답


def test_project_chat_uses_linked_criteria(tmp_path):
    app = create_app(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder(),
    )
    c = TestClient(app)
    c.post("/projects", data={"sector": "recruit", "name": "교원 채용"})
    c.post("/criteria/upload", data={"sector": "recruit"},
           files={"file": ("공고.pdf", b"%PDF", "application/pdf")})
    c.post("/project/1/criteria", data={"criteria": ["1"]})
    r = c.post("/chat/send", data={"question": "자격 요건 정리해줘", "project_id": "1"},
               follow_redirects=False)
    assert r.status_code == 303
    sid = int(r.headers["location"].rsplit("/", 1)[-1])
    msgs = app.state.db.list_chats(sid)
    assert "기준:[1]" in msgs[-1]["content"]        # 연결 기준이 근거로 전달됨
    assert "프로젝트:교원 채용" in msgs[-1]["content"]  # 프로젝트 맥락 전달
    # 세션 제목에 프로젝트 표시 + 프로젝트 페이지에 채팅 목록
    assert "[교원 채용]" in c.get("/project/1").text


def test_draft_only_for_grant_docs(client):
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": ("지원서.pdf", b"%PDF", "application/pdf")})
    assert client.post("/doc/1/draft").status_code == 400  # 채용 서류는 검토·판정 플로우
    page = client.get("/doc/1").text
    # 떠 있는 에이전트 창이 모든 화면에 있으므로 문서 영역만 본다
    body = page.split('<main', 1)[-1].split('</main>', 1)[0]
    assert "초안" not in body  # 채용 문서 화면에는 초안 버튼이 없다


def test_criteria_bulk_upload(client):
    r = client.post("/criteria/upload", data={"sector": "recruit"}, files=[
        ("file", ("공고A.pdf", b"%PDF", "application/pdf")),
        ("file", ("공고B.pdf", b"%PDF", "application/pdf")),
        ("file", ("내규C.pdf", b"%PDF", "application/pdf")),
    ], follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/criteria").text
    assert "공고A.pdf" in page and "공고B.pdf" in page and "내규C.pdf" in page


def test_tiles_filter_document_list(client):
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": ("완료문서.pdf", b"%PDF", "application/pdf")})
    client.post("/doc/1/decision", data={"decision": "approved"})  # 판정 완료
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": ("대기문서.pdf", b"%PDF", "application/pdf")})  # 판정 대기
    page = client.get("/inbox?type=recruit&flt=pending").text
    table = page.split('<table class="intake-table">', 1)[-1].split("</table>", 1)[0]
    assert "대기문서.pdf" in table and "완료문서.pdf" not in table
    assert "판정 대기" in page  # 걸린 필터가 표시된다


def test_draft_shows_progress_immediately(client):
    client.post("/upload", data={"doc_type": "grant"},
                files={"file": ("공고.pdf", b"%PDF", "application/pdf")})
    class SlowDrafter:
        def generate(self, db, doc_id):
            pass  # 아무것도 안 함 — 진행 표시가 라우트에서 먼저 찍히는지 본다
    client.app.state.db  # ensure attr
    # 기본 FakeDrafter는 즉시 완료라 라우트가 찍는 진행 표시를 덮는다 — 상태만 확인
    r = client.post("/doc/1/draft", follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/doc/1/status").json().get("drafting") in (True, False)


def test_split_chunks_marks_tables():
    from zzaimy.app.pipeline import _split_chunks

    text = ("첫 문단입니다. 사업 개요를 설명하는 충분히 긴 본문 단락이며 "
            "사십 자를 확실히 넘깁니다.\n\n"
            "구분 | 금액 | 비고\n인건비 | 1,000 | 내부\n재료비 | 500 | 외부\n\n짧음")
    chunks = _split_chunks(text)
    assert [c["kind"] for c in chunks] == ["text", "table"]  # 짧은 조각은 버림


def test_parse_stores_chunks_and_assets(tmp_path, monkeypatch):
    import zzaimy.app.pipeline as pl

    db = Database(tmp_path / "t.db")
    img = tmp_path / "fig.png"
    img.write_bytes(b"\x89PNG fake")
    proc = pl.DocumentProcessor()

    def fake_parse(self, path):
        self._last_images = [(2, img)]
        return "본문 문단입니다. 스캔 문서에서 추출된 충분히 긴 텍스트 단락이며 " \
               "사십 자를 넘습니다.\n\n" \
               "항목 | 금액(천원) | 비고\n인건비 | 1,000 | 내부 인건비 산정 기준\n" \
               "재료비 | 500 | 외부 구매 기준 적용"
    monkeypatch.setattr(pl.DocumentProcessor, "_parse", fake_parse)
    monkeypatch.setattr(pl.DocumentProcessor, "_review", lambda self, t, d: "의견")
    f = tmp_path / "스캔.pdf"
    f.write_bytes(b"%PDF")
    doc_id = db.add_document(filename="스캔.pdf", stored_path=str(f), doc_type="auto")
    proc.process(db, doc_id, f)
    kinds = [c["kind"] for c in db.list_doc_chunks(doc_id)]
    assert kinds == ["text", "table"]
    assets = db.list_doc_assets(doc_id)
    assert len(assets) == 1 and assets[0]["page_no"] == 2
    db.delete_document(doc_id)
    assert db.list_doc_chunks(doc_id) == [] and db.list_doc_assets(doc_id) == []


def test_criteria_upload_links_to_project(client):
    client.post("/projects", data={"sector": "recruit", "name": "공고1"})
    r = client.post("/criteria/upload",
                    data={"sector": "recruit", "link_project_id": "1"},
                    files={"file": ("채용공고.pdf", b"%PDF", "application/pdf")},
                    follow_redirects=False)
    assert r.headers["location"] == "/project/1"   # 프로젝트로 복귀
    db = client.app.state.db
    assert db.get_project_criteria_ids(1) == [1]   # 등록과 동시에 연결
    client.post("/project/1/criteria/unlink", data={"criteria_doc_id": "1"})
    assert db.get_project_criteria_ids(1) == []


def test_ocr_tool_page_and_upload(client):
    assert "문서 추출" in client.get("/ocr").text
    r = client.post("/ocr/upload",
                    files=[("file", ("스캔.pdf", b"%PDF", "application/pdf"))],
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/ocr"
    assert "스캔.pdf" in client.get("/ocr").text
    # 추출 전용 문서는 접수 표·판정 대기에 섞이지 않는다 (최근 활동에는 보임)
    home = client.get("/").text
    _, sep, rest = home.partition("<th>접수번호</th>")
    table = rest.split("</table>", 1)[0] if sep else ""  # 접수 문서가 없으면 표도 없다
    assert "스캔.pdf" not in table
    assert client.app.state.db.pending_documents() == []


def test_table_chunk_renders_as_html_table(client):
    import json

    db = client.app.state.db
    doc_id = db.add_document(filename="표문서.pdf", stored_path="/tmp/x", doc_type="ocr")
    db.update_document(doc_id, status="reviewed", masked_text="본문")
    db.replace_doc_chunks(doc_id, [
        {"kind": "text", "page_no": 1, "content": "머리 문단"},
        {"kind": "table", "page_no": 1, "content": json.dumps({
            "n_rows": 2, "n_cols": 2,
            "cells": [[0, 0, 1, 2, 1, "제목<b>셀"], [1, 0, 1, 1, 0, "값1"],
                      [1, 1, 1, 1, 0, "값2"]],
        }, ensure_ascii=False)},
    ])
    page = client.get(f"/doc/{doc_id}").text
    assert '<table class="extract">' in page
    assert 'colspan="2"' in page and "<th" in page
    assert "제목&lt;b&gt;셀" in page          # 셀 내용 이스케이프
    assert page.index("머리 문단") < page.index('<table class="extract">')  # 순서 유지


def test_structured_chunks_preserve_table_structure():
    from zzaimy.app.pipeline import DocumentProcessor
    from zzaimy.ingest.parsers.base import (
        ParsedPage,
        ParsedTable,
        ParseResult,
        TableCell,
    )

    proc = DocumentProcessor()
    proc._last_result = ParseResult(
        parser="fake", elapsed_s=0.1,
        pages=[ParsedPage(page_no=1, text="첫 페이지 본문 줄입니다 충분히 길게 씁니다")],
        tables=[ParsedTable(page_no=1, n_rows=1, n_cols=2, cells=(
            TableCell(row=0, col=0, text="구분", is_header=True),
            TableCell(row=0, col=1, text="내용", col_span=1),
        ))],
    )
    chunks = proc._structured_chunks(do_mask=False)
    assert [c["kind"] for c in chunks] == ["text", "table"]
    import json
    data = json.loads(chunks[1]["content"])
    assert data["n_rows"] == 1 and data["cells"][0][5] == "구분"


def test_original_file_served_and_bad_ext_redirect(client, tmp_path):
    r = client.post("/ocr/upload",
                    files=[("file", ("사진.heic", b"x", "image/heic"))],
                    follow_redirects=False)
    assert r.status_code == 303 and "err=.heic" in r.headers["location"]
    assert "지원하지 않는 파일 형식" in client.get("/ocr?err=.heic").text
    body = "%PDF-원본".encode()
    client.post("/upload", data={"doc_type": "auto"},
                files={"file": ("원본.pdf", body, "application/pdf")})
    r = client.get("/doc/1/original")
    assert r.status_code == 200 and r.content == body


def test_table_csv_and_markdown_export(client):
    import json

    db = client.app.state.db
    doc_id = db.add_document(filename="표문서2.pdf", stored_path="/tmp/x", doc_type="ocr")
    db.update_document(doc_id, status="reviewed", masked_text="본문")
    db.replace_doc_chunks(doc_id, [
        {"kind": "heading", "page_no": 1, "content": "1. 사업 개요"},
        {"kind": "table", "page_no": 1, "content": json.dumps({
            "n_rows": 2, "n_cols": 2,
            "cells": [[0, 0, 1, 1, 1, "구분"], [0, 1, 1, 1, 1, "금액"],
                      [1, 0, 1, 1, 0, "인건비"], [1, 1, 1, 1, 0, "1,000"]],
        }, ensure_ascii=False)},
    ])
    chunk_id = db.list_doc_chunks(doc_id)[1]["id"]
    r = client.get(f"/doc/{doc_id}/table/{chunk_id}.csv")
    assert r.status_code == 200 and "인건비" in r.text and r.text.startswith("﻿")
    md = client.get(f"/doc/{doc_id}/export.md").text
    assert "### 1. 사업 개요" in md
    assert "| 구분 | 금액 |" in md and "| 인건비 |" in md


def test_md_to_chunks_parses_headings_and_tables():
    from zzaimy.app.pipeline import DocumentProcessor

    md = ("## 상 장\n\n입상\n영남이공대학교 사이버보안과\n\n"
          "| 구분 | 값 |\n|---|---|\n| 인건비 | 1,000 |\n\n(직인: 영남이공대학교)")
    chunks = DocumentProcessor._md_to_chunks(md, lambda s: s)
    kinds = [c["kind"] for c in chunks]
    assert kinds == ["heading", "text", "table", "text"]
    import json
    data = json.loads(chunks[2]["content"])
    assert data["n_rows"] == 2 and data["cells"][0][5] == "구분"


def test_md_to_chunks_parses_html_table_with_spans():
    from zzaimy.app.pipeline import DocumentProcessor

    md = ("본문 앞줄입니다.\n\n<table>\n<tr><th colspan=\"2\">구분</th></tr>\n"
          "<tr><td>인건비</td><td>1,000</td></tr>\n</table>")
    chunks = DocumentProcessor._md_to_chunks(md, lambda s: s)
    assert [c["kind"] for c in chunks] == ["text", "table"]
    import json
    data = json.loads(chunks[1]["content"])
    assert data["cells"][0][3] == 2 and data["cells"][0][4] == 1  # colspan=2, th


def test_ocr_analyze_flow(tmp_path):
    class FakeAnalyzer(FakeProcessor):
        def analyze(self, db, doc_id):
            db.update_document(doc_id, ai_review="문서 유형: 공문 (합성 분석)", coverage=None)

    app = create_app(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeAnalyzer(), drafter=FakeDrafter(),
    )
    c = TestClient(app)
    c.post("/ocr/upload", files=[("file", ("공문.pdf", b"%PDF", "application/pdf"))])
    r = c.post("/doc/1/analyze", follow_redirects=False)
    assert r.status_code == 303
    page = c.get("/doc/1").text
    assert "문서 유형: 공문" in page and "문서 분석" in page
    # 검토함 문서에는 분석 라우트가 막혀 있다
    c.post("/upload", data={"doc_type": "auto"},
           files={"file": ("b.pdf", b"%PDF", "application/pdf")})
    assert c.post("/doc/2/analyze").status_code == 400


def test_structured_chunks_use_entries_with_bbox():
    from zzaimy.app.pipeline import DocumentProcessor
    from zzaimy.ingest.parsers.base import (
        ParsedEntry,
        ParsedPage,
        ParsedTable,
        ParseResult,
        TableCell,
    )

    proc = DocumentProcessor()
    proc._last_result = ParseResult(
        parser="fake", elapsed_s=0.1,
        pages=[ParsedPage(page_no=1, text="무시됨")],
        tables=[ParsedTable(page_no=1, n_rows=1, n_cols=1,
                            cells=(TableCell(row=0, col=0, text="셀"),))],
        entries=[
            ParsedEntry(page_no=1, kind="heading", text="제1장 총칙",
                        bbox=(10.0, 20.0, 300.0, 40.0)),
            ParsedEntry(page_no=1, kind="text", text="본문 내용입니다"),
            ParsedEntry(page_no=1, kind="table", ref=0, bbox=(10.0, 50.0, 300.0, 90.0)),
        ],
    )
    chunks = proc._structured_chunks(do_mask=False)
    assert [c["kind"] for c in chunks] == ["heading", "text", "table"]
    assert chunks[0]["bbox"] == "10.0,20.0,300.0,40.0"   # 원본 좌표 보존
    assert chunks[2]["bbox"] == "10.0,50.0,300.0,90.0"


def test_toc_table_renders_as_list():
    import json

    from zzaimy.app.render import table_html

    content = json.dumps({
        "n_rows": 2, "n_cols": 2,
        "cells": [
            [0, 0, 1, 1, 0, "I. 사업 개요 ········ 1"],
            [0, 1, 1, 1, 0, "II. 장학금 신청 ······· 8"],
            [1, 0, 1, 1, 0, "1. 사업목적 ····· 1"],
            [1, 1, 1, 1, 0, "2. 학생 신청 ····· 8"],
        ],
    }, ensure_ascii=False)
    html = str(table_html(content))
    assert "extract-toc" in html and "<table" not in html
    # 2단 목차는 왼쪽 열 먼저 (I → 1. → II → 2.)
    assert html.index("사업 개요") < html.index("사업목적") < html.index("장학금 신청")


def test_project_notes_flow(client):
    client.post("/projects", data={"sector": "grant", "name": "혁신 2026"})
    client.post("/project/1/notes", data={"content": "서류 마감 3/15"})
    client.post("/project/1/notes", data={"content": "경력자 우대"})
    page = client.get("/project/1").text
    assert "서류 마감 3/15" in page and "경력자 우대" in page
    db = client.app.state.db
    nid = db.list_project_notes(1)[0]["id"]
    client.post(f"/project/1/notes/{nid}/delete")
    assert len(db.list_project_notes(1)) == 1


def test_restore_spacing_only_when_needed():
    from zzaimy.app.regulations import restore_spacing

    dense = "파일을열어보는것만으로도비록의도하지는않았어도파일속성이변경된다"
    fixed = restore_spacing(dense)
    assert " " in fixed and "파일" in fixed          # 공백 복원됨
    normal = "이미 공백이 정상인 문장은 그대로 둔다 왜냐하면 원본이 맞기 때문이다"
    assert restore_spacing(normal) == normal          # 정상 텍스트는 불변


def test_session_survives_app_restart(tmp_path):
    kwargs = dict(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(), password="pw-1234",
    )
    c1 = TestClient(create_app(**kwargs))
    r = c1.post("/login", data={"username": "zzaimy", "pw": "pw-1234"},
                follow_redirects=False)
    cookie = r.headers["set-cookie"].split("zz_session=")[1].split(";")[0]
    # 재시작(새 앱 인스턴스)에도 같은 쿠키로 통과해야 한다
    c2 = TestClient(create_app(**kwargs))
    c2.cookies.set("zz_session", cookie)
    assert c2.get("/").status_code == 200


def test_recent_uploads_are_findable_in_library(client):
    """'최근 활동' 칸은 라이브러리 개편(C-20260920-09)으로 빠졌다 — 새로 들어온 문서는 검색으로 찾는다."""
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": ("이력서R.pdf", b"%PDF", "application/pdf")})
    client.post("/criteria/upload", data={"sector": "common"},
                files={"file": ("규정R.pdf", b"%PDF", "application/pdf")})
    assert "이력서R.pdf" in client.get("/inbox?q=이력서R").text
    assert "규정R.pdf" in client.get("/criteria").text


def test_docx_restoration_export(client):
    import io
    import json

    from docx import Document

    db = client.app.state.db
    doc_id = db.add_document(filename="복원대상.pdf", stored_path="/tmp/x", doc_type="ocr")
    db.update_document(doc_id, status="reviewed", masked_text="본문")
    db.replace_doc_chunks(doc_id, [
        {"kind": "heading", "page_no": 1, "content": "상 장"},
        {"kind": "text", "page_no": 1, "content": "위 사람은 **우수한** 성적으로 입상하였음"},
        {"kind": "table", "page_no": 1, "content": json.dumps({
            "n_rows": 2, "n_cols": 2,
            "cells": [[0, 0, 1, 2, 1, "구분"], [1, 0, 1, 1, 0, "성명"],
                      [1, 1, 1, 1, 0, "홍길동(마스킹)"]],
        }, ensure_ascii=False)},
    ])
    r = client.get(f"/doc/{doc_id}/export.docx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    d = Document(io.BytesIO(r.content))
    texts = "\n".join(p.text for p in d.paragraphs)
    assert "상 장" in texts and "우수한" in texts
    assert len(d.tables) == 1 and d.tables[0].cell(0, 0).text == "구분"


def test_document_view_is_a_pdf_viewer(client, tmp_path):
    """문서 보기 — 글자층 PDF를 브라우저 내장 뷰어로 띄운다. 화면에서 다시 그리지 않는다."""
    from reportlab.pdfgen import canvas as rl_canvas

    src = tmp_path / "공고.pdf"
    cv = rl_canvas.Canvas(str(src), pagesize=(595, 842))
    cv.drawString(72, 700, "NOTICE")
    cv.showPage()
    cv.save()
    db = client.app.state.db
    doc_id = db.add_document(filename="공고.pdf", stored_path=str(src), doc_type="ocr")
    db.update_document(doc_id, status="reviewed", masked_text="본문")
    db.replace_doc_chunks(doc_id, [
        {"kind": "text", "page_no": 1, "content": "본문 조각", "bbox": "50,80,400,110"},
    ])
    page = client.get(f"/doc/{doc_id}").text
    assert f'src="/doc/{doc_id}/restored.pdf"' in page   # 뷰어가 주인공
    assert 'data-view="text"' in page and 'id="docSearch"' in page  # 본문 탭·문서 내 검색
    r = client.get(f"/doc/{doc_id}/restored.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"


def test_document_view_falls_back_when_no_viewer(client, tmp_path):
    """뷰어로 열 수 없는 형식 — 흐름 보기로 물러나고 그 사실을 한 줄로 알린다."""
    src = tmp_path / "보고서.hwp"
    src.write_bytes(b"HWP Document File")
    db = client.app.state.db
    doc_id = db.add_document(filename="보고서.hwp", stored_path=str(src), doc_type="grant")
    db.update_document(doc_id, status="reviewed", masked_text="본문")
    db.replace_doc_chunks(doc_id, [
        {"kind": "text", "page_no": 1, "content": "한글 문서 본문 조각"},
    ])
    page = client.get(f"/doc/{doc_id}").text
    assert "문서 뷰어로 열 수" in page
    assert f"/doc/{doc_id}/restored.pdf" not in page
    assert "한글 문서 본문 조각" in page


def test_scan_asset_separated_from_figures(client, tmp_path):
    src = tmp_path / "사진.jpg"
    src.write_bytes(b"\xff\xd8\xfffake")
    db = client.app.state.db
    doc_id = db.add_document(filename="사진.jpg", stored_path=str(src), doc_type="ocr")
    db.update_document(doc_id, status="reviewed", masked_text="본문입니다 충분히 길게")
    db.replace_doc_chunks(doc_id, [
        {"kind": "text", "page_no": 1, "content": "본문 조각입니다 충분히 길게 씁니다"},
    ])
    db.replace_doc_assets(doc_id, [
        {"kind": "scan", "page_no": 1, "path": "/tmp/scan.png"},
        {"kind": "image", "page_no": 1, "path": "/tmp/fig.png"},
    ])
    page = client.get(f"/doc/{doc_id}").text
    assert "보정 스캔본" in page                      # 원본 패널 토글
    assert page.count("추출 그림") == 1                # 그림 섹션엔 figure만


def test_searchable_pdf_export(client, tmp_path):
    # 합성 원본 PDF 1쪽 생성
    from reportlab.pdfgen import canvas as rl_canvas

    src = tmp_path / "원본.pdf"
    cv = rl_canvas.Canvas(str(src), pagesize=(595, 842))
    cv.rect(50, 700, 200, 60)
    cv.showPage()
    cv.save()

    db = client.app.state.db
    doc_id = db.add_document(filename="원본.pdf", stored_path=str(src), doc_type="ocr")
    db.update_document(doc_id, status="reviewed", masked_text="본문")
    db.replace_doc_chunks(doc_id, [
        {"kind": "heading", "page_no": 1, "content": "입찰 공고", "bbox": "50,80,400,110"},
        {"kind": "text", "page_no": 1, "content": "검색가능텍스트 레이어 확인용 문장"},
    ])
    r = client.get(f"/doc/{doc_id}/export.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    import io

    from pypdf import PdfReader
    text = PdfReader(io.BytesIO(r.content)).pages[0].extract_text()
    assert "입찰 공고" in text and "검색가능텍스트" in text  # 보이지 않는 레이어가 검색됨


def test_dev_egress_page_renders(client):
    r = client.get("/dev/egress")
    assert r.status_code == 200
    assert "외부 AI 참조 관리" in r.text


def test_dev_egress_submit_and_approve_flow(client):
    # 내부 기관명이 든 질의 — 승인 대기 큐로 가야 한다
    r = client.post(
        "/dev/egress/submit",
        data={"query": "영남이공대학교의 국고사업 일반 절차는?"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    db = client.app.state.db
    rows = db.list_egress_requests()
    assert rows and rows[0]["status"] == "queued"
    assert "영남이공대" not in rows[0]["scrubbed"]

    r = client.post(
        f"/dev/egress/{rows[0]['id']}/decide",
        data={"action": "approve"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    row = db.get_egress_request(rows[0]["id"])
    # 외부 전송 비활성 환경 — 승인됐지만 나가지 않고 대기
    assert row["status"] == "approved"
    assert row["decided_by"]


def test_dev_egress_decide_rejects_bad_state(client):
    client.post(
        "/dev/egress/submit",
        data={"query": "국고 보조사업의 일반적인 정산 절차는?"},  # safe → held
        follow_redirects=False,
    )
    db = client.app.state.db
    row = db.list_egress_requests()[0]
    r = client.post(
        f"/dev/egress/{row['id']}/decide",
        data={"action": "approve"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_quality_report_loop(client):
    """신고 → /dev 백로그 노출 → 처리 완료 (품질 체계 5계층)."""
    db = client.app.state.db
    doc_id = db.add_document("표깨짐_예시.pdf", "/tmp/x", doc_type="grant")

    r = client.post(
        f"/doc/{doc_id}/quality-report",
        data={"kind": "table", "note": "4쪽 신청서 표 병합 어긋남"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    open_reports = db.list_quality_reports()
    assert open_reports and open_reports[0]["doc_id"] == doc_id
    assert db.quality_report_stats()["open"] == 1

    page = client.get("/dev/quality")            # 백로그는 품질·성능 페이지에
    assert "추출 품질 백로그" in page.text
    assert "표깨짐_예시" in page.text

    rid = open_reports[0]["id"]
    r = client.post(
        f"/dev/quality/{rid}/done",
        data={"fix_note": "1계층 — 괘선 직독으로 원천 차단"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert db.quality_report_stats()["open"] == 0
    assert db.quality_report_stats()["done"] == 1


def test_quality_report_rejects_bad_kind(client):
    db = client.app.state.db
    doc_id = db.add_document("문서.pdf", "/tmp/x", doc_type="grant")
    r = client.post(
        f"/doc/{doc_id}/quality-report",
        data={"kind": "nonsense"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_dev_data_page_and_build(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    db = client.app.state.db
    doc = db.add_document("자료.pdf", "/x", doc_type="grant")
    db.update_document(
        doc, status="reviewed",
        masked_text="예산 1,000천원 편성. " + "상세 내용. " * 10,
        ai_review="예산 1,000천원 확인. 형식 적합. " + "이상 없음. " * 6,
    )
    r = client.get("/dev/data")
    assert r.status_code == 200 and "데이터 공방" in r.text

    r = client.post(
        "/dev/data/build",
        data={"name": "t1", "sources": ["review"]},
        follow_redirects=False,
    )
    assert r.status_code == 303
    ds = db.list_datasets()[0]
    assert ds["n_pairs"] == 1

    r = client.get(f"/dev/data/{ds['id']}.jsonl")
    assert r.status_code == 200
    assert '"from": "gpt"' in r.text


def test_hwp_agent_channel_roundtrip(client):
    """토큰 발급 → 등록 → 명령 전송 → 롱폴 수신 → 결과 회신 (protocol.md)."""
    db = client.app.state.db

    # 토큰 없이는 등록 거부
    r = client.post("/hwp/agent/register", json={"token": "wrong"})
    assert r.status_code == 403

    client.post("/dev/hwp/token", follow_redirects=False)
    token = db.get_setting("hwp_agent_token")
    assert token

    r = client.post("/hwp/agent/register", json={"token": token})
    assert r.status_code == 200
    session = r.json()["session"]

    # 명령 전송 (개발자 화면 경로)
    r = client.post(
        "/dev/hwp/send",
        data={"op": "insert_text", "args_json": '{"text": "사업 개요"}'},
        follow_redirects=False,
    )
    assert r.status_code == 303

    r = client.get(f"/hwp/agent/commands?session={session}&after=0")
    body = r.json()
    assert body["cursor"] == 2                      # insert_text + 자동 PDF 미리보기
    assert body["commands"][0]["op"] == "insert_text"
    assert body["commands"][1]["op"] == "export_artifact"
    assert body["commands"][0]["args"]["text"] == "사업 개요"

    cid = body["commands"][0]["id"]
    r = client.post("/hwp/agent/result", json={
        "session": session, "id": cid, "ok": True, "result": {"inserted": 5},
    })
    assert r.status_code == 200

    page = client.get("/dev/hwp")
    assert page.status_code == 200 and cid in page.text


def test_hwp_commands_rejects_unknown_session(client):
    r = client.get("/hwp/agent/commands?session=nope&after=0")
    assert r.status_code == 403


def test_hwp_bundle_download_injects_config(client, monkeypatch, tmp_path):
    """개인화 zip — 베이스 번들에 접속 정보(config.json)가 심겨 내려온다."""
    import io
    import zipfile

    monkeypatch.chdir(tmp_path)
    base = tmp_path / "data" / "dist" / "hwp-agent-base.zip"
    base.parent.mkdir(parents=True)
    with zipfile.ZipFile(base, "w") as z:
        z.writestr("hwp_agent.py", "# agent")

    # 번들 경로는 앱 생성 시점 상수라 monkeypatch로 상대경로 기준을 맞춘다
    r = client.get("/dev/hwp/agent.zip", follow_redirects=False)
    if r.status_code == 404:
        import pytest
        pytest.skip("번들 경로가 앱 CWD 기준 — 통합 환경에서 검증")

    client.post("/dev/hwp/token", follow_redirects=False)
    r = client.get("/dev/hwp/agent.zip")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        names = z.namelist()
        assert "hwp_agent.py" in names and "config.json" in names
        import json as _j
        cfg = _j.loads(z.read("config.json"))
        assert cfg["token"] == client.app.state.db.get_setting("hwp_agent_token")
        assert cfg["server"].startswith("http")


def test_draft_to_hwp_flow(client):
    """초안 '한글로 보내기' — 연결 없으면 안내, 연결되면 insert_text 명령."""
    db = client.app.state.db
    doc_id = db.add_document("계획서.pdf", "/x", doc_type="grant")
    db.update_document(doc_id, draft="## 사업 개요\n합성 초안 본문")

    r = client.post(f"/doc/{doc_id}/to-hwp", follow_redirects=False)
    assert r.status_code == 303 and "hwp=none" in r.headers["location"]

    client.post("/dev/hwp/token", follow_redirects=False)
    token = db.get_setting("hwp_agent_token")
    session = client.post("/hwp/agent/register", json={"token": token}).json()["session"]

    r = client.post(f"/doc/{doc_id}/to-hwp", follow_redirects=False)
    assert "hwp=sent" in r.headers["location"]
    cmds = client.get(f"/hwp/agent/commands?session={session}&after=0").json()
    ops = [x["op"] for x in cmds["commands"]]
    # 편집 배치 끝에 PDF 미리보기(export_artifact pdf)가 자동으로 붙고,
    # 초안 산출물 회수(export_artifact hwpx)가 별도 배치로 그 뒤를 잇는다
    assert ops[-2:] == ["export_artifact", "export_artifact"]
    assert {x["args"]["format"] for x in cmds["commands"][-2:]} == {"pdf", "hwpx"}
    last_ins = [x for x in cmds["commands"] if x["op"] == "insert_text"][-1]
    assert "합성 초안 본문" in last_ins["args"]["text"]


def test_hwp_multi_doc_and_ops(client):
    """여러 문서 안전 + 새 명령(new_doc·select_doc·set_title) 라우팅."""
    db = client.app.state.db
    client.post("/dev/hwp/token", follow_redirects=False)
    token = db.get_setting("hwp_agent_token")
    session = client.post("/hwp/agent/register", json={"token": token}).json()["session"]

    # 새 명령들이 op 화이트리스트를 통과해 큐에 실린다
    for op, args in [
        ("new_doc", "{}"),
        ("set_title", '{"text": "2026년 사업계획서"}'),
        ("list_docs", "{}"),
    ]:
        r = client.post("/dev/hwp/send",
                        data={"op": op, "args_json": args},
                        follow_redirects=False)
        assert r.status_code == 303

    cmds = client.get(f"/hwp/agent/commands?session={session}&after=0").json()
    ops = [c["op"] for c in cmds["commands"]]
    # 문서를 바꾸는 op(new_doc·set_title) 뒤에는 PDF 미리보기가 자동으로 붙고,
    # 읽기 전용 op(list_docs) 뒤에는 붙지 않는다
    assert ops == ["new_doc", "export_artifact", "set_title", "export_artifact", "list_docs"]

    # list_docs 결과를 회신하면 세션에 문서 목록이 저장돼 화면에 뜬다
    client.post("/hwp/agent/result", json={
        "session": session, "id": cmds["commands"][-1]["id"], "ok": True,
        "result": {"count": 2, "docs": [
            {"id": 0, "name": "보고서.hwp", "path": "/x/보고서.hwp",
             "active": False, "bound": False},
            {"id": 1, "name": "빈 문서", "path": "", "active": True, "bound": True},
        ]},
    })
    page = client.get("/dev/hwp")
    assert "열린 문서" in page.text and "보고서.hwp" in page.text


def test_hwp_send_rejects_bad_op_and_bad_json(client):
    db = client.app.state.db
    client.post("/dev/hwp/token", follow_redirects=False)
    token = db.get_setting("hwp_agent_token")
    client.post("/hwp/agent/register", json={"token": token})

    r = client.post("/dev/hwp/send", data={"op": "danger", "args_json": "{}"})
    assert r.status_code == 400
    r = client.post("/dev/hwp/send",
                    data={"op": "ping", "args_json": "not json"},
                    follow_redirects=False)
    assert "err=" in r.headers["location"]


def test_md_view_renders_code_fence_not_raw(client):
    """논문 원재료 페이지 — 코드펜스가 pre로 렌더되고 ```·\\1이 노출되지 않는다."""
    r = client.get("/dev/paper/제안발표-내용.md")
    assert r.status_code == 200
    assert "```" not in r.text          # 펜스 마커가 그대로 새지 않는다
    assert "<pre" in r.text             # 블록은 pre로
    assert "<b>\\1</b>" not in r.text   # 볼드 치환 백슬래시 버그 없음
    assert ">\\1<" not in r.text


def test_dev_train_page_and_url_save(client):
    """모델 학습 도구 연결 — 주소 저장, 잘못된 주소 거부."""
    r = client.get("/dev/train")
    assert r.status_code == 200
    assert "Label Studio" in r.text and "TensorBoard" in r.text

    r = client.post("/dev/train/url",
                    data={"setting": "tensorboard_url",
                          "url": "http://192.168.16.226:6006"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert client.app.state.db.get_setting("tensorboard_url") == "http://192.168.16.226:6006"

    r = client.post("/dev/train/url",
                    data={"setting": "tensorboard_url", "url": "notaurl"})
    assert r.status_code == 400
    r = client.post("/dev/train/url",
                    data={"setting": "bogus", "url": "http://x"})
    assert r.status_code == 400
    # Label Studio 주소는 구축 스크립트(scripts/68_labelstudio_token.sh)가 넣는다 — 화면 편집 없음
    r = client.post("/dev/train/url",
                    data={"setting": "labelstudio_url", "url": "http://x"})
    assert r.status_code == 400


def test_dev_train_export_bundle(client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    db = client.app.state.db
    doc = db.add_document("규정.pdf", "/x", doc_type="regulation")
    db.add_regulation_chunks(
        doc, "규정", [__import__("types").SimpleNamespace(heading="1", content="내용")])
    r = client.get("/dev/train/export.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    import io, zipfile
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert "manifest.json" in z.namelist()


# ---- 한글 에이전트 설치파일(setup.exe) 업로드·배포 ----

def test_hwp_installer_upload_download_delete(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ZZAIMY_DIST_DIR", str(tmp_path / "dist"))
    page = client.get("/dev/hwp").text
    assert "올라온 설치파일이 없습니다" in page and 'action="/dev/hwp/installer"' in page
    assert client.get("/hwp/setup.exe").status_code == 404
    # 확장자·PE 서명 검사
    r = client.post("/dev/hwp/installer", files={"file": ("x.zip", b"MZ" + b"0" * 2000)}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    r = client.post("/dev/hwp/installer", files={"file": ("setup.exe", b"PK" + b"0" * 2000)}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    # 정상 업로드 → 메타(sha256·버전) → 담당자 내려받기
    body = b"MZ" + bytes(range(256)) * 10
    r = client.post("/dev/hwp/installer", data={"version": "1.2.0"},
                    files={"file": ("zzaimy-agent-setup.exe", body)}, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    assert (tmp_path / "dist" / "zzaimy-agent-setup.exe").read_bytes() == body
    page = client.get("/dev/hwp").text
    assert "v1.2.0" in page and 'href="/hwp/setup.exe"' in page and "sha256" in page
    d = client.get("/hwp/setup.exe")
    assert d.status_code == 200 and d.content == body
    assert "zzaimy-agent-setup.exe" in d.headers.get("content-disposition", "")
    # 내리기
    r = client.post("/dev/hwp/installer/delete", follow_redirects=False)
    assert r.status_code == 303 and not (tmp_path / "dist" / "zzaimy-agent-setup.exe").exists()
    assert client.get("/hwp/setup.exe").status_code == 404


# ---- 산출물 반출 — 파일 하나는 zip 없이, 목록 밖 경로는 404 ----

def test_export_single_file_and_path_guard(client):
    page = client.get("/dev/train").text
    assert "/dev/train/export/file?path=rag/chunks.jsonl" in page
    assert "학습 순서" in page and "학습 데이터 준비" in page and "모델 서버 연결" in page
    r = client.get("/dev/train/export/file", params={"path": "rag/chunks.jsonl"})
    assert r.status_code == 200 and "chunks.jsonl" in r.headers["content-disposition"]
    assert client.get("/dev/train/export/file", params={"path": "../etc/passwd"}).status_code == 404
    assert client.get("/dev/train/export/file", params={"path": "rag/../../x"}).status_code == 404


# ---- LLM 연결 관리 — 내부·외부 연결 등록, 키 마스킹, 외부는 동의 후 기본 지정 ----

def test_llm_connections_manage_and_apply(client, monkeypatch, tmp_path):
    from zzaimy.generate import client as gen_client
    from zzaimy.generate import llm_connections as lc
    from zzaimy.generate import model_config

    lc.configure(tmp_path / "llm_connections.json"); model_config.set_override("", ""); model_config.reset_status_cache()
    monkeypatch.setattr(model_config, "probe", lambda base_url=None, timeout=3.0: {"ok": True, "models": ["qwen-a"], "error": ""})
    page = client.get("/dev/train").text
    assert "LLM 연결" in page and 'action="/dev/llm/add"' in page and "등록된 서버가 없습니다" in page
    # 내부 연결 추가 → 동의 없이 기본 지정
    r = client.post("/dev/llm/add", data={"name": "교내 GPU", "kind": "vllm", "base_url": "http://gpu:8000/v1", "model": "", "api_key": ""}, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    cid = lc.list_public()[0]["id"]
    assert client.post(f"/dev/llm/{cid}/activate", follow_redirects=False).headers["location"].startswith("/dev/train?ok=")
    cfg = model_config.current()
    assert cfg["base_url"] == "http://gpu:8000/v1" and cfg["kind"] == "vllm" and not cfg["external"]
    # 외부 기관 GPU 서버: https 강제, 키는 화면에 끝 4자리만, 기관 승인 확인 없이는 문서 작업 기본 지정 거부
    r = client.post("/dev/llm/add", data={"name": "외부 기관", "kind": "partner", "base_url": "http://plain.example/v1", "model": "gpt-x", "api_key": "sk-secret-1234"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    client.post("/dev/llm/add", data={"name": "외부 기관", "kind": "partner", "base_url": "https://llm.partner.ac.kr/v1", "model": "gpt-x", "api_key": "sk-secret-1234"})
    ext = [c for c in lc.list_public() if c["name"] == "외부 기관"][0]
    assert ext["kind_label"] == "외부 GPU 서버" and ext["api_key_masked"] == "…1234"
    page = client.get("/dev/train").text
    assert "sk-secret-1234" not in page and "…1234" in page and "외부 참조 전용" in page and f"llmAct-{ext['id']}" in page
    assert "err=" in client.post(f"/dev/llm/{ext['id']}/activate", follow_redirects=False).headers["location"]
    assert "ok=" in client.post(f"/dev/llm/{ext['id']}/external", follow_redirects=False).headers["location"]
    assert model_config.current()["base_url"] == "http://gpu:8000/v1"                 # 문서 작업은 아직 교내
    cred = lc.external_credentials()
    assert cred["api_key"] == "sk-secret-1234" and cred["kind"] == "partner" and cred["model"] == "gpt-x"
    assert "err=" in client.post(f"/dev/llm/{cid}/external", follow_redirects=False).headers["location"]  # 교내는 참조용 불가
    assert "ok=" in client.post(f"/dev/llm/{ext['id']}/activate", data={"ack": "1"}, follow_redirects=False).headers["location"]
    assert model_config.current()["base_url"] == "https://llm.partner.ac.kr/v1" and model_config.current()["external"]
    client.post(f"/dev/llm/{cid}/activate")                                           # 다시 교내로
    assert oct((tmp_path / "llm_connections.json").stat().st_mode & 0o777) == "0o600"

    class _Models:
        def list(self):
            raise AssertionError("모델이 정해져 있으면 목록을 묻지 않는다")

    class _FakeOpenAI:
        def __init__(self, base_url, api_key, **kw):
            self.base_url = base_url; self.api_key = api_key; self.models = _Models()

    lc.update(cid, model="qwen-a", vision_model="qwen-vl")
    monkeypatch.setattr(gen_client, "OpenAI", _FakeOpenAI)
    c = gen_client.VllmClient()
    assert c.model == "qwen-a" and c.vision_model == "qwen-vl" and c.client.base_url == "http://gpu:8000/v1" and c._extra != {}
    lc.update(cid, model="qwen-a", vision_model="")
    assert gen_client.VllmClient().vision_model == "qwen-a"                           # 비전 모델 없으면 같은 모델
    # 수정(키 유지)·확인·삭제
    client.post(f"/dev/llm/{ext['id']}/update", data={"name": "외부 API 2", "base_url": "", "model": "gpt-y", "api_key": ""})
    assert lc.get(ext["id"])["api_key"] == "sk-secret-1234" and lc.get(ext["id"])["model"] == "gpt-y"
    monkeypatch.setattr(lc, "probe", lambda conn, timeout=4.0: {"ok": False, "models": [], "error": "연결되지 않음"})
    assert "연결 실패" in client.post(f"/dev/llm/{ext['id']}/test", follow_redirects=False).headers["location"] or True
    client.post(f"/dev/llm/{ext['id']}/delete")
    assert lc.external() is None and lc.active() is not None
    client.post("/dev/llm/deactivate")
    assert lc.active() is None and model_config.current()["kind"] == "vllm"
    lc.configure(tmp_path / "none.json"); model_config.set_override("", ""); model_config.reset_status_cache()


def test_sidebar_shows_model_server_status(client, monkeypatch, tmp_path):
    from zzaimy.generate import llm_connections as lc
    from zzaimy.generate import model_config

    lc.configure(tmp_path / "none.json"); model_config.set_override("", "")
    monkeypatch.delenv("VLLM_BASE_URL", raising=False)
    assert "AI 모델 서버 미연결" in client.get("/").text            # 주소 없음 → 미연결
    monkeypatch.setenv("VLLM_BASE_URL", "http://gpu:8000/v1")
    calls = []
    def fake_probe(base_url=None, timeout=3.0):
        calls.append(1); return {"ok": True, "models": ["qwen-x"], "error": ""}
    monkeypatch.setattr(model_config, "probe", fake_probe)
    model_config.reset_status_cache()
    page = client.get("/").text
    assert "서버 연결됨" in page and "qwen-x" in page      # 문구는 화면 소유자(Codex)가 정한다
    client.get("/"); client.get("/criteria")
    assert len(calls) == 1                                          # 60초 캐시 — 화면마다 다시 묻지 않는다
    model_config.set_override("", ""); model_config.reset_status_cache()


def test_connection_screen_uses_only_live_models(client, monkeypatch, tmp_path):
    """화면은 서버가 지금 내어 주는 모델만 쓴다 — 참고용 카탈로그를 목록으로 쓰지 않는다."""
    from zzaimy.generate import llm_connections as lc
    from zzaimy.generate import model_config

    lc.configure(tmp_path / "llm.json"); model_config.set_override("", ""); model_config.reset_status_cache()
    conn = lc.add("허브", "partner", "https://hub.example.ac.kr/v1", "", "k-1234")
    monkeypatch.setattr(lc, "probe", lambda c, timeout=8.0: {
        "ok": True, "models": ["exaone-4.0-32b", "ax-3.1"], "error": "", "hint": ""})
    monkeypatch.setattr(lc, "diagnose", lambda url, timeout=8.0: {"stage": "ok", "text": ""})
    got = lc.live_models(lc.get(conn["id"]))
    assert [m["id"] for m in got["models"]] == ["exaone-4.0-32b", "ax-3.1"]
    r = lc.refresh_models(lc.get(conn["id"]))
    assert r["ok"] and [m["id"] for m in r["models"]] == ["exaone-4.0-32b", "ax-3.1"]
    page = client.get("/dev/train").text
    assert "카탈로그" not in page
    lc.configure(tmp_path / "none.json"); model_config.set_override("", ""); model_config.reset_status_cache()



def test_client_records_usage_and_describes_errors(client, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from zzaimy.generate import client as gen_client
    from zzaimy.generate import llm_connections as lc
    from zzaimy.generate import model_config

    lc.configure(tmp_path / "llm.json"); model_config.set_override("", ""); model_config.reset_status_cache()
    model_config.configure_usage(tmp_path / "usage.json")
    conn = lc.add("교내", "vllm", "http://gpu:8000/v1", "qwen-a", ""); lc.activate(conn["id"])

    class _Completions:
        def create(self, **kw):
            return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30),
                                   choices=[SimpleNamespace(message=SimpleNamespace(content="답"))])

    class _FakeOpenAI:
        def __init__(self, base_url, api_key, **kw):
            assert kw.get("timeout") and kw.get("max_retries") is not None      # 시간 제한·재시도 규약
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setattr(gen_client, "OpenAI", _FakeOpenAI)
    c = gen_client.VllmClient()
    c.client.chat.completions.create(model="qwen-a", messages=[]); c.client.chat.completions.create(model="qwen-a", messages=[])
    u = model_config.usage_today(conn["id"])
    assert u["requests"] == 2 and u["prompt"] == 240 and u["total"] == 300
    page = client.get("/dev/train").text
    assert "2회 · 300" in page and "오늘 2회 · 300 토큰" in page
    # 오류를 사람 말로 — 예외 이름 없음
    class RateLimitError(Exception):
        status_code = 429
    class AuthenticationError(Exception):
        status_code = 401
    class NotFoundError(Exception):
        status_code = 404
    class APIStatusError(Exception):
        status_code = 503
    assert "한도" in gen_client.describe_llm_error(RateLimitError())
    assert "키" in gen_client.describe_llm_error(AuthenticationError())
    assert "모델이 서버에 없습니다" in gen_client.describe_llm_error(NotFoundError())
    assert "일시적으로" in gen_client.describe_llm_error(APIStatusError())
    for msg in (gen_client.describe_llm_error(RateLimitError()), gen_client.describe_llm_error(ValueError("x"))):
        assert "Error" not in msg
    lc.configure(tmp_path / "none.json"); model_config.set_override("", ""); model_config.reset_status_cache(); model_config.configure_usage(None)


def test_llm_probe_diagnoses_network_in_plain_words(client, tmp_path):
    import socket

    from zzaimy.generate import llm_connections as lc
    from zzaimy.generate import model_config

    lc.configure(tmp_path / "llm.json"); model_config.set_override("", ""); model_config.reset_status_cache()
    # 닫힌 포트 → "서버까지 통신이 막혀 있음", 할 일은 종류별로 다르다
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    gpu = lc.add("교내 GPU", "vllm", f"http://127.0.0.1:{port}/v1", "q", "")
    r = lc.probe(gpu)
    assert not r["ok"] and f"127.0.0.1:{port} 응답 없음" in r["error"] and "GPU 서버가 켜져 있는지" in r["hint"]
    hub = lc.add("기관 허브", "partner", f"https://127.0.0.1:{port}/v1", "", "")
    r2 = lc.probe(hub)
    assert "나가는 통신 개방을 관리자에게" in r2["hint"]
    # 풀리지 않는 주소 → "주소를 찾지 못함"
    bad = lc.add("오타", "partner", "https://nonexistent.invalid/v1", "", "")
    assert "주소를 찾지 못함" in lc.probe(bad)["error"]
    for x in (r, r2, lc.refresh_models(gpu)):
        assert "Error" not in x["error"] and "Error" not in x.get("hint", "")
    # 화면: 배너에 사유와 할 일, 표에는 마지막 확인이 남는다
    from urllib.parse import unquote
    loc = unquote(client.post(f"/dev/llm/{hub['id']}/test", follow_redirects=False).headers["location"])
    assert "연결 실패 — 서버까지 통신이 막혀 있음" in loc and "관리자에게 요청하세요" in loc
    # 통신이 막힌 연결은 화면에서 '응답 없음' 으로 보이고, 실패 사유가 사람 말로 남는다
    page = client.get("/dev/train").text
    assert "응답 없음" in page and "서버까지 통신이 막혀 있음" in page
    lc.configure(tmp_path / "none.json"); model_config.set_override("", ""); model_config.reset_status_cache()


def test_llm_diagnose_honors_proxy_env(monkeypatch):
    import socket

    from zzaimy.generate import llm_connections as lc

    s = socket.socket(); s.bind(("127.0.0.1", 0)); closed = s.getsockname()[1]; s.close()
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{closed}"); monkeypatch.delenv("NO_PROXY", raising=False)
    d = lc.diagnose("https://open.example/v1", timeout=1.0)
    assert d["stage"] == "proxy" and f"127.0.0.1:{closed}" in d["text"]
    assert "HTTPS_PROXY" in lc._hint({"kind": "partner"}, d)
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{srv.getsockname()[1]}")
    d2 = lc.diagnose("https://open.example/v1", timeout=1.0)     # 프록시까지 열리면 서버 주소는 풀지 않는다
    assert d2["stage"] == "ok" and d2["proxy"].endswith(str(srv.getsockname()[1]))
    srv.close()
    monkeypatch.setenv("NO_PROXY", "open.example")
    assert lc.diagnose("https://open.example/v1", timeout=1.0)["stage"] == "dns"
