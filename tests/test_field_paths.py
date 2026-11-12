"""같은 이름 필드의 자리(11-12 특성화 재현) — 반복 블록으로 생긴 「(1) 5개년 추진계획#n」 가운데 요청한 과제 것을 고르고,
서식에 없는 소제목은 위 절로 안내한다. 대화 199 에서 3.1 의 글이 2.3 의 칸(#14)에 들어가고, 「[Ⅲ] 1.1.」 은 「없는 절」 로 멈췄다."""
import io
import zipfile

from zzaimy.app import webhwp_blocks as wb
from zzaimy.app.webhwp_agent import HwpTools


def _p(text):
    return f'<hp:p id="1"><hp:run><hp:t>{text}</hp:t></hp:run></hp:p>'


def _fp(name, fid):
    return (f'<hp:p id="2"><hp:run><hp:ctrl><hp:fieldBegin id="{fid}" type="CLICK_HERE" name="{name}" editable="1"><hp:parameters/></hp:fieldBegin></hp:ctrl>'
            f'<hp:t>본문 입력</hp:t><hp:ctrl><hp:fieldEnd beginIDRef="{fid}" fieldid="{fid}"/></hp:ctrl></hp:run></hp:p>')


def _doc():
    parts = [_p("Ⅱ. 사업추진 계획"),
             _fp("절:2. 집중 육성 계획", 1), _fp("절:2.3. 제도 혁신", 2), _fp("절:1) 유연학사", 3), _fp("절:(1) 5개년 추진계획#14", 4),
             _fp("절:3. 취업 지원 계획", 5), _fp("절:3.1. 진로설계", 6), _fp("절:1) 진로설계 기반", 7), _fp("절:(1) 5개년 추진계획#3", 8),
             '<hp:tbl id="9"><hp:tr><hp:tc><hp:subList>' + _p("Ⅲ. 표 안 글은 장이 아니다") + "</hp:subList></hp:tc></hp:tr></hp:tbl>",
             _p("Ⅲ. 성과관리 계획"), _fp("절:1. 사업추진체계 구축 및 운영 계획", 10), _fp("절:2. 성과관리체계", 11), _fp("칸:목표값#2", 12)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", '<hs:sec xmlns:hs="s" xmlns:hp="p">' + "".join(parts) + "</hs:sec>")
    return buf.getvalue()


def test_field_paths_chain():
    p = wb.field_paths(_doc())
    assert p["절:(1) 5개년 추진계획#14"] == ["Ⅱ. 사업추진 계획", "2. 집중 육성 계획", "2.3. 제도 혁신", "1) 유연학사"]
    assert p["절:(1) 5개년 추진계획#3"] == ["Ⅱ. 사업추진 계획", "3. 취업 지원 계획", "3.1. 진로설계", "1) 진로설계 기반"]
    assert p["절:1. 사업추진체계 구축 및 운영 계획"] == ["Ⅲ. 성과관리 계획"]          # 표 안 「Ⅲ.」 은 장으로 보지 않는다
    assert p["칸:목표값#2"] == ["Ⅲ. 성과관리 계획", "2. 성과관리체계"]


def _tools(request):
    t = HwpTools.__new__(HwpTools)
    paths = wb.field_paths(_doc())
    t.request = request
    t._paths = lambda: paths
    return t


def test_place_check_refuses_other_task_and_names_right_one():
    t = _tools("[Ⅱ] 「3.1. 진로설계」 에 이어서 써 줘")
    out = t.place_check("절:(1) 5개년 추진계획#14", {})
    assert out.startswith("쓰지 않았다") and "#3" in out
    assert t.place_check("절:(1) 5개년 추진계획#3", {}) == ""


def test_place_check_under_picks():
    t = _tools("")
    assert t.place_check("절:(1) 5개년 추진계획", {"under": "3.1. 진로설계"}) == "절:(1) 5개년 추진계획#3"


def test_missing_subsection_points_to_parent_in_chapter():
    t = _tools("[Ⅲ] 「1.1. 전담조직 구축」 을 써 줘")
    out = t.place_check("절:1.1. 전담조직 구축", {})
    assert out.startswith("쓰지 않았다") and "절:1. 사업추진체계 구축 및 운영 계획" in out and "append" in out
