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
