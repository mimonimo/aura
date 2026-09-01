"""최소 스모크 테스트 — 패키지가 import되는지만 확인한다 (W1-W2 TASK-01)."""

import importlib

import pytest

SUBMODULES = ["ingest", "index", "extract", "retrieve", "generate", "verify", "eval"]


def test_import_zzaimy():
    import zzaimy  # noqa: F401


@pytest.mark.parametrize("name", SUBMODULES)
def test_import_submodules(name: str):
    importlib.import_module(f"zzaimy.{name}")
