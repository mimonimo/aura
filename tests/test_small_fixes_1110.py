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


def test_finish_with_all_sections_empty_is_nudged_once_to_delegate():
    """채워 달라는 요청인데 양식만 만들고 빈 절을 다 남긴 채 끝내던 일(11-10 대화 182·185, 절 25개 가운데 0개) —
    끝내기 전에 한 번만 빈 절을 delegate_section 으로 한 번에 맡기라고 되짚는다(걸음이 남았고 맡기기 도구가 있을 때만, 판단은 에이전트)."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src/zzaimy/app/main.py").read_text(encoding="utf-8")
    fin = src[src.index("def t_finish(a: dict)"):]
    fin = fin[:fin.index("\n        def ", 10)]
    assert 'empty_checked["done"] = True' in fin and "delegate_section {{sections: [빈 절 전부]}}" in fin
    assert "doc_agent._STEP.get()" in fin and '== "delegate_section" for t_ in tools' in fin
    assert 'empty_checked = {"done": False}' in src



def test_finish_keeps_first_message_after_empty_nudge():
    """빈 절 되짚기 뒤 짧은 덧말만 다시 내면 앞서 쓴 끝내는 말(넣은 값 목록)이 사라졌다(11-12 특성화 재현 3차 2단계) — 앞 말을 살린다."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src/zzaimy/app/main.py").read_text(encoding="utf-8")
    fin = src[src.index("def t_finish(a: dict)"):]
    fin = fin[:fin.index("\n        def ", 10)]
    assert 'held_finish["text"] = msg' in fin and 'msg = prev_ + "\\n\\n" + msg' in fin
    assert 'held_finish = {"text": ""}' in src

def test_calc_from_supported_numbers_is_not_flagged():
    """근거 수치끼리 셈한 결과에는 「근거 확인 필요」를 붙이지 않는다 — 근거에 없는 수로 한 셈은 그대로 표시."""
    from zzaimy.verify.numbers import flag_unsupported
    ev = ["사업비 월 500만 원, 기간 12개월"]
    out, flagged = flag_unsupported("총액은 500만 원 × 12개월 = 6,000만 원입니다. 결론적으로 6,000만 원이 필요합니다.", ev)
    assert not flagged and "확인 필요" not in out
    out, flagged = flag_unsupported("총액은 700만 원 × 12개월 = 8,400만 원입니다.", ev)
    assert flagged


def test_agent_state_lists_uploaded_pictures_by_upload_name():
    """에이전트 상태에 프로젝트에 올린 그림 파일을 올린 이름으로 보인다 — 이름이 그림 속 글로 바뀌거나 다른 문서에 이어져 「없다」 고 판단하던 것(11-13 5차)."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src/zzaimy/app/main.py").read_text(encoding="utf-8")
    assert "프로젝트에 올린 그림 파일" in src and 'get("original_filename")' in src
    blk = src[src.index("pics_ = []"):src.index("프로젝트에 올린 그림 파일")]
    assert "_viewer_sees(session_id, d)" in blk                     # 열람 범위 밖 문서 이름은 보이지 않는다
