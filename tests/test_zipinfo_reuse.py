"""기안기가 저장한 hwpx(압축 크기가 우리 것과 다름)를 고쳐 쓸 때 — 읽는 zip 의 ZipInfo 를 writestr 에 그대로 주면 위치 값이 새 zip 것으로 바뀌어
같은 zip 을 다시 읽다 「Bad magic number for file header」 로 멈췄다(11-27 시연 ③: 사람이 편집기에서 저장한 직후 AURA 쓰기가 모두 실패)."""
import io
import zipfile


def _repack_like_editor(data: bytes) -> bytes:
    """같은 내용을 다른 압축 정도로 다시 싼다 — 항목 위치(header_offset)가 우리가 싼 것과 달라진다."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in zin.namelist():
            z.writestr(n, zin.read(n),
                       compress_type=zipfile.ZIP_STORED if n in ("mimetype", "version.xml") else zipfile.ZIP_DEFLATED, compresslevel=1)
    return buf.getvalue()


def test_write_section_on_editor_saved_zip():
    from tests.test_form_guide_pages import _form_bytes
    from zzaimy.app import webhwp_convert, webhwp_write as ww
    work, _fields = webhwp_convert.work_form(_form_bytes(), ".hwpx")
    data = _repack_like_editor(work)
    name = next(k for k in ww.field_values(data) if k.startswith("절:"))
    out, put = ww.write_section(data, name, ww.parse_body("가) 시험 문장을 씀."), {})
    assert "시험 문장" in ww.field_values(out)[name]


def test_pack_does_not_change_source_zipinfo():
    from tests.test_form_guide_pages import _form_bytes
    from zzaimy.app import webhwp_write as ww
    zin = zipfile.ZipFile(io.BytesIO(_repack_like_editor(_form_bytes())))
    before = [(i.filename, i.header_offset, i.compress_size) for i in zin.infolist()]
    ww._pack(zin, {})
    assert [(i.filename, i.header_offset, i.compress_size) for i in zin.infolist()] == before
    assert zin.testzip() is None
