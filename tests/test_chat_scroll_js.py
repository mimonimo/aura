"""채팅 화면 스크립트의 단위 시험(node) — 대화 칸 고정(chat-scroll.js)과 JS 문법을 pytest 에서 함께 돌린다."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_chat_scroll_unit():
    r = subprocess.run([NODE, "--test", str(ROOT / "tests/chat_scroll.test.cjs")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.skipif(NODE is None, reason="node 없음")
@pytest.mark.parametrize("name", ["chat-scroll.js", "chat-workspace.js", "chat-documents.js"])
def test_chat_scripts_parse(name):
    r = subprocess.run([NODE, "--check", str(ROOT / "src/zzaimy/app/static" / name)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
