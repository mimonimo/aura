"""운영 중 느린 곳 찾기 — 외부 도구(py-spy 등) 없이 정해진 시간 동안 다른 스레드의 호출 자리를 몇 ms마다 모아 센다(11-11).

여럿이 동시에 열면 느려지는 화면(프로젝트·문서함)의 원인을 운영 그대로 재려고 만들었다. 켤 때만 돌고(/dev/profile), 끄면 비용이 없다.
결과: 많이 걸린 자리(누적 — 그 자리가 스택 어딘가에 있었던 표본 수)와 지금 실행 중이던 자리(맨 끝 자리) 위쪽 몇 줄."""
from __future__ import annotations

import collections
import os
import sys
import threading
import time

_LOCK = threading.Lock()


def _where(fr) -> str:
    co = fr.f_code
    path = co.co_filename
    i = path.rfind("/zzaimy/")
    short = path[i + 1:] if i >= 0 else os.path.basename(path)
    return f"{short}:{co.co_name}:{fr.f_lineno}"


def _fn(fr) -> str:
    co = fr.f_code
    path = co.co_filename
    i = path.rfind("/zzaimy/")
    short = path[i + 1:] if i >= 0 else os.path.basename(path)
    return f"{short}:{co.co_name}"


def sample(seconds: float = 10.0, interval: float = 0.005, top: int = 40, only: str = "") -> str:
    """seconds 동안 다른 스레드를 표본으로 모아 글로 돌려준다. only 를 주면 그 글자가 스택에 든 표본만 센다(예: project_page).
    한 번에 하나만 돈다 — 겹쳐 부르면 바로 알린다."""
    if not _LOCK.acquire(blocking=False):
        return "다른 측정이 돌고 있습니다. 끝난 뒤 다시 부르세요.\n"
    try:
        me = threading.get_ident()
        names = {t.ident: t.name for t in threading.enumerate()}
        cum: collections.Counter = collections.Counter()
        leaf: collections.Counter = collections.Counter()
        busy = 0
        n = 0
        end = time.monotonic() + max(0.5, min(float(seconds), 60.0))
        while time.monotonic() < end:
            n += 1
            for tid, fr in sys._current_frames().items():
                if tid == me:
                    continue
                stack = []
                f = fr
                while f is not None:
                    stack.append(f)
                    f = f.f_back
                if only and not any(only in _fn(x) for x in stack):
                    continue
                top_name = _fn(stack[0])
                if any(w in top_name for w in ("threading.py:wait", "selectors.py:select", "queue.py:get", "base_events.py:_run_once")) \
                        or top_name.endswith((":wait", ":select", ":sleep", ":accept", ":recv_into", ":poll")):
                    continue                                   # 노는 스레드(기다림)는 세지 않는다
                busy += 1
                leaf[_where(stack[0])] += 1
                for key in {_fn(x) for x in stack}:
                    cum[key] += 1
            time.sleep(interval)
        out = [f"표본 {n}회 · 일하던 스레드 표본 {busy} · 스레드 {len(names)}개" + (f" · 거르기 {only!r}" if only else ""), "", "[누적: 스택에 있던 표본 수]"]
        out += [f"{c:6d}  {k}" for k, c in cum.most_common(top)]
        out += ["", "[맨 끝: 실제로 돌던 줄]"]
        out += [f"{c:6d}  {k}" for k, c in leaf.most_common(top // 2)]
        return "\n".join(out) + "\n"
    finally:
        _LOCK.release()


def start_watch(data_dir, every: float = 2.0) -> None:
    """서버 셸에서 재기 — data_dir/.profile_request 파일({"seconds": 10, "only": ""})을 놓으면 재고 .profile_out.txt 에 쓴 뒤 요청 파일을 지운다.
    관리자 비밀번호 없이 서버에 들어온 사람만 쓸 수 있다(화면 쪽은 /dev/profile)."""
    import json
    from pathlib import Path
    req = Path(data_dir) / ".profile_request"
    out = Path(data_dir) / ".profile_out.txt"

    def loop():
        while True:
            time.sleep(every)
            try:
                if not req.exists():
                    continue
                try:
                    opt = json.loads(req.read_text(encoding="utf-8") or "{}")
                except ValueError:
                    opt = {}
                req.unlink(missing_ok=True)
                text = sample(float(opt.get("seconds") or 10), only=str(opt.get("only") or ""))
                tmp = out.with_suffix(".tmp")
                tmp.write_text(text, encoding="utf-8")
                tmp.replace(out)
            except Exception:
                pass

    threading.Thread(target=loop, name="profile-watch", daemon=True).start()
