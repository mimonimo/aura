"""라이브러리 하나로(2026-10-06) — 문서를 보는 곳은 라이브러리의 세 탭(프로젝트·문서 / 기준 문서 / 원본 보관소)이고,
자료 연결 메뉴·화면과 문서 추출 화면은 없앴다(가져오기는 관리자 설정, 구글 계정 연결은 프로필 설정)."""
from tests.test_dev_pages import client  # noqa: F401


def test_library_tabs_and_no_connections_menu(client):
    for path in ("/?type=all", "/criteria", "/archive"):
        page = client.get(path).text
        assert 'aria-label="라이브러리"' in page, path
        assert 'href="/criteria"' in page and 'href="/archive"' in page, path
        assert ">자료 연결<" not in page, path
    assert client.get("/connections").status_code == 404
    assert client.get("/ocr").status_code == 404
    assert "접수·첨부 문서" not in client.get("/criteria").text      # 같은 목록이 두 곳에 있던 것 — 라이브러리 쪽만 남김
