"""답 꼴 점검(scripts/213)·학습셋 틀 찾기(210 FRAME)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


s210 = _load("s210f", "210_sft_heldout_compare.py")
s213 = _load("s213", "213_format_probe.py")


def test_frame_detects_training_prefix_only():
    f = s210.FRAME.search
    assert f("교육 원문: 교육혁신 사업은 …\n답변: 목표는 …")
    assert f("근거 설명: 규정 제3조에 따름\n답변: ## 계획")
    assert f("**근거 설명**: x\n**답변**: y")
    assert not f("근거: 학칙 제5조\n- 휴학은 2학기까지")          # 「근거:」 만은 정상 답
    assert not f("교육혁신 사업의 목표는 학생 역량을 높이는 것입니다.")
    assert not f("- 근거 규정을 확인함\n- 답변 기한은 7일")


def test_platform_system_prompt_is_read_from_responder():
    sysm = s213.platform_system(ROOT / "src" / "zzaimy" / "app" / "responder.py")
    assert sysm.startswith("당신은") and "답변 원칙" in sysm
    assert len(s213.PROMPTS) >= 10
