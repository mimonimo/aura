"""초기 설정 점검(11-15) — 새로 설치한 서버에서 관리자가 할 연동을 한 곳에 차례로 보인다. 다 끝나면 카드를 접는다.

항목마다 지금 상태를 그 자리에서 확인한다(저장된 「했음」 표시가 아니라 실제 값): 관리자 첫 비밀번호, LLM 연결과 문서 작업 모델,
검색 서빙, 원본 폴더, 부서·사용자, 한글 웹 기안기, 백업. 필수가 아닌 것(검색 서빙·원본 폴더)은 「선택」 으로 둔다.
화면: 도구·모델 연결(/dev/tools) 맨 위 카드, 개발 현황(/dev) 한 줄 안내.
"""
from __future__ import annotations

import os
import time
import urllib.request
from pathlib import Path


def _webhwp_ok() -> bool:
    from zzaimy.app import webhwp
    try:
        with urllib.request.urlopen(webhwp.base_url() + "/webhwpctrl/version.txt", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _backup_age_h() -> float | None:
    p = Path(os.environ.get("AURA_BACKUP_DIR") or Path.home() / "aura-backups") / ".last_ok"
    try:
        return (time.time() - float(p.read_text().strip())) / 3600
    except (OSError, ValueError):
        return None


def items(st, live=None) -> list[dict]:
    """[{key, label, ok, optional, detail, href}] — live(cid)→{ok,...} 는 /dev/tools 가 쓰는 연결 확인(캐시)."""
    from zzaimy.generate import llm_connections as lc
    accounts = getattr(st, "accounts", {}) or {}
    conns = lc.list_public()
    live_ok = any((live(c["id"]) if live else {}).get("ok") for c in conns) if conns else False
    roles = {r["role"]: r for r in lc.roles_public()}
    answer = roles.get("answer") or {}
    users = [u for u, a in accounts.items() if (a or {}).get("active", True) and u not in ("zzdev", "zzaimy")]
    age = _backup_age_h()
    src = os.environ.get("ZZAIMY_ARCHIVE_SOURCE", "").strip()
    try:
        from zzaimy import hosts
        src = src or hosts.get("ZZAIMY_DGX_SSH")
    except Exception:
        pass
    dev = accounts.get("zzdev") or {}
    return [
        {"key": "pw", "label": "관리자 첫 비밀번호 바꾸기", "ok": not dev.get("must_change"), "optional": False,
         "detail": "첫 로그인 때 바꾼다", "href": "/settings"},
        {"key": "llm", "label": "LLM 서버 연결", "ok": live_ok, "optional": False,
         "detail": f"등록 {len(conns)}개" + (" · 응답함" if live_ok else " · 응답하는 서버 없음" if conns else ""), "href": "/dev/tools#llm"},
        {"key": "answer", "label": "문서 작업 모델 지정", "ok": bool(answer.get("id") or any(c.get("active") for c in conns)), "optional": False,
         "detail": answer.get("model") or "기본 서버를 따름", "href": "/dev/tools#llm"},
        {"key": "search", "label": "검색 서빙(임베딩·리랭커)", "ok": bool(os.environ.get("ZZAIMY_EMBED_URL") and os.environ.get("ZZAIMY_RERANK_URL")),
         "optional": True, "detail": "비우면 앱 서버 CPU 로(느림)", "href": "/dev/tools#server"},
        {"key": "source", "label": "원본 폴더", "ok": bool(src), "optional": True,
         "detail": "기관 문서를 1분마다 들인다", "href": "/dev/tools#server"},
        {"key": "users", "label": "부서·사용자 등록", "ok": len(users) > 0, "optional": False,
         "detail": f"사용자 {len(users)}명", "href": "/team"},
        {"key": "webhwp", "label": "한글 웹 기안기", "ok": _webhwp_ok(), "optional": False,
         "detail": "bash deploy/webhwp/install.sh", "href": "/hwp"},
        {"key": "backup", "label": "백업", "ok": age is not None and age < 30, "optional": False,
         "detail": (f"{age:.0f}시간 전" if age is not None else "아직 없음 · cron 매일 02:30 · bash deploy/backup.sh"), "href": ""},
    ]


def summary(rows: list[dict]) -> dict:
    need = [r for r in rows if not r["optional"]]
    done = sum(1 for r in need if r["ok"])
    return {"done": done, "total": len(need), "complete": done == len(need)}
