"""한글 작업본 품질 점검(webhwp_lint) — 끝내기 전 되짚을 지저분한 곳을 찾고, 이번에 새로 생긴 것만 가린다(11-12)."""
from zzaimy.app import webhwp_edit_ops as eo
from zzaimy.app import webhwp_lint as lint
from zzaimy.app import webhwp_templates as wt


def _doc(rows):
    d, _ = eo.table_insert(wt.build(wt.GRANT["budget_change"]), {"head": ["구분", "학과", "교과목"], "values": rows})
    return d


def test_lint_finds_ditto_empty_rows_and_marks():
    d = _doc([["기초", "기계", "CAD"], ["〃", "〃", "도면"], ["", "", ""], ["", "", ""], ["전공", "[그림 image_011.png]", "OOO"]])
    found = lint.issues(d)
    assert found["ditto"] and found["empty_rows"] and found["picture_mark"] and any("OOO" in x for x in found["placeholder"])
    assert "「〃」" in lint.report(found)


def test_new_issues_only_counts_what_this_request_added():
    before = lint.issues(_doc([["기초", "기계", "CAD"]]))
    after = lint.issues(_doc([["기초", "기계", "CAD"], ["〃", "〃", "도면"]]))
    fresh = lint.new_issues(before, after)
    assert "ditto" in fresh and "placeholder" not in fresh
    assert lint.new_issues(after, after) == {}
