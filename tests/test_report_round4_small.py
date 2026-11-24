"""결과보고서 4차 시험에서 찾은 작은 것들 — 끝 「|」 빠진 표 머리줄, 쉼표 없는 큰 수 알림."""
from zzaimy.app import webhwp_write as ww


def test_table_header_without_trailing_pipe_stays_in_table():
    items = ww.parse_body("요약\n\n| 성과지표 | 목표 | 실적 | 달성률(%)\n|---|---|---|---|\n| 교육 | 300 | 264 | 88.0 |\n\n끝")
    assert items[1] == ("table", [["성과지표", "목표", "실적", "달성률(%)"], ["교육", "300", "264", "88.0"]])
    assert ww.parse_body("| 구분 이라는 글\n다음 줄") == [("text", "| 구분 이라는 글\n다음 줄")]   # 표가 아닌 「|」 글은 그대로


def test_table_number_gate_names_the_offending_cell():
    # 11-22 5차: 거절 문구가 어느 칸인지 말하지 않아 모델이 같은 값을 되풀이해 넣었다
    from types import SimpleNamespace
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.verify.numbers import mask_unsupported

    def gate(md):
        _, bad = mask_unsupported(md, ["4월 파이썬 캠프 참여 인원은 학생 42명이다."], context=True)
        return ("근거에 없는 수치: " + ", ".join(bad) + " — 모르는 값은 확인 필요") if bad else None
    t = eo.EditTools(SimpleNamespace(numbers_gate=gate, db=None))
    a = {"op": "set_cells", "table": 9, "start": [2, 1],
         "values": [["4월", "파이썬 캠프", "학생 42명"], ["5월", "교과목 개발", "학생 57명"]]}
    why = t._numbers_check(1, "hwp_table_edit", a)
    assert why and "「5월」 행의 「학생 57명」" in why and "4월" not in why.split("걸린 칸:")[1]


def test_replace_is_number_gated_only_for_new_numbers():
    from types import SimpleNamespace
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.verify.numbers import mask_unsupported

    def gate(md):
        _, bad = mask_unsupported(md, ["총 사업비는 300,000,000원이다."], context=True)
        return ("근거에 없는 수치: " + ", ".join(bad) + " — 모르는 값은 확인 필요") if bad else None
    t = eo.EditTools(SimpleNamespace(numbers_gate=gate, db=None))
    why = t._numbers_check(1, "hwp_replace", {"find": "총 집행액은 확인 필요이다.", "replace": "총 집행액은 108,600,000원이다."})
    assert why and "108,600,000" in why
    assert t._numbers_check(1, "hwp_replace", {"items": [{"find": "2025 가상 사업 7명", "replace": "가상 사업 7명"}]}) is None


def test_rate_column_alone_sees_row_operands_in_table(monkeypatch):
    """11-22 11차: 달성률 열만 넣자(start [2, 4]) 같은 행의 목표·실적이 관문에 안 보여 9/10=90.0 이 막혔다 — 넣은 뒤 행 모습으로 본다."""
    from types import SimpleNamespace
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.verify.numbers import mask_unsupported

    cur = [["성과지표", "목표", "실적", "달성률(%)"], ["산업체 협약(건)", "10", "9", ""], ["교과목 개발(개)", "6", "5", ""]]
    cells = [{"r": r, "c": c, "text": v} for r, row in enumerate(cur) for c, v in enumerate(row)]
    monkeypatch.setattr(eo.webhwp, "get", lambda db, fid: {"path": __file__})
    monkeypatch.setattr(eo, "_Doc", lambda data: None)
    monkeypatch.setattr(eo, "list_tables", lambda d: [{"no": 4, "head": " | ".join(cur[0])}])
    monkeypatch.setattr(eo, "_table_xml", lambda d, t: "")
    monkeypatch.setattr(eo.ws, "_cells", lambda x: cells)

    def gate(md):
        _, bad = mask_unsupported(md, ["산업체 협약 실적 9건", "교과목 개발 실적 5개", "목표 10건 6개"], context=True)
        return ("근거에 없는 수치: " + ", ".join(bad) + " — 모르는 값은 확인 필요") if bad else None
    t = eo.EditTools(SimpleNamespace(numbers_gate=gate, db=None))
    assert t._numbers_check(1, "hwp_table_edit", {"op": "set_cells", "table": 4, "start": [2, 4], "values": [["90.0"], ["83.3"]]}) is None
    why = t._numbers_check(1, "hwp_table_edit", {"op": "set_cells", "table": 4, "start": [2, 4], "values": [["91.0"], ["83.3"]]})
    assert why and "91.0" in why and "83.3" not in why.split(" —")[0]
    # 목표·달성률 두 값을 [2, 2] 부터 넣으면 달성률이 실적 열에 — 넣은 뒤 행을 열 머리와 함께 보인다(12차)
    why2 = t._numbers_check(1, "hwp_table_edit", {"op": "set_cells", "table": 4, "start": [2, 2], "values": [["10", "90.0"]]})
    assert why2 and "넣은 뒤 행:" in why2 and "실적=90.0" in why2


def test_check_needed_in_ledger_column_gets_aggregate_hint(monkeypatch):
    """11-22 12차: 참여 인원을 모아 두고도 칸을 모두 「확인 필요」 로 — 그 열이 장부 항목이면 집계로 채울 수 있다고 알린다."""
    from types import SimpleNamespace
    from zzaimy.app import webhwp_edit_ops as eo, fact_ledger as fl

    cur = [["월", "주요 활동", "참여 인원(명)"], ["3월", "", ""], ["4월", "", ""]]
    cells = [{"r": r, "c": c, "text": v} for r, row in enumerate(cur) for c, v in enumerate(row)]
    monkeypatch.setattr(eo.webhwp, "get", lambda db, fid: {"path": __file__})
    monkeypatch.setattr(eo, "_Doc", lambda data: None)
    monkeypatch.setattr(eo, "list_tables", lambda d: [{"no": 2, "head": " | ".join(cur[0])}])
    monkeypatch.setattr(eo, "_table_xml", lambda d, t: "")
    monkeypatch.setattr(eo.ws, "_cells", lambda x: cells)
    monkeypatch.setattr(fl, "records", lambda db, sid: [{"참여 인원": "학생 42명"}, {"참여 인원": "학생 38명"}])
    monkeypatch.setattr(fl, "load", lambda db, sid: {"items": ["날짜", "참여 인원"]})
    t = eo.EditTools(SimpleNamespace(db=None, sid=7))
    hint = t._ledger_hint(1, {"op": "set_cells", "table": 2, "start": [2, 1], "values": [["3월", "워크숍", "확인 필요"]]})
    assert "「참여 인원」" in hint and "aggregate_facts" in hint
    assert t._ledger_hint(1, {"op": "set_cells", "table": 2, "start": [2, 1], "values": [["3월", "확인 필요", "31"]]}) == ""


def test_live_and_native_text_inserts_go_through_the_number_gate():
    # 11-23 24차: 관문에 막힌 「12,000천원」(근거 1억 2천만 원)을 hwp_live insert_text 로 넣어 관문을 돌아갔다
    from types import SimpleNamespace
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.verify.numbers import mask_unsupported

    def gate(md):
        _, bad = mask_unsupported(md, ["총 사업비는 1억 2천만 원이다."], context=True)
        return ("근거에 없는 수치: " + ", ".join(bad) + " — 모르는 값은 확인 필요") if bad else None
    t = eo.EditTools(SimpleNamespace(numbers_gate=gate, db=None))
    out = t.live(1, {"action": "insert_text", "params": {"field": "총 사업비", "text": "12,000천원"}})
    assert out.startswith("넣지 않았다") and "12,000" in out
    out2 = t.action(1, {"action": "InsertText", "params": {"Text": "총 사업비 12,000천원"}})
    assert out2.startswith("넣지 않았다")


def test_table_gate_holds_when_message_has_scale_note():
    # 11-24 34차: 단위 셈 꼬리가 붙은 거절 문구를 잘못 갈라 set_cells 가 「12,000,000원」(1억 2천만 원의 1/10)을 넣었다
    from types import SimpleNamespace
    from zzaimy.app import webhwp_edit_ops as eo

    def gate(md):
        if "12,000,000" in md:
            return ("근거에 없는 수치: 12,000,000 [단위 셈 확인: 12,000,000 → 근거 금액 120,000,000원과 자릿수만 다르다]"
                    " — 모르는 값은 확인 필요")
        return None
    t = eo.EditTools(SimpleNamespace(numbers_gate=gate, db=None))
    why = t._numbers_check(1, "hwp_table_edit", {"op": "set_cells", "table": 1, "start": [3, 2], "values": [["12,000,000원"]]})
    assert why and "단위 셈 확인" in why and "12,000,000" in why
    why_r = t._numbers_check(1, "hwp_replace", {"find": "확인 필요", "replace": "12,000,000원"})
    assert why_r and "단위 셈 확인" in why_r
