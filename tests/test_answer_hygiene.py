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
