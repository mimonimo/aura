"""단계별 글자 맞추기(app/level_ladder)는 몇 번을 돌려도 같은 크기 — 긴 문단이 모두 1단계 항목인 문서에서 저장할 때마다
1pt 씩 커져 본문이 20pt 가 됐다(11-28 구구단 문서)."""
import io
import re
import zipfile

from tests.test_webhwp_form_create import DESIGN
from zzaimy.app import level_ladder as ll
from zzaimy.app import webhwp_form_create as fc
from zzaimy.app import webhwp_write as ww


def _heights(data: bytes, starts: tuple[str, ...]) -> list[int]:
    z = zipfile.ZipFile(io.BytesIO(data))
    h = z.read("Contents/header.xml").decode()
    cps = {m.group(1): int(m.group(2)) for m in re.finditer(r'<hh:charPr\b[^>]*\bid="(\d+)"[^>]*\bheight="(\d+)"', h)}
    x = "".join(z.read(n).decode() for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n))
    out = []
    for p in re.findall(r"<hp:p\b.*?</hp:p>", x, re.S):
        t = "".join(re.findall(r"<hp:t>([^<]*)</hp:t>", p))
        if t.startswith(starts):
            out.append(cps[re.search(r'charPrIDRef="(\d+)"', p).group(1)])
    return out


def test_ladder_is_a_fixed_point_when_every_long_paragraph_is_a_first_level_item():
    clean, _ = fc.validate(DESIGN)
    data = fc.build(clean)
    name = next(f["name"] for f in ww.fields(data) if f["name"].startswith("절:"))
    body = "\n".join(f"{i}) 항목 {i} 은 반복문으로 각 단을 차례로 돌며 곱셈 결과를 화면에 내보내는 일을 맡아 처리하도록 짠다" for i in range(1, 5))
    data, _ = ww.set_fields(data, {name: body})
    first = ll.apply(data)
    sizes = _heights(first, ("1) 항목", "2) 항목"))
    assert sizes, "항목 문단을 찾았다"
    again = first
    for _ in range(4):
        again = ll.apply(again)
    assert _heights(again, ("1) 항목", "2) 항목")) == sizes


def test_code_cells_are_left_aligned():
    """코드를 넣은 표 칸은 왼쪽 정렬 — 짧은 라벨처럼 가운데에 두어 들여쓰기가 안 보였다(11-28 구구단 표)."""
    from zzaimy.app import webhwp_edit_ops as eo
    clean, _ = fc.validate(DESIGN)
    data = fc.build(clean)
    name = next(f["name"] for f in ww.fields(data) if f["name"].startswith("절:"))
    data, _ = eo.table_insert(data, {"at": {"field": name, "where": "end"}, "head": ["번호", "코드"],
                                     "values": [["1", "for i in range(1, 101):"], ["2", "    print(i)"], ["3", "print()"]]})
    z = zipfile.ZipFile(io.BytesIO(data))
    h = z.read("Contents/header.xml").decode()
    x = "".join(z.read(n).decode() for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n))
    def align_of(txt):
        tc = next(t for t in re.findall(r"<hp:tc\b.*?</hp:tc>", x, re.S) if f"<hp:t>{txt}</hp:t>" in t)
        pp = re.search(r'paraPrIDRef="(\d+)"', tc).group(1)
        return re.search(rf'<hh:paraPr\b[^>]*\bid="{pp}".*?<hh:align horizontal="([A-Z]+)"', h, re.S).group(1)
    assert align_of("print()") == "LEFT" and align_of("for i in range(1, 101):") == "LEFT"
    assert align_of("1") == "CENTER"                       # 번호 칸은 그대로 가운데
