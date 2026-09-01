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

    def reprocess(self, db: Database, doc_id: int) -> None:
        db.update_document(
            doc_id, status="reviewed", ai_review="재검토 의견: 담당자 요청 반영."
        )

    def extract_text(self, file_path: Path) -> str:
        return "합성 첨부 본문"


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
    r = client.get("/")
    assert r.status_code == 200
    assert "문서 접수" in r.text


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
    all_page = client.get("/").text
    assert "공고문.pdf" in all_page and "이력서.pdf" in all_page
    recruit_page = client.get("/?type=recruit").text
    # 표(문서함 목록) 기준으로 확인 — 사이드바 판정 대기 목록에는 둘 다 뜰 수 있다
    assert '<td><a href="/doc/2">이력서.pdf' in recruit_page
    assert '<td><a href="/doc/1">공고문.pdf' not in recruit_page


def test_password_protection_requires_auth(tmp_path):
    app = create_app(
        db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox",
        processor=FakeProcessor(), drafter=FakeDrafter(), password="secret-1234",
    )
    c = TestClient(app)
    assert c.get("/").status_code == 401  # 인증 없이 거부
    assert c.get("/", auth=("zzaimy", "wrong")).status_code == 401
    assert c.get("/", auth=("zzaimy", "secret-1234")).status_code == 200


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
    assert "계획서.pdf" in inbox
    assert "학칙.pdf" not in inbox  # 기준 문서는 문서함에 안 섞인다
    criteria = client.get("/criteria").text
    assert "학칙.pdf" in criteria
    assert ">1건</span>" in criteria  # 등록 건수 1건 — 접수 문서는 기준 목록에 안 들어간다


class FakeResponder:
    def answer(self, db, question, attachment_text=None, criteria_ids=None, session_id=None):
        tail = f" / 첨부:{attachment_text}" if attachment_text else ""
        tail += f" / 기준:{sorted(criteria_ids)}" if criteria_ids else ""
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


def test_sector_page_offers_sector_criteria_options(client):
    client.post("/criteria/upload", data={"sector": "recruit"},
                files={"file": ("채용공고B.pdf", b"%PDF", "application/pdf")})
    client.post("/criteria/upload", data={"sector": "grant"},
                files={"file": ("국고공고.pdf", b"%PDF", "application/pdf")})
    page = client.get("/?type=recruit").text
    assert "채용공고B.pdf" in page       # 채용 섹터 접수 폼에 채용 공고 옵션
    assert "국고공고.pdf" not in page    # 다른 섹터 기준은 안 나온다


def test_receipt_number_scheme(client):
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": ("a.pdf", b"%PDF", "application/pdf")})
    client.post("/upload", data={"doc_type": "recruit"},
                files={"file": ("b.pdf", b"%PDF", "application/pdf")})
    client.post("/upload", data={"doc_type": "grant"},
                files={"file": ("c.pdf", b"%PDF", "application/pdf")})
    page = client.get("/").text
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
    page = client.get("/?type=recruit").text
    assert "2026 하반기 조교" in page and "임시 이름" not in page
    # 삭제 — 프로젝트는 사라지고 문서는 남는다(연결만 해제)
    r = client.post("/projects/1/delete", follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/?type=recruit").text
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
        "name": "김미몬", "call_me": "미몬님", "dept": "산학협력단",
        "instructions": "검토 의견은 개조식으로 작성한다.",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "김미몬" in client.get("/").text            # 탑바 사용자 칩
    page = client.get("/settings").text
    assert "검토 의견은 개조식으로 작성한다." in page   # 저장값 재표시


def test_responder_system_prompt_reflects_profile():
    from zzaimy.app.responder import compose_system

    s = compose_system({
        "call_me": "미몬님", "dept": "산학협력단",
        "instructions": "반려 사유에는 근거 조항을 명시한다.",
    })
    assert "미몬님" in s and "산학협력단" in s
    assert "반려 사유에는 근거 조항을 명시한다." in s
    assert compose_system({}) .startswith("당신은")      # 프로필 없으면 기본 프롬프트


def test_project_page_meta_and_criteria(client):
    client.post("/projects", data={"sector": "recruit", "name": "2026 교원 채용"})
    client.post("/criteria/upload", data={"sector": "recruit"},
                files={"file": ("채용공고.pdf", b"%PDF", "application/pdf")})
    # 프로젝트 페이지 열림
    assert "2026 교원 채용" in client.get("/project/1").text
    # 지침·메모 저장
    r = client.post("/project/1/meta", data={
        "instructions": "경력 3년 미만은 반려.", "memo": "상반기 3명 채용."},
        follow_redirects=False)
    assert r.status_code == 303
    page = client.get("/project/1").text
    assert "경력 3년 미만은 반려." in page and "상반기 3명 채용." in page
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
