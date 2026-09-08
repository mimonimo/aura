"""Label Studio REST API 클라이언트 — 링크 아웃이 아닌 실제 연동 (ADR-0011).

파일 주고받기 대신 API로: 프로젝트 생성(라벨링 설정 포함) · 검수 태스크
밀어넣기 · 검수 결과 가져오기 · 진행률 조회. 로컬 Label Studio 인스턴스
전제, API 토큰으로 인증. 순수 클라이언트 — DB 의존 없음.

토큰·주소는 플랫폼 설정(labelstudio_url·labelstudio_token)에 저장하고
호출부에서 주입한다.
"""

from __future__ import annotations

import requests

from zzaimy.dataset.labelstudio import (
    LABEL_CONFIG,
    export_to_label_studio,
    import_from_label_studio,
)


class LabelStudioError(RuntimeError):
    pass


class LabelStudioClient:
    def __init__(self, base_url: str, token: str, timeout: int = 20) -> None:
        if not base_url or not token:
            raise LabelStudioError("Label Studio 주소·토큰이 필요합니다")
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {"Authorization": f"Token {token}"}

    def _req(self, method: str, path: str, **kw) -> object:
        try:
            r = requests.request(
                method, f"{self.base}{path}",
                headers=self.headers, timeout=self.timeout, **kw,
            )
        except requests.RequestException as e:
            raise LabelStudioError(f"연결 실패: {e}") from e
        if r.status_code == 401:
            raise LabelStudioError("토큰이 유효하지 않습니다")
        if not r.ok:
            raise LabelStudioError(f"{r.status_code}: {r.text[:200]}")
        return r.json() if r.content else {}

    def ping(self) -> bool:
        """토큰·주소 검증 — 현재 사용자 조회."""
        self._req("GET", "/api/current-user/whoami")
        return True

    def ensure_project(self, title: str) -> int:
        """같은 제목 프로젝트가 있으면 그 id, 없으면 우리 라벨링 설정으로 생성."""
        data = self._req("GET", "/api/projects")
        results = data.get("results", data) if isinstance(data, dict) else data
        for p in results or []:
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

    def progress(self, project_id: int) -> dict:
        """검수 진행률 — 전체·완료(annotation 있는) 태스크 수."""
        p = self._req("GET", f"/api/projects/{project_id}")
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
