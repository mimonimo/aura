"""45차 결과보고서 시험(11-29)에서 드러난 것 — 앞 요청에서 넣은 값이 이번 요청에서 덮인 뒤 되돌리기가 막힘."""
from pathlib import Path


def _hw(tmp_path, monkeypatch):
    from tests.test_webhwp_agent import _setup, wa, wt
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    hw = wa.HwpTools(db, base, 77, "kim", "기획처", {"id": 1, "name": "AI 사업"}, lambda: [])
    tid = next(line.split()[0] for line in hw.forms({"query": "예산 변경"}).splitlines() if "예산 변경" in line)
    hw.start({"form": tid, "title": "예산 변경 신청서"})
    return hw


def test_value_in_doc_at_request_start_stays_grounded(tmp_path, monkeypatch):
    """B 짧게: 1차 요청에서 찾아 넣은 목표치 250 을 2차 요청에서 hwp_fill 이 덮었고, 되돌리는 set_cells 가 「근거에 없는 수치」 로 막혔다."""
    hw = _hw(tmp_path, monkeypatch)
    hw.evidence = lambda: ["담당자 요청: 빈칸 고쳐 줘"]
    monkeypatch.setattr(hw, "form_text", lambda: "핵심지표 | 목표치 | 달성치 강좌 수료자(명) | 250 | 확인 필요")
    hw.remember_start()
    monkeypatch.setattr(hw, "form_text", lambda: "핵심지표 | 목표치 | 달성치 강좌 수료자(명) | 171 | 확인 필요")
    assert hw.numbers_gate("| 강좌 수료자(명) | 250 | 171 |") is None
    assert "근거에 없는 수치" in (hw.numbers_gate("| 강좌 수료자(명) | 260 | 171 |") or "")


def test_start_text_of_other_doc_is_not_evidence(tmp_path, monkeypatch):
    hw = _hw(tmp_path, monkeypatch)
    hw.evidence = lambda: [""]
    hw.start_text = ((hw.fid() or 0) + 999, "목표치 250")
    monkeypatch.setattr(hw, "form_text", lambda: "")
    assert "근거에 없는 수치" in (hw.numbers_gate("목표치 250명") or "")


def test_fill_reports_overwritten_value(tmp_path, monkeypatch):
    from zzaimy.app import webhwp_edit_ops as eo
    hw = _hw(tmp_path, monkeypatch)
    monkeypatch.setattr(hw, "numbers_gate", lambda md: None)
    monkeypatch.setattr(hw, "_gate_evidence", lambda: ["250 171"])
    monkeypatch.setattr(eo, "field_cell_where", lambda raw, k: "표5 2행 「목표치」 열")
    hw.fill({"values": {"칸:사업명": "250"}})
    out = hw.fill({"values": {"칸:사업명": "171"}})
    assert "전에 있던 「250」 을 덮었다" in out


def test_fill_keeps_filled_cell_and_offers_empty_column(tmp_path, monkeypatch):
    """B 한번에: 구성비를 칸:운영비 로 넣어 집행액 22,700 이 44.0 으로 덮였다 — 값이 든 칸은 덮지 않고 같은 행 빈 열을 짚는 인자를 보인다."""
    from zzaimy.app import webhwp_edit_ops as eo
    hw = _hw(tmp_path, monkeypatch)
    monkeypatch.setattr(hw, "numbers_gate", lambda md: None)
    monkeypatch.setattr(hw, "_gate_evidence", lambda: ["22,700 44.0"])
    hw.fill({"values": {"칸:사업명": "22,700"}})
    monkeypatch.setattr(eo, "field_cell_pos", lambda raw, k: {"no": 5, "r": 2, "c": 1, "head": "집행액(천원)",
                                                              "heads": ["세목", "집행액(천원)", "구성비(%)"], "row": ["운영비", "22,700", "확인 필요"]})
    out = hw.fill({"values": {"칸:사업명": "44.0"}})
    assert out.startswith("채웠다: 없음") and "덮지 않음" in out
    assert '"start": [3, 3]' in out and "구성비(%)" in out
