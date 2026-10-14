"""테스트 격리 — 실제 구글 토큰·운영 데이터에 닿지 않게 한다.

운영 VM 에서 pytest 를 돌리면 data/platform/gdrive_tokens.json 의 실제 계정으로 시험 파일이 드라이브에
올라갈 수 있다. 그래서 토큰 저장소(ZZAIMY_DATA_DIR)를 임시 폴더로 돌리고 허용 계정 목록을 비운다.
계정이 필요한 테스트는 스스로 monkeypatch 한다(테스트 안의 patch 가 이 기본값을 덮는다)."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_google(monkeypatch, tmp_path_factory):
    monkeypatch.setenv("ZZAIMY_DATA_DIR", str(tmp_path_factory.mktemp("zzaimy-data")))   # 토큰 저장소가 비어 허용 계정 0
    monkeypatch.setenv("ZZAIMY_LLM_FAILOVER", "0")      # 연결 넘김은 서버에 포트를 두드린다 — 그 테스트만 켠다
    monkeypatch.setenv("ZZAIMY_DEV_PASSWORD", "devpass")   # 첫 기동 개발자 비밀번호 — 시험 계정만(운영은 무작위·변경 강제)
    monkeypatch.setenv("ZZAIMY_WARM_NOUNS", "0")         # 서비스 시작 때 규정 명사 미리 세기(뒤 스레드) — 시험 DB 와 다투지 않게
    monkeypatch.setenv("ZZAIMY_KIND_READ", "0")          # 반입 때 서류 갈래 27B 판정 — 모델 서버가 없으니 규칙으로(test_kind_read 가 따로 켠다)
    monkeypatch.setenv("ZZAIMY_DOC_AGENT", "0")          # 문서 작업 에이전트는 모델 서버가 있어야 돈다 — 에이전트 테스트가 따로 켠다(test_doc_agent)
    yield
