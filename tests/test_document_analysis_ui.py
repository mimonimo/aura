"""Registration status must not masquerade as document analysis."""
from fastapi.testclient import TestClient

from zzaimy.app.main import create_app
from test_app import FakeDrafter, FakeProcessor


def test_analysis_card_distinguishes_registration_and_results(tmp_path):
    class Processor(FakeProcessor):
        def process(self, db, doc_id, stored):
            db.update_document(
                doc_id, status="reviewed", masked_text="검증용 문서",
                ai_review="기준 등록 완료. 문서 검토와 채팅의 근거로 쓰입니다.",
            )
            db.set_doc_identity(doc_id, {"program": "검증 사업", "year": "2026"})

        def analyze(self, db, doc_id):
            db.update_document(doc_id, ai_review="## 핵심 요약\n검증된 분석 결과", coverage=None)

    app = create_app(db_path=tmp_path / "ui.db", inbox_dir=tmp_path / "inbox",
                     processor=Processor(), drafter=FakeDrafter())
    client = TestClient(app)
    client.post('/criteria/upload', data={"sector": "common"},
                files={"file": ("기준.pdf", b"%PDF", "application/pdf")})
    page = client.get('/doc/1').text
    assert "기준 등록 완료. 문서 검토와 채팅의 근거로 쓰입니다." not in page
    assert "문서 추출 완료 · 분석 결과 없음" in page
    assert "검증 사업" in page
    assert "사업 정보 읽기" not in page
    assert page.count('action="/doc/1/analyze"') == 1
    client.post('/doc/1/analyze')
    page = client.get('/doc/1').text
    assert "검증된 분석 결과" in page
    assert "다시 분석" in page
    assert "분석 결과 없음" not in page
