"""온톨로지 점검(174) — 막연한 사업 이름 판별."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("oa174", ROOT / "scripts" / "174_ontology_audit.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_vague_names():
    assert m.vague("재정지원 사업") and m.vague("지자체 연계 사업") and m.vague("25재정지원사업")
    assert not m.vague("3단계 산학연협력 선도전문대학 육성사업") and not m.vague("HiVE사업") and not m.vague("직업교육혁신지구 지원사업")
