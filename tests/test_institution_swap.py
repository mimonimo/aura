"""다른 대학에 설치해도 그 학교 이름으로 동작 — 코드에 박힌 학교 이름 없이 기관 정보·환경 변수에서(11-10 데이터 교체 점검)."""
from zzaimy.app import egress, institution, regulation_register, responder


def _set(monkeypatch, name):
    monkeypatch.setenv("ZZAIMY_INSTITUTION_NAME", name)


def test_prompt_uses_configured_institution(monkeypatch):
    _set(monkeypatch, "한빛대학교")
    s = responder.compose_system({}, "staff")
    assert "한빛대학교" in s and "영남이공" not in s
    assert "한빛대학교" in responder.compose_system({}, "student")


def test_prompt_without_institution_is_generic(monkeypatch):
    monkeypatch.delenv("ZZAIMY_INSTITUTION_NAME", raising=False)
    monkeypatch.setattr(institution, "_RUNTIME", {})
    s = responder.compose_system({}, "staff")
    assert "우리 대학" in s and "〔기관〕" not in s


def test_egress_hides_configured_institution(monkeypatch):
    _set(monkeypatch, "한빛대학교")
    r = egress.scrub("한빛대학교 산학협력단 규정과 한빛대 학칙")
    assert "한빛대학교" not in r.text and "한빛대" not in r.text


def test_issuer_treats_configured_institution_as_ours(monkeypatch):
    _set(monkeypatch, "한빛대학교")
    assert regulation_register.issuer("한빛대학교 학칙") == ""
    assert regulation_register.rule_key("한빛대학교 학칙") == regulation_register.rule_key("학칙")


def test_aliases():
    import os
    old = os.environ.get("ZZAIMY_INSTITUTION_NAME")
    os.environ["ZZAIMY_INSTITUTION_NAME"] = "한빛대학교"
    try:
        assert institution.aliases() == ("한빛대학교", "한빛대학", "한빛대")
    finally:
        if old is None:
            os.environ.pop("ZZAIMY_INSTITUTION_NAME")
        else:
            os.environ["ZZAIMY_INSTITUTION_NAME"] = old


def test_remember_reads_settings(tmp_path, monkeypatch):
    from zzaimy.app.db import Database
    monkeypatch.delenv("ZZAIMY_INSTITUTION_NAME", raising=False)
    db = Database(tmp_path / "p.db")
    assert institution.remember(db) == ""                 # 빈 설치 — 오류 없이 빈 이름
    institution.set_fact(db, "대학명", "한빛대학교")
    assert institution.name() == "한빛대학교"


def test_empty_file_reason(tmp_path):
    from zzaimy.app import pipeline, read_failure
    f = tmp_path / "빈.pdf"
    f.write_bytes(b"")
    why = pipeline._format_mismatch(f)
    assert why and read_failure.classify(why) == "empty"
    z = tmp_path / "압축.pdf"
    z.write_bytes(b"PK\x03\x04" + b"0" * 100)
    assert read_failure.classify(pipeline._format_mismatch(z)) == "mismatch"
