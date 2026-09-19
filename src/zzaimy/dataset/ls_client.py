"""Label Studio REST API 클라이언트 — 링크 아웃이 아닌 실제 연동 (ADR-0011).

파일 주고받기 대신 API로: 프로젝트 생성(라벨링 설정 포함) · 검수 태스크
밀어넣기 · 검수 결과 가져오기 · 진행률 조회. 로컬 Label Studio 인스턴스
전제, API 토큰으로 인증. 순수 클라이언트 — DB 의존 없음.

토큰·주소는 플랫폼 설정(labelstudio_url·labelstudio_token)에 저장하고
호출부에서 주입한다 — 사람이 붙여넣지 않고 서버 구축 스크립트
(scripts/68_labelstudio_token.sh)가 넣는다. 페이지용 status()는 짧은 시간
제한으로 한 번만 묻는다.
"""

from __future__ import annotations

import requests

from zzaimy.dataset.labelstudio import (
    LABEL_CONFIG,
    export_to_label_studio,
    import_from_label_studio,
)

# 상태 조회 전용 시간 제한(초) — 화면을 붙잡지 않도록 짧게
STATUS_TIMEOUT = 2.0

# 오류 종류 → 화면용 짧은 문구
_SHORT_ERROR = {
    "unreachable": "응답 없음",
    "auth": "토큰 불일치",
    "config": "설정 없음",
}


class LabelStudioError(RuntimeError):
    """kind: unreachable(연결·시간 초과) · auth(토큰 불일치) · config(설정 없음) · http(그 외)."""

    def __init__(self, message: str, kind: str = "http") -> None:
        super().__init__(message)
        self.kind = kind


class LabelStudioClient:
    def __init__(self, base_url: str, token: str, timeout: float = 20) -> None:
        if not base_url or not token:
            raise LabelStudioError("Label Studio 주소·토큰이 필요합니다", kind="config")
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {"Authorization": f"Token {token}"}

    def _req(self, method: str, path: str, timeout: float | None = None, **kw) -> object:
        try:
            r = requests.request(
                method, f"{self.base}{path}", headers=self.headers,
                timeout=self.timeout if timeout is None else timeout, **kw,
            )
        except requests.RequestException as e:
            raise LabelStudioError(f"연결 실패: {e}", kind="unreachable") from e
        if r.status_code == 401:
            raise LabelStudioError("토큰이 유효하지 않습니다", kind="auth")
        if not r.ok:
            raise LabelStudioError(f"{r.status_code}: {r.text[:200]}")
        return r.json() if r.content else {}

    def ping(self, timeout: float | None = None) -> bool:
        """토큰·주소 검증 — 현재 사용자 조회."""
        self._req("GET", "/api/current-user/whoami", timeout=timeout)
        return True

    def _projects(self, timeout: float | None = None) -> list[dict]:
        data = self._req("GET", "/api/projects", timeout=timeout)
        results = data.get("results", data) if isinstance(data, dict) else data
        return list(results or [])

    def status(self, title: str, timeout: float = STATUS_TIMEOUT) -> dict:
        """페이지용 연결 상태 — 짧은 시간 제한으로 프로젝트 목록을 한 번만 묻는다.

        반환: ok · error(짧은 문구, 정상이면 None) · project · project_id ·
        total · done · pending. 프로젝트가 아직 없으면 project_id=None
        (첫 보내기 때 만들어진다). 목록에 진행 수치가 없으면 progress()로 보충.
        """
        st = {"ok": False, "error": None, "project": title, "project_id": None,
              "total": 0, "done": 0, "pending": 0}
        try:
            projects = self._projects(timeout=timeout)
        except LabelStudioError as e:
            st["error"] = _SHORT_ERROR.get(e.kind) or f"응답 오류 ({str(e)[:60]})"
            return st
        st["ok"] = True
        for p in projects:
            if p.get("title") != title:
                continue
            st["project_id"] = int(p["id"])
            if p.get("task_number") is None or p.get("num_tasks_with_annotations") is None:
                try:
                    st.update(self.progress(st["project_id"], timeout=timeout))
                except LabelStudioError:
                    pass
            else:
                total = int(p["task_number"] or 0)
                done = int(p["num_tasks_with_annotations"] or 0)
                st.update(total=total, done=done, pending=max(0, total - done))
            break
        return st

    def ensure_project(self, title: str) -> int:
        """같은 제목 프로젝트가 있으면 그 id, 없으면 우리 라벨링 설정으로 생성."""
        for p in self._projects():
            if p.get("title") == title:
                return int(p["id"])
        created = self._req("POST", "/api/projects", json={
            "title": title,
            "label_config": LABEL_CONFIG,
            "description": "ZZAIMY 데이터 공방 검수 (자동 생성)",
        })
        return int(created["id"])

    def push_tasks(self, project_id: int, pairs: list[dict]) -> int:
        """학습 쌍을 검수 태스크로 변환해 프로젝트에 밀어넣는다. 반환: 건수."""
        tasks = export_to_label_studio(pairs)
        if not tasks:
            return 0
        # 태스크의 data(입력·출력·_pair 원쌍 포함)를 그대로 import
        self._req(
            "POST", f"/api/projects/{project_id}/import",
            json=[t["data"] for t in tasks],
        )
        return len(tasks)

    def progress(self, project_id: int, timeout: float | None = None) -> dict:
        """검수 진행률 — 전체·완료(annotation 있는) 태스크 수."""
        p = self._req("GET", f"/api/projects/{project_id}", timeout=timeout)
        total = int(p.get("task_number") or 0)
        done = int(p.get("num_tasks_with_annotations") or 0)
        return {"total": total, "done": done,
                "pending": max(0, total - done)}

    def pull_reviewed(self, project_id: int) -> list[dict]:
        """검수 완료 결과를 가져와 학습 쌍(build.py 형식)으로 되돌린다."""
        data = self._req(
            "GET", f"/api/projects/{project_id}/export?exportType=JSON",
        )
        anns = data if isinstance(data, list) else data.get("results", [])
        return import_from_label_studio(anns)
