"""배포 후 실동작 점검(운영 VM, 실제 DB) — 화면·검색·마스킹·연동을 한 번에 돈다.

'되겠지'가 아니라 실제로 돌려서 확인한다. 앱 프로세스와 별개로 같은 코드를 인프로세스
(TestClient, 인증 없음)로 띄워 실제 platform.db 를 읽는다 — GET 만 치므로 데이터는
바뀌지 않는다(PII 자가 점검·잔여 스캔 결과만 settings 에 저장).

  1) 모든 /dev·주요 화면 GET → 200 이 아니면 FAIL (템플릿 오류·경로 깨짐 즉시 발견)
  2) 규정 검색 실측 — 대표 질의 6개: 빈 조각·중복 조각이 상위에 오르면 FAIL, 지연 시간 기록
  3) PII 마스커 자가 점검(알려진 정답) + 색인된 본문 잔여 스캔
  4) Label Studio whoami(토큰 설정 시)

실행: env PYTHONPATH=src HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python scripts/78_post_deploy_smoke.py
종료 코드 0 = 전부 통과. 결과 요약은 마지막 줄 SMOKE_RESULT.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

DB = Path("data/platform/platform.db")
ROUTES = [
    "/", "/criteria", "/chat", "/graph", "/graph.json", "/settings",
    "/dev", "/dev/db", "/dev/db?table=documents", "/dev/db?table=regulation_chunks&q=휴학",
    "/dev/data", "/dev/train", "/dev/corpus", "/dev/corpus?q=장학금", "/dev/egress",
    "/dev/hwp", "/dev/pii", "/dev/quality", "/dev/docs", "/dev/history", "/dev/nas",
    "/dev/doc/quality-system.md", "/dev/doc/HANDOFF.md",
    "/dev/paper/논문-원재료.md", "/dev/weekly.md",
]
QUERIES = ["장학금 지급 기준과 신청 자격", "산학협력단 사무분장", "휴학과 복학 절차",
           "기부장학금 지급 대상", "계약직원 임용 절차", "개인정보 제공 동의"]


def main() -> int:
    from fastapi.testclient import TestClient

    from zzaimy.app.drafter import SliceDrafter
    from zzaimy.app.main import create_app
    from zzaimy.app.pipeline import DocumentProcessor

    fails: list[str] = []
    app = create_app(
        db_path=DB, inbox_dir=DB.parent / "inbox",
        processor=DocumentProcessor(), drafter=SliceDrafter(), responder=None, password=None,
    )
    c = TestClient(app)
    db = app.state.db

    # 1) 화면
    print("== 1) 화면 GET ==")
    first_doc = (db.list_documents() or [{}])[0].get("id")
    routes = ROUTES + ([f"/doc/{first_doc}"] if first_doc else [])
    for r in routes:
        t = time.time()
        try:
            resp = c.get(r)
            code = resp.status_code
        except Exception as e:  # noqa: BLE001
            code = f"EXC {type(e).__name__}: {e}"[:80]
        ok = code == 200
        print(f"  {'OK ' if ok else 'BAD'} {code} {r} ({time.time() - t:.1f}s)")
        if not ok:
            fails.append(f"GET {r} -> {code}")

    # 2) 검색
    print("== 2) 규정 검색 실측 ==")
    from zzaimy.app.regulations import find_relevant
    find_relevant(db, "예열", top_k=1)
    for q in QUERIES:
        t = time.time()
        hits = find_relevant(db, q, top_k=3)
        dt = time.time() - t
        bodies = [re.sub(r"\s+", " ", h["content"]).strip() for h in hits]
        dup = len(bodies) - len(set(bodies))
        short = sum(1 for b in bodies if len(b) < 30)
        flag = "OK " if (hits and not dup and not short) else "BAD"
        print(f"  {flag} {q} — {len(hits)}건 dup={dup} short={short} {dt:.1f}s")
        for h in hits:
            print(f"      [{h['reg_title'][:18]}] {(h.get('heading') or '')[:20]} | {len(h['content'])}자")
        if flag == "BAD":
            fails.append(f"검색 '{q}': hits={len(hits)} dup={dup} short={short}")

    # 3) PII
    print("== 3) PII 자가 점검·잔여 스캔 ==")
    try:
        from zzaimy.app import pii_audit
        st = pii_audit.run_selftest(db)          # settings에 저장까지 한다
        print(f"  자가 점검: 통과 {st.get('passed')}/{st.get('total')}"
              f"{' — ' + st['error'] if st.get('error') else ''} ({st.get('elapsed_ms')}ms)")
        if not st.get("ok"):
            fails.append(f"PII 자가 점검 {st.get('passed')}/{st.get('total')} {st.get('error', '')}"[:120])
        sc = pii_audit.run_scan(db)
        print(f"  잔여 스캔: 검사 {sc.get('scanned')} · 잔여 {sc.get('hits')}건 · 제외 {sc.get('excluded_docs')}건"
              f"{' — ' + sc['error'] if sc.get('error') else ''}")
        if sc.get("hits") or sc.get("error"):
            fails.append(f"PII 잔여 {sc.get('hits')}건 {sc.get('error', '')}"[:120])
    except Exception as e:  # noqa: BLE001
        print(f"  PII 점검 실행 실패: {type(e).__name__}: {e}")
        fails.append(f"PII 점검 실행 실패 {type(e).__name__}")

    # 4) Label Studio
    print("== 4) Label Studio ==")
    url, tok = db.get_setting("labelstudio_url"), db.get_setting("labelstudio_token")
    if url and tok:
        try:
            from zzaimy.dataset.ls_client import LabelStudioClient
            cli = LabelStudioClient(url, tok, timeout=5)
            cli.ping()
            print(f"  OK  whoami ({url})")
        except Exception as e:  # noqa: BLE001
            print(f"  BAD {type(e).__name__}: {e}")
            fails.append(f"Label Studio {type(e).__name__}")
    else:
        print("  미설정 (scripts/68_labelstudio_token.sh)")

    print("SMOKE_RESULT", "PASS" if not fails else "FAIL: " + " | ".join(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
