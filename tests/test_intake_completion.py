"""Intake reporting must follow stored results, not just subprocess exit codes."""
import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "intake_completion", Path(__file__).parents[1] / "scripts/156_intake_files.py"
)
intake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(intake)


@pytest.mark.parametrize("code,status,expected", [
    (0, "reviewed", True),
    (0, "failed", False),  # duplicate-content rejection can return normally
    (0, "processing", False),
    (0, "received", False),
    (0, None, False),
    (1, "reviewed", False),
    (None, "reviewed", False),
])
def test_completion_requires_accepted_document(code, status, expected):
    doc = {"status": status} if status else {}
    assert intake._completed_ok(code, doc) is expected


def test_timeout_is_not_success_even_if_worker_finishes_during_termination():
    assert not intake._completed_ok(0, {"status": "reviewed"}, timed_out=True)
