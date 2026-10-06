#!/usr/bin/env python3
"""폐기된 목차 요약 양식 게시 경로. 기존 호출은 외부 변경 없이 차단한다."""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # 기존 자동화 호출에 명확한 중단 사유를 출력한다. 게시 우회 옵션은 없다.
    ap.add_argument("--email", required=True)
    ap.add_argument("--common", action="store_true")
    ap.add_argument("--dir", default="")
    ap.add_argument("--folder", default="")
    ap.parse_args()
    print(
        "게시 중단: 162·172의 목차 요약은 편집용 양식이 아닙니다. "
        "원본의 표·작성 항목·구조를 보존하고 검증한 양식의 게시 경로를 사용하세요. "
        "Google Drive의 기존 문서는 변경하지 않았습니다.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
