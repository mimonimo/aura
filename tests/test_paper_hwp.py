"""논문을 웹 한글 기안기에서 쓰기(ADR-0072) — 논문 양식(쓸 자리·꾸밈 스타일·머리말·꼬리말), 차례 읽기, 원고 → 칸 글,
논문 문서 만들기·열기 화면(개발자), 공통/논문 권한, Claude 참여자가 공동 편집 길로 쓰기(임대·사람 글 지키기)."""
from __future__ import annotations

import io
import re
import subprocess
import sys
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_accounts import _login
from tests.test_webhwp import _setup, _staff
from zzaimy.app import paper_hwp as ph
from zzaimy.app import webhwp
from zzaimy.app import webhwp_coedit as ce
from zzaimy.app import webhwp_edit_ops as eo
from zzaimy.app import webhwp_write as ww

ROOT = Path(__file__).resolve().parents[1]

CH1 = """# 1. 서론

## 1.1 프로젝트 배경 및 목적

대학 행정은 문서로 움직인다.
같은 문단의 둘째 줄이다.

둘째 문단이다.

## 1.2 핵심내용

본 프로젝트의 핵심은 두 축이다.

1. 첫 단계를 한다
2. 둘째 단계를 한다

> 참고: 모델에는 지식을 넣지 않는다.

[표 1-1] 장비별 역할

| 장비 | 역할 |
|---|---|
| 운영 VM | 웹 |

[표 3-1] 은 장비별 역할이다.

[그림 1-2] 자리: docs/images/diagrams/01-system.png

[그림 1-2] 전체 프로젝트 구조

```python
print("hi")
```

## 이 장의 그림·표 자리

- 원고 메모라 문서에 들어가지 않는다
"""
CH2 = "# 2. 관련 연구\n\n## 2.1 생성 모델\n\n### 2.1.1 vLLM\n\nvLLM 은 서빙 엔진이다.\n"
CH6 = "# 6. 결론\n\n## 6.1 연구 요약 및 기여점\n\n요약이다.\n"
BACK = "# 참고 문헌\n\n[1] 출처. 제목. URL\n\n# Abstract\n\nThis is AURA.\n"
APPX = "# 부록. AURA 사용 설명서\n\n## 부록 1. 한눈에 보기\n\n### 1.2 화면 구성\n\n화면은 셋이다.\n"


def _src(tmp_path) -> Path:
    d = tmp_path / "논문"
    d.mkdir()
    for name, text in {"01-서론.md": CH1, "02-관련-연구.md": CH2, "06-결론.md": CH6, "99-참고-문헌.md": BACK,
                       "부록-사용-설명서.md": APPX, "README.md": "# 목록\n\n## 1.1 무시\n"}.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


def _names(data: bytes) -> list[str]:
    return [x["name"] for x in ww.fields(data)]


def test_form_has_field_per_section_styles_and_page_frame():
    data = ph.build()
    names = _names(data)
    want = [ph.key_of(n, t) for lv, n, t in ph.OUTLINE if not (lv == 1 and n)]
    assert [n for n in names if n.startswith("절:") and n not in ("절:그림 목차", "절:표 목차")] == want
    assert {"칸:학과", "칸:프로젝트 번호", "칸:논문 제목", "칸:팀원", "칸:팀명", "절:그림 목차", "절:표 목차"} <= set(names)
    assert "절:1. 서론" not in names                                 # 장 제목에는 쓸 자리가 없다
    assert eo.check_bytes(data) == []
    assert set(ph.styles_in(data)) == set(ph.STYLES)
    z = zipfile.ZipFile(io.BytesIO(data))
    sec = z.read("Contents/section0.xml").decode()
    assert re.search(r'<hp:pagePr\b[^>]*width="59528"[^>]*height="84186"', sec)       # A4
    assert ph.META["short"] in re.search(r"<hp:header\b.*?</hp:header>", sec, re.S).group(0)
    assert "영남이공대학교 사이버보안과" in re.search(r"<hp:footer\b.*?</hp:footer>", sec, re.S).group(0)
    assert ww.field_values(data)["칸:학과"] == "사이버보안과"                # 표지 머리 줄 학과 칸의 기본값
    assert "<hp:pageNum" in sec and 'hideFirstHeader="1"' in sec
    # 제목은 필드 밖, 쓸 자리는 제목 바로 다음 문단
    ctx = ww.section_context(data, "절:1.1 프로젝트 배경 및 목적")
    assert "1.1 프로젝트 배경 및 목적" in ctx and "1. 서론" in ctx
    assert ww.field_values(data)["칸:논문 제목"] == ph.META["title"]
    # 목차: 세 단계, 줄 끝은 오른쪽 탭(점선 채움) — 쪽 번호는 편집 화면이 잰 뒤
    assert "    1.1 프로젝트 배경 및 목적" + ph._TOC_TAB + "</hp:t>" in sec
    hdr = z.read("Contents/header.xml").decode()
    assert 'type="RIGHT" leader="DASH"' in hdr


def test_outline_from_manuscripts_fills_missing_chapters_back_matter_and_appendix(tmp_path):
    ol = ph.outline_for(_src(tmp_path))
    chapters = [ph.heading_text(n, t, lv) for lv, n, t in ol if lv == 1]
    assert chapters[:6] == ["1. 서론", "2. 관련 연구", "3. 환경 구축 및 시스템 설계", "4. 구현", "5. 평가 및 결과", "6. 결론"]
    assert chapters[6:] == ["참고 문헌", "Abstract", "팀원 소개", "부록. AURA 사용 설명서"]
    keys = [ph.key_of(n, t) for _, n, t in ol]
    assert "절:1.2 핵심내용" in keys and "절:4.3 검증기 세 종" in keys       # 1장은 원고, 4장은 기본 차례
    assert "절:부록 1.2 화면 구성" in keys and "절:1.2 화면 구성" not in keys  # 부록 번호는 본문 장과 겹치지 않는다
    assert all("이 장의" not in t for _, _, t in ol)
    assert ph.outline_for(tmp_path / "없음") == ph.OUTLINE


def test_markdown_to_field_text_and_rich_items(tmp_path):
    secs = {s["title"]: s for s in ph.split_sections(CH1)}
    assert secs["이 장의 그림·표 자리"]["note"]
    bl = ph.blocks(secs["핵심내용"]["body"])
    assert [b["kind"] for b in bl] == ["p", "steps", "tip", "table", "p", "figure", "code"]
    nums = ph.Numbering()
    text = ph.to_text(bl, "1", nums)
    lines = text.split("\n")
    assert "1) 첫 단계를 한다" in lines and "※ 참고: 모델에는 지식을 넣지 않는다." in lines
    assert lines.index("장비 | 역할") < lines.index("[표 1-1] 장비별 역할")        # 캡션은 표 아래
    assert "[표 3-1] 은 장비별 역할이다." in lines                                # 문장 속 언급은 캡션이 아니다
    assert "〔그림 자리: docs/images/diagrams/01-system.png〕" in lines and "[그림 1-2] 전체 프로젝트 구조" in lines
    assert '    print("hi")' in lines
    assert nums.listed == {"그림": ["[그림 1-2] 전체 프로젝트 구조"], "표": ["[표 1-1] 장비별 역할"]}
    items, style = ph.to_items(bl, "1", ph.Numbering())
    assert [k for k, _ in items] == ["text", "table", "table", "text", "table", "text", "table"]
    assert style["[표 1-1] 장비별 역할"] == "표 캡션" and style["1) 첫 단계를 한다"] == "단계 목록"
    assert "팁 상자" in style.values() and "코드" in style.values() and "그림 자리" in style.values()


def test_match_field_needs_same_number_and_similar_title():
    fields = ["절:1.2 핵심 내용", "절:참고 문헌"]
    assert ph.match_field(fields, "1.2", "핵심내용") == "절:1.2 핵심 내용"
    assert ph.match_field(fields, "1.2", "화면 구성") == ""
    assert ph.match_field(fields, "", "참고 문헌") == "절:참고 문헌"


def _dev(app, uid="pdev"):
    app.state.accounts[uid] = app.state.new_account(uid, "pass-12345678", "dev")


def test_create_and_open_paper_doc_from_docs_page(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    _dev(app)
    app.state.accounts["stu"] = app.state.new_account("stu", "pass-12345678", "student")
    dev, kim, lee, stu = TestClient(app), TestClient(app), TestClient(app), TestClient(app)
    assert _login(dev, "pdev", "pass-12345678") and _login(kim, "kim", "pass-12345678")
    assert _login(lee, "lee", "pass-12345678") and _login(stu, "stu", "pass-12345678")
    page = dev.get("/dev/docs").text
    assert "논문 문서 만들기" in page and 'action="/dev/paper/hwp"' in page
    assert dev.get("/dev/paper/hwp", follow_redirects=False).headers["location"].startswith("/dev/docs")
    r = dev.post("/dev/paper/hwp", follow_redirects=False)
    assert r.status_code == 303
    fid = int(r.headers["location"].rsplit("id=", 1)[1])
    assert r.headers["location"] == f"/webhwpctrl/aura-edit?id={fid}"
    assert dev.post("/dev/paper/hwp", follow_redirects=False).headers["location"].endswith(f"id={fid}")     # 하나만
    f = webhwp.get(app.state.db, fid)
    assert Path(f["path"]).parent.name == "논문" and Path(f["path"]).parent.parent.name == webhwp.COMMON
    assert not webhwp.locked(app.state.db, fid)
    assert "논문 작성" in dev.get("/dev/docs").text
    assert dev.get("/dev/paper/hwp", follow_redirects=False).headers["location"] == f"/webhwpctrl/aura-edit?id={fid}"
    # 교직원은 부서와 상관없이 열고 칸을 쓴다, 학생은 못 연다, 만들기·열기 경로(/dev)는 개발자만
    for c in (kim, lee):
        assert c.get(f"/hwp/{fid}/file").status_code == 200
    assert lee.post(f"/hwp/{fid}/lease", json={"field": "절:1.2 핵심 내용", "client": "w1"}).status_code == 200
    assert stu.get(f"/hwp/{fid}/file").status_code in (403, 404)
    assert kim.post("/dev/paper/hwp", follow_redirects=False).status_code in (303, 403, 404)
    assert ph.current(app.state.db)["id"] == fid


def _paper(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    app.state.db.set_setting("user:kim:name", "김가람")
    fid, made = ph.create(app.state.db, Path(app.state.data_dir), "pdev", _src(tmp_path))
    assert made
    return app, fid


def _plan(app, fid, rich=False):
    data = Path(webhwp.get(app.state.db, fid)["path"]).read_bytes()
    return ph.plan(data, [CH1, CH2, CH6, BACK, APPX], rich=rich)


def test_claude_writes_sections_through_coedit_and_respects_people(tmp_path, monkeypatch):
    app, fid = _paper(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    p = _plan(app, fid)
    by = {w["field"]: w for w in p["writes"]}
    assert "절:1.1 프로젝트 배경 및 목적" in by and "절:부록 1.2 화면 구성" in by and "절:Abstract" in by
    assert "이 장의 그림·표 자리" in p["notes"]
    assert p["lists"]["절:표 목차"] == "[표 1-1] 장비별 역할"
    # 사람이 지금 쥔 칸, 사람이 쓴 칸은 건너뛴다
    assert ce.acquire(db, fid, "절:1.2 핵심내용", "kim", "k1")["ok"]
    ce.put(db, fid, "kim", {"절:6.1 연구 요약 및 기여점": "김가람이 쓴 요약"}, base=base, client="k2")
    out = {w["field"]: ph.write(db, base, fid, w, sleep=lambda s: None) for w in p["writes"]}
    assert out["절:1.1 프로젝트 배경 및 목적"]["ok"]
    assert out["절:1.2 핵심내용"] == {"field": "절:1.2 핵심내용", "ok": False, "why": "held", "by": "kim"}
    assert out["절:6.1 연구 요약 및 기여점"]["why"] == "human"
    vals = ww.field_values(Path(webhwp.get(db, fid)["path"]).read_bytes())
    assert vals["절:1.1 프로젝트 배경 및 목적"] == "대학 행정은 문서로 움직인다. 같은 문단의 둘째 줄이다.\n둘째 문단이다."
    assert vals["절:6.1 연구 요약 및 기여점"] == "김가람이 쓴 요약"
    assert vals["절:부록 1.2 화면 구성"] == "화면은 셋이다."
    st = ce.state(db, fid, ["절:1.1 프로젝트 배경 및 목적"])["절:1.1 프로젝트 배경 및 목적"]
    assert st["by"] == ph.WRITER and not ce.holder(db, fid, "절:1.1 프로젝트 배경 및 목적")     # 쓰고 놓았다
    assert webhwp.display_name(db, ph.WRITER) == "Claude"
    # 다시 쓰면 같은 글(자기 글은 덮어쓴다), overwrite 면 사람 글도
    assert ph.write(db, base, fid, by["절:1.1 프로젝트 배경 및 목적"], sleep=lambda s: None)["ok"]
    assert ph.write(db, base, fid, by["절:6.1 연구 요약 및 기여점"], overwrite=True, sleep=lambda s: None)["ok"]
    ph.leave(db, fid)
    assert eo.check_bytes(Path(webhwp.get(db, fid)["path"]).read_bytes()) == []


def test_rich_write_puts_tables_and_styles_into_file(tmp_path, monkeypatch):
    app, fid = _paper(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    w = next(x for x in _plan(app, fid, rich=True)["writes"] if x["field"] == "절:1.2 핵심내용")
    assert ph.write(db, base, fid, w, sleep=lambda s: None)["ok"]
    data = Path(webhwp.get(db, fid)["path"]).read_bytes()
    assert eo.check_bytes(data) == []
    st = ph.styles_in(data)
    sec = zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()
    cap = re.search(r'<hp:p\b[^>]*styleIDRef="(\d+)"[^>]*>(?:(?!<hp:p\b).)*?\[표 1-1\] 장비별 역할', sec, re.S)
    assert cap and cap.group(1) == st["표 캡션"]["style"]
    assert f'styleIDRef="{st["팁 상자"]["style"]}"' in sec and f'styleIDRef="{st["코드"]["style"]}"' in sec
    assert ww.field_values(data)["절:1.2 핵심내용"] == "본 프로젝트의 핵심은 두 축이다."
    # 다시 서식째 쓰면 앞서 넣은 문단을 지우고 새로(표가 겹치지 않는다)
    n = sec.count("[표 1-1] 장비별 역할")
    assert ph.write(db, base, fid, w, sleep=lambda s: None)["ok"]
    sec2 = zipfile.ZipFile(io.BytesIO(Path(webhwp.get(db, fid)["path"]).read_bytes())).read("Contents/section0.xml").decode()
    assert sec2.count("[표 1-1] 장비별 역할") == n
    # 구조 잠금을 쥔 사람이 있으면 서식째 쓰기는 하지 않는다
    assert webhwp.take_lock(db, fid, "kim")["ok"]
    assert ph.write(db, base, fid, w, sleep=lambda s: None)["why"] == "struct"


def test_fragment_is_valid_hwpx_with_styles():
    bl = ph.blocks("문단이다.\n\n> 참고: 팁\n\n| 가 | 나 |\n|---|---|\n| 1 | 2 |\n[표 1-1] 시험")
    data = ph.fragment(bl, "1", ph.Numbering())
    assert eo.check_bytes(data) == [] and set(ph.styles_in(data)) == set(ph.STYLES)


def test_writer_script_dry_run(tmp_path, monkeypatch):
    app, fid = _paper(tmp_path, monkeypatch)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "214_paper_write.py"), "--db", str(Path(app.state.data_dir) / "t.db"),
                        "--src", str(tmp_path / "논문"), "--dry-run"], capture_output=True, text=True,
                       env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"}, timeout=120)
    assert r.returncode == 0, r.stderr
    assert f"문서 {fid}" in r.stdout and "절:1.1 프로젝트 배경 및 목적" in r.stdout and "원고 메모라 쓰지 않음" in r.stdout
    assert ce.state(app.state.db, fid) == {}                         # 미리 보기는 쓰지 않는다
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "214_paper_write.py"), "--db", str(Path(app.state.data_dir) / "t.db"),
                        "--src", str(tmp_path / "논문"), "--rich", "--formatted", "--dry-run"], capture_output=True, text=True,
                       env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"}, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "절:1.2 핵심내용" in r.stdout and "절:1.1 프로젝트 배경 및 목적 ·" not in r.stdout   # 표·그림이 있는 절만


def _events(db, fid, kind):
    return [e for e in ce.events_since(db, fid, 0) if e["kind"] == kind]


def test_restamp_changes_dept_outside_fields_only_and_keeps_version(tmp_path, monkeypatch):
    monkeypatch.setitem(ph.META, "dept", ph.PLACEHOLDER_DEPT)            # 예전 판으로 만든 문서(○○과)
    app, fid = _paper(tmp_path, monkeypatch)
    monkeypatch.setitem(ph.META, "dept", "사이버보안과")
    db, base = app.state.db, Path(app.state.data_dir)
    ce.put(db, fid, "kim", {"절:6.1 연구 요약 및 기여점": "○○과 학생이 쓴 글", "칸:팀명": "AURA"}, base=base, client="k1")
    before = ww.field_values(Path(webhwp.get(db, fid)["path"]).read_bytes())
    seen = len(_events(db, fid, "doc-saved"))
    r = ph.restamp(db, base, fid)
    assert r["ok"] and r["changed"] and r["note"] == "2곳"                  # 표지 아래 줄·꼬리말
    p = Path(webhwp.get(db, fid)["path"])
    data = p.read_bytes()
    sec = zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()
    assert "영남이공대학교 사이버보안과" in re.search(r"<hp:footer\b.*?</hp:footer>", sec, re.S).group(0)
    assert ww.field_values(data) == before                                # 칸 글(사람이 쓴 ○○과 포함)은 그대로
    assert ww.field_values(data)["절:6.1 연구 요약 및 기여점"] == "○○과 학생이 쓴 글"
    olds = list((p.parent / ".판").glob(f"{p.stem} *{p.suffix}"))
    assert any(b"\xe2\x97\x8b\xe2\x97\x8b\xea\xb3\xbc" in zipfile.ZipFile(o).read("Contents/section0.xml") for o in olds)   # 앞 판(○○과)이 남았다
    assert len(_events(db, fid, "doc-saved")) == seen + 1                 # 열린 편집 화면이 새 판을 부른다
    assert webhwp.meta(db, fid)["paper"]["dept"] == "사이버보안과"
    assert ph.restamp(db, base, fid)["changed"] is False                  # 다시 하면 바꿀 것이 없다
    # 다른 학과로 다시 바꾸면 앞서 넣은 이름을 바꾼다, 구조 잠금을 쥔 사람이 있으면 하지 않는다
    assert ph.restamp(db, base, fid, "정보보안과")["note"] == "2곳"
    assert webhwp.take_lock(db, fid, "kim")["ok"]
    assert ph.restamp(db, base, fid, "사이버보안과")["why"] == "struct"


def test_writer_script_restamp_and_set_dept(tmp_path, monkeypatch):
    monkeypatch.setitem(ph.META, "dept", ph.PLACEHOLDER_DEPT)
    app, fid = _paper(tmp_path, monkeypatch)
    env = {"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"}
    cmd = [sys.executable, str(ROOT / "scripts" / "214_paper_write.py"), "--db", str(Path(app.state.data_dir) / "t.db"), "--restamp",
           "--set", "칸:학과=사이버보안과", "--delay", "0", "--pause", "0"]
    r = subprocess.run(cmd + ["--dry-run"], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "2곳" in r.stdout and "칸:학과" in r.stdout
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    data = Path(webhwp.get(app.state.db, fid)["path"]).read_bytes()
    assert ww.field_values(data)["칸:학과"] == "사이버보안과"
    assert "영남이공대학교 사이버보안과" in zipfile.ZipFile(io.BytesIO(data)).read("Contents/section0.xml").decode()


def test_toc_pages_from_editor_are_written_outside_fields(tmp_path, monkeypatch):
    app, fid = _paper(tmp_path, monkeypatch)
    db = app.state.db
    data = Path(webhwp.get(db, fid)["path"]).read_bytes()
    ents = ph.toc_entries(data)
    by = {e["text"]: e for e in ents}
    assert by["1. 서론"]["field"] == "절:1.1 프로젝트 배경 및 목적" and by["1. 서론"]["from_next"]      # 장 줄은 첫 절의 쪽
    assert by["참고 문헌"]["field"] == "절:참고 문헌"
    assert by["1.2 화면 구성"]["field"] == "절:부록 1.2 화면 구성"                                        # 부록 소절도 차례대로 짝
    assert all(e["field"] for e in ents)
    kim = TestClient(app)
    assert _login(kim, "kim", "pass-12345678")
    fields = kim.get(f"/hwp/{fid}/paper-toc").json()["fields"]
    assert fields[0] == "절:1.1 프로젝트 배경 및 목적" and len(fields) == len(set(fields))
    before = ww.field_values(data)
    r = kim.post(f"/hwp/{fid}/paper-toc", json={"pages": {f: i + 3 for i, f in enumerate(fields)}})
    assert r.status_code == 200 and r.json()["changed"]
    new = Path(webhwp.get(db, fid)["path"]).read_bytes()
    sec = zipfile.ZipFile(io.BytesIO(new)).read("Contents/section0.xml").decode()
    assert "1. 서론" + ph._TOC_TAB + "3</hp:t>" in sec and "    1.2 핵심내용" + ph._TOC_TAB + "4</hp:t>" in sec
    assert ww.field_values(new) == before and eo.check_bytes(new) == []
    assert kim.post(f"/hwp/{fid}/paper-toc", json={"pages": {f: i + 3 for i, f in enumerate(fields)}}).json()["changed"] is False
    # 논문 문서가 아니면 없다, 학생은 못 쓴다
    other = webhwp.add(db, Path(app.state.data_dir), webhwp.COMMON, "다른.hwpx", data, owner="kim")
    assert kim.get(f"/hwp/{other}/paper-toc").status_code == 404
    # 편집 화면 「⋯」 메뉴에 논문 문서만 「목차 쪽 번호 넣기」
    assert "목차 쪽 번호 넣기" in kim.get(f"/webhwpctrl/aura-edit?id={fid}").text
    assert "목차 쪽 번호 넣기" not in kim.get(f"/webhwpctrl/aura-edit?id={other}").text


def test_create_answers_json_url_for_the_page_button(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    _dev(app)
    dev = TestClient(app)
    assert _login(dev, "pdev", "pass-12345678")
    assert "data-paper-create" in dev.get("/dev/docs").text
    j = dev.post("/dev/paper/hwp", headers={"Accept": "application/json"}).json()
    assert j["url"] == f"/webhwpctrl/aura-edit?id={j['id']}"


def test_writer_holds_lease_and_shows_writing_while_streaming(tmp_path, monkeypatch):
    app, fid = _paper(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    w = {"field": "절:1.1 프로젝트 배경 및 목적", "kind": "text", "text": "\n".join(f"{i}번째 문단이다." for i in range(8))}
    seen = []

    def watch(_s):
        seen.append((ce.holder(db, fid, w["field"]), {p["user"]: p["field"] for p in ce.people(db, fid)}.get(ph.WRITER)))
    assert ph.write(db, base, fid, w, pause=0.01, sleep=watch)["ok"]
    assert len(seen) >= 3 and all(h == ph.WRITER and f == w["field"] for h, f in seen)    # 걸음마다 임대·표시가 그 칸
    assert not ce.holder(db, fid, w["field"])                                           # 다 쓰고 놓았다
    assert {p["user"]: p["field"] for p in ce.people(db, fid)}[ph.WRITER] == ""          # 칸 없이(문서 보는 중)


def _png(w: int, h: int) -> bytes:
    import struct
    import zlib
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_rich_write_embeds_listed_figures_and_keeps_placeholders(tmp_path, monkeypatch):
    app, fid = _paper(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    root = tmp_path / "repo"
    (root / "docs" / "images" / "diagrams").mkdir(parents=True)
    (root / "docs" / "images" / "diagrams" / "01-system.png").write_bytes(_png(40, 20))
    lst = tmp_path / "list.md"
    lst.write_text("| 번호 | 캡션 | 자료 |\n|---|---|---|\n| [그림 1-2] | 전체 구조 | docs/images/diagrams/01-system.png |\n"
                   "| [그림 1-3] | 없는 그림 | 캡처 필요: /chat |\n| [그림 1-4] | 빠진 파일 | docs/images/없음.png |\n", encoding="utf-8")
    figs = ph.figure_files(lst, root)
    assert list(figs) == ["1-2"]
    data = Path(webhwp.get(db, fid)["path"]).read_bytes()
    w = next(x for x in ph.plan(data, [CH1], rich=True, figures=figs)["writes"] if x["field"] == "절:1.2 핵심내용")
    assert list(w["pictures"].values()) == [str(root / "docs/images/diagrams/01-system.png")]
    assert ph.write(db, base, fid, w, sleep=lambda s: None)["ok"]
    new = Path(webhwp.get(db, fid)["path"]).read_bytes()
    z = zipfile.ZipFile(io.BytesIO(new))
    sec = z.read("Contents/section0.xml").decode()
    assert eo.check_bytes(new) == [] and sec.count("<hp:pic ") == 1 and "⟦그림 파일" not in sec
    assert sec.index("<hp:pic ") < sec.index("[그림 1-2] 전체 프로젝트 구조")                     # 캡션은 그림 아래
    bins = [n for n in z.namelist() if n.startswith("BinData/")]
    assert len(bins) == 1 and 'href="' + bins[0] + '"' in z.read("Contents/content.hpf").decode()
    assert ph.write(db, base, fid, w, sleep=lambda s: None)["ok"]                            # 다시 써도 그림·BinData 가 늘지 않는다
    z2 = zipfile.ZipFile(io.BytesIO(Path(webhwp.get(db, fid)["path"]).read_bytes()))
    assert z2.read("Contents/section0.xml").decode().count("<hp:pic ") == 1
    assert len([n for n in z2.namelist() if n.startswith("BinData/")]) == 1


def test_comments_are_left_out_and_references_get_hanging_style(tmp_path, monkeypatch):
    app, fid = _paper(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    back = "# 참고 문헌\n\n<!-- 인쇄 전에 주소를 확인한다 -->\n\n[1] 출처. 제목. URL\n\n[2] 둘째 출처. 제목. URL\n"
    data = Path(webhwp.get(db, fid)["path"]).read_bytes()
    w = next(x for x in ph.plan(data, [back], rich=True)["writes"] if x["field"] == "절:참고 문헌")
    assert "인쇄 전에" not in w["text"]
    assert ph.write(db, base, fid, w, sleep=lambda s: None)["ok"]
    new = Path(webhwp.get(db, fid)["path"]).read_bytes()
    st = ph.styles_in(new)
    sec = zipfile.ZipFile(io.BytesIO(new)).read("Contents/section0.xml").decode()
    cap = re.search(r'<hp:p\b[^>]*styleIDRef="(\d+)"[^>]*>(?:(?!<hp:p\b).)*?\[2\] 둘째 출처', sec, re.S)
    assert cap and cap.group(1) == st["참고 문헌"]["style"]
