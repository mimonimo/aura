"""연관 자료를 답이 쓴 조항 근거부터 — 11-23 시연: 제18조를 든 답의 첫 근거가 제6조 조각이었다."""
from zzaimy.app.citations import order_by_answer


def test_cited_article_first_and_duplicates_once():
    src = [{"doc_id": 1, "title": "여비 규정", "heading": "", "snippet": "제6조(근무지 외 거주지 등으로부터 직접 여행 시의 여비) …"},
           {"doc_id": 2, "title": "LINC", "heading": "", "snippet": "국내 여비 지급표"},
           {"doc_id": 1, "title": "여비 규정", "heading": "", "snippet": "제18조(근무지 내 국내 출장 시의 여비) ① …"},
           {"doc_id": 1, "title": "여비 규정", "heading": "", "snippet": "제18조(근무지 내 국내 출장 시의 여비) ① …"}]
    out = order_by_answer(src, "<p>《여비 규정》 제18조에 따라 4시간 이상이면 2만원입니다.</p>")
    assert out[0]["snippet"].startswith("제18조") and len(out) == 3
    assert order_by_answer(src, "조항 없는 답")[0]["snippet"].startswith("제6조")
