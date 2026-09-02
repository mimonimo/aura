"""디지털 PDF 정밀 줄 추출 — 원본 좌표·색상 그대로 (SVG식 복원)."""
from pathlib import Path


def _make_pdf(path: Path) -> None:
    from reportlab.lib.colors import red
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas as rl_canvas

    try:
        pdfmetrics.getFont("HYSMyeongJo-Medium")
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    cv = rl_canvas.Canvas(str(path), pagesize=(595, 842))
    cv.setFont("HYSMyeongJo-Medium", 14)
    cv.drawString(72, 770, "통영시 공고 제2026-1669호")
    cv.setFillColor(red)
    cv.setFont("HYSMyeongJo-Medium", 12)
    cv.drawString(100, 700, "중복 지원 불가")
    cv.save()


def test_pdf_line_boxes_exact_text_and_position(tmp_path):
    from zzaimy.app.pdf_lines import pdf_line_boxes

    pdf = tmp_path / "t.pdf"
    _make_pdf(pdf)
    pages = pdf_line_boxes(pdf)
    assert 1 in pages
    lines = pages[1]
    texts = [ln["content"] for ln in lines]
    # 잘림 없이 전체 문자열 그대로 — "통"이 사라지면 실패
    assert any("통영시 공고 제2026-1669호" in t for t in texts), texts
    assert any("중복 지원 불가" in t for t in texts), texts
    first = next(ln for ln in lines if "통영시 공고" in ln["content"])
    x0, y0, x1, y1 = (float(v) for v in first["bbox"].split(","))
    assert abs(x0 - 72) < 4, x0          # 원본 x 좌표 그대로
    assert abs((842 - y1) - 770) < 8      # top 기준 y — 베이스라인 근방
    # 줄이 두 번 나오지 않는다 (중복 금지)
    assert sum("통영시 공고" in t for t in texts) == 1


def test_pdf_line_boxes_color(tmp_path):
    from zzaimy.app.pdf_lines import pdf_line_boxes

    pdf = tmp_path / "t.pdf"
    _make_pdf(pdf)
    lines = pdf_line_boxes(pdf)[1]
    red_line = next(ln for ln in lines if "중복 지원" in ln["content"])
    color = red_line.get("color")
    assert color, "색상 정보가 없다"
    r, g, b = color
    assert r > 150 and g < 100, color


def test_same_line_runs_stay_separate(tmp_path):
    """같은 줄의 떨어진 조각은 병합하지 않는다 — 2단 목차 보호.

    각 조각은 자기 좌표에 그대로 있고, 박스 폭 채움(justify)만 표시한다.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas as rl_canvas

    from zzaimy.app.pdf_lines import pdf_line_boxes

    try:
        pdfmetrics.getFont("HYSMyeongJo-Medium")
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    pdf = tmp_path / "j.pdf"
    cv = rl_canvas.Canvas(str(pdf), pagesize=(595, 842))
    cv.setFont("HYSMyeongJo-Medium", 12)
    cv.drawString(72, 700, "1. 사업목적 .......... 1")   # 왼쪽 단
    cv.drawString(320, 700, "2. 심사요건 .......... 19")  # 오른쪽 단
    cv.save()

    lines = pdf_line_boxes(pdf)[1]
    same_line = [ln for ln in lines if "사업목적" in ln["content"] or "심사요건" in ln["content"]]
    assert len(same_line) == 2, [ln["content"] for ln in lines]
    assert all(ln.get("justify") for ln in same_line)
    assert not any("사업목적" in t and "심사요건" in t
                   for t in (ln["content"] for ln in lines)), "단이 병합됨"


def test_build_restored_pdf_scan(tmp_path):
    """스캔 PDF → 보정 페이지 이미지 + 투명 텍스트 레이어의 복원 PDF."""
    # 이미지 기반(텍스트 레이어 없는) PDF 흉내
    from PIL import Image
    from reportlab.pdfgen import canvas as rl_canvas

    from zzaimy.app.pdf_layer import build_restored_scan_pdf
    img = tmp_path / "page.png"
    Image.new("RGB", (700, 500), "#eeeeec").save(img)
    src = tmp_path / "scan.pdf"
    cv = rl_canvas.Canvas(str(src), pagesize=(700, 500))
    cv.drawImage(str(img), 0, 0, width=700, height=500)
    cv.save()

    lines = [{
        "page_no": 1, "kind": "text", "content": "긁히는 스캔 문장",
        "bbox": "50.0,60.0,400.0,90.0", "justify": True,
    }]
    out = build_restored_scan_pdf(src, lines, {1: (700.0, 500.0)})
    assert out is not None
    import io

    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(out))
    assert len(r.pages) == 1
    assert "긁히는 스캔 문장" in r.pages[0].extract_text()
