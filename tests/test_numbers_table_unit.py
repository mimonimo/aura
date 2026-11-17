from zzaimy.verify.numbers import mask_unsupported, scaled_values

EV = ["인건비 1억 2천만 원, 교육운영비 2억 1천만 원, 성과 확산비 5천만 원, 합계 5억 원."]
TABLE = """| 비목 | 금액(천원) | 비고 |
|---|---|---|
| 인건비 | {a} | 강사료 |
| 교육운영비 | {b} | 운영 |"""


def test_compound_amount_in_evidence_is_one_value():
    assert "120000000" in scaled_values(EV[0]) and "210000000" in scaled_values(EV[0])


def test_column_header_unit_checks_table_amounts():
    assert mask_unsupported(TABLE.format(a="120,000", b="210,000"), EV)[1] == []
    assert set(mask_unsupported(TABLE.format(a="12,000", b="21,000"), EV)[1]) == {"12,000", "21,000"}


def test_header_unit_stays_in_its_column():
    t = "| 구분 | 금액(천원) | 인원 |\n|---|---|---|\n| 가 | 120,000 | 120,000 |"
    assert mask_unsupported(t, EV)[1] == ["120,000"]          # 인원 열의 숫자는 금액으로 보지 않는다
