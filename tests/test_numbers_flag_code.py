"""코드 안의 숫자는 근거 수치 점검에서 뺀다(11-28 — 「range(1, 101(근거 확인 필요))」)."""
from zzaimy.verify.numbers import FLAG, flag_unsupported


def test_numbers_in_code_are_not_flagged():
    text = ("1~100단으로 확장하려면 `range(1, 101)`로 수정하시면 됩니다.\n\n```python\nfor i in range(2, 101):\n"
            "    for j in range(1, 10):\n        print(i * j)\n```\n참여자는 300명입니다.")
    out, flagged = flag_unsupported(text, ["관련 없는 근거"])
    assert "`range(1, 101)`" in out and "range(2, 101):" in out
    assert flagged == ["300"] and "300명" + FLAG in out


def test_code_without_backticks_in_document_tables_is_not_a_number_claim():
    """문서 표 칸·본문에 백틱 없이 넣은 코드의 숫자는 수치 관문이 막지 않는다 — 글 속 수치는 그대로 본다(11-28 구구단 문서)."""
    from zzaimy.verify.numbers import mask_unsupported
    table = ("| 번호 | 코드 |\n|---|---|\n| 1 | for i in range(1, 101): |\n| 2 |     for j in range(1, 10): |\n"
             "| 3 |         print(f\"{i} * {j} = {i * j}\") |")
    _, bad = mask_unsupported(table, ["파이썬 구구단 코드를 표에 넣어 줘"])
    assert bad == []
    _, bad = mask_unsupported("외부 반복문 range(1, 101) 은 단을 돈다. 참여 학생은 300명이다.", ["관련 없는 근거"])
    assert bad == ["300"]


def test_admin_parentheses_are_still_checked():
    from zzaimy.verify.numbers import mask_unsupported
    _, bad = mask_unsupported("성과지표 KPI(85%) 를 달성하고 LINC(4.5) 사업에 참여한다.", ["관련 없는 근거"])
    assert "85" in bad                                    # 「KPI(85%)」 는 코드가 아니다
