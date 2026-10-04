"""문서 갈래별 공통 양식(172) — 여러 사업에 공통인 절만 남고, 한 사업에만 있는 절·다른 대학 자료는 빠진다."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("ct172", ROOT / "scripts" / "172_common_templates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _graph(progs, other_org=()):
    nodes, contains = {}, []
    did = 0
    for p, docs in progs.items():
        nodes[f"program:{p}"] = {"id": f"program:{p}", "type": "program", "label": p, "props": {}}
        for titles in docs:
            did += 1
            d = f"doc:{did}"
            nodes[d] = {"id": d, "type": "doc", "label": f"{did}.hwp", "doc_id": did,
                        "props": {"kind": "plan", "other_org": did in other_org}}
            contains.append({"src": f"program:{p}", "dst": d})
            for i, (path, t) in enumerate(titles):
                sid = f"{d}:sec:{path}"
                nodes[sid] = {"id": sid, "type": "section", "label": t, "props": {"seq": i}}
    return nodes, contains


def test_keeps_sections_shared_by_programs_and_nests_them():
    m = _load()
    common = [("1", "Ⅰ. 사업 개요"), ("1.1", "1. 추진 배경(1차년도)"), ("2", "Ⅱ. 추진 실적")]
    progs = {p: [common + [("3", f"Ⅲ. {p} 고유 과제")]] * 2 for p in ("a", "b", "c")}
    nodes, contains = _graph(progs)
    items = m.build(nodes, contains, "plan", min_programs=3, min_docs=3)
    keys = {x["key"]: x for x in items}
    assert set(keys) == {"사업개요", "추진배경", "추진실적"}
    assert keys["추진배경"]["parent"] == "사업개요"
    assert keys["추진배경"]["title"] == "추진 배경"            # 연차 꼬리표를 뗀다


def test_other_institution_docs_do_not_count():
    m = _load()
    progs = {"a": [[("1", "Ⅰ. 사업 개요")]], "b": [[("1", "Ⅰ. 사업 개요")]], "c": [[("1", "Ⅰ. 사업 개요")]]}
    nodes, contains = _graph(progs, other_org={3})
    assert m.build(nodes, contains, "plan", min_programs=3, min_docs=1) == []


def test_render_renumbers_without_program_names():
    m = _load()
    items = [{"key": "사업개요", "title": "사업 개요", "parent": "", "pos": 0.1, "programs": 3, "docs": 6, "secs": []},
             {"key": "추진배경", "title": "추진 배경", "parent": "사업개요", "pos": 0.2, "programs": 3, "docs": 6, "secs": []},
             {"key": "추진실적", "title": "추진 실적", "parent": "", "pos": 0.5, "programs": 3, "docs": 6, "secs": []}]
    md, n_sec, _ = m.render(items, "사업계획서", {}, db=None)
    assert "## Ⅰ. 사업 개요" in md and "### 1. 추진 배경" in md and "## Ⅱ. 추진 실적" in md
    assert n_sec == 3


def test_size_and_share_split_annual_from_program_docs_and_skip_toc():
    m = _load()
    big = [(str(i), f"Ⅰ. 절{i}") for i in range(1, 32)] + [("40", "Ⅱ. 목 차")]
    small = [("1", "1. 행사 일정"), ("2", "2. 소요예산"), ("3", "3. 기대효과")]
    progs = {p: [big, small] for p in ("a", "b", "c")}
    nodes, contains = _graph(progs)
    annual = {x["key"] for x in m.build(nodes, contains, "plan", 3, 1, lambda n: n >= 30, 0.5)}
    program = {x["key"] for x in m.build(nodes, contains, "plan", 3, 1, lambda n: 3 <= n <= 20, 0.5)}
    assert "행사일정" in program and "행사일정" not in annual
    assert "절1" in annual and "목차" not in annual


def test_consensus_keeps_only_sections_verified_in_three_skeletons():
    """모델이 묶은 공통 절은 출처(사업 번호·그 사업의 제목 그대로)를 뼈대와 대조해 셋 이상일 때만 남긴다."""
    import json as _json
    m = _load()
    sks = [("A", [{"level": 1, "title": "추진 배경", "table": None}, {"level": 1, "title": "예산", "table": "<table>t</table>"}]),
           ("B", [{"level": 1, "title": "사업 필요성", "table": None}, {"level": 1, "title": "소요 예산", "table": None}]),
           ("C", [{"level": 1, "title": "추진 목적", "table": None}]),
           ("D", [{"level": 1, "title": "예산 계획", "table": None}])]
    reply = {"sections": [
        {"title": "사업 개요", "level": 1, "sources": [{"p": 1, "t": "추진 배경"}, {"p": 2, "t": "사업 필요성"}, {"p": 3, "t": "추진 목적"}]},
        {"title": "예산 계획", "level": 1, "sources": [{"p": 1, "t": "예산"}, {"p": 2, "t": "소요 예산"}, {"p": 3, "t": "없는 제목"}]},
    ]}
    got = m.consensus(sks, "연차 사업계획서", lambda prompt: _json.dumps(reply, ensure_ascii=False))
    assert [g["title"] for g in got] == ["사업 개요"] and got[0]["programs"] == 3
    md, n_sec, _ = m.render_consensus(got, "연차 사업계획서", 4)
    assert "## Ⅰ. 사업 개요" in md and n_sec == 1


def test_table_skeleton_blanks_key_value_cells_and_drops_chapter_banner():
    import importlib.util as u
    import json as _json
    spec = u.spec_from_file_location("bt162", ROOT / "scripts" / "162_business_template.py")
    bt = u.module_from_spec(spec)
    spec.loader.exec_module(bt)
    kv = {"n_rows": 3, "n_cols": 6, "cells": [[0, 0, 1, 1, 1, "프로그램명"], [0, 1, 1, 5, 1, "임플란트 전문 치위생프로그램"],
                                              [1, 0, 1, 1, 1, "기간"], [1, 1, 1, 5, 1, "2023.3~"], [2, 0, 1, 1, 0, "a"]]}
    out = bt.table_skeleton(_json.dumps(kv, ensure_ascii=False))
    assert "프로그램명" in out and "임플란트" not in out and "2023" not in out
    banner = {"n_rows": 3, "n_cols": 4, "cells": [[0, 0, 1, 1, 1, "CHAPTER"], [0, 1, 1, 1, 1, "x"], [1, 0, 1, 1, 1, "Ⅲ"],
                                                  [1, 1, 1, 1, 1, "성과관리"], [2, 0, 1, 1, 0, "y"]]}
    assert bt.table_skeleton(_json.dumps(banner, ensure_ascii=False)) is None
