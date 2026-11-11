"""문서 작성 시험 묶음(scripts 225·226, zzaimy.eval.eval_set) — 가짜 문서·가짜 서버로만(운영에 시험 금지)."""
from __future__ import annotations

import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

from zzaimy.eval import eval_set as es

ROOT = Path(__file__).resolve().parents[1]


def _hwpx(paras: list) -> bytes:
    """문단 글 또는 표(행 목록)로 된 아주 작은 hwpx."""
    body = []
    for p in paras:
        if isinstance(p, list):
            rows = "".join("<hp:tr>" + "".join(f"<hp:tc><hp:subList><hp:p><hp:run><hp:t>{c}</hp:t></hp:run></hp:p></hp:subList></hp:tc>" for c in r)
                           + "</hp:tr>" for r in p)
            body.append(f"<hp:p><hp:run><hp:tbl>{rows}</hp:tbl></hp:run></hp:p>")
        else:
            body.append(f"<hp:p><hp:run><hp:t>{p}</hp:t></hp:run></hp:p>")
    xml = '<?xml version="1.0"?><hs:sec xmlns:hs="s" xmlns:hp="p">' + "".join(body) + "</hs:sec>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", xml)
    return buf.getvalue()


ANSWER = ["2026년 사업계획서",
          "1. 대학 현황",
          "본교 입학정원은 1,250명이며 전임교원은 213명임.",
          "2. 정원 감축 계획",
          "2030학년도까지 입학정원의 3.5%를 감축함.",
          [["구분", "2026", "2027"], ["감축 인원", "20명", "24명"]],
          "3. 재정 집행 계획",
          "총 사업비 28,000백만 원을 5년간 집행함."]
RESULT = ["2026년 사업계획서",
          "1. 대학 현황",
          "본교 입학정원은 [확인 필요]명이며 전임교원은 213명임.",
          "2. 정원 감축 계획",
          "2030학년도까지 입학정원의 3%를 감축하며 미충원 99명을 반영함.",
          [["구분", "2026", "2027"], ["감축 인원", "20명", "확인 필요"]],
          "Ⅲ. 재정 집행 계획",
          "확인 필요"]


def test_blocks_sections_and_score(tmp_path):
    a, r = tmp_path / "answer.hwpx", tmp_path / "result.hwpx"
    a.write_bytes(_hwpx(ANSWER))
    r.write_bytes(_hwpx(RESULT))
    ev = tmp_path / "ev.txt"
    ev.write_text("미충원 인원은 99명, 필수 감축 3%", encoding="utf-8")
    blocks = es.read_blocks(r)
    assert [b.kind for b in blocks].count("table") == 1
    assert [s.title for s in es.sections(blocks)][1:] == ["1. 대학 현황", "2. 정원 감축 계획", "Ⅲ. 재정 집행 계획"]
    res = es.score(a, r, [ev])
    s = res["summary"]
    assert s["answer_sections"] == 3 and s["matched"] == 3          # 「3.」 과 「Ⅲ.」 도 제목 열쇠로 맞는다
    rows = {x["title"]: x for x in res["sections"]}
    assert rows["1. 대학 현황"]["num_hit"] == 1 and rows["1. 대학 현황"]["num_total"] == 2   # 213명만 맞음
    assert rows["1. 대학 현황"]["pending"] == 1
    assert not rows["3. 재정 집행 계획"]["filled"]                   # 「확인 필요」 뿐인 절은 채우지 않은 절
    assert rows["2. 정원 감축 계획"]["num_only_n"] == 0              # 99명·3%는 근거에 있어 지어낸 수치가 아니다
    assert (rows["2. 정원 감축 계획"]["table_full"], rows["2. 정원 감축 계획"]["table_cells"]) == (2, 3)
    md = es.report_md(res, "시험")
    assert "## 절별" in md and "| 1. 대학 현황 |" in md


def test_made_up_number_is_counted(tmp_path):
    a, r = tmp_path / "a.hwpx", tmp_path / "r.hwpx"
    a.write_bytes(_hwpx(["1. 대학 현황", "입학정원 1,250명"]))
    r.write_bytes(_hwpx(["1. 대학 현황", "입학정원 1,250명, 재학생 4,321명"]))
    x = es.score(a, r)["sections"][0]
    assert x["num_hit"] == 1 and x["num_only"] == ["4321명"]


def test_prompts_file_and_template(tmp_path):
    d = tmp_path / "set"
    import shutil
    shutil.copytree(ROOT / "scripts" / "eval_set_template", d)
    ps = es.EvalSet(d).prompts()
    assert len(ps) == 2 and all(p["effort"] == "fast" for p in ps)
    (d / "prompts.txt").write_text("# 메모\n\n[꼼꼼] 목차부터\n본문 써 줘\n", encoding="utf-8")
    assert es.EvalSet(d).prompts() == [{"text": "목차부터", "effort": "deep"}, {"text": "본문 써 줘", "effort": "fast"}]
    with pytest.raises(FileNotFoundError):
        es.EvalSet(d).blank


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), ROOT / "scripts" / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_script_against_fake_server(tmp_path, monkeypatch):
    """225 가 로그인 → 프로젝트 만들기(양식+근거 올림) → 요청 차례로 → 작업본 받기 → 비교까지 한 흐름으로 도는지(가짜 서버)."""
    import httpx
    d = tmp_path / "set"
    (d / "evidence").mkdir(parents=True)
    (d / "blank.hwpx").write_bytes(_hwpx(["1. 대학 현황"]))
    (d / "evidence" / "공고.txt").write_text("공고", encoding="utf-8")
    (d / "answer.hwpx").write_bytes(_hwpx(ANSWER))
    (d / "prompts.txt").write_text("목차부터 잡아 줘\n[꼼꼼] 본문 써 줘\n", encoding="utf-8")
    seen = {"sent": [], "files": 0, "status": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/login":
            assert b"pw=secret" in req.content
            return httpx.Response(303, headers={"location": "/", "set-cookie": "zz_session=x; Path=/"})
        if p == "/projects/bundle":
            seen["files"] = req.content.count(b'name="file"')
            return httpx.Response(303, headers={"location": "/project/77?bundle=1+1"})
        if p == "/project/77":
            return httpx.Response(200, text="<p>문서 2건</p>")
        if p == "/chat/send":
            body = req.content.decode("utf-8", "replace")
            seen["sent"].append(body)
            return httpx.Response(303, headers={"location": "/chat/501"})
        if p == "/chat/501/status":
            seen["status"] += 1
            return httpx.Response(200, json={"waiting": seen["status"] % 2 == 1, "progress": {"step": 3}})
        if p == "/chat/501/messages":
            return httpx.Response(200, json={"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "다 썼습니다"}]})
        if p == "/api/chat/501/hwp":
            return httpx.Response(200, json={"file": {"id": 9}, "files": [{"id": 9, "name": "작업본"}]})
        if p == "/hwp/9/file":
            return httpx.Response(200, content=_hwpx(RESULT))
        return httpx.Response(404)

    mod = _load("225_eval_set_run.py")
    real = httpx.Client

    def fake_client(*a, **kw):
        kw.pop("verify", None)
        return real(*a, transport=httpx.MockTransport(handler), **kw)
    monkeypatch.setattr(httpx, "Client", fake_client)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setenv("AURA_EVAL_PW", "secret")
    assert mod.main([str(d), "--base", "https://fake", "--user", "aura_test1", "--compare"]) == 0
    assert seen["files"] == 2
    assert "project_id=77" in seen["sent"][0] and "session_id=501" in seen["sent"][1] and "effort=deep" in seen["sent"][1]
    run_dir = next((d / "runs").iterdir())
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["session_id"] == 501 and len(run["turns"]) == 2 and run["result_file"] == 9
    assert "secret" not in (run_dir / "run.json").read_text(encoding="utf-8")
    assert (run_dir / "result.hwpx").exists() and (run_dir / "report.md").exists()


def test_run_refuses_without_password(tmp_path, monkeypatch):
    mod = _load("225_eval_set_run.py")
    monkeypatch.delenv("AURA_EVAL_PW", raising=False)
    d = tmp_path / "s"
    d.mkdir()
    (d / "prompts.txt").write_text("a\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        mod.main([str(d), "--user", "aura_test1"])
    assert mod.main([str(tmp_path / "new"), "--init"]) == 0 and (tmp_path / "new" / "prompts.txt").exists()


def test_prompts_folder_multiline(tmp_path):
    from zzaimy.eval.eval_set import EvalSet
    d = tmp_path / "set" / "prompts"
    d.mkdir(parents=True)
    (d / "002_표.md").write_text("[꼼꼼]\n표를 써 줘\n| 가 | 나 |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")
    (d / "001_목차.txt").write_text("목차부터 잡아 줘", encoding="utf-8")
    ps = EvalSet(tmp_path / "set").prompts()
    assert [p["name"] for p in ps] == ["001_목차", "002_표"]
    assert ps[1]["effort"] == "deep" and ps[1]["text"].startswith("표를 써 줘\n| 가 | 나 |") and ps[0]["effort"] == "fast"
