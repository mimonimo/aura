"""모델 비교 화면(/dev/models/compare) — 관리자만, 덜 찬 결과도 그린다, 문서 내려받기는 실행의 docs/ 안만,
품질·성능(/dev/quality)의 「모델 비교」 카드가 마지막 실행 요약을 보이고 이 화면은 그 자세히,
개발 현황 Writer 줄의 바로가기는 결과가 있을 때만(그 카드로)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor
from zzaimy.app import model_compare
from zzaimy.app.main import create_app

PW = {"zzdev": "devpass", "plan": "plan-pass", "stu": "stu-pass"}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    base = tmp_path / "model_compare"
    base.mkdir()
    monkeypatch.setenv("ZZAIMY_MODEL_COMPARE_DIR", str(base))
    (tmp_path / "accounts.json").write_text(json.dumps({
        "zzdev": {"pw": "devpass", "role": "dev"}, "zzaimy": {"pw": "boot-pass-1", "role": "staff"},
        "plan": {"pw": "plan-pass", "role": "staff", "dept": "기획처"}, "stu": {"pw": "stu-pass", "role": "student"}}))
    app = create_app(db_path=tmp_path / "t.db", inbox_dir=tmp_path / "inbox", processor=FakeProcessor(),
                     drafter=FakeDrafter(), password="boot-pass-1")
    return app, base, tmp_path


def _client(app, uid) -> TestClient:
    c = TestClient(app)
    r = c.post("/login", data={"username": uid, "pw": PW[uid]}, follow_redirects=False)
    assert r.status_code == 303, uid
    return c


def _partial_run(base, run_id="run-1"):
    d = base / run_id
    (d / "docs" / "open").mkdir(parents=True)
    (d / "docs" / "open" / "기안문.hwpx").write_bytes(b"PK-hwpx")
    (d / "summary.json").write_text(json.dumps({
        "run_id": run_id, "created": "2026-10-23T10:00",
        "arms": [{"key": "open", "label": "Qwen 27B", "model": "q27", "kind": "open"},
                 {"key": "lora", "label": "writer-lora", "model": "w", "kind": "sft-lora"}],
        "blocks": [{"key": "writer", "title": "절 작성",
                    "pairwise": [{"a": "lora", "b": "open", "win": 6, "tie": 2, "loss": 2, "ci_low": 0.41, "ci_high": 0.83, "n": 10}],
                    "per_dimension": {"instruction": {"open": 3.1, "lora": 3.9}, "style": {"open": 2.5}},
                    "metrics": {"ttft_s": {"open": 0.8, "lora": 0.9}}}],     # per_type 없음 — 진행 중
        "notes": ["판정 모델은 같은 27B"]}, ensure_ascii=False))
    (d / "samples.json").write_text(json.dumps([
        {"id": "s1", "type": "기안문", "input": "<script>alert(1)</script> 입력",
         "outputs": {"open": "오픈 출력", "lora": "어댑터 출력"},
         "scores": {"open": {"instruction": 3}, "lora": {"instruction": 4}}, "judge_reason": "어댑터가 형식을 지킴"}],
        ensure_ascii=False))
    (d / "docs.json").write_text(json.dumps([{"arm": "open", "title": "기안문 초안", "file": "open/기안문.hwpx",
                                              "created": "2026-10-23"}], ensure_ascii=False))
    return d


@pytest.mark.parametrize("uid,code", [("zzdev", 200), ("plan", 403), ("stu", 403)])
def test_admin_only(world, uid, code):
    app, base, _ = world
    _partial_run(base)
    c = _client(app, uid)
    assert c.get("/dev/models/compare").status_code == code
    assert c.get("/dev/models/compare/run-1").status_code == code
    assert c.get("/dev/models/compare/run-1/doc", params={"f": "open/기안문.hwpx"}).status_code == code


def test_renders_partial_run(world):
    app, base, _ = world
    _partial_run(base)
    page = _client(app, "zzdev").get("/dev/models/compare").text
    assert "run-1" in page and "오픈 27B" in page and "SFT 어댑터" in page
    assert "절 작성" in page and "이김 6" in page and "0.41~0.83" in page
    assert "지시 충실" in page and "문체" in page and "첫 글자까지" in page
    assert "진행 중" in page                                   # 서류 갈래별 표가 아직 없다
    assert "<script>alert(1)</script>" not in page              # 표본 글은 JSON 안에서도 이스케이프
    assert "기안문 초안" in page and "/dev/models/compare/run-1/doc?f=" in page
    assert "기안기로 열기" not in page


def test_samples_only_run_and_run_list(world):
    app, base, _ = world
    _partial_run(base, "run-1")
    d = base / "run-2"
    d.mkdir()
    (d / "samples.json").write_text(json.dumps([{"id": "a", "type": "공문", "input": "x", "outputs": {"open": "y"}}]))
    c = _client(app, "zzdev")
    page = c.get("/dev/models/compare/run-2").text
    assert page.count("진행 중") >= 2 and 'href="/dev/models/compare/run-1"' in page
    assert c.get("/dev/models/compare/nope").status_code == 404
    assert c.get("/dev/models/compare/..%2F..").status_code == 404


def test_doc_download_stays_inside_docs(world):
    app, base, tmp = world
    d = _partial_run(base)
    (tmp / "secret.hwpx").write_bytes(b"secret")
    (d / "summary.hwpx").write_bytes(b"outside docs")
    c = _client(app, "zzdev")
    ok = c.get("/dev/models/compare/run-1/doc", params={"f": "open/기안문.hwpx"})
    assert ok.status_code == 200 and ok.content == b"PK-hwpx"
    for bad in ("../summary.hwpx", "../../../secret.hwpx", str(tmp / "secret.hwpx"), "open/../../summary.hwpx",
                "../summary.json", "open", ""):
        assert c.get("/dev/models/compare/run-1/doc", params={"f": bad}).status_code == 404, bad
    assert model_compare.safe_doc(d, "../../secret.hwpx") is None


def test_dev_link_only_when_runs_exist(world):
    app, base, _ = world
    c = _client(app, "zzdev")
    assert "/dev/quality#model-compare" not in c.get("/dev").text
    _partial_run(base)
    page = c.get("/dev").text
    assert 'href="/dev/quality#model-compare"' in page and "모델 비교 결과" in page
    assert 'href="/dev/models/compare"' not in page                 # 바로가기는 한 곳(품질·성능 카드)으로만


def test_quality_card_without_runs(world):
    app, _, _ = world
    page = _client(app, "zzdev").get("/dev/quality").text
    assert 'id="model-compare"' in page and "아직 비교 실행 결과 없음" in page
    assert "/dev/models/compare" not in page


def test_quality_card_shows_latest_run_from_files(world):
    """카드 수치는 결과 파일에서만 — 대상·쌍대 점수(95% 구간, 대조군 빼고)·문서 작성·속도·결론, 지난 실행."""
    app, base, _ = world
    _partial_run(base, "run-1")
    d = _finished_run(base)
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    s["verdict"] = "합친 판은 운영 모델보다 못해 채택하지 않음"
    (d / "summary.json").write_text(json.dumps(s, ensure_ascii=False))
    page = _client(app, "zzdev").get("/dev/quality").text
    card = page.split('id="model-compare"')[1].split("</div></div>")[0]
    assert "2026-10-24 06:02" in card
    assert '>오픈 27B</th>' in card and '>SFT 합친 판</th>' in card and 'title="SFT 병합본"' in card   # 칸 이름은 짧게(갈래 종류 이름), 원래 이름은 풍선 도움말
    assert "SFT 합친 판 대 오픈 27B 점수 0.31 (95% 구간 0.26~0.35)" in card
    assert "0.52" not in card                                        # 대조군(같은 모델 두 번)은 요약에서 뺀다
    assert "100.0%" in card and "82.0%" in card                      # 문서 작성 묶음의 비율 지표
    assert "첫 글자까지" in card and "24.32" in card                     # 속도 묶음
    assert card.count("<tr>") <= 5                                   # 머리 + 핵심 줄 넷까지
    assert "합친 판은 운영 모델보다 못해 채택하지 않음" in card
    assert 'href="/dev/models/compare/sft-compare">자세히' in card and card.count("<a ") == 1   # 들어가는 단추 하나
    tiles = page.split('class="q-tiles"', 1)[1].split("</nav>", 1)[0]
    assert "합친 판은 운영 모델보다 못해 채택하지 않음" in tiles and 'href="#model-compare"' in tiles


def test_compare_page_belongs_to_quality(world):
    from zzaimy.app import dev_nav
    app, base, _ = world
    _partial_run(base)
    page = _client(app, "zzdev").get("/dev/models/compare/run-1").text
    assert '<a class="page-crumb" href="/dev/quality#model-compare">품질·성능</a>' in page
    assert '<a href="/dev/quality" class="active" aria-current="page">' in page      # 사이드바도 품질·성능
    assert '<a href="/dev/train" class="active"' not in page
    for path in ("/dev/models/compare", "/dev/models/compare/run-1"):
        assert dev_nav.owner(path) == "/dev/quality"
        assert dev_nav.is_active("/dev/quality", path, "") and not dev_nav.is_active("/dev/train", path, "")
        assert dev_nav.parent(path) == ("/dev/quality", "품질·성능")
    assert dev_nav.owner("/dev/models/other") == "/dev/train"
    assert dev_nav.owner("/dev/history") == "/dev" and dev_nav.owner("/dev/doc/x.md") == "/dev/docs"
    assert dev_nav.owner("/dev/archive/7") == "/dev/archive" and dev_nav.owner("/dev/quality") == "/dev/quality"
    assert dev_nav.parent("/dev/quality") is None and dev_nav.owner("/dev/없는") == ""


def test_short_names_are_generic():
    """칸 이름 — 결과 파일의 짧은 이름이 먼저, 없으면 하나뿐인 갈래 종류 이름, 그다음 괄호를 뗀 이름. 겹치면 원래 이름."""
    from zzaimy.app import model_compare as mc
    arms = [{"key": "a", "label": "큰 모델 (NVFP4, 토르)", "kind_label": "오픈 27B"},
            {"key": "b", "label": "학습본 v1 (LoRA)", "kind_label": "SFT 어댑터"},
            {"key": "c", "label": "학습본 v2 (LoRA)", "kind_label": "SFT 어댑터"},
            {"key": "d", "label": "기타 판", "kind_label": "", "short": "짧은 판"}]
    mc._short_names(arms, {"a": "운영 27B"})
    assert [a["short"] for a in arms] == ["운영 27B", "학습본 v1", "학습본 v2", "짧은 판"]
    same = [{"key": "x", "label": "판 (1)", "kind_label": ""}, {"key": "y", "label": "판 (2)", "kind_label": ""}]
    mc._short_names(same, {})
    assert [a["short"] for a in same] == ["판 (1)", "판 (2)"]


def test_labels_and_percent_keys_from_summary():
    from zzaimy.app import model_compare as mc
    b = mc._block({"key": "x", "metrics": {"invented_any": {"o": 0.041}, "chrf": {"o": 27.9}}}, ["o"],
                  {"invented_any": "질문에 없는 숫자가 든 답 비율", "chrf": "정답 겹침(chrF)"}, {"invented_any"})
    rows = {r["name"]: r for r in b["other"]}
    assert rows["질문에 없는 숫자가 든 답 비율"]["pct"] is True and rows["정답 겹침(chrF)"]["pct"] is False


def _finished_run(base, run_id="sft-compare"):
    """212 내보내기와 같은 꼴 — 지표는 {갈래: {지표: 값}}, 종류별은 metrics 안에, 문서 작성은 회차별."""
    d = base / run_id
    d.mkdir()
    arms = [{"key": "open-nvfp4", "label": "오픈 모델", "model": "q", "kind": "open"},
            {"key": "sft-merged", "label": "SFT 병합본", "model": "m", "kind": "sft-merged"},
            {"key": "sft-lora", "label": "SFT 어댑터", "model": "l", "kind": "sft-lora"}]
    blocks = [
        {"key": "heldout220", "title": "시험 문항 220개", "n": 220,
         "metrics": {"open-nvfp4": {"chrf": 27.89, "invented_any": 0.041, "sec": 37.1},
                     "sft-merged": {"chrf": 36.6, "invented_any": 0.0, "sec": 16.8},
                     "sft-lora": {"chrf": 27.9, "invented_any": 0.04, "sec": 37.0}},
         "pairwise": [{"a": "sft-merged", "b": "open-nvfp4", "win": 28, "tie": 80, "loss": 112, "n": 220, "score": 0.309,
                       "ci_low": 0.264, "ci_high": 0.355, "control": False, "flip_rate": 0.364},
                      {"a": "sft-lora", "b": "open-nvfp4", "win": 49, "tie": 129, "loss": 42, "n": 220, "score": 0.516,
                       "ci_low": 0.475, "ci_high": 0.559, "control": True}],
         "per_dimension": {"지시 충실": {"open-nvfp4": 4.95, "sft-merged": 5.0}},
         "per_type": [{"type": "규정·제도 문서·첫 질문", "n": 46,
                       "metrics": {"open-nvfp4": {"chrf": 20.632}, "sft-merged": {"chrf": 31.25}},
                       "per_dimension": {"open-nvfp4": {"지시 충실": 4.9}}, "pairwise": []}]},
        {"key": "context", "title": "맥락 이해", "metrics": {"open-nvfp4": {"overall_rate": 1.0}, "sft-merged": {"overall_rate": 1.0}},
         "pairwise": [], "per_dimension": {},
         "per_type": [{"type": "긴 자료 속 사실 찾기", "key": "longctx", "n": 10,
                       "metrics": {"open-nvfp4": {"n": 10, "ok": 10, "rate": 1.0}, "sft-merged": {"n": 10, "ok": 9, "rate": 0.9}}}]},
        {"key": "doc202", "title": "문서 작성", "metrics": {"open-nvfp4": {"문서 만듦": 1.0, "ask_only": 0.14},
                                                        "sft-merged": {"문서 만듦": 0.0, "ask_only": 0.82}},
         "pairwise": [], "per_dimension": {"근거": {"open-nvfp4": 2.75, "sft-merged": None}},
         "per_type": [{"arm": "open-nvfp4", "label": "open-nvfp4-1", "metrics": {"쓴 절": 7, "ask_only": 0.182}},
                      {"arm": "sft-merged", "label": "sft-merged-1", "metrics": {"쓴 절": 0, "ask_only": 0.727, "오류": "작업본이 없다"}}]},
        {"key": "speed", "title": "속도", "metrics": {"open-nvfp4": {"ttft": 0.556, "tok_per_sec": 24.316},
                                                   "sft-merged": {"ttft": 0.65, "tok_per_sec": 21.5}},
         "pairwise": [], "per_dimension": {}, "per_type": []}]
    (d / "summary.json").write_text(json.dumps({
        "run_id": run_id, "created": "2026-10-24 06:02", "arms": arms, "blocks": blocks, "notes": ["끝난 실행"],
        "labels": {"chrf": "정답 겹침(chrF)", "invented_any": "질문에 없는 숫자가 든 답 비율", "ask_only": "묻기만 하고 끝난 턴 비율",
                   "문서 만듦": "문서(작업본)를 만든 회차 비율", "overall_rate": "맥락 이해 전체 통과율", "sec": "문항당 시간(초)"},
        "percent_keys": ["invented_any", "ask_only", "문서 만듦", "overall_rate"]}, ensure_ascii=False))
    (d / "samples.json").write_text(json.dumps([{"id": "t1", "type": "규정", "input": "질문",
                                                 "outputs": {"open-nvfp4": "오픈 답", "sft-merged": "병합본 답"}}], ensure_ascii=False))
    return d


def test_finished_run_shows_real_numbers(world):
    """끝난 실행(212 꼴)은 묶음마다 실제 수치를 그린다 — 「진행 중」 없이, 재지 않은 갈래만 빠진다."""
    app, base, _ = world
    _finished_run(base)
    page = _client(app, "zzdev").get("/dev/models/compare/sft-compare").text
    assert "진행 중" not in page.split('id="mc-data"')[0]
    assert "36.60" in page and "27.89" in page                              # 지표(갈래 → 지표 꼴을 뒤집어 읽음)
    assert "4.1%" in page and "82.0%" in page and "0.0%" in page            # percent_keys
    assert "점수 0.31" in page and "대조군" in page and "뒤집힌 문항 36%" in page
    assert "첫 글자까지" in page and "24.32" in page and "문항당 시간" in page  # 속도
    assert "31.25" in page and "규정·제도 문서·첫 질문" in page                  # 문항 종류별(metrics 안)
    assert "90.0%" in page and "통과율" in page                                # 맥락 종류별 rate
    assert "회차별" in page and "sft-merged-1" in page and "작업본이 없다" in page  # 문서 작성 회차
    assert "SFT 어댑터 을(를) 돌리지 않았습니다" in page                         # 202 등은 어댑터 없음
    assert '"finished": true' in page


def test_metric_major_and_arm_major_read_the_same():
    from zzaimy.app import model_compare as mc
    a = mc._block({"metrics": {"chrf": {"o": 1.5, "s": 2.5}}}, ["o", "s"])
    b = mc._block({"metrics": {"o": {"chrf": 1.5}, "s": {"chrf": 2.5}}}, ["o", "s"])
    assert a["other"] == b["other"] and a["other"][0]["vals"] == {"o": 1.5, "s": 2.5}


def test_analysis_md_rendered_safely(world):
    """analysis.md 가 있으면 비교 화면 「분석」 절(문서 열람과 같은 렌더 — 원문 HTML 은 글자 그대로),
    품질·성능 카드에는 결론 한 줄과 「분석 보기」."""
    app, base, _ = world
    d = _finished_run(base)
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    s["verdict"] = "SFT 합친 판은 문서 작성을 못 해 운영에 넣지 않음"
    (d / "summary.json").write_text(json.dumps(s, ensure_ascii=False))
    (d / "analysis.md").write_text(
        "# 학습본 비교 분석\n\n## 무엇이 문제인가\n\n- 문서를 만들지 못하고 묻기만 함\n- 학습셋 틀이 답에 섞임\n\n"
        "## 왜 문서 작성을 못 했나\n\n학습 문답이 **한 번 묻고 답하기** 꼴이라 도구 부르기를 잃었다.\n\n"
        "| 갈래 | 문서 만듦 |\n|---|---|\n| 오픈 모델 | 100% |\n| SFT 병합본 | 0% |\n\n"
        "### 문답 검수 방향\n\n1. 플랫폼 요청 꼴로 다시 만든다\n\n<script>alert('x')</script> <img src=x onerror=alert(1)>\n",
        encoding="utf-8")
    c = _client(app, "zzdev")
    page = c.get("/dev/models/compare/sft-compare").text
    sec = page.split('id="analysis"')[1].split("</section>")[0]
    assert "<h2>분석" in sec and "학습본 비교 분석" in sec
    assert "무엇이 문제인가" in sec and "왜 문서 작성을 못 했나" in sec and "문답 검수 방향" in sec
    assert "<li" in sec and "문서를 만들지 못하고 묻기만 함" in sec
    assert "<table" in sec and "<th>갈래</th>" in sec and "<td>SFT 병합본</td>" in sec
    assert "<b>한 번 묻고 답하기</b>" in sec
    assert "<script>alert" not in page and "<img src=x" not in sec and "&lt;script&gt;" in sec
    assert page.index('id="analysis"') < page.index('data-mc-block=')        # 묶음 표보다 위
    assert 'href="#analysis">분석 보기' in page
    card = c.get("/dev/quality").text.split('id="model-compare"')[1].split("</div></div>")[0]
    assert "SFT 합친 판은 문서 작성을 못 해 운영에 넣지 않음" in card
    assert "분석 보기" not in card                                    # 분석은 자세히 화면에서(카드는 들어가는 단추 하나)


def test_no_analysis_no_section(world):
    app, base, _ = world
    _finished_run(base)
    c = _client(app, "zzdev")
    assert 'id="analysis"' not in c.get("/dev/models/compare").text
    assert "분석 보기" not in c.get("/dev/quality").text
