"""코드 안의 숫자는 근거 수치 점검에서 뺀다(11-28 — 「range(1, 101(근거 확인 필요))」)."""
from zzaimy.verify.numbers import FLAG, flag_unsupported


def test_numbers_in_code_are_not_flagged():
    text = ("1~100단으로 확장하려면 `range(1, 101)`로 수정하시면 됩니다.\n\n```python\nfor i in range(2, 101):\n"
            "    for j in range(1, 10):\n        print(i * j)\n```\n참여자는 300명입니다.")
    out, flagged = flag_unsupported(text, ["관련 없는 근거"])
    assert "`range(1, 101)`" in out and "range(2, 101):" in out
    assert flagged == ["300"] and "300명" + FLAG in out
