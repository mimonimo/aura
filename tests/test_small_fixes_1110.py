"""11-10 운영 검증에서 남은 작은 결함 — 작업본을 바꾼 뒤 같은 인자 다시 하기, 찾기 공백 꼴, 금액을 자리표시로 보지 않기, 날짜 열 최소 폭."""
import json
from types import SimpleNamespace

from zzaimy.app import doc_agent
from zzaimy.app import webhwp_edit_ops as eo
from zzaimy.app import webhwp_templates as wt
from zzaimy.app.main import _PLACEHOLDER
from zzaimy.ingest.hwpx_fill import _min_col_widths


def _client(replies):
    it = iter(replies)

    class C:
        def create(self, **kw):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(it)))])
    return SimpleNamespace(model="m", client=SimpleNamespace(chat=SimpleNamespace(completions=C())))


def _step(action, **args):
    return json.dumps({"thought": "t", "action": action, "args": args}, ensure_ascii=False)


def test_same_args_after_switching_work_doc_is_a_new_try():
    cur = {"doc": 1}
    calls = []
    tools = [doc_agent.Tool("hwp_replace", "바꾸기", lambda a: calls.append((cur["doc"], a["find"])) or f"문서{cur['doc']}", writes=True),
             doc_agent.Tool("hwp_switch", "바꾸기", lambda a: cur.update(doc=2) or "바꿨다"),
             doc_agent.Tool("finish", "끝", lambda a: "", final=True)]
    replies = [_step("hwp_replace", find="시험1"), _step("hwp_switch", doc="원본"), _step("hwp_replace", find="시험1"),
               _step("hwp_replace", find="시험1"), _step("finish", message="끝")]
    ep = doc_agent.run(_client(replies), "지워 줘", lambda: "s", tools, scope=lambda: str(cur["doc"]))
    assert calls == [(1, "시험1"), (2, "시험1")]                       # 바꾼 뒤 첫 번은 실제로 돌고, 같은 문서의 되풀이만 막힌다
    assert "이미 썼다" in ep.steps[3]["observation"] and ep.finished


def test_replace_finds_text_despite_leading_space_or_fullwidth_space():
    d = wt.build(wt.GRANT["budget_change"])
    d, _ = eo.replace_text(d, {"find": "변경 사유", "replace": "변경　사유"})       # 문서 글에 전각 공백
    d2, msg = eo.replace_text(d, {"find": " 변경 사유", "replace": "조정 사유"})
    assert "곳을 바꿨다" in msg


def test_amounts_are_not_placeholders():
    assert not _PLACEHOLDER.search("총사업비 500,000,000원") and not _PLACEHOLDER.search("1000명")
    assert _PLACEHOLDER.search("제000호") and _PLACEHOLDER.search("20○○. ○.") and _PLACEHOLDER.search("「○○○○ 사업」")


def test_date_and_short_value_columns_keep_one_line_width():
    rows = [["프로그램명", "일시", "장소", "주요 내용"],
            ["고객만족 서비스 관리사 특강", "2018. 10. 06(토) 1일", "협동관 컨벤션홀", "아주 긴 서술 " * 10],
            ["소방안전관리자 특강", "2018. 11. 03(토) ~ 11. 11(일)", "미래관 251호", "긴 서술 " * 10]]
    m = _min_col_widths(rows, 4, 1000)
    one_char = 1000 * 1.05
    assert m[1] >= 7 * one_char           # 「2018. 10. 06(토)」 한 덩이(약 8자 폭)가 한 줄
    assert m[2] >= 7 * one_char           # 짧은 값 열 「협동관 컨벤션홀」
