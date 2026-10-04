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
