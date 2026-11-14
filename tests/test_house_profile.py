"""기관 서식 프로필(11-15) — 기관 문서에서 역할별 모양을 캐고, 양식이 정하지 않은 새 본문·새 표 칸에 입힌다."""
from __future__ import annotations

import re


def _doc_with(text_lines, table_rows):
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.app import webhwp_templates as wt
    d = wt.build(wt.GRANT["budget_change"])
    d, _ = eo.table_insert(d, {"head": table_rows[0], "values": table_rows[1:]})
    return d


def test_role_of_and_style_threshold():
    from zzaimy.app import house_profile as hp
    assert hp.role_of("Ⅰ. 사업추진 목표") == "chapter" and hp.role_of("1. 대학 현황") == "h1"
    assert hp.role_of("1.2. 대내 여건") == "h2" and hp.role_of("· 산업인력양성") == "bullet_ㆍ"
    assert hp.role_of("x" * 50) == "body" and hp.role_of("짧은 글") == ""
    prof = {"roles": {"body": {"font": "한컴 윤고딕 720", "pt": 12.0, "bold": False, "chars": 5000},
                      "bullet_ㆍ": {"font": "굴림", "pt": 9.0, "bold": False, "chars": 10}}}
    assert hp.style(prof, "bullet_ㆍ")["font"] == "한컴 윤고딕 720"          # 근거 적은 부호 역할은 본문으로
    assert hp.style(prof, "table_cell") is None


def test_mine_and_apply_new_table(tmp_path):
    from zzaimy.app import house_profile as hp
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.app import webhwp_templates as wt
    from zzaimy.eval import style_compare as sc
    d = wt.build(wt.GRANT["budget_change"])
    d, _ = eo.table_insert(d, {"head": ["구분", "내용"], "values": [["가" * 20, "나" * 400]] * 3})
    src = tmp_path / "학교_계획서.hwpx"
    src.write_bytes(d)
    prof = hp.mine([src])
    assert "table_cell" in prof["roles"] and prof["roles"]["table_cell"]["chars"] > 300
    prof["roles"]["table_cell"].update({"font": "한컴 윤고딕 250", "pt": 11.0, "bold_share": 0.0})
    doc = eo._Doc(d)
    t = eo.list_tables(doc)[-1]
    pid = re.findall(r'<hp:p\b[^>]*\bid="(\d+)"', doc.secs[t["sec"]][:t["s"]])[-1]
    out = hp.apply_to(d, {pid}, prof)
    assert eo.check_bytes(out) == []
    p = tmp_path / "out.hwpx"
    p.write_bytes(out)
    paras, st = sc.read_hwpx(p)
    fonts = {st.chars[r.get("charPrIDRef")].font for para in paras for r in para.iter()
             if sc._local(r.tag) == "run" and "나나나" in sc._run_text(r) and not any(sc._local(c.tag) == "tbl" for c in r.iter())}
    assert fonts == {"한컴 윤고딕 250"}
    assert hp.apply_to(d, {pid}, {}) == d                                  # 프로필 없으면 그대로


def test_font_registered_once_per_language():
    from zzaimy.app import webhwp_style as ws
    from zzaimy.app import webhwp_templates as wt
    import io, zipfile
    h = ws._Header(zipfile.ZipFile(io.BytesIO(wt.build(wt.GRANT["budget_change"]))).read("Contents/header.xml").decode())
    a = h.char_like("0", "한컴 윤고딕 720", 12.0, None)
    b = h.char_like("0", "한컴 윤고딕 720", 12.0, None)
    assert a == b and h.xml.count('face="한컴 윤고딕 720"') == 7
    assert 'height="1200"' in re.search(rf'<hh:charPr\b[^>]*\bid="{a}"[^>]*>', h.xml).group(0)
