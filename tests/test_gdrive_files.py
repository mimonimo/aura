

def test_view_name_keeps_title_without_extension(tmp_path):
    from zzaimy.ingest import gdrive_files
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF-1.4")
    _data, name, _mime, _target = gdrive_files.bytes_for_view(None, {"id": 1, "stored_path": str(src), "filename": "교육부 공고 제2025–247호"})
    assert name == "교육부 공고 제2025–247호.pdf"                          # 제목 끝(「–247호」)을 확장자 길이만큼 자르지 않는다


def test_docx_page_layout_reads_section_margins():
    import io

    from docx import Document
    from docx.shared import Pt

    from zzaimy.ingest import gdrive_files
    d = Document()
    d.sections[0].left_margin = d.sections[0].right_margin = Pt(56.7)
    buf = io.BytesIO()
    d.save(buf)
    lay = gdrive_files.docx_page_layout(buf.getvalue())
    assert abs(lay["left"] - 56.7) < 0.1 and abs(lay["right"] - 56.7) < 0.1 and lay["width"] > 500
