"""kordoc 어댑터 — JSON 블록을 우리 ParseResult(문단·제목·표 좌표·그림)로 옮긴다."""

import json
from pathlib import Path

from zzaimy.ingest.parsers import kordoc


def _data():
    return {"success": True, "blocks": [
        {"type": "heading", "level": "1", "pageNumber": "1", "text": "1.1. 교육여건 분석"},
        {"type": "paragraph", "pageNumber": "1", "text": "지역 산업  수요가\n늘고 있다."},
        {"type": "table", "pageNumber": "1", "table": {"rows": 2, "cols": 3, "cells": [
            # kordoc 격자: 병합 셀 뒤에 빈 자리표 셀이 따라온다
            [{"text": "구분", "colSpan": "2", "rowSpan": "1"}, {"text": "", "colSpan": "1", "rowSpan": "1"}, {"text": "값", "colSpan": "1", "rowSpan": "1"}],
            [{"text": "가", "colSpan": "1", "rowSpan": "1"}, {"text": "<table><tr><td>중첩</td><td>표</td></tr></table>", "colSpan": "1", "rowSpan": "1"},
             {"text": "다<br>라", "colSpan": "1", "rowSpan": "1"}]]}},
        {"type": "image", "pageNumber": "2", "text": "image_001.png"},
        {"type": "image", "pageNumber": "2", "text": "missing.png"},
        {"type": "paragraph", "pageNumber": "2", "text": ""},
    ]}


def test_from_json_maps_blocks_tables_and_images(tmp_path):
    img_dir = tmp_path / "images"; img_dir.mkdir(); (img_dir / "image_001.png").write_bytes(b"x")
    r = kordoc.from_json(_data(), image_dir=img_dir)
    assert [e.kind for e in r.entries] == ["heading", "text", "table", "image"]
    assert r.entries[1].text == "지역 산업 수요가 늘고 있다."
    t = r.tables[0]
    assert (t.n_rows, t.n_cols) == (2, 3)
    cells = {(c.row, c.col): c for c in t.cells}
    assert cells[(0, 0)].col_span == 2 and cells[(0, 2)].text == "값" and (0, 1) not in cells   # 자리표 셀은 세지 않는다
    assert cells[(1, 1)].text == "중첩 표" and cells[(1, 2)].text == "다\n라"           # 중첩 표는 글로, <br> 은 줄바꿈
    assert len(r.images) == 1 and r.images[0].path.name == "image_001.png" and any("missing.png" in w for w in r.warnings)
    assert [p.page_no for p in r.pages] == [1, 2] and "[[img]]image_001.png" in r.pages[1].text


def test_available_and_version_without_install(monkeypatch, tmp_path):
    monkeypatch.delenv("ZZAIMY_KORDOC", raising=False)
    monkeypatch.setattr(kordoc.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(kordoc.shutil, "which", lambda name: None)
    assert not kordoc.available() and kordoc.version() == ""
    exe = tmp_path / "opt" / "kordoc" / "node_modules" / ".bin" / "kordoc"
    exe.parent.mkdir(parents=True); exe.write_text("")
    (tmp_path / "opt" / "kordoc" / "node_modules" / "kordoc" / "dist").mkdir(parents=True)
    (tmp_path / "opt" / "kordoc" / "node_modules" / "kordoc" / "package.json").write_text(json.dumps({"name": "kordoc", "version": "4.15.7"}))
    exe.unlink(); exe.symlink_to(tmp_path / "opt" / "kordoc" / "node_modules" / "kordoc" / "dist" / "cli.js")
    (tmp_path / "opt" / "kordoc" / "node_modules" / "kordoc" / "dist" / "cli.js").write_text("")
    assert kordoc.available() and kordoc.version() == "4.15.7"
    monkeypatch.setenv("ZZAIMY_KORDOC_OFF", "1")
    assert not kordoc.available()
