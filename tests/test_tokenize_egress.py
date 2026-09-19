"""가역 토큰화 — 외부에는 토큰본만, 원값 복원은 교내에서(결정론적). 반출은 상태가 켜져야 시도."""
from __future__ import annotations


def test_reversible_tokenize_roundtrip():
    from zzaimy.ingest.pii import PiiMasker

    m = PiiMasker()
    text = "연락처 010-1234-5678, 메일 hong@ync.ac.kr 로. 010-1234-5678 재확인."
    tok, vault = m.tokenize(text)
    assert vault, "개인정보를 하나도 못 잡음"
    # 원값이 토큰본에 남아 있으면 안 된다
    assert "010-1234-5678" not in tok and "hong@ync.ac.kr" not in tok
    # 같은 값은 같은 토큰이고, 두 번 등장한 전화번호는 토큰도 두 번
    phone_tok = [t for t, v in vault.items() if v == "010-1234-5678"]
    assert len(phone_tok) == 1 and tok.count(phone_tok[0]) == 2
    # 결정론적 복원 → 원문 그대로
    assert PiiMasker.restore(tok, vault) == text
    # 토큰 형식은 [[...]]
    assert all(t.startswith("[[") and t.endswith("]]") for t in vault)


def test_process_external_tokenized_restores_locally(monkeypatch):
    from zzaimy.app import egress

    monkeypatch.setattr(egress, "external_status", lambda: (True, ""))
    sent = {}

    def fake_send(text, system=None):
        sent["text"] = text
        sent["system"] = system
        return "정리 결과 — " + text  # 외부가 토큰을 그대로 유지한 결과

    monkeypatch.setattr(egress, "_send_external", fake_send)
    out = egress.process_external_tokenized("담당자 010-9999-8888, 메일 kim@ync.ac.kr 로 전달.")
    assert out["ok"] and out["tokens"] >= 2
    # 외부로 나간 텍스트에는 원값이 없다
    assert "010-9999-8888" not in sent["text"] and "kim@ync.ac.kr" not in sent["text"]
    # 토큰 유지 지시가 시스템 프롬프트로 들어간다
    assert "자리표시자" in (sent["system"] or "")
    # 교내에서 원값이 복원된다
    assert "010-9999-8888" in out["result"] and "kim@ync.ac.kr" in out["result"]


def test_process_external_tokenized_blocked_when_off(monkeypatch):
    from zzaimy.app import egress

    monkeypatch.setattr(egress, "external_status", lambda: (False, "외부 전송 꺼짐"))
    out = egress.process_external_tokenized("주민번호 900101-1234567")
    assert not out["ok"] and out["error"] == "외부 전송 꺼짐"
