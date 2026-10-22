#!/usr/bin/env python3
"""한글 기안기 에이전트 평가 — 임시 DB 로 앱을 띄우고(운영 DB·문서함은 건드리지 않음) 실제 Writer(27B)에 채팅 요청을 보내,
에이전트가 양식을 고르고 한글 작업본의 칸·절을 채우는 품질을 잰다.

잴 것(요청마다):
- form_ok      고른 양식 이름에 기대 낱말이 있는가
- fill_ratio   칸·표 필드 가운데 채운 비율
- sections     쓴 절 수 / 절 필드 수
- facts        요청에 준 사실(숫자·이름·날짜)이 작업본에 들어갔는가(비율)
- invented     작업본 숫자 가운데 요청·양식 어디에도 없는 숫자(지어낸 숫자 후보) 수
- style        절 본문이 번호 체계(1) 가) (1))로 쓰였는가
- asked        에이전트가 모르는 값을 물었는가(「필요한 값 입력」)
결과는 data/eval/webhwp_agent/<시각>.json·.md. 같은 요청 묶음으로 판을 바꿔 가며 견준다.

사용(VM, 모델 주소가 든 .env.local 을 읽고): set -a; . ./.env.local; set +a;
  PYTHONPATH=src .venv/bin/python scripts/201_webhwp_agent_eval.py [--only 이름]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

EVALDOCS = Path.home() / "ops" / "evaldocs"          # 공개 공고·기본계획·사업 서식(VM 에만, 저장소 밖)

CASES = [
    {"name": "사업계획서", "expect": ["사업계획서"], "project": "2026 지방대학 특성화 선도대학 육성사업",
     "docs": ["(공고문) 2026년 지방대학 특성화 선도대학 육성사업 공고.hwpx",
              "(붙임) 2026년 지방대학 특성화 선도대학 육성사업 기본계획.hwpx",
              "(서식2-1) 2026년 지방대학 특성화 선도대학 육성사업 사업계획서 서식(단독형).hwpx"],
     "q": "사업계획서 서식(단독형)으로 사업계획서 초안을 작성해 줘. 공고와 기본계획의 사업 목적·추진 방향·평가 기준을 근거로 쓰고, "
          "추진 일정과 예산은 표로 정리해 줘. 우리 대학 현황처럼 자료에 없는 값은 비워 두고 마지막에 물어봐.",
     "facts": ["지방대학", "특성화"]},
    {"name": "예산변경", "expect": ["예산 변경"],
     "q": "2차년도 예산 변경 신청서 써 줘. 기자재 비목을 100,000천원에서 120,000천원으로 늘리고, 홍보비를 30,000천원에서 10,000천원으로 줄이는 내용이야. "
          "사유는 장비 단가 상승과 홍보의 온라인 전환이야. 신청 부서는 기획처, 담당자는 홍길동.",
     "facts": ["100,000", "120,000", "30,000", "10,000", "기획처", "홍길동", "단가"]},
    {"name": "기안문", "expect": ["기안문"],
     "q": "내부결재 기안문 작성해 줘. 제목은 「AI 실습실 장비 구입 계획 보고」이고, 관련은 2026학년도 AI 융합 인재 양성 사업 운영 계획, "
          "내용은 실습실 GPU 서버 2대(총 48,000천원) 구입 계획 보고야. 붙임은 견적서 1부.",
     "facts": ["AI 실습실 장비 구입 계획 보고", "48,000", "2대", "견적서"]},
    {"name": "출장보고", "expect": ["출장"],
     "q": "출장 결과 보고서 만들어 줘. 출장자 홍길동(기획처), 2026. 10. 20.(월) 서울 코엑스 AI 엑스포 참관, "
          "목적은 AI 교육 장비 동향 조사. 주요 내용은 GPU 실습 장비 3개 업체 상담, 후속 조치로 견적 요청. 경비는 교통비 59,800원.",
     "facts": ["홍길동", "코엑스", "2026. 10. 20", "59,800", "견적"]},
]


def _nums(text: str) -> set[str]:
    return {re.sub(r"[,\s]", "", n) for n in re.findall(r"\d[\d,]{1,}", text or "") if len(re.sub(r"\D", "", n)) >= 3}


def run_case(case: dict, tmp: Path) -> dict:
    from fastapi.testclient import TestClient

    from zzaimy.app import webhwp, webhwp_agent, webhwp_write as ww
    from zzaimy.app.drafter import SliceDrafter
    from zzaimy.app.main import create_app
    from zzaimy.app.pipeline import DocumentProcessor

    d = tmp / case["name"]
    d.mkdir(parents=True, exist_ok=True)
    # 모델 연결 설정은 앱 데이터 폴더의 llm_connections.json 에서 읽는다 — 임시 폴더에 운영 설정을 복사(0600, 끝나면 지운다)
    import shutil
    src_conn = ROOT / "data" / "platform" / "llm_connections.json"
    if src_conn.exists():
        shutil.copy(src_conn, d / "llm_connections.json")
        os.chmod(d / "llm_connections.json", 0o600)
    app = create_app(db_path=d / "eval.db", inbox_dir=d / "inbox", processor=DocumentProcessor(), drafter=SliceDrafter(),
                     password="eval-pass-123")
    acc = app.state.accounts["zzaimy"]
    acc["dept"] = "기획처"
    db = app.state.db
    webhwp.set_editor(db, "zzaimy", "webhwp")
    pid = db.create_project("grant", case.get("project") or "2026 AI 융합 인재 양성 사업", owner="zzaimy")
    if case.get("docs"):                                  # 프로젝트 문서(공고·기본계획·사업 서식)를 들인다 — 처리기는 운영과 같은 것
        import shutil
        proc = app.state.processor if hasattr(app.state, "processor") else DocumentProcessor()
        for name in case["docs"]:
            src = EVALDOCS / name
            dst = d / "inbox" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)
            kind = "form" if "서식" in name else None
            did = db.add_document(name, str(dst), doc_type="grant", project_id=pid, owner="zzaimy")
            proc.process(db, did, dst, defer_review=True)
            if kind:
                db.set_document_kind(did, kind)
        print(f"  프로젝트 문서 {len(case['docs'])}건 처리", flush=True)
    c = TestClient(app)
    r = c.post("/login", data={"username": "zzaimy", "pw": "eval-pass-123"}, follow_redirects=False)
    assert r.status_code == 303, "로그인 실패"
    t0 = time.time()
    r = c.post("/chat/send", data={"question": case["q"], "project_id": str(pid)}, follow_redirects=False)
    sid = None
    loc = r.headers.get("location", "")
    m = re.search(r"/chat/(\d+)", loc)
    if m:
        sid = int(m.group(1))
    if sid is None:
        rows = db.list_chat_sessions(owner="zzaimy") if hasattr(db, "list_chat_sessions") else []
        sid = max((int(x["id"]) for x in rows), default=None)
    out: dict = {"name": case["name"], "sec": 0, "sid": sid}
    msgs = [x for x in db.list_chats(sid)] if sid else []
    out["sec"] = round(time.time() - t0, 1)
    eps = list(d.rglob("doc_agent_episodes.jsonl"))              # 에이전트 걸음 기록(어떤 도구를 왜 골랐는지) — 결과에 함께 남긴다
    if eps:
        try:
            last = json.loads(eps[0].read_text(encoding="utf-8").strip().splitlines()[-1])
            out["steps"] = last.get("steps")
            out["stop"] = last.get("stop")
            out["writes"] = last.get("writes")
        except (ValueError, IndexError):
            pass
    out["assistant"] = [(x.get("content") or "")[:600] for x in msgs if x.get("role") == "assistant"]
    fid = webhwp_agent.bound(db, sid) if sid else None
    out["form"] = webhwp.get(db, fid)["name"] if fid and webhwp.get(db, fid) else ""
    out["form_ok"] = any(w in out["form"] for w in case["expect"])
    if not fid:
        out.update(fill_ratio=0.0, sections="0/0", facts=0.0, invented=[], style=False, asked=False)
        return out
    data = Path(webhwp.get(db, fid)["path"]).read_bytes()
    keep = ROOT / "data" / "eval" / "webhwp_agent" / "files"            # 결과 한글 파일 — 기안기로 열어 서식을 눈으로 본다
    keep.mkdir(parents=True, exist_ok=True)
    (keep / f"{case['name']}.hwpx").write_bytes(data)
    fl = ww.fields(data)
    cells = [x for x in fl if x["kind"] in ("칸", "표")]
    secs = [x for x in fl if x["kind"] == "절"]
    out["fill_ratio"] = round(sum(1 for x in cells if x["text"]) / len(cells), 2) if cells else None
    out["sections"] = f"{sum(1 for x in secs if x['text'])}/{len(secs)}"
    import zipfile
    import io
    xml = "".join(zipfile.ZipFile(io.BytesIO(data)).read(n).decode() for n in zipfile.ZipFile(io.BytesIO(data)).namelist()
                  if re.match(r"Contents/section\d+\.xml$", n))
    text = re.sub(r"<[^>]+>", " ", xml)
    norm = re.sub(r"\s+", "", text)
    out["facts"] = round(sum(1 for f in case["facts"] if re.sub(r"\s+", "", f) in norm) / len(case["facts"]), 2)
    given = _nums(case["q"]) | {"2026", "2027", "2025"}
    tpl_nums = set()
    out["invented"] = sorted(n for n in _nums(text) - given - tpl_nums if not n.startswith("20"))[:20]
    body = " ".join(x["text"] for x in secs)
    out["style"] = bool(re.search(r"(^|\s)(\d\)|[가-하]\)|\(\d\))", body)) or "1)" in text
    out["asked"] = any("필요한 값" in a or "알려 주" in a for a in out["assistant"])
    out["filled_fields"] = {x["name"]: x["text"][:80] for x in fl if x["text"]}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    os.environ.pop("ZZAIMY_PLATFORM_SQLITE_PATH", None)        # 임시 DB 경로라 운영 PostgreSQL 로 가지 않는다(database_backend)
    tmp = Path(tempfile.mkdtemp(prefix="webhwp-eval-"))
    res = []
    for case in CASES:
        if a.only and case["name"] != a.only:
            continue
        try:
            r = run_case(case, tmp)
        except Exception as e:
            r = {"name": case["name"], "error": f"{type(e).__name__}: {e}"[:300]}
        print(json.dumps({k: v for k, v in r.items() if k not in ("assistant", "filled_fields", "steps")}, ensure_ascii=False), flush=True)
        res.append(r)
    out = ROOT / "data" / "eval" / "webhwp_agent"
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out / f"{stamp}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"결과 {out / (stamp + '.json')}", flush=True)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)                     # 임시 DB·복사한 연결 설정을 지운다
    print("EVAL_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
