"""기안기 — 불러오는 동안 입력 막기(11-09: 불러오기 전에 친 글이 문서가 열리며 사라졌다). 덮개는 열림(공동 편집 층 시작)·실패·새 문서에서 걷힌다."""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "src/zzaimy/app/templates/hwp_edit.html").read_text(encoding="utf-8")


def test_load_block_markup_and_release_points():
    assert 'data-load-block' in SRC and "다 열리면 입력할 수 있습니다" in SRC
    start = SRC.index("eng.start().then(function () {")
    assert "unblock();" in SRC[start:start + 200]                 # 공동 편집 층이 열리면 걷힌다(보기 전용·칸·문서 전체 모드 모두 이 앞)
    assert "say('불러오지 못함', 'bad'); unblock();" in SRC       # 못 불러오면 걷는다
    assert "if (isNew) { unblock();" in SRC                        # 새 문서는 바로
    assert "setTimeout(unblock, 120000)" in SRC                    # 열림 신호가 안 와도 남지 않게
