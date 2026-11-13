"""한글 작업본 품질 점검(webhwp_lint) — 끝내기 전 되짚을 지저분한 곳을 찾고, 이번에 새로 생긴 것만 가린다(11-12)."""
from zzaimy.app import webhwp_edit_ops as eo
from zzaimy.app import webhwp_lint as lint
from zzaimy.app import webhwp_templates as wt


def _doc(rows):
    """「〃」 를 글자로 남긴 표 — 새 표는 「〃」 를 세로로 합치므로(hwpx_fill.ditto_spans) 넣은 뒤 표시 글을 「〃」 로 바꾼다
    (다른 길로 들어와 글자로 남은 「〃」 를 점검이 찾는지 본다)."""
    import io
    import zipfile
    d, _ = eo.table_insert(wt.build(wt.GRANT["budget_change"]), {"head": ["구분", "학과", "교과목"],
                                                                 "values": [[("같음표" if c == "〃" else c) for c in r] for r in rows]})
    z = zipfile.ZipFile(io.BytesIO(d))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for info in z.infolist():
            body = z.read(info.filename)
            if info.filename.startswith("Contents/section"):
                body = body.decode().replace(">같음표<", ">〃<").encode()
            out.writestr(info, body)
    return buf.getvalue()


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


def test_new_issues_ignore_table_renumbering():
    """앞쪽 표를 빼서 번호가 밀려도 원래 있던 문제는 새것이 아니다(11-13 8차)."""
    before = {"empty_rows": ["표15(「사업계획서 목차」) 빈 행 2줄 연속"]}
    after = {"empty_rows": ["표13(「사업계획서 목차」) 빈 행 2줄 연속"]}
    assert lint.new_issues(before, after) == {}
