"""대화 답의 위생 — 입력 전각 정리, 중국어 낱말 고치기, 번호 목록 그리기."""
from types import SimpleNamespace

from zzaimy.app.responder import fix_foreign_han, normalize_input


def test_normalize_input_fullwidth_and_cjk_punct():
    assert normalize_input("ＡＩＤ 사업、 ＩＣＴ반도체전자과。") == "AID 사업, ICT반도체전자과. "
    assert normalize_input("한글 그대로") == "한글 그대로"


class _Fake:
    def __init__(self, reply):
        self.model = "m"
        self.sent = []
        outer = self

        class _C:
            def create(self, **kw):
                outer.sent.append(kw)
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=_C()))


def test_fix_foreign_han_rewrites_only_lines_with_new_han():
    ans = "1. 배경\n- 인재缺口 분석\n- 산학협력(産學協力) 강화"
    fake = _Fake("0|- 인재 부족 분석")
    out = fix_foreign_han(ans, "질문 産學協力", fake)
    assert out.splitlines() == ["1. 배경", "- 인재 부족 분석", "- 산학협력(産學協力) 강화"]   # 근거에 있던 한자는 그대로
    assert fix_foreign_han("한자 없음", "", fake) == "한자 없음"


def test_md_view_numbered_line_after_bullets_starts_new_block(tmp_path):
    from tests.test_accounts import _app
    md_view = _app(tmp_path).state.templates.env.filters["md_view"]
    html = str(md_view("1.  배경\n    *   가\n2.  목표\n    *   나"))
    assert "가 2." not in html and "목표</p>" in html


def test_md_view_deep_headings_rules_and_italics(tmp_path):
    from tests.test_accounts import _app
    md_view = _app(tmp_path).state.templates.env.filters["md_view"]
    html = str(md_view("#### Ⅰ. 개요\n---\n- *AI물류:* 로봇 제어\n- 2 * 3 = 6"))
    assert "####" not in html and "<h5" in html and "<hr" in html
    assert "<i>AI물류:</i>" in html and "2 * 3" in html                       # 띄어 쓴 곱셈 별표는 그대로


def test_plain_reply_hides_prompt_names():
    from zzaimy.app.gdocs_agent import plain_reply
    out = plain_reply("제공된 [지난 사업 자료]와 [근거 조각]에 없어 asks에 기재했습니다. JSON 편집 계획을 제시")
    assert "asks" not in out and "[근거" not in out and "JSON" not in out and "근거 자료" in out


def test_other_institution_material_is_marked(tmp_path):
    from zzaimy.app import drafting
    from zzaimy.app.db import Database
    db = Database(tmp_path / "t.db")
    db.set_setting("institution:대학명", "영남이공대학교")
    m = drafting.Materials.__new__(drafting.Materials)
    m.db = db
    assert m._other_institution("경복대학교 5차년도 사업수행계획서") is True
    assert m._other_institution("영남이공대학교 2-3 과제계획서") is False
    assert m._other_institution("참여대학교 현황") is False


def test_plain_reply_cell_coordinates_and_op_names():
    from zzaimy.app.gdocs_agent import plain_reply
    out = plain_reply("insert로 추가했습니다. **표 채움(Fill)**: 연도 칸(r1의 c2~c4), 표 1 r4")
    assert "insert" not in out and "Fill" not in out and "2행 3~5열" in out and "표 1 5행" in out


def test_paragraph_ranges_keep_guides_tables_and_newline_before_table():
    from zzaimy.ingest import gdocs as g

    def para(s, e, t):
        return {"startIndex": s, "endIndex": e, "paragraph": {"elements": [{"textRun": {"content": t + "\n"}}]}}
    els = [para(1, 10, "3. 대학 여건"), para(10, 30, g.GUIDE_PREFIX + "우리 대학 …"), para(30, 50, "□ 대학 여건"),
           para(50, 70, "○ 최근 3년"), {"startIndex": 70, "endIndex": 120, "table": {}}, para(120, 140, "- 지역 산업")]
    assert g._paragraph_ranges(els, set()) == [(30, 69), (120, 139)]


def test_noop_replace_is_skipped(tmp_path, monkeypatch):
    from zzaimy.app import gdocs_agent
    from zzaimy.ingest import gdocs as _g
    calls = []
    monkeypatch.setattr(_g, "replace_text", lambda *a, **k: calls.append(a) or {"count": 1})
    monkeypatch.setattr(gdocs_agent, "quality_pass", lambda *a, **k: [])
    lines = gdocs_agent.apply([{"op": "replace", "old": "□ 과제명", "text": "□ 과제명"}], "a@b", "D", user="u", data_dir=tmp_path)
    assert calls == [] and lines == []


def test_form_choice_model_pick_and_fallback():
    from types import SimpleNamespace
    from zzaimy.app import form_choice

    cands = [{"id": "outline", "kind": "outline", "label": "대화에서 정한 목차", "note": ""},
             {"id": "common:plan", "kind": "common", "label": "사업계획서 공통 양식", "note": ""}]

    def fake(reply):
        class C:
            def create(self, **kw):
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])
        return SimpleNamespace(model="m", client=SimpleNamespace(chat=SimpleNamespace(completions=C())))
    d = form_choice.decide(fake('{"choice": "outline", "why": "담당자가 목차를 정했다"}'), "위 목차대로 써 줘", cands)
    assert d["choice"] == "outline" and d["by"] == "model"
    d = form_choice.decide(fake('{"choice": "form:999"}'), "사업계획서 써 줘", cands)            # 후보 밖 → 규칙
    assert d["choice"] == "common:plan" and d["by"] == "rule"
    assert form_choice.decide(None, "위 목차대로 써 줘", cands)["choice"] == "outline"


def test_strip_emoji_keeps_admin_symbols():
    from zzaimy.app.responder import strip_emoji
    assert strip_emoji("💡 팁: □ 항목 ○ 세부 ※ 참고 → 다음 ✅").strip() == "팁: □ 항목 ○ 세부 ※ 참고 → 다음"


def test_latest_outline_looks_back_past_other_answers():
    from zzaimy.app.form_choice import latest_outline
    outline = "#### Ⅰ. 대학의 역량\n1. 교육 현황\n2. 실습 여건\n#### Ⅱ. 추진내용\n1. 추진체계\n2. 교육과정\n"
    hist = [{"role": "assistant", "content": outline}, {"role": "user", "content": "좋아"},
            {"role": "assistant", "content": "[1. 추진 배경]\n716자 추가"}]
    assert latest_outline(hist) is not None and latest_outline(hist[2:]) is None
