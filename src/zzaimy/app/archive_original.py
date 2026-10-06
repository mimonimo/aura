"""읽기 전용 rsync 연결로 DGX 원본 한 개를 임시 열람한다."""
from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from threading import BoundedSemaphore

MAX_BYTES = 100_000_000
_slots = BoundedSemaphore(2)


def fetch(row: dict) -> Path:
    """호출부에서 대장·권한을 확인한 파일만 받아 임시 경로를 반환한다."""
    rel = str(row["rel"])
    path = PurePosixPath(rel)
    size = int(row["size"])
    if path.is_absolute() or ".." in path.parts or "\\" in rel or any(ord(c) < 32 for c in rel):
        raise ValueError("허용되지 않는 원본 경로입니다.")
    if rel.startswith("_플랫폼업로드/"):
        raise ValueError("플랫폼 첨부 파일은 연결된 문서 화면에서 열어 주세요.")
    if not 0 <= size <= MAX_BYTES:
        raise ValueError("100 MB를 초과한 파일은 원본 보관 서버에서 확인해 주세요.")
    if not _slots.acquire(blocking=False):
        raise RuntimeError("다른 원본을 불러오는 중입니다. 잠시 후 다시 시도해 주세요.")
    output = None
    try:
        # 기존 VM 동기화와 같은 읽기 전용 키. 서버 명령 실행·쓰기 권한은 추가하지 않는다.
        key = os.environ.get("ZZAIMY_DGX_READ_KEY", str(Path.home() / ".ssh/id_ed25519_dgx_ro"))
        rsh = shlex.join(["ssh", "-i", key, "-p", "8022", "-o", "BatchMode=yes",
                         "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8"])
        with tempfile.TemporaryDirectory(prefix="zzaimy-original-transfer-") as directory:
            subprocess.run(
                ["rsync", "-e", rsh, "-t", "--no-links", "--max-size=100000000",
                 "--from0", "--files-from=-", "--", "aura@211.170.162.110:./", directory + "/"],
                input=rel.encode("utf-8") + b"\0", stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=45, check=True,
            )
            downloaded = Path(directory) / rel
            if (not downloaded.is_file() or downloaded.is_symlink()
                    or not downloaded.resolve().is_relative_to(Path(directory).resolve())
                    or downloaded.stat().st_size != size
                    or int(downloaded.stat().st_mtime) != int(row["mtime"])):
                raise RuntimeError("원본과 목록 정보가 다릅니다. 목록 동기화 후 다시 시도해 주세요.")
            with tempfile.NamedTemporaryFile(prefix="zzaimy-original-", delete=False) as target:
                output = Path(target.name)
            downloaded.replace(output)
            output.chmod(0o600)
        return output
    except (subprocess.SubprocessError, OSError) as exc:
        if output:
            output.unlink(missing_ok=True)
        raise RuntimeError("원본을 불러오지 못했습니다. DGX 연결 상태를 확인한 뒤 다시 시도해 주세요.") from exc
    except Exception:
        if output:
            output.unlink(missing_ok=True)
        raise
    finally:
        _slots.release()
