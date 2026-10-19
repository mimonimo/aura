

def test_settle_placeholders_cells_and_school():
    """표 칸 값이 자리 표시뿐이면 「확인 필요」(양식 지침), 본문의 「○○대학교」는 아는 대학명으로 — 본문 문장 속 ○○명은 둔다."""
    from zzaimy.app.gdocs_agent import settle_placeholders
    ops = [{"op": "fill", "cells": [{"row": 1, "col": 1, "text": "○○○"}, {"row": 2, "col": 1, "text": "○○백만원"},
                                    {"row": 3, "col": 1, "text": "○○○대학교"}, {"row": 4, "col": 1, "text": "쿠팡"}]},
           {"op": "insert", "text": "○○대학교는 ○○명을 양성한다"}]
    settle_placeholders(ops, {"대학명": "가나대학교"})
    assert [c["text"] for c in ops[0]["cells"]] == ["확인 필요", "확인 필요", "확인 필요", "쿠팡"]
    assert ops[1]["text"] == "가나대학교는 ○○명을 양성한다"
    ops = [{"op": "fill", "cells": [{"row": 0, "col": 1, "text": "○○○대학교"}]}]
    settle_placeholders(ops, {})
    assert ops[0]["cells"][0]["text"] == "확인 필요"                    # 대학명을 모르면 지어내지 않는다
