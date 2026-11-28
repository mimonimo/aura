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


def test_missing_past_line_is_named():
    """A 짧게: 지난 문서 예산 절의 「총 집행액은 …원으로 집행률은 …%이다」 줄이 올해 문서에서 빠졌다."""
    from zzaimy.app import doc_review as d
    past = [["4. 예산 집행 실적", "총 집행액은 210,200,000원으로 집행률은 84.1%이다."]]
    out = d.missing_past_lines({"4. 예산 집행 실적": []}, past)
    assert out and "총 집행액은 ○원으로 집행률은 ○%이다" in out[0], out
    assert d.missing_past_lines({"4. 예산 집행 실적": ["총 집행액은 132,700,000원으로 집행률은 44.2%이다."]}, past) == []
    # 다른 절·표에 같은 값이 있으면(달리 썼어도) 알리지 않는다
    assert d.missing_past_lines({"다른 절": ["집행률 44.2%"]}, past, "합계 | 132,700,000 집행액(원) 132,700,000 집행률 44.2%") == []


def test_past_lines_from_real_shape():
    from zzaimy.app import doc_review as d
    xml = ('<hs:sec><hp:p><hp:run><hp:t>4. 예산 집행 실적</hp:t></hp:run></hp:p>'
           '<hp:p><hp:run><hp:t>총 집행액은 210,200,000원으로 집행률은 84.1%이다.</hp:t></hp:run></hp:p></hs:sec>')
    import io, zipfile
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("Contents/section0.xml", xml)
    got = d.past_lines(b.getvalue())
    assert any("집행률" in x[1] for x in got)


def test_repeated_review_issue_is_marked(tmp_path, monkeypatch):
    """B 짧게: 두 표 합 후보를 두 요청 내내 보고도 그대로 마쳤다 — 앞 다시 읽기와 같은 후보에 표시."""
    from zzaimy.app import main as m
    hw = _hw(tmp_path, monkeypatch)
    t = ("마치기 전에.\n\n코드가 본 문제 후보(참고):\n1. 표2 합 300 과 표4 합 315 이 다르다\n2. 빈 칸이 있다\n\n담당자가…\n\n지금 문서 전체:\n\n1. 사업 개요")
    first = m._mark_repeated_issues(hw.db, 5, t)
    assert "앞 다시 읽기에도" not in first
    t2 = t.replace("2. 빈 칸이 있다", "2. 다른 후보")
    second = m._mark_repeated_issues(hw.db, 5, t2)
    assert "315 이 다르다 [앞 다시 읽기에도" in second and "다른 후보 [" not in second
    assert second.rstrip().endswith("1. 사업 개요")


def test_common_skeleton_for_draft_request():
    """11-28 양식 시나리오: 기안문 설계에 관련·붙임이 빠졌다 — 설계 재료에 공통 기안문 뼈대(관련 → 추진 내용 → 붙임·끝)를 참고로 준다."""
    from zzaimy.app.webhwp_form_create import common_skeleton
    name, txt = common_skeleton("장비 구입 건으로 내부결재 기안문 양식부터 만들어 줘")
    assert "기안문" in name and "1. 관련" in txt and "붙임" in txt and "끝." in txt
    assert common_skeleton("2026 결과보고서 양식 만들어 줘") == ("", "")
    assert "출장" in common_skeleton("출장 다녀온 결과 보고 양식")[0]
