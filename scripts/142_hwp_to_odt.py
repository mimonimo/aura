#!/usr/bin/env python3
"""옛 한글(.hwp 5.0) → ODT — pyhwp 의 변환기를 RelaxNG 검증 없이 돌린다.

hwp5odt 명령은 산출 ODT 를 RelaxNG 로 검증하고 큰 실물 문서(30MB 사업계획서, 2026-09-24)에서 검증 실패로 아무것도
내놓지 않았다. 검증은 리브레오피스 호환용이라 구글 드라이브가 독스로 바꾸는 데는 필요 없다. 산출은 그림을 안에 넣는다.

실행: .venv/bin/python scripts/142_hwp_to_odt.py <입력.hwp> <출력.odt>
"""

from __future__ import annotations

import sys
from contextlib import closing
from pathlib import Path


def main() -> int:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    from hwp5.hwp5odt import ODTTransform, open_odtpkg
    from hwp5.xmlmodel import Hwp5File

    t = ODTTransform(embedbin=True)
    t.relaxng_compile = None                       # 검증 생략 — odf_validator 가 None 이 된다
    with closing(Hwp5File(str(src))) as f, open_odtpkg(str(dst)) as pkg:
        t.transform_hwp5_to_package(f, pkg)
    print(dst, dst.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
