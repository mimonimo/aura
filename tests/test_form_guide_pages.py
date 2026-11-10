"""사업계획서 작성 서식을 양식으로 쓸 때(11-10 대화 187) — 서식 설명 쪽(작성 및 제출 요령)·목차 표·총괄표 칸의 번호 글은 본문 절이 아니고,
라벨 옆 칸의 작성 안내(「~을 요약 기술」)는 쓸 자리, 「O개 학과(O%)」 같은 예시 값은 라벨이 아니다."""
from __future__ import annotations

from tests.test_webhwp_fields import _hwpx, _names, _p
from zzaimy.app import webhwp_fields as wf
from zzaimy.app import webhwp_write as ww


def _tc(text: str, pid: int, span: int = 1, inner: str = "") -> str:
    body = (_p(text, pid) if text else f'<hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"/></hp:p>') + inner
    return f'<hp:tc><hp:subList>{body}</hp:subList><hp:cellSpan colSpan="{span}" rowSpan="1"/></hp:tc>'


def _tbl(rows: list[list], base: int, cols: int) -> str:
    """rows: 칸 글 또는 (글, colSpan, 안쪽 표 xml)."""
    trs = ""
    for i, r in enumerate(rows):
        tds = ""
        for j, c in enumerate(r):
            t, span, inner = (c if isinstance(c, tuple) else (c, 1, ""))
            tds += _tc(t, base + i * 10 + j + 2, span, inner)
        trs += f"<hp:tr>{tds}</hp:tr>"
    return (f'<hp:p id="{base}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0">'
            f'<hp:tbl id="{base + 1}" colCnt="{cols}">{trs}</hp:tbl></hp:run></hp:p>')


def _secs(data: bytes) -> list[str]:
    return [n for n in _names(data) if n.startswith("절:")]


def test_instruction_page_and_toc_numbers_are_not_sections():
    deadline = _tbl([["신청단계", "제출서류"], ["본접수", "사업계획서"]], 900, 2)
    guide_page = _tbl([["사업계획서 작성 및 제출 요령"], ["1. 제출서류"], ["◦ 전자공문 및 온라인 : 사업계획서"],
                       ["2. 사업계획서 규격"], ["◦ 작성 : 워드프로세서"], [("4. 제출기한 및 제출처", 1, deadline)]], 100, 1)
    toc = _tbl([["사업계획서 목차"], ["1. 대내·외 여건 분석 및 특성화분야 선정0"], ["2. 특성화 계획 및 사업수행 의지0"]], 300, 1)
    sec = guide_page + toc + _p("Ⅰ. 사업추진 목표", 11) + _p("1. 대내·외 여건 분석 및 특성화분야 선정", 12) + _p("", 13) + _p("2. 특성화 계획 및 사업수행 의지", 14)
    data, _ = wf.fieldize(_hwpx(sec))
    assert _secs(data) == ["절:1. 대내·외 여건 분석 및 특성화분야 선정", "절:2. 특성화 계획 및 사업수행 의지"]
    assert not any(n.startswith("칸:") for n in _names(data))                 # 서식 설명 쪽 안의 제출기한 표에도 칸을 박지 않는다


def test_numbered_text_in_grid_table_cell_is_not_a_section_but_full_row_heading_is():
    grid = _tbl([["영역", "과제", "프로그램"], ["정원 감축", "1. OOO", "①"], [("2. 추진 계획", 3, "")]], 100, 3)
    data, _ = wf.fieldize(_hwpx(grid + _p("1) 표 서식 변경 불가", 21) + _p("", 22)))
    secs = _secs(data)
    assert "절:2. 추진 계획" in secs                                          # 한 줄을 다 차지한 칸의 제목은 절
    assert not any("OOO" in n for n in secs)                                  # 총괄표 칸의 번호 글은 표 내용
    assert not any("변경 불가" in n for n in secs)                            # 「~불가」 로 끝나는 번호 줄은 안내


def test_instruction_text_in_value_cell_becomes_writable_field_and_reads_empty():
    summary = _tbl([["특성화분야", ""], ["사업추진 목표", "본 사업을 통해 달성하고자 하는 추진배경 및 추진목표를 요약 기술"],
                    ["변경", "O개 학과(O%)", ""]], 100, 3)
    data, _ = wf.fieldize(_hwpx(summary))
    names = _names(data)
    assert "칸:특성화분야" in names and "칸:사업추진 목표" in names
    assert not any("O개" in n for n in names)                                 # 예시 값은 라벨이 아니다
    got = {x["name"]: x["text"] for x in ww.fields(data)}
    assert got["칸:사업추진 목표"] == ""                                       # 안내 글 그대로면 아직 안 쓴 칸
    out, done = ww.set_fields(data, {"칸:사업추진 목표": "지역 전략산업 인재 양성"})
    assert done == ["칸:사업추진 목표"] and {x["name"]: x["text"] for x in ww.fields(out)}["칸:사업추진 목표"] == "지역 전략산업 인재 양성"


def test_outer_body_table_with_inner_guide_box_is_not_a_guide_page():
    """본문 전체를 감싼 겉 표가 안쪽 【작성방법】 상자 때문에 통째로 안내가 되지 않는다(첫머리가 안내일 때만)."""
    box = _tbl([["【작성방법】 1) 대외 여건을 분석하여 기술"]], 700, 1)
    body = _tbl([["1. 대학 현황"], [("", 1, box)], [""]], 100, 1)
    data, _ = wf.fieldize(_hwpx(body))
    assert _secs(data) == ["절:1. 대학 현황"]


# ── 첨부 서식을 제출본 꼴로·담당자 요구대로 시작(11-10) ──

def _form_bytes() -> bytes:
    deadline = _tbl([["신청단계", "제출서류"], ["본접수", "사업계획서"]], 900, 2)
    guide_page = _tbl([["사업계획서 작성 및 제출 요령"], ["1. 제출서류"], ["◦ 분량은 본문 80쪽 이내"], [("4. 제출기한", 1, deadline)]], 100, 1)
    cover = _tbl([["권역", "예) 수도권"]], 200, 2)
    box = _tbl([["【작성방법】 1) 대외 여건을 분석하여 구체적으로 기술"]], 400, 1)
    data_tbl = _tbl([["지표명", "기준값"], ["취업률(예시)", ""]], 500, 2)
    sec = (_p("[서식 2-1]", 10) + guide_page + _p("# 사업계획서 표지", 11) + cover + _p("Ⅰ. 사업추진 목표", 12)
           + _p("1. 대내·외 여건 분석", 13) + box + _p("※ 그림 또는 도표를 활용하여 작성 가능", 14) + data_tbl + _p("2. 특성화 계획", 15))
    return _hwpx(sec)


def test_submission_clean_drops_form_scaffolding_and_keeps_guides_by_slot():
    data, n = wf.clear_samples(_form_bytes())
    assert n == 1                                                             # 「예) 수도권」 견본 값
    out, fields = wf.fieldize(data)
    clean, guides, removed = wf.submission_clean(out)
    kinds = [r.split(":", 1)[0] for r in removed]
    assert {"서식 번호 머리", "서식 설명 쪽", "서식 표시 줄", "작성방법 상자", "안내 줄"} <= set(kinds)
    left = {x["name"] for x in ww.fields(clean)}
    assert left == {x["name"] for x in ww.fields(out)}                        # 쓸 자리는 하나도 잃지 않는다
    assert "칸:권역" in left and "절:1. 대내·외 여건 분석" in left
    assert "대외 여건" in guides["절:1. 대내·외 여건 분석"]                       # 뺀 작성방법은 그 절 이름으로
    assert "80쪽" in guides["서식 안내"]
    body = _text_of(clean)
    assert "작성 및 제출 요령" not in body and "【작성방법】" not in body and "[서식 2-1]" not in body and "예) 수도권" not in body
    assert "지표명" in body                                                   # 제출용 표 틀(칸 글 「(예시)」)은 남는다


def _text_of(data: bytes) -> str:
    import io
    import re as _re
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(data))
    return " ".join(_re.sub(r"<[^>]+>", " ", z.read(n).decode()) for n in z.namelist() if n.startswith("Contents/section"))


def _hw(tmp_path, monkeypatch, sid, docs):
    from pathlib import Path

    from tests.test_webhwp import _setup
    from zzaimy.app import webhwp_agent as wa
    from zzaimy.app import webhwp_templates as wt
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    return wa.HwpTools(db, base, sid, "kim", "기획처", {"id": 7, "name": "특성화 사업"}, lambda: docs), db


def _up(tmp_path, did, title, kind="form"):
    p = tmp_path / f"up-{did}.hwpx"
    p.write_bytes(_form_bytes())
    return {"id": did, "filename": title, "stored_path": str(p), "kind": kind}


def test_start_from_attached_form_is_cleaned_and_records_guides(tmp_path, monkeypatch):
    from pathlib import Path

    from zzaimy.app import webhwp
    docs = []
    hw, db = _hw(tmp_path, monkeypatch, 611, docs)
    docs.append(_up(tmp_path, 9101, "[서식 2-1] 특성화 선도대학 사업계획서 작성 서식.hwpx"))
    hw.request = "올린 작성 서식으로 사업계획서 써 줘"
    msg = hw.start({"form": "doc:9101", "title": "2026 특성화 사업계획서"})
    assert "만들고 이 대화에 이었다" in msg and "제출본 꼴로 정리" in msg and "80쪽" in msg
    fid = hw.fid()
    assert "작성 및 제출 요령" not in _text_of(Path(webhwp.get(db, fid)["path"]).read_bytes())
    assert "대외 여건" in webhwp.meta(db, fid)["guides"]["절:1. 대내·외 여건 분석"]
    assert "대외 여건" in hw.section_ctx("절:1. 대내·외 여건 분석")            # 쓸 때 다시 읽힌다


def test_named_doc_is_used_and_other_sources_refused(tmp_path, monkeypatch):
    docs = []
    hw, db = _hw(tmp_path, monkeypatch, 612, docs)
    docs += [_up(tmp_path, 9201, "[서식 2-1] 특성화 선도대학 사업계획서 작성 서식.hwpx"),
             _up(tmp_path, 9202, "2025 LINC 결과보고서.hwpx", "report")]
    hw.request = "올린 서식 2-1 양식으로 써 줘"
    assert "짚은 문서: doc:9201" in hw.forms({"query": "사업계획서"})
    tid = next(x.split()[0] for x in hw.forms({"query": "예산 변경"}).splitlines() if x.startswith("tpl:"))
    for other in (tid, "doc:9202"):
        out = hw.start({"form": other, "title": "계획서"})
        assert "doc:9201" in out and "바꾸지 않는다" in out
    assert "만들고 이 대화에 이었다" in hw.start({"form": "doc:9201", "title": "계획서"})


def test_continue_fresh_and_missing_reference(tmp_path, monkeypatch):
    docs = []
    hw, db = _hw(tmp_path, monkeypatch, 613, docs)
    docs.append(_up(tmp_path, 9301, "[서식 2-1] 특성화 선도대학 사업계획서 작성 서식.hwpx"))
    hw.request = "작성 서식으로 시작해 줘"
    assert "만들고 이 대화에 이었다" in hw.start({"form": "doc:9301", "title": "계획서"})
    from pathlib import Path

    from zzaimy.app import webhwp
    fid = hw.fid()
    out, _ = ww.set_fields(Path(webhwp.get(db, fid)["path"]).read_bytes(), {"절:1. 대내·외 여건 분석": "가" * 300})
    Path(webhwp.get(db, fid)["path"]).write_bytes(out)
    hw.request = "이어서 써 줘"                                                # (c) 지금 작업본에
    assert "새 작업본을 만들지 않는다" in hw.start({"form": "doc:9301", "title": "계획서 2", "new": False})
    hw2, _ = _hw(tmp_path / "b", monkeypatch, 614, docs)
    hw2.request = "양식부터 새로 설계해 줘"                                     # (b) 새로 설계
    assert "hwp_form_create" in hw2.start({"form": "doc:9301", "title": "계획서"})
    hw3, _ = _hw(tmp_path / "c", monkeypatch, 615, docs)
    hw3.request = "올린 작년 결과보고서 양식으로 써 줘"                           # (d) 짚은 문서를 못 찾음 — 한 번 묻게
    out = hw3.start({"form": "doc:9301", "title": "계획서"})
    assert "찾지 못했다" in out and "ask_user" in out and "doc:9301" in out
    assert "만들고 이 대화에 이었다" in hw3.start({"form": "doc:9301", "title": "계획서"})   # 한 번 물은 뒤에는 막지 않는다
