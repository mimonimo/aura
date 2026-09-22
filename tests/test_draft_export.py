"""초안 내보내기 — 사용자 요구 항목 구조의 초안을 docx/pdf/md 파일로."""
import io

DRAFT = """## 사업 개요
본 사업은 지역 인재 양성을 목표로 한다.

## 예산 계획
| 항목 | 단가 | 수량 | 금액 |
|---|---|---|---|
| 장학금 | 250만 원 | 24명 | 6,000만 원 |
| 운영비 | 50만 원 | 10회 | 500만 원 |

## 추진 일정
1분기에 착수한다."""


def test_parse_draft_sections_and_tables():
    from zzaimy.app.draft_export import parse_draft

    secs = parse_draft(DRAFT)
    assert [s["title"] for s in secs] == ["사업 개요", "예산 계획", "추진 일정"]
    table_blocks = [b for b in secs[1]["blocks"] if b["kind"] == "table"]
    assert len(table_blocks) == 1
    assert table_blocks[0]["rows"][0] == ["항목", "단가", "수량", "금액"]
    assert table_blocks[0]["rows"][1][3] == "6,000만 원"


def test_draft_docx_contains_headings_and_table():
    from docx import Document

    from zzaimy.app.draft_export import build_draft_docx

    payload = build_draft_docx("테스트 계획서", DRAFT)
    doc = Document(io.BytesIO(payload))
    heads = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert "사업 개요" in heads and "예산 계획" in heads
    assert len(doc.tables) == 1
    assert doc.tables[0].rows[1].cells[0].text == "장학금"


def test_draft_pdf_text_extractable():
    from pypdf import PdfReader

    from zzaimy.app.draft_export import build_draft_pdf

    payload = build_draft_pdf("테스트 계획서", DRAFT)
    assert payload is not None
    text = "".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(payload)).pages)
    assert "사업 개요" in text and "지역 인재 양성" in text
    assert "6,000만 원" in text  # 표 내용도 문서에 들어간다


def test_draft_hwpx_opens_and_contains_content():
    import zipfile

    from zzaimy.app.draft_export import build_draft_hwpx

    payload = build_draft_hwpx("테스트 계획서", DRAFT)
    assert payload is not None
    z = zipfile.ZipFile(io.BytesIO(payload))
    assert "mimetype" in z.namelist()
    xml = b"".join(
        z.read(n) for n in z.namelist() if n.startswith("Contents/section")
    ).decode("utf-8", errors="ignore")
    assert "사업 개요" in xml and "지역 인재 양성" in xml
    assert "6,000만 원" in xml  # 표 셀


def _make_img(tmp_path, name="fig.png"):
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", (400, 240), "#3355aa").save(p)
    return p


def test_draft_docx_with_images(tmp_path):
    from docx import Document

    from zzaimy.app.draft_export import build_draft_docx

    img = _make_img(tmp_path)
    payload = build_draft_docx("계획서", DRAFT, images=[str(img)])
    doc = Document(io.BytesIO(payload))
    assert any("붙임 그림" in p.text for p in doc.paragraphs)
    assert len(doc.inline_shapes) == 1


def test_draft_hwpx_with_images(tmp_path):
    import zipfile

    from zzaimy.app.draft_export import build_draft_hwpx

    img = _make_img(tmp_path)
    payload = build_draft_hwpx("계획서", DRAFT, images=[str(img)])
    assert payload is not None
    names = zipfile.ZipFile(io.BytesIO(payload)).namelist()
    assert any("BinData" in n or n.lower().endswith(".png") for n in names), names


def test_draft_pdf_with_images(tmp_path):
    from pypdf import PdfReader

    from zzaimy.app.draft_export import build_draft_pdf

    img = _make_img(tmp_path)
    payload = build_draft_pdf("계획서", DRAFT, images=[str(img)])
    r = PdfReader(io.BytesIO(payload))
    has_img = any(
        "/XObject" in (p.get("/Resources") or {}) for p in r.pages
    )
    assert has_img


def test_numbered_items_with_sub_bullets_become_separate_list_paragraphs():
    """연번 항목과 그 세부 불릿은 한 문단이 아니라 항목별 문단이다 — 워드·한글에서 1,2,3 이 그대로 보이게."""
    import io

    from docx import Document

    from zzaimy.app.draft_export import build_draft_docx, parse_draft

    md = ("【이번 주 한 일】\n1. 토르·DGX 장비 구성\n   - 젯슨 토르 2대에 27B, DGX는 학습 전용\n"
          "2. 문서 반입 및 검색 구축\n   - 문서 194건 재반입\n3. 모델 준비\n\n【다음 주 계획】\n1. 실물 문서 반입\n2. 파인튜닝 착수")
    secs = parse_draft(md)
    kinds = [b["kind"] for s in secs for b in s["blocks"]]
    assert kinds.count("list") == 2
    items = [b for s in secs for b in s["blocks"] if b["kind"] == "list"][0]["items"]
    assert [lv for lv, _ in items] == [0, 0, 1, 0, 1, 0] and items[1][1].startswith("1. ") and items[2][1].startswith("젯슨")
    doc = Document(io.BytesIO(build_draft_docx("주간업무보고", md)))
    texts = [p.text for p in doc.paragraphs]
    assert "2. 문서 반입 및 검색 구축" in texts and "· 문서 194건 재반입" in texts
    assert all("\n" not in t for t in texts if t.startswith(("1.", "2.", "3.")))
