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


def test_replace_across_runs():
    from zzaimy.app.webhwp_edit_ops import _replace_across_runs
    p = ('<hp:p id="1"><hp:run charPrIDRef="3"><hp:t>1.1. OOO</hp:t></hp:run>'
         '<hp:run charPrIDRef="4"><hp:t>(과제명) 끝</hp:t></hp:run></hp:p>')
    q, n = _replace_across_runs(p, "OOO(과제명)", "정원감축 체계")
    assert n == 1
    assert "<hp:t>1.1. 정원감축 체계</hp:t>" in q and "<hp:t> 끝</hp:t>" in q
    assert _replace_across_runs(p, "없는 글", "x") == (p, 0)



def test_set_cells_grows_rows_and_view_pages():
    """값 줄이 표 끝을 넘으면 행을 늘려 넣고(43줄에 40행이라 통째로 거절되던 것), 긴 표는 from 부터 본다(11-12 특성화 재현 3차 12단계)."""
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.app import webhwp_templates as wt
    data = wt.build(wt.GRANT["budget_change"])
    d, _ = eo.table_insert(data, {"head": ["구분", "내용"], "values": [["가", "1"], ["나", "2"]]})
    import re as _re
    t = max(x["no"] for x in eo.list_tables(eo._Doc(d)))
    nr = int(_re.search(r"— (\d+)행", eo.table_view(d, {"table": t})).group(1))
    vals = [[f"k{i}", str(i)] for i in range(5)]
    new, msg = eo.table_edit(d, {"table": t, "op": "set_cells", "start": [nr, 1], "values": vals})
    assert "늘림" in msg and eo.check_bytes(new) == []
    view = eo.table_view(new, {"table": t, "from": nr + 2})
    assert "k4" in view and "1행(머리)" in view


def test_echo_labels_fills_same_label_cells_elsewhere():
    """칸 필드에 넣은 값을 다른 표의 같은 라벨 빈 칸(견본 글 칸 포함)에도 — 표지에만 차고 신청서 칸이 비던 것(11-12 특성화 재현 3차)."""
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.app import webhwp_templates as wt
    d, _ = eo.table_insert(wt.build(wt.GRANT["budget_change"]), {"head": ["항목", "값"], "values": [["권 역", ""], ["주소", "(우편번호)"], ["기타", "있는 글"]]})
    new, done = eo.echo_labels(d, {"칸:권역": "대구‧경북권", "칸:주소": "대구 남구", "칸:기타": "바꾸면 안 됨"})
    assert set(done) == {"칸:권역", "칸:주소"} and eo.check_bytes(new) == []
    txt = eo.table_view(new, {"table": max(x["no"] for x in eo.list_tables(eo._Doc(new)))})
    assert "대구‧경북권" in txt and "대구 남구" in txt and "있는 글" in txt


def test_picture_found_by_name_despite_title_conversion(monkeypatch):
    """반입이 파일 이름을 제목 꼴로 바꿔도(「image_003.png」 → 「image 003」) 그림을 이름으로 찾는다(11-13 4차 4단계)."""
    from types import SimpleNamespace
    from zzaimy.app import webhwp_edit_ops as eo
    import zzaimy.app.webhwp_routes as wr
    t = eo.EditTools.__new__(eo.EditTools)
    db = SimpleNamespace(list_files=lambda **k: [])
    t.hw = SimpleNamespace(db=db, sid=1, project_docs=lambda: [{"id": 7, "filename": "image 003"}, {"id": 8, "filename": "image 013"},
                                                              {"id": 9, "filename": "사업목표", "identity": '{"original_filename": "image_002.png"}'},
                                                              {"id": 10, "filename": "image_022.jpg", "status": "failed", "version_of": 7}])
    monkeypatch.setattr(wr, "_original_bytes", lambda st, d: b"PNG" + str(d["id"]).encode())
    assert t._image("image_003.png") == b"PNG7"
    assert t._image("image_002.png") == b"PNG9"            # 본문 제목으로 이름이 바뀐 그림 — 올릴 때 이름으로
    assert t._image("image_022.jpg") == b"PNG7"            # 같은 그림을 두 번 올린 것 — 먼저 들인 것으로
    try:
        t._image("image_999.png")
        raise AssertionError("없는 이름")
    except eo.EditError:
        pass


def test_autofit_widens_only_written_narrow_columns():
    """값을 넣은 열에서 낱말 가운데 접힘이 생기면 그 열만 넓히고(다른 열의 남는 폭에서), 값을 넣지 않은 열·표 전체 폭은 그대로(11-13 4차)."""
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.app import webhwp_templates as wt
    d, _ = eo.table_insert(wt.build(wt.GRANT["budget_change"]), {"head": ["연번", "전공", "주요경력 및 전문성"], "values": [["1", "가", "있음"]]})
    t = max(x["no"] for x in eo.list_tables(eo._Doc(d)))
    tb = eo._Table(eo._table_xml(eo._Doc(d), eo._pick_table(eo._Doc(d), t)))
    tb.col_w = [int(tb.total_w * r) for r in (0.1, 0.08, 0.82)]
    tb.set_text(1, 1, "기계공학전공", eo._Doc(d).ids)
    before = list(tb.col_w)
    assert tb.autofit_narrow({1}) is True
    assert tb.col_w[1] > before[1] and tb.col_w[0] >= 2 * 950 + 1300 and abs(sum(tb.col_w) - sum(before)) < 5   # 다른 열은 제 낱말 폭까지만 줄어든다
    assert tb.autofit_narrow({0}) is False



def test_picture_found_from_own_upload_linked_to_other_project(monkeypatch):
    """같은 그림이 다른 프로젝트에 먼저 들어가 이 프로젝트의 올림이 그쪽에 이어졌어도(목록엔 그쪽 문서), 이 프로젝트에 올린 줄의 이름으로 찾는다(11-13 5차)."""
    from types import SimpleNamespace
    from zzaimy.app import webhwp_edit_ops as eo
    import zzaimy.app.webhwp_routes as wr
    t = eo.EditTools.__new__(eo.EditTools)
    own = {"id": 20, "filename": "image_002.png", "status": "failed", "version_of": 15, "project_id": 9}
    db = SimpleNamespace(list_files=lambda **k: [], list_documents=lambda **k: [own])
    t.hw = SimpleNamespace(db=db, sid=1, project={"id": 9}, project_docs=lambda: [{"id": 15, "filename": "재정 집행 계획"}])
    monkeypatch.setattr(wr, "_original_bytes", lambda st, d: b"IMG" + str(d["id"]).encode())
    assert t._image("image_002.png") == b"IMG20"


def test_picture_own_upload_fallback_requires_visible_link(monkeypatch):
    """이어진 문서가 이 대화의 열람 목록에 없으면 이 프로젝트 올림이라도 읽지 않는다(보안 검토, fail closed)."""
    from types import SimpleNamespace
    from zzaimy.app import webhwp_edit_ops as eo
    import zzaimy.app.webhwp_routes as wr
    t = eo.EditTools.__new__(eo.EditTools)
    own = {"id": 20, "filename": "image_002.png", "status": "failed", "version_of": 15, "project_id": 9}
    db = SimpleNamespace(list_files=lambda **k: [], list_documents=lambda **k: [own])
    t.hw = SimpleNamespace(db=db, sid=1, project={"id": 9}, project_docs=lambda: [{"id": 99, "filename": "다른 문서"}])
    monkeypatch.setattr(wr, "_original_bytes", lambda st, d: b"IMG")
    try:
        t._image("image_002.png")
        raise AssertionError("열람 목록 밖")
    except eo.EditError:
        pass


def test_table_choice_guard_and_picture_fit_and_map():
    """같은 라벨이 든 작은 표에 신청서 값을 넣으려 하면 라벨이 더 겹치는 표를 알려 주고, 좁은 칸에 큰 그림은 넣지 않으며, 양식 지도는 표를 절별로 보인다(11-13 5차)."""
    import pytest
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.app import webhwp_templates as wt
    d = wt.build(wt.GRANT["budget_change"])
    d, _ = eo.table_insert(d, {"head": ["권역", "대구"], "values": [["특성화분야", "반도체"]]})
    d, _ = eo.table_insert(d, {"head": ["구분", "값"], "values": [["권역", ""], ["대학명", ""], ["주 소", ""], ["URL", ""], ["성 명", ""], ["소속(직위)", ""]]})
    tabs = sorted(x["no"] for x in eo.list_tables(eo._Doc(d)))
    small, big = tabs[-2], tabs[-1]
    vals = [["권역", "대구"], ["대학명", "영남이공대학교"], ["주 소", "대구 남구"], ["URL", "ync"], ["성 명", "박민규"], ["소속(직위)", "단장"]]
    with pytest.raises(eo.EditError, match=f"표{big}"):
        eo.table_edit(d, {"table": small, "op": "set_cells", "start": [1, 1], "values": vals})
    assert "표" in eo.form_map(d)


def test_pictures_replace_table_in_order():
    """표 자리를 그림 여러 장으로 대신한다(4번 요약본처럼) — 표는 빠지고 그 자리에 그림이 차례로(11-13 6차)."""
    import base64
    from zzaimy.app import webhwp_edit_ops as eo
    from zzaimy.app import webhwp_templates as wt
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")
    d, _ = eo.table_insert(wt.build(wt.GRANT["budget_change"]), {"head": ["요약", "값"], "values": [["가", "1"]]})
    t = max(x["no"] for x in eo.list_tables(eo._Doc(d)))
    new, msg = eo.picture(d, [png, png], {"at": {"table": t, "where": "replace"}, "width_mm": 50})
    assert "그림 2장" in msg and "표는 뺐다" in msg and eo.check_bytes(new) == []
    assert len(eo.list_tables(eo._Doc(new))) == t - 1
