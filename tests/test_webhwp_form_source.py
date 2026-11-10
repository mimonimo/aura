"""보고서·계획서 양식 출처(11-10) — 프로젝트·대화에 올린 양식·작년 문서가 있으면 그것, 없으면 한 번 묻기, 「만들어」면 바로 설계,
공통 양식으로 쓰던 중 양식이 올라오면 옮겨 담을지 묻고 같은 이름 절·칸으로 옮기기."""
from __future__ import annotations

from pathlib import Path

from tests.test_webhwp import _setup
from zzaimy.app import webhwp, webhwp_agent as wa
from zzaimy.app import webhwp_templates as wt
from zzaimy.app import webhwp_write as ww

PROJECT = {"id": 7, "name": "LINC 사업"}


def _tpl(hw, word: str = "예산 변경") -> str:
    out = hw.forms({"query": word})
    return next(line.split()[0] for line in out.splitlines() if word in line and line.startswith("tpl:"))


def _uploaded(tmp_path: Path, db, tid: str, did: int, title: str, kind: str = "report") -> dict:
    """프로젝트에 올린 작년 결과보고서 꼴 — 실제 한글 파일(공통 양식 바이트)을 원본 자리에 둔 문서 기록."""
    t = webhwp.get(db, int(tid[4:]))
    p = tmp_path / f"up-{did}.hwpx"
    p.write_bytes(Path(t["path"]).read_bytes())
    return {"id": did, "filename": title, "stored_path": str(p), "kind": kind}


def test_project_form_used_first(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    docs: list[dict] = []
    hw = wa.HwpTools(db, base, 501, "kim", "기획처", PROJECT, lambda: docs)
    tid = _tpl(hw)
    docs += [_uploaded(tmp_path, db, tid, 9001, "2025 LINC 결과보고서", "report"),
             {"id": 9002, "filename": "2026 LINC 공고문", "stored_path": str(tmp_path / "x.hwpx"), "kind": "announcement"}]
    hw.request = "올린 문서로 올해 결과보고서 써 줘"
    out = hw.forms({"query": "결과보고서"})
    lines = out.splitlines()
    head = next(i for i, x in enumerate(lines) if x.startswith("【이 프로젝트"))
    assert lines[head + 1].startswith("doc:9001") and "보고서" in lines[head + 1]
    assert "근거 문서라 양식으로는 맞지 않을 수 있음" in out.split("doc:9002", 1)[1].splitlines()[0]   # 공고는 양식 후보가 아니다
    assert "맞춤 양식 없음" not in out
    assert [d["id"] for d in hw.project_form_docs()] == [9001]
    msg = hw.start({"form": "doc:9001", "title": "2026 LINC 결과보고서"})
    assert "만들고 이 대화에 이었다" in msg and wa.bound(db, 501)


def test_no_form_asks_once(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    hw = wa.HwpTools(db, base, 502, "kim", "기획처", PROJECT, lambda: [])
    hw.request = "올해 LINC 결과보고서 한글로 써 줘"
    out = hw.forms({"query": "예산 변경"})
    assert "맞춤 양식 없음" in out and wa._FORM_ASK_LINE in out and "「올리기」" in out
    assert "만들어 진행하라고 하면" not in out
    tid = _tpl(hw)
    msg = hw.start({"form": tid, "title": "2026 LINC 결과보고서"})
    assert "ask_user" in msg and wa._FORM_ASK_LINE in msg and not wa.bound(db, 502)
    # 한 번 물은 뒤에는 담당자의 답(올리기·만들어 진행·채팅 답)이 무엇이든 막지 않는다
    assert "만들고 이 대화에 이었다" in hw.start({"form": tid, "title": "2026 LINC 결과보고서"})
    # 양식 설계도 같은 관문 — 설계 모델을 부르기 전에 묻는다
    hw2 = wa.HwpTools(db, base, 503, "kim", "기획처", PROJECT, lambda: [])
    hw2.request = "올해 결과보고서 써 줘"
    hw2.forms({"query": "보고서"})
    assert "ask_user" in hw2.form_create({"purpose": "결과보고서"})
    # 프로젝트 밖 대화는 묻지 않는다
    hw3 = wa.HwpTools(db, base, 504, "kim", "기획처", None, lambda: [])
    hw3.request = "결과보고서 써 줘"
    assert "만들고 이 대화에 이었다" in hw3.start({"form": tid, "title": "결과보고서"})


def test_make_request_skips_ask(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    hw = wa.HwpTools(db, base, 505, "kim", "기획처", PROJECT, lambda: [])
    for said, want in [("양식은 알아서 만들어 진행해 줘", True), ("한글 양식부터 잡아 줘", True),
                       ("양식은 공고 평가 항목에 맞춰 7~8개 절로 간단히 잡고 본문까지 채워 줘", True),
                       ("양식 없이 그냥 써 줘", True), ("결과보고서 양식을 새로 설계해 줘", True),
                       ("작년 양식이 있으면 그걸로 만들어 줘", False), ("결과보고서 써 줘", False)]:
        hw.request = said
        assert hw.wants_form_made() is want, said
    hw.request = "결과보고서 써 줘"
    assert "맞춤 양식 없음" in hw.forms({"query": "예산 변경"})
    # 묻기 카드 대신 채팅으로 답해도 같다 — 담당자 글에 「만들어 진행」
    db.add_chat(505, "user", "만들어 진행")
    hw.request = "응"
    assert hw.wants_form_made()
    assert "만들고 이 대화에 이었다" in hw.start({"form": _tpl(hw), "title": "2026 LINC 결과보고서"})


def test_late_upload_offers_carry(tmp_path, monkeypatch):
    app = _setup(tmp_path, monkeypatch)
    db, base = app.state.db, Path(app.state.data_dir)
    wt.ensure_all(db, base)
    docs: list[dict] = []
    hw = wa.HwpTools(db, base, 506, "kim", "기획처", PROJECT, lambda: docs)
    hw.request = "양식은 알아서 만들어 진행해 줘"
    tid = _tpl(hw)
    hw.start({"form": tid, "title": "2026 LINC 결과보고서"})
    first = wa.bound(db, 506)
    assert webhwp.meta(db, first)["form_known"] == []
    assert "채웠다" in hw.fill({"values": {"칸:사업명": "LINC 3.0 육성사업"}})
    assert not any("옮겨 담을지" in ln for ln in hw.state_lines())
    # 담당자가 작년 결과보고서를 올렸다 — 옮겨 담을지 묻는 안내(한 요청에 한 번, 다음 요청에는 다시 묻지 않는다)
    docs.append(_uploaded(tmp_path, db, tid, 9101, "2025 LINC 결과보고서"))
    hw.request = "작년 보고서 올렸어"
    note = [ln for ln in hw.state_lines() if "옮겨 담을지" in ln]
    assert note and "doc:9101" in note[0] and "carry: true" in note[0]
    assert any("옮겨 담을지" in ln for ln in hw.state_lines())          # 같은 요청 안의 다음 걸음에도 보인다
    hw.request = "다음 절 써 줘"
    assert not any("옮겨 담을지" in ln for ln in hw.state_lines())
    # 옮기기 — 같은 이름 칸의 글이 새 양식 작업본으로 간다
    hw.request = "옮기기"
    msg = hw.start({"form": "doc:9101", "title": "2026 LINC 결과보고서(작년 양식)", "carry": True})
    second = wa.bound(db, 506)
    assert second != first and "만들고 이 대화에 이었다" in msg and "칸:사업명" in msg
    vals = ww.field_values(Path(webhwp.get(db, second)["path"]).read_bytes())
    assert vals["칸:사업명"] == "LINC 3.0 육성사업"
