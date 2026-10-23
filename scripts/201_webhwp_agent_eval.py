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
절 맡기기 비교(ADR-0065 4번, --delegate off|auto → ZZAIMY_DELEGATE):
- delegated    서브에 맡겨 쓴 절 수(성공) / 맡긴 횟수
- blocks       본문을 쓴 자리 수(절 필드 + hwp_write 로 쓴 큰 칸)
- sec_chars    쓴 자리별 분량(글자) — 작업본에 넣은 문단 글 합
- mismatch     앞뒤 절 일관성 — 같은 이름(「총 사업비」 등) 뒤의 값이 절마다 다르게 적힌 곳 수
- unknown      서브가 돌려준 「확인 필요」 수 + 에이전트가 물은 값 수(asked_items)
- peak/limit   가장 큰 모델 호출의 입력+출력 / 문맥 한도, calls 호출 수(서브 호출 수 sub_calls)
- compress     서브 근거 압축 전후 토큰 합과 방식(rerank·fallback)
- shape        본문을 쓴 자리마다 표·도식 블록 수, 번호 체계, 글자 수, 작성방법이 요구한 요소(표·도식·출처)와 빠진 것 — 합계 tables·diagrams·numbered·needs_met
결과 한글 파일은 data/eval/webhwp_agent/files/<요청>-<off|auto>.hwpx(기안기로 열어 서식을 눈으로 본다).
결과는 data/eval/webhwp_agent/<시각>-<off|auto>.json·.md. 같은 요청 묶음으로 판을 바꿔 가며 견준다.

사용(VM, 모델 주소가 든 .env.local 을 읽고): set -a; . ./.env.local; set +a;
  PYTHONPATH=src .venv/bin/python scripts/201_webhwp_agent_eval.py [--only 이름] [--delegate off|auto]
모델 연결 설정은 data/platform/llm_connections.json — 다른 폴더에서 돌리면 ZZAIMY_EVAL_CONN 으로 그 파일을 짚는다(임시 폴더에 복사, 끝나면 지운다).
후보 모델 견주기(scripts/195 와 같은 방식): --candidate-conn <연결 id>(또는 장부 밖 교내 서버 --candidate-url <주소>) --candidate-model <서빙 이름> — 이 프로세스에서만 answer·review 를 후보로
(zzaimy.eval.model_trial). 결과에 모델 정보·연결 이름(주소 없이)을 남기고, --label·--out 으로 결과 파일 이름·폴더를 정한다(scripts/203 이 모은다).
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
    # AURA 공통 양식(webhwp_templates 가 만드는 「사업계획서 공통 양식」) — 작성방법 상자가 요구한 표·도식을 쓰는지 본다. 사업은 가상의 개요
    {"name": "공통양식_사업계획서", "expect": ["사업계획서"], "project": "2027 AI 융합 교육 혁신 사업",
     "q": "AURA 공통 양식 「사업계획서」로 사업계획서 초안을 써 줘. 사업명은 2027 AI 융합 교육 혁신 사업, 대상은 1학년, 예산은 5억 원, "
          "기간은 2027. 3. ~ 2028. 2.야. 추진 체계·추진 일정·기대 효과를 포함해 줘. 자료에 없는 값은 비워 두고 마지막에 물어봐.",
     "facts": ["AI 융합 교육 혁신", "1학년", "2027"]},
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
    src_conn = Path(os.environ.get("ZZAIMY_EVAL_CONN") or ROOT / "data" / "platform" / "llm_connections.json")
    if src_conn.exists():
        shutil.copy(src_conn, d / "llm_connections.json")
        os.chmod(d / "llm_connections.json", 0o600)
    app = create_app(db_path=d / "eval.db", inbox_dir=d / "inbox", processor=DocumentProcessor(), drafter=SliceDrafter(),
                     password="eval-pass-123")
    from zzaimy.eval import model_trial
    models = model_trial.snapshot()
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
    out: dict = {"name": case["name"], "sec": 0, "sid": sid, "models": models}
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
    out["file"] = str(keep / (f"{case['name']}-{os.environ.get('ZZAIMY_DELEGATE', 'auto')}"
                             + (f"-{os.environ['ZZAIMY_EVAL_LABEL']}" if os.environ.get("ZZAIMY_EVAL_LABEL") else "") + ".hwpx"))
    Path(out["file"]).write_bytes(data)
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
    tpl_nums = set()                                              # 양식·근거 문서에 원래 있던 숫자는 지어낸 것이 아니다
    for name in case.get("docs") or []:
        try:
            z = zipfile.ZipFile(EVALDOCS / name)
            tpl_nums |= _nums(re.sub(r"<[^>]+>", " ", "".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist()
                                                            if re.match(r"Contents/section\d+\.xml$", n))))
        except (OSError, zipfile.BadZipFile):
            pass
    src_ = str(webhwp.meta(db, fid).get("source") or "")                # 공통 양식에서 시작했으면 그 양식의 숫자(작성방법 글 등)도 양식에 있던 것
    if src_.startswith("양식복사:") and src_[5:].isdigit() and webhwp.get(db, int(src_[5:])):
        try:
            z = zipfile.ZipFile(webhwp.get(db, int(src_[5:]))["path"])
            tpl_nums |= _nums(re.sub(r"<[^>]+>", " ", "".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist()
                                                            if re.match(r"Contents/section\d+\.xml$", n))))
        except (OSError, zipfile.BadZipFile):
            pass
    out["invented"] = sorted(n for n in _nums(text) - given - tpl_nums if not n.startswith("20"))[:20]
    body = " ".join(x["text"] for x in secs)
    out["style"] = bool(re.search(r"(^|\s)(\d\)|[가-하]\)|\(\d\))", body)) or "1)" in text
    out["asked"] = any("필요한 값" in a or "알려 주" in a for a in out["assistant"])
    out["filled_fields"] = {x["name"]: x["text"][:80] for x in fl if x["text"]}
    # 절 맡기기 비교 지표
    from zzaimy.app import asks as _asks
    from zzaimy.app import delegate as _dg
    meta = webhwp.meta(db, fid)
    bodies = section_bodies(xml, meta.get("written") or {}, {x["name"]: x["text"] for x in secs})
    # 본문 쓰기(hwp_write·절 맡기기)로 넣은 자리 — 절 필드와 큰 칸(요약 표의 본문 칸) 모두
    out["sec_chars"] = {re.sub(r"^(절|칸|표\d+):", "", k)[:30]: len(v) for k, v in bodies.items()}
    out["blocks"] = len(bodies)
    vals = list(out["sec_chars"].values())
    out["sec_chars_mean"] = round(sum(vals) / len(vals)) if vals else 0
    out["mismatch"] = mismatches(bodies)
    log = _dg.load_log(db, sid)
    out["delegated"] = f"{sum(1 for r in log if r.get('written'))}/{len(log)}"
    out["unknown"] = sum(len(r.get("unknown") or []) for r in log)
    out["asked_items"] = len(_asks.pending(db, sid))
    out["compress"] = {"before": sum((r.get("compress") or {}).get("before", 0) for r in log),
                       "after": sum((r.get("compress") or {}).get("after", 0) for r in log),
                       "method": sorted({(r.get("compress") or {}).get("method", "") for r in log} - {""})}
    try:
        u = json.loads(db.get_setting(f"chat_ctx:{sid}", "") or "{}")
    except ValueError:
        u = {}
    out["calls"] = u.get("calls", 0)
    out["peak"] = u.get("peak", 0)
    out["limit"] = u.get("limit", 0)
    out["sub_calls"] = sum(1 for it in u.get("items") or [] if it.get("group") == "서브")
    out["delegate_log"] = log
    # 쓴 모양(작업본 meta 의 shape — 쓰기 때 코드가 남긴 기록): 절마다 표·도식 수, 번호 체계, 글자 수, 작성방법 요구 요소 반영
    shapes = meta.get("shape") or {}
    out["shape"] = {re.sub(r"^(절|칸|표\d+):", "", k)[:30]: v for k, v in shapes.items()}
    out["tables"] = sum(v.get("tables", 0) for v in shapes.values())
    out["diagrams"] = sum(v.get("diagrams", 0) for v in shapes.values())
    out["numbered"] = f"{sum(1 for v in shapes.values() if v.get('numbered'))}/{len(shapes)}"
    need_n = sum(len(v.get("need") or []) for v in shapes.values())
    out["needs_met"] = f"{need_n - sum(len(v.get('missing') or []) for v in shapes.values())}/{need_n}"
    return out


def section_bodies(xml: str, written: dict, first: dict) -> dict[str, str]:
    """절마다 작업본에 넣은 글 — 필드 안 첫 줄 + 그 절에 넣은 문단(webhwp meta written 의 문단 id)."""
    out = {}
    for name, pids in written.items():
        parts = [first.get(name, "")]
        for pid in pids or []:
            m = re.search(rf'<hp:p id="{pid}"[^>]*>', xml)
            if not m:
                continue
            depth = 0
            for t in re.finditer(r"<hp:p\b|</hp:p>", xml[m.start():]):
                depth += 1 if t.group(0) != "</hp:p>" else -1
                if depth == 0:
                    parts.append(re.sub(r"<[^>]+>", " ", xml[m.start():m.start() + t.end()]))
                    break
        out[name] = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return out


_LABELED = re.compile(r"([가-힣]{2,12})\s*[:：]?\s*(\d[\d,.]*\s*(?:천원|백만원|만원|억원|원|명|개|건|%|회|대))")


def mismatches(bodies: dict[str, str]) -> int:
    """앞뒤 절 일관성 — 같은 이름 뒤의 값(「총 사업비 50,000천원」)이 절마다 다르게 적힌 이름 수(같은 절 안의 여러 값은 세지 않는다)."""
    seen: dict[str, dict[str, set[str]]] = {}
    for sec, text in bodies.items():
        for m in _LABELED.finditer(text):
            seen.setdefault(m.group(1), {}).setdefault(sec, set()).add(re.sub(r"[\s,]", "", m.group(2)))
    n = 0
    for name, per in seen.items():
        if len(per) < 2:
            continue
        sets = list(per.values())
        if not set.intersection(*sets):                       # 절마다 겹치는 값이 하나도 없으면 다르게 적힌 것
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--delegate", choices=("off", "auto"), default=os.environ.get("ZZAIMY_DELEGATE", "auto") or "auto",
                    help="절 맡기기 — off: 항상 직접, auto: 에이전트가 판단(ZZAIMY_DELEGATE)")
    ap.add_argument("--candidate-conn", default="", help="후보 연결 id(운영 용도 지정은 바꾸지 않는다)")
    ap.add_argument("--candidate-url", default="", help="장부에 없는 교내 서버 주소(/v1 까지) — llm_connections.json 을 고치지 않고 잰다(ZZAIMY_LLM_TRY_URL)")
    ap.add_argument("--candidate-model", default="", help="후보 서빙 이름(LoRA 어댑터 이름 등)")
    ap.add_argument("--label", default="", help="결과 파일 이름에 붙일 이름(예: 운영·후보)")
    ap.add_argument("--out", default=str(ROOT / "data" / "eval" / "webhwp_agent"))
    a = ap.parse_args()
    from zzaimy.eval import model_trial
    model_trial.apply(a.candidate_conn.strip(), a.candidate_model.strip(), a.candidate_url.strip())
    os.environ["ZZAIMY_DELEGATE"] = a.delegate
    os.environ["ZZAIMY_EVAL_LABEL"] = a.label
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
        print(json.dumps({k: v for k, v in r.items() if k not in ("assistant", "filled_fields", "steps", "delegate_log")}, ensure_ascii=False), flush=True)
        res.append(r)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{a.delegate}" + (f"-{a.label}" if a.label else "")
    (out / f"{stamp}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    rows = ["| 요청 | 절 맡기기 | 쓴 절 | 본문 쓴 자리 | 맡겨 쓴 절 | 사실 반영 | 지어낸 숫자 | 절별 분량(평균 자) | 일관성 어긋남 | 확인 필요+물은 값 | 걸린 시간(초) | 가장 큰 호출/한도 | 호출(서브) | 근거 압축(토큰) | 표 | 도식 | 번호 체계 | 요구 요소 반영 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res:
        if r.get("error"):
            rows.append(f"| {r['name']} | {a.delegate} | 오류: {r['error'][:60]} |" + " |" * 15)
            continue
        c = r.get("compress") or {}
        rows.append(f"| {r['name']} | {a.delegate} | {r.get('sections')} | {r.get('blocks')} | {r.get('delegated')} | {r.get('facts')} | {len(r.get('invented') or [])} | "
                    f"{r.get('sec_chars_mean')} | {r.get('mismatch')} | {r.get('unknown', 0)}+{r.get('asked_items', 0)} | {r.get('sec')} | "
                    f"{r.get('peak')}/{r.get('limit')} | {r.get('calls')}({r.get('sub_calls')}) | "
                    f"{(str(c.get('before')) + '→' + str(c.get('after')) + ' ' + ','.join(c.get('method') or [])) if c.get('before') else '-'} | "
                    f"{r.get('tables')} | {r.get('diagrams')} | {r.get('numbered')} | {r.get('needs_met')} |")
    (out / f"{stamp}.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print("\n".join(rows), flush=True)
    print(f"결과 {out / (stamp + '.json')}", flush=True)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)                     # 임시 DB·복사한 연결 설정을 지운다
    print("EVAL_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
