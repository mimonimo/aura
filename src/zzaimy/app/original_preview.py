"""원본 열기 — 브라우저가 바로 여는 형식(PDF·그림·글)은 그대로, 한글·워드·엑셀·PPT 는 열람 PDF 로 바꿔 보여 준다.

변환과 DGX 원본 받기는 큰 파일에서 몇 분이 걸려 요청 하나 안에서 하면 브라우저가 끝없이 도는 것처럼 보인다.
그래서 뒤에서 작업(Jobs)으로 돌리고 기다림 화면이 상태를 물어 단계(원본 받기 → 변환 → 미리보기 만들기)를 보여 준다.
만든 결과는 캐시에 두어 두 번째부터는 바로 열린다. 같은 작업 틀을 「구글에서 열기」도 쓴다.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

INLINE_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".csv": "text/plain; charset=utf-8",
}
CACHE_LIMIT_BYTES = int(os.environ.get("ZZAIMY_PREVIEW_CACHE_MB", "2048")) * 1024 * 1024

# 기다림 화면의 단계 — (키, 이름)
STEPS_PREVIEW = [("fetch", "원본 받기"), ("convert", "변환"), ("render", "미리보기 만들기")]
STEPS_FETCH = [("fetch", "원본 받기"), ("render", "미리보기 준비")]
STEPS_GOOGLE = [("fetch", "원본 받기"), ("convert", "변환"), ("upload", "구글에 올리기")]


class ConvertFailed(RuntimeError):
    """변환기가 PDF 를 내지 못했다(변환기 없음·변환 실패). 화면 문구는 호출부가 고른다."""


def kind_of(name: str) -> str:
    """'inline'(브라우저가 바로 연다) · 'office'(PDF 로 바꿔 보여 준다) · 'download'(내려받기만)."""
    from zzaimy.app import office_pdf

    ext = Path(name or "").suffix.lower()
    if ext in INLINE_TYPES:
        return "inline"
    if ext in office_pdf.OFFICE_EXTS:
        return "office"
    return "download"


def media_type(name: str) -> str:
    return INLINE_TYPES.get(Path(name or "").suffix.lower(), "application/octet-stream")


def cache_dir(base: Path) -> Path:
    from zzaimy.app import paths

    return paths.cache_dir(base) / "preview"


def cache_path(base: Path, doc_id: int, version: str, ext: str) -> Path:
    """DGX 원본처럼 문서 폴더가 없는 문서의 미리보기 캐시 — 원본 판(경로·크기·수정 시각)이 바뀌면 이름도 바뀐다."""
    key = hashlib.sha1(version.encode("utf-8")).hexdigest()[:12]
    return cache_dir(base) / f"{int(doc_id)}-{key}{ext}"


def prune(directory: Path, limit: int = CACHE_LIMIT_BYTES, keep: Path | None = None) -> None:
    """캐시가 상한을 넘으면 오래 안 쓴 것부터 지운다(방금 만든 keep 은 남긴다)."""
    try:
        files = [p for p in directory.iterdir() if p.is_file()]
    except OSError:
        return
    total = sum(p.stat().st_size for p in files)
    for p in sorted(files, key=lambda p: p.stat().st_mtime):
        if total <= limit:
            break
        if keep is not None and p == keep:
            continue
        total -= p.stat().st_size
        p.unlink(missing_ok=True)


class Jobs:
    """문서별 뒤 작업 상태 — state(running·done·error)·step·url·msg 와 시작 시각. 같은 문서에 작업은 한 번에 하나."""

    def __init__(self) -> None:
        self._jobs: dict[int, dict] = {}
        self._lock = threading.Lock()

    def get(self, key: int) -> dict | None:
        return self._jobs.get(key)

    def start(self, key: int, work, steps: list[tuple[str, str]], fail) -> dict:
        """작업이 돌고 있으면 그것을, 아니면 새로 띄운다. work(set_step) 은 결과 주소를 돌려준다.
        fail(exc) 는 사용자에게 보일 실패 문구를 만든다."""
        with self._lock:
            job = self._jobs.get(key)
            if job and job.get("state") == "running":
                return job
            job = self._jobs[key] = {"state": "running", "step": steps[0][0], "url": "", "msg": "",
                                     "started": time.time(), "steps": steps}

        def set_step(step: str) -> None:
            if any(k == step for k, _ in steps):
                job["step"] = step

        def run() -> None:
            try:
                url = work(set_step)
                job.update(state="done", url=url or "")
            except Exception as exc:                                  # 화면에 사유를 남기고 다시 시도할 수 있게
                job.update(state="error", msg=fail(exc))
            job["ended"] = time.time()

        threading.Thread(target=run, daemon=True, name=f"zz-job-{key}").start()
        return job

    def status(self, key: int) -> dict:
        job = self._jobs.get(key)
        if not job:
            return {"state": "none", "step": "", "url": "", "msg": "", "elapsed": 0}
        end = job.get("ended") or time.time()
        return {"state": job["state"], "step": job.get("step", ""), "url": job.get("url", ""),
                "msg": job.get("msg", ""), "elapsed": int(end - job["started"])}
