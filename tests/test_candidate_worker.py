"""Continuous generation must drain new work without looping on held sources."""
import importlib.util
from pathlib import Path
import sys


def worker():
    path = Path(__file__).resolve().parents[1] / 'scripts/generate_grounded_candidates.py'
    spec = importlib.util.spec_from_file_location('candidate_worker', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_continuous_drains_and_stops_when_no_new_work(monkeypatch):
    module = worker()
    calls = []
    pending = iter([200, 37, 0])
    def run(args):
        calls.append(args)
        return next(pending)
    monkeypatch.setattr(sys, 'argv', ['worker', '--apply', '--continuous', '--defer-semantic-review'])
    monkeypatch.setattr(module, 'run_batch', run)
    monkeypatch.setattr(module.time, 'sleep', lambda _:None)
    module.main()
    assert len(calls) == 3
    assert all(a.apply and a.defer_semantic_review for a in calls)


def test_default_is_one_batch(monkeypatch):
    module = worker()
    calls = []
    monkeypatch.setattr(sys, 'argv', ['worker'])
    monkeypatch.setattr(module, 'run_batch', lambda args:calls.append(args) or 20)
    module.main()
    assert len(calls) == 1 and not calls[0].apply
