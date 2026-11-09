"""큰 한글 양식의 구조 사본(form_skeleton)·변환 한도 감지·끝내는 말 절 수 대조(11-10 시나리오 A)."""
import json

import pytest

from zzaimy.app import form_skeleton as fs
from zzaimy.app import webhwp_convert


def _tbl(rows):
    cells = [[r, c, 1, 1, 0, v] for r, row in enumerate(rows) for c, v in enumerate(row)]
    return {"kind": "table", "content": json.dumps({"n_rows": len(rows), "n_cols": max(len(r) for r in rows), "cells": cells},
                                                   ensure_ascii=False)}


def _p(t):
    return {"kind": "text", "content": t}


CHUNKS = [
    _tbl([["2017 LINC+ 페스티벌"]]),
    _tbl([["1", "", "행사개요"]]),
    _p("□ 행사개요"),
    _p("◦ (행사명) 2017 산학협력 EXPO 「LINC+ 페스티벌」"),
    _p("◦ (기 간) 2017. 10. 31(화) ~ 11. 2(목), 3일간"),
    _p("◦ (장 소) 부산 벡스코(BEXCO) 제2전시장 5홀"),
    _p("□ 행사구성 및 주요 프로그램"),
    _tbl([["구분", "주요 내용", "비고"], ["전시", "성과 전시관 55개", ""]]),
    _tbl([["1", "2017. 10. 31", "x"], ["a", "b", "c"]]),
    _tbl([["2", "", "주요 프로그램"]]),
    _p("(1) LINC+ 성과 전시"),
    _p("(2) 3D 프린팅존"),
    _p("◦ (일자/장소) 2017.10.31(화) ~ 11.2(목)/ 벡스코 5홀"),
]


def test_design_keeps_outline_levels_tables_and_labels():
    d = fs.design_from_chunks(CHUNKS, "2018 LINC+ 페스티벌 결과보고서")
    secs = [(s["level"], s["title"]) for s in d["sections"]]
    # 장 상자=1단계, □·(1)=2단계, 「□ 행사개요」 는 같은 제목이라 한 번만
    assert secs[0] == (1, "행사개요")
    assert (2, "행사구성 및 주요 프로그램") in secs
    assert (1, "주요 프로그램") in secs and (2, "LINC+ 성과 전시") in secs
    assert (2, "3D 프린팅존") in secs                      # 「3D」 의 3 은 번호가 아니다
    tbl = next(s for s in d["sections"] if s["title"] == "행사구성 및 주요 프로그램")["table"]
    assert tbl == ["구분", "주요 내용", "비고"]              # 값이 든 첫 행(날짜) 표는 열 머리로 쓰지 않는다
    assert d["summary"] == ["행사명", "기간", "장소"]         # 첫 절의 항목 라벨이 요약 표 칸
    g = d["sections"][0]["guide"]
    assert "행사명" in g and "2017" not in g                # 옛 값은 옮기지 않는다
    assert d["_extra_tables"] == 0


def test_skeleton_builds_blank_hwpx_with_fields():
    class _Db:
        def list_doc_chunks(self, doc_id):
            return CHUNKS
    data, fields, info = fs.skeleton_hwpx(_Db(), {"id": 1}, "2018 LINC+ 페스티벌 결과보고서", {})
    assert data[:2] == b"PK" and fields
    names = [f["name"] for f in fields]
    assert any(n.startswith("절:") and "3D 프린팅존" in n for n in names)
    assert info["sections"] >= 5 and info["tables"] >= 1
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(data))
    xml = "".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist() if n.startswith("Contents/section"))
    assert "벡스코" not in xml and "2017. 10. 31" not in xml   # 지난 해 값이 새 양식에 들어가지 않는다


def test_skeleton_without_chunks_says_why():
    class _Db:
        def list_doc_chunks(self, doc_id):
            return []
    with pytest.raises(ValueError):
        fs.skeleton_hwpx(_Db(), {"id": 1}, "x", {})


def test_convert_too_big_is_detected_before_upload(monkeypatch):
    monkeypatch.setenv("ZZAIMY_WEBHWP_UPLOAD_MAX_MB", "1")

    class _Never:
        def post(self, *a, **k):
            raise AssertionError("한도를 넘는 파일은 서버에 보내지 않는다")

        def close(self):
            pass
    with pytest.raises(webhwp_convert.TooBig):
        webhwp_convert.convert(b"\0" * (2 * 1024 * 1024), "HWP", "HWPX", "x.hwpx", http=_Never())


def test_convert_server_500_on_big_file_is_too_big(monkeypatch):
    monkeypatch.setenv("ZZAIMY_WEBHWP_UPLOAD_MAX_MB", "500")

    class _R:
        status_code = 500
        text = "exceededException"

        def raise_for_status(self):
            raise AssertionError("크기 오류는 연결 오류로 바꾸지 않는다")

    class _H:
        def post(self, *a, **k):
            return _R()

        def close(self):
            pass
    monkeypatch.setattr("zzaimy.app.webhwp.base_url", lambda: "http://x")
    with pytest.raises(webhwp_convert.TooBig):
        webhwp_convert.convert(b"\0" * (25 * 1024 * 1024), "HWP", "HWPX", "x.hwpx", http=_H())


def test_finish_claims_counts_only_completion_claims():
    from zzaimy.app.main import finish_claims
    assert finish_claims("작성 완료된 절(10개): 1. 행사 개요") == [10]
    assert finish_claims("10개 절을 모두 작성했습니다.") == [10]
    assert finish_claims("남은 빈 절은 3개이고, 다음은 「2. 추진 경과」") == []
    assert finish_claims("표 2개를 채웠습니다") == []
