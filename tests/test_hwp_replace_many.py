"""여러 곳 지우기·바꾸기(11-20) — 6곳 지우기 요청에서 27B 가 읽기 도구만 되풀이하다 멈추던 일(운영 기록 713·714)의 고침.

- hwp_replace 가 items 목록으로 한 번에 여러 곳을 바꾸고, 곳마다 결과(바꿈·못 찾음)를 돌려준다.
- 각주·표가 든 문단(안에 문단이 든 문단)의 글도 바꾼다 — 예전엔 통째로 건너뛰어 「글자 모양 경계로 쪼개져 있다」 고 잘못 알렸다.
- 글 조각 안에 탭·줄바꿈 표시가 든 문단(「가)⇥본문」)의 글도 바꾼다.
- 잇달아 못 바꾼 쓰기 막기는 같은 글(겹치는 글)만 — 다른 곳을 지우는 걸음까지 막지 않는다.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from zzaimy.app import doc_agent
from zzaimy.app import webhwp_edit_ops as eo
from zzaimy.app import webhwp_templates as wt


def _doc() -> bytes:
    return wt.build(wt.GRANT["budget_change"])


def _sec(data: bytes) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode("utf-8")


def _put(data: bytes, xml: str) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for it in src.infolist():
            z.writestr(it, xml.encode("utf-8") if it.filename == "Contents/section0.xml" else src.read(it.filename))
    return out.getvalue()


def _plain_doc_with(sentence: str) -> bytes:
    """본문 첫 글 조각 뒤에 sentence 를 가진 글 조각을 하나 더 둔다(실제 문서처럼 한 문단 안 글)."""
    d = _doc()
    x = _sec(d)
    m = re.search(r"<hp:t>([^<]{2,})</hp:t>", x)
    assert m
    x = x[:m.end(1)] + sentence + x[m.end(1):]
    return _put(d, x)


def test_replace_inside_paragraph_with_footnote():
    """출처 각주가 달린 문단 — 안에 각주 문단이 들어 있어 예전엔 건너뛰었다(운영 714: 「학과별 수요를 반영해 단계적으로 추진함.」)."""
    d = _plain_doc_with(" 학과별 수요를 반영해 단계적으로 추진함.")
    d, _ = eo.footnote(d, {"after_text": "단계적으로 추진함.", "text": "출처: 기본계획"})
    x = _sec(d)
    assert "<hp:footNote " in x and "학과별 수요를 반영해" in x
    d2, msg = eo.replace_text(d, {"find": " 학과별 수요를 반영해 단계적으로 추진함.", "replace": ""})
    x2 = _sec(d2)
    assert "1곳을 바꿨다" in msg and "학과별 수요를 반영해" not in x2
    assert "출처: 기본계획" in x2 and "<hp:footNote " in x2            # 각주는 그대로
    assert eo.check_bytes(d2) == []


def test_replace_inside_footnote_and_table_cell_still_works():
    """안쪽 문단(각주 글·표 칸)의 글도 함께 바뀐다 — 바깥 문단을 다루면서 안쪽을 놓치지 않는다."""
    d = _plain_doc_with(" 바깥 글 가나다.")
    d, _ = eo.footnote(d, {"after_text": "바깥 글 가나다.", "text": "각주 안 가나다"})
    d2, msg = eo.replace_text(d, {"find": "가나다", "replace": "라마바"})
    x2 = _sec(d2)
    assert "가나다" not in x2 and "바깥 글 라마바." in x2 and "각주 안 라마바" in x2 and "2곳" in msg
    assert eo.check_bytes(d2) == []


def test_replace_text_in_run_with_tab_child():
    """「가)⇥본문」 처럼 글 조각 안에 탭 표시가 든 문단."""
    d = _doc()
    x = _sec(d)
    m = re.search(r"<hp:t>([^<]{2,})</hp:t>", x)
    x = x[:m.start()] + '<hp:t>가)<hp:tab width="1000" leader="0" type="1"/>시험 글을 지움.</hp:t>' + x[m.end():]
    d = _put(d, x)
    d2, msg = eo.replace_text(d, {"find": "시험 글을 지움.", "replace": ""})
    x2 = _sec(d2)
    assert "시험 글을 지움." not in x2 and '가)<hp:tab width="1000" leader="0" type="1"/></hp:t>' in x2 and "1곳" in msg


def test_replace_many_items_reports_each_place():
    d = _plain_doc_with(" 첫째 시험 글. 둘째 시험 글.")
    d2, msg = eo.replace_text(d, {"items": [{"find": " 첫째 시험 글."}, " 둘째 시험 글.", {"find": "없는 글"}]})
    x2 = _sec(d2)
    assert "첫째 시험 글" not in x2 and "둘째 시험 글" not in x2
    assert "3곳 가운데 2곳" in msg and "없는 글" in msg and "찾지 못했다" in msg
    with pytest.raises(eo.EditError) as e:
        eo.replace_text(d, {"items": ["없는 글 하나", "없는 글 둘"]})
    assert "없는 글 하나" in str(e.value) and "없는 글 둘" in str(e.value)
    # 각 항목의 replace 를 따른다(빠지면 지우기)
    d3, _ = eo.replace_text(d, {"items": [{"find": "첫째", "replace": "1번"}, {"find": "둘째"}]})
    assert "1번 시험 글." in _sec(d3) and "둘째" not in _sec(d3)


def test_replace_tool_desc_mentions_items():
    t = next(t for t in eo.TOOLS if t["name"] == "hwp_replace")
    assert "items" in t["desc"] and "items" in t["args"]


# ── 에이전트 루프: 잇달아 못 바꾼 쓰기 막기는 같은 글만 ──

def _client(replies):
    it = iter(replies)

    class C:
        def create(self, **kw):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=next(it)))])
    return SimpleNamespace(model="m", client=SimpleNamespace(chat=SimpleNamespace(completions=C())))


def _step(action, **args):
    return json.dumps({"thought": "t", "action": action, "args": args}, ensure_ascii=False)


def test_noop_block_only_stops_same_target_not_a_new_place():
    ran = []

    def rep(a):
        ran.append(a["find"])
        return "바뀐 것 1곳" if a["find"].startswith("지표별") else f"바꾸지 않았다 — 「{a['find']}」 을 찾지 못했다(0곳)."
    tools = [doc_agent.Tool("hwp_replace", "찾아 바꾸기", rep, writes=True),
             doc_agent.Tool("finish", "끝", lambda a: a.get("message", ""), final=True)]
    replies = [_step("hwp_replace", find="학과별 수요를 반영해 단계적으로 추진함.", replace=""),
               _step("hwp_replace", find="학과별 수요를 반영해", replace=""),
               _step("hwp_replace", find="단계적으로 추진함.", replace=""),
               _step("hwp_replace", find="단계적", replace=""),                  # 같은 글의 조각 — 막는다
               _step("hwp_replace", find="지표별 목표는 매 학기 점검함.", replace=""),   # 다른 곳 — 한다
               _step("finish", message="한 곳은 찾지 못했습니다")]
    ep = doc_agent.run(_client(replies), "「학과별 수요를 반영해 단계적으로 추진함.」과 「지표별 목표는 매 학기 점검함.」 지워 줘", lambda: "s", tools)
    assert "단계적" not in ran and "지표별 목표는 매 학기 점검함." in ran
    assert "아무것도 바꾸지 못했다" in ep.steps[3]["observation"]
    assert ep.finished and ep.writes >= 1


def test_repeat_note_points_to_write_tools_first():
    tools = [doc_agent.Tool("hwp_review", "다시 읽기", lambda a: "글"),
             doc_agent.Tool("hwp_fields", "칸 보기", lambda a: "칸"),
             doc_agent.Tool("hwp_replace", "찾아 바꾸기", lambda a: "1곳을 바꿨다", writes=True),
             doc_agent.Tool("finish", "끝", lambda a: a.get("message", ""), final=True)]
    replies = [_step("hwp_review"), _step("hwp_review"), _step("hwp_review"),
               _step("hwp_replace", items=["가"]), _step("finish", message="지웠습니다")]
    ep = doc_agent.run(_client(replies), "여러 곳 지워 줘", lambda: "s", tools)
    obs = ep.steps[2]["observation"]
    assert "hwp_replace" in obs and obs.index("hwp_replace") < obs.index("hwp_fields")
    assert ep.finished


def test_review_section_arg_reads_later_section():
    """hwp_review {section} — 인자 없는 다시 읽기가 결과 한도에 앞 절만 보이던 것(운영 713). 뒤쪽 절을 짚어 읽는다."""
    from zzaimy.app import doc_review
    from zzaimy.app.main import _review_scope
    assert _review_scope({}, "요청 글") == "요청 글"
    assert doc_review.asked_sections(_review_scope({"section": 5}, "")) == {5}
    assert doc_review.asked_sections(_review_scope({"section": "4. 세부 프로그램"}, "")) == {4}
