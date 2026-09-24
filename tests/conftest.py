"""테스트 격리 — 실제 구글 토큰·운영 데이터에 닿지 않게 한다.

실측 2026-09-24: 운영 VM 에서 pytest 를 돌리자 data/platform/gdrive_tokens.json 의 실제 계정으로 시험 파일이 드라이브에 올라가
같은 첨부가 폴더에 8벌 쌓였다. 토큰 저장소(ZZAIMY_DATA_DIR)를 임시 폴더로 돌리고 허용 계정 목록을 비운다.
계정이 필요한 테스트는 스스로 monkeypatch 한다(테스트 안의 patch 가 이 기본값을 덮는다)."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_google(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("ZZAIMY_DATA_DIR", str(tmp_path_factory.mktemp("zzaimy-data")))   # 토큰 저장소가 비어 허용 계정 0
    yield
