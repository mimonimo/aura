

def test_view_name_keeps_title_without_extension(tmp_path):
    from zzaimy.ingest import gdrive_files
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF-1.4")
    _data, name, _mime, _target = gdrive_files.bytes_for_view(None, {"id": 1, "stored_path": str(src), "filename": "교육부 공고 제2025–247호"})
    assert name == "교육부 공고 제2025–247호.pdf"                          # 제목 끝(「–247호」)을 확장자 길이만큼 자르지 않는다
