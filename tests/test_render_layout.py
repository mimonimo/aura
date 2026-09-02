

def test_layout_font_shrinks_to_box_width():
    """긴 줄은 박스 폭에 맞춰 글자 크기를 줄인다 — 페이지 밖으로 흘러나가지 않게."""
    from zzaimy.app.render import layout_pages

    long_line = "통영시대학생학자금이자지원공고문안내사항" * 3  # 60자 한 줄
    chunks = [
        {"kind": "text", "content": long_line, "bbox": "10,10,310,90", "page_no": 1},
        {"kind": "text", "content": "짧은 줄", "bbox": "10,100,310,130", "page_no": 1},
    ]
    out = layout_pages(chunks, page_sizes={1: (595.0, 842.0)})
    assert out is not None
    html = str(out[0])
    import re
    sizes = [float(m) for m in re.findall(r"font-size:([0-9.]+)px", html)]
    # 첫 박스: 폭 383px에 60자 → 폭 기준이면 7px 미만이어야 함 (높이 기준이면 15px)
    assert sizes[0] < 8.0, f"긴 줄 글자 크기가 폭을 무시함: {sizes[0]}px"


def test_layout_pages_with_background_image():
    """원본 배치 뷰 — 원본 페이지 렌더를 배경으로 깔고 텍스트는 투명 레이어."""
    from zzaimy.app.render import layout_pages

    chunks = [
        {"kind": "text", "content": "사업 목적", "bbox": "10,10,200,40", "page_no": 1},
        {"kind": "text", "content": "지원 내용", "bbox": "10,60,200,90", "page_no": 1},
    ]
    out = layout_pages(
        chunks, page_sizes={1: (595.0, 842.0)},
        page_image_url=lambda pg: f"/doc/7/page/{pg}.png",
    )
    html = str(out[0])
    assert "background-image:url('/doc/7/page/1.png')" in html
    assert "color:transparent" in html          # 겹침 방지 — 원본 글자가 보인다
    assert "사업 목적" in html                    # 선택·복사·검색은 가능
