#!/usr/bin/env python3
"""한글 기안기 시나리오 평가 — 두루뭉술한 시작부터 양식 고르기·만들기, 절 쓰기, 고쳐 쓰기, 디자인 바꾸기까지 여러 턴 대화로 잰다.

scripts/201 과 같은 방식: 임시 DB 로 앱을 띄우고(운영 DB·문서함은 건드리지 않음) 실제 Writer(27B)에 채팅 요청을 차례로 보낸다.
판(variant) 둘:
- forms   문서함에 공통 양식(webhwp_templates)과 사업이 준 서식(프로젝트 문서)이 있는 판 — 기존 양식을 고르는지
- noforms 문서함·프로젝트에 양식이 없는 판(공통 양식 만들기를 끈다) — 양식을 설계해 만드는지(hwp_form_create)
두 판 모두 근거 문서(공고·기본계획)는 프로젝트에 넣어 둔다.

턴(TURNS): 두루뭉술한 시작 → 대상·예산 → 기간·양식 → 절 쓰기 → 계속 → 고쳐 쓰기 둘 → 디자인 넷.
지표(코드가 세는 것):
- form          고른·만든 양식(이름·출처·까닭 — 그 걸음의 생각), 만든 양식이면 절 수와 구조 점수(STRUCTURE 기준표)
- goal          작업 메모 「지금 목표」 가 대상·예산·기간을 담았나(메모 전체에 담았는지도)
- sections      쓴 절 / 절 필드, facts 담당자가 준 값(대상·예산·기간) 반영, evidence 근거 낱말 반영, invented 지어낸 숫자 후보
- tables·diagrams·needs_met   표·도식 수, 작성방법 요구 요소 반영(작업본 meta shape)
- rewrite       고쳐 쓰기 전후 — 바뀐 자리, 글자 수·숫자 수·표 수 전후, 일정 표 생김
- design        디자인 요청 반영 — 실제 XML 값: 방향(landscape), 좌우 여백(mm), 표 머리줄 칸 음영(#DFEAF5 비율), 쪽 번호(pageNum)
- broken        서식 깨짐 — 자가 점검(hwpx_fill.check: ZIP·웰폼드·참조 id·표 격자) 문제 수, 필드 열기·닫기 짝 어긋남
- 시간·호출     턴마다 걸린 시간, 가장 큰 호출/한도(chat_ctx), 호출 수
글 품질(구체성·논리·문체·근거)은 review 연결 27B 를 채점자로 쓴다(JUDGE_SYSTEM·RUBRIC 을 결과에 함께 남긴다). 쓰는 모델과 채점 모델이 같으므로
점수는 같은 모델의 자기 평가 한계가 있다 — 판끼리·회차끼리 견주는 데만 쓴다.

사용(VM, 모델 주소가 든 .env.local 을 읽고): set -a; . ./.env.local; set +a;
  PYTHONPATH=src python scripts/202_webhwp_scenario_eval.py [--variant forms|noforms|both] [--label 1차] [--out 폴더]
모델 연결 설정은 ZZAIMY_EVAL_CONN(기본 data/platform/llm_connections.json) — 임시 폴더에 복사해 쓰고 끝나면 지운다.
결과: <out>/<시각>-<판>-<label>.json·.md, 한글 파일 <out>/<판>-<label>-글.hwpx(고쳐 쓰기까지)·<판>-<label>.hwpx(디자인까지).

양식 만들기 묶음(--forms): 기존 양식을 숨긴 판(noforms)에서 목적 여덟 가지(FORM_CASES)마다 한 턴 요청을 보내 에이전트가 양식을 만들게 하고 채점한다 —
기준 절 포함률(학교 서식에서 흔히 쓰는 절 이름 갈래, 정답 채우기가 아니라 포함률), 작성방법 안내(덮은 비율·평균 길이·근거 언급률), 분량 안내, 요약 표 칸,
번호 체계, 빌드 성공·서식 깨짐. 에이전트가 만들지 않으면 같은 설계 도구를 직접 불러 설계 품질은 따로 잰다(「직접」 표시).
후보 모델 견주기(scripts/195 와 같은 방식): --candidate-conn·--candidate-model(zzaimy.eval.model_trial) — 결과에 모델 정보·연결 이름(주소 없이).
되풀이·판 견주기(ADR-0068): --repeat N 으로 같은 요청을 N 번(회차 <label>-1 …), --old 는 새 기능 설정(쓰기 품질·고쳐 쓰기·동시 맡기기·메모 목표 줄)을 끈 이전 판.
끝에 판·판본마다 평균(최소~최대) 표 — 고쳐 쓰기 반영(구체: 바뀐 자리 글자 1.15배 넘게, 표: 바뀐 자리에 표 생김), 절 채움, 지어낸 숫자(수치 대조기로 같은 값 인정 뒤·
표면형), 목표·목표 유지(기간·양식 턴 뒤 메모 목표 갈래에 대상·예산·기간이 다 든 턴 비율), 채점 4항목, 시간. --rescore <json…> 는 저장된 결과를 같은 잣대로 다시 센다.
채점자(review 27B)는 후보 설정을 빼고 운영 연결로 고정한다(판끼리 같은 채점자).
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

EVALDOCS = Path(os.environ.get("ZZAIMY_EVALDOCS") or Path.home() / "ops" / "evaldocs")   # 공개 공고·기본계획·사업 서식(저장소 밖)
EVIDENCE_DOCS = ["(공고문) 2026년 지방대학 특성화 선도대학 육성사업 공고.hwpx",
                 "(붙임) 2026년 지방대학 특성화 선도대학 육성사업 기본계획.hwpx"]
FORM_DOC = "(서식2-1) 2026년 지방대학 특성화 선도대학 육성사업 사업계획서 서식(단독형).hwpx"
PROJECT = "2027 AI 교육 사업 준비"

TURNS = [
    ("시작", "내년 AI 교육 쪽 사업 하나 준비해야 하는데"),
    ("대상·예산", "대상은 우리 대학 1학년 전체 학생이고, 예산은 5억 원 정도로 생각하고 있어."),
    ("기간·양식", "기간은 2027. 3. ~ 2028. 2.야. 이 사업 계획서를 쓸 한글 문서 양식부터 잡아 줘."),
    ("절 쓰기", "프로젝트에 넣어 둔 공고와 기본계획을 근거로 빈 절을 채워 줘. 자료에 없는 값은 비워 두고 마지막에 물어봐."),
    ("계속", "계속 써 줘."),
    ("고쳐 쓰기·구체", "2번 절을 더 구체적으로 다시 써 줘."),
    ("고쳐 쓰기·표", "추진 일정은 표로 정리해 줘."),
    ("디자인·가로", "문서를 가로 방향으로 바꿔 줘."),
    ("디자인·여백", "여백을 좁게 해 줘."),
    ("디자인·머리줄", "표 머리줄 색을 연파랑으로 바꿔 줘."),
    ("디자인·쪽 번호", "쪽 번호를 아래 가운데에 넣어 줘."),
]
GIVEN = {"대상": r"1\s*학년", "예산": r"5\s*억|500\s*(?:백만|,000\s*천)", "기간": r"2027.{0,24}(?:2028|2\s*월)"}
EVIDENCE_WORDS = ["지방대학", "특성화", "선도대학", "지역"]         # 근거 문서(공고·기본계획)에서 온 낱말
HEAD_FILL = "#DFEAF5"                                               # 「연파랑」(webhwp_style._color)
NARROW_MM = 20                                                      # 좁은 여백 — 좌우 여백이 이 아래면 좁게 바뀐 것(보통 30mm)

# 만든 양식의 구조 기준표 — 사업계획서로서 갖출 꼴(코드가 센다)
STRUCTURE = [
    ("절 5개 이상", lambda d, sh: sh["slots"] >= 5),
    ("번호 체계(1. → 가. → 1))", lambda d, sh: all(re.match(r"^(\d+\.|[가-하]\.|\d+\))\s", s["heading"]) for s in d["sections"])),
    ("쓸 자리마다 작성방법", lambda d, sh: all(s.get("guide") for s in d["sections"] if not s["parent"])),
    ("요약 표", lambda d, sh: sh["summary"] >= 3),
    ("표 자리 1개 이상", lambda d, sh: sh["tables"] >= 1),
    ("도식 자리 1개 이상", lambda d, sh: sh["diagrams"] >= 1),
    ("분량 안내 절 절반 이상", lambda d, sh: sh["lengths"] * 2 >= max(1, sh["slots"])),
    ("배경·목표·추진·일정·예산·성과 갈래", lambda d, sh: sum(bool(re.search(p, " ".join(s["title"] for s in d["sections"])))
                                                       for p in (r"배경|필요성", r"목표", r"추진|운영|내용", r"일정", r"예산|사업비", r"성과|효과")) >= 5),
]

RUBRIC = {
    "구체성": "1=추상적 구호뿐 2=대상·활동 일부 3=대상·활동·방법이 있음 4=대상·활동·방법·시기·담당이 있음 5=4에 측정 가능한 목표·근거 수치까지",
    "논리": "1=항목이 서로 무관 2=나열 3=목적→내용 흐름 4=목적→내용→효과가 이어짐 5=4에 근거와 결론이 맞물림",
    "문체": "1=서술형·구어 2=섞임 3=개조식이나 번호 체계 흔들림 4=개조식·번호 체계 일관 5=4에 문장 끝 「~함」「~임」 일관·군더더기 없음",
    "근거": "1=근거 없는 주장·수치 2=근거 흐릿 3=일부 근거(공고·기본계획) 4=대부분 근거에서 5=모든 사실·수치가 근거나 담당자가 준 값",
}
JUDGE_SYSTEM = """당신은 대학 행정 문서(사업계획서)의 절 하나를 기준표로 채점한다. 기준표 밖의 생각은 넣지 않는다.
[기준표]
{rubric}
[담당자가 준 값] 대상 1학년 전체, 예산 5억 원, 기간 2027. 3. ~ 2028. 2.
답은 JSON 하나만: {{"구체성": 1~5, "논리": 1~5, "문체": 1~5, "근거": 1~5, "까닭": "한 문장"}}"""


def _load201():
    spec = importlib.util.spec_from_file_location("eval201", ROOT / "scripts" / "201_webhwp_agent_eval.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _xml(data: bytes, pat: str = r"Contents/section\d+\.xml$") -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    return "".join(z.read(n).decode("utf-8", "ignore") for n in sorted(z.namelist()) if re.match(pat, n))


def _text(xml: str) -> str:
    return re.sub(r"<[^>]+>", " ", xml)


def _nums(text: str) -> set[str]:
    return {re.sub(r"[,\s]", "", n) for n in re.findall(r"\d[\d,]{1,}", text or "") if len(re.sub(r"\D", "", n)) >= 3}


def invented_numbers(text: str, evidence: list[str]) -> list[str]:
    """지어낸 숫자 — 문서 글의 숫자 가운데 근거(담당자 말·근거 문서·빈 양식 글)에 같은 값이 없는 것. 판정은 앱 관문과 같은 수치 대조기
    (verify.numbers — 단위 바꿈 5억 = 500,000,000원 = 500(백만원) = 「(단위: 백만원)」 표의 500 은 같은 값). 세 자리 이상만, 연도 꼴은 뺀다."""
    from zzaimy.verify.numbers import mask_unsupported
    _, bad = mask_unsupported(text or "", [e for e in evidence if e])
    out = set()
    for b in bad:
        d = re.sub(r"[^\d.]", "", b)
        if len(re.sub(r"\D", "", d)) >= 3 and not re.fullmatch(r"(?:19|20)\d{2}", d):
            out.add(d)
    return sorted(out)[:20]


# ── 디자인·깨짐 검사(실제 XML 값) ──
def design_values(data: bytes) -> dict:
    """방향·좌우 여백(mm)·표 머리줄 음영·쪽 번호 — 작업본 XML 에서 읽는다."""
    from zzaimy.app import webhwp_style as ws
    sec = _xml(data)
    hdr = _xml(data, r"Contents/header\.xml$")
    pp = re.search(r"<hp:pagePr\b.*?</hp:pagePr>", sec, re.S)
    out: dict = {"landscape": bool(pp and 'landscape="NARROWLY"' in pp.group(0))}
    mg = re.search(r"<hp:margin\b[^>]*/>", pp.group(0)) if pp else None
    if mg:
        vals = dict(re.findall(r'\b(left|right|top|bottom)="(\d+)"', mg.group(0)))
        out["margin_mm"] = {k: round(int(v) / 283.465, 1) for k, v in vals.items()}
    fills = {}
    for m in re.finditer(r'<hh:borderFill\b[^>]*\bid="(\d+)".*?</hh:borderFill>', hdr, re.S):
        c = re.search(r'faceColor="(#[0-9A-Fa-f]{6})"', m.group(0))
        fills[m.group(1)] = c.group(1).upper() if c else "none"
    heads = with_fill = 0
    for t in ws._TBL.finditer(sec):
        cells = ws._cells(t.group(0))
        roles = ws.roles(t.group(0))
        hc = [c for c in cells if roles.get((c["r"], c["c"])) == "head"]
        if not hc:
            continue
        heads += 1
        refs = [re.search(r'borderFillIDRef="(\d+)"', c["xml"]).group(1) for c in hc]
        if refs and all(fills.get(r) == HEAD_FILL for r in refs):
            with_fill += 1
    out["head_tables"], out["head_fill_tables"] = heads, with_fill
    pn = re.search(r"<hp:pageNum\b[^>]*/>", sec)
    out["page_number"] = re.search(r'pos="(\w+)"', pn.group(0)).group(1) if pn else ""
    return out


def design_score(v: dict, base: dict | None = None) -> dict:
    """디자인 요청 반영 — base(디자인 요청 전 값)가 있으면 그보다 바뀌었는지로 본다(양식이 처음부터 그 값이었던 것은 반영으로 치지 않는다)."""
    mm = v.get("margin_mm") or {}
    bm = (base or {}).get("margin_mm") or {}
    lr = max(mm.get("left", 99), mm.get("right", 99)) if mm else 99
    blr = max(bm.get("left", 99), bm.get("right", 99)) if bm else 99
    fill = (v.get("head_fill_tables", 0) * 2 >= v.get("head_tables", 0)) and bool(v.get("head_tables"))
    return {"가로": bool(v.get("landscape")) and not (base or {}).get("landscape"),
            "여백 좁게": bool(mm) and (lr < blr if bm else lr <= NARROW_MM),
            "머리줄 연파랑": fill and (v.get("head_fill_tables", 0) > (base or {}).get("head_fill_tables", 0) if base else True),
            "쪽 번호": v.get("page_number") == "BOTTOM_CENTER"}


def broken(data: bytes) -> dict:
    """서식 깨짐 — 자가 점검 문제와 필드 짝."""
    from zzaimy.ingest import hwpx_fill as hf
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.hwpx"
        p.write_bytes(data)
        probs = hf.check(p)
    sec = _xml(data)
    begins = re.findall(r'<hp:fieldBegin\b[^>]*\bid="(\d+)"', sec)
    ends = re.findall(r'<hp:fieldEnd\b[^>]*\bbeginIDRef="(\d+)"', sec)
    return {"check": probs[:6], "check_n": len(probs), "field_unpaired": len(set(begins) ^ set(ends))}


def structure_score(design: dict) -> dict:
    from zzaimy.app import webhwp_form_create as fc
    sh = fc.shape(design)
    got = {name: bool(fn(design, sh)) for name, fn in STRUCTURE}
    return {"shape": sh, "checks": got, "score": f"{sum(got.values())}/{len(got)}"}


def goal_score(memo_text: str) -> dict:
    """목표 갈래(최종 결과물 + 지금 목표)가 담당자가 준 대상·예산·기간을 담았나. 최종 결과물 줄이 없는 판(이전 판)은 지금 목표만."""
    from zzaimy.app import chat_memory
    parts = chat_memory.parts(memo_text or "")
    goal = "\n".join(x for x in (parts.get("최종 결과물", ""), parts.get("지금 목표", "")) if x)
    return {"goal": {k: bool(re.search(p, goal)) for k, p in GIVEN.items()},
            "memo": {k: bool(re.search(p, memo_text or "")) for k, p in GIVEN.items()}, "goal_text": goal[:400],
            "final": parts.get("최종 결과물", "")[:200]}


def goal_kept(turns: list[dict]) -> str:
    """목표 유지 — 대상·예산·기간이 다 나온 턴(기간·양식) 뒤 턴들 가운데 메모 목표 갈래에 셋이 다 든 턴 수."""
    names = [t["turn"] for t in turns]
    start = names.index("기간·양식") if "기간·양식" in names else 0
    after = [t for t in turns[start:] if t.get("memo")]
    ok = sum(1 for t in after if all(goal_score(t["memo"])["goal"].values()))
    return f"{ok}/{len(after)}"


def body_stats(text: str) -> dict:
    from zzaimy.app import webhwp_agent as wa
    sh = wa.body_shape(text)
    return {"chars": sh["chars"], "nums": len(re.findall(r"\d[\d,.]*", text or "")), "tables": sh["tables"], "diagrams": sh["diagrams"]}


# ── 채점자(review 연결 27B) ──
def judge(client, section: str, text: str) -> dict:
    rub = "\n".join(f"- {k}: {v}" for k, v in RUBRIC.items())
    msgs = [{"role": "system", "content": JUDGE_SYSTEM.format(rubric=rub)},
            {"role": "user", "content": f"[절] {section}\n[본문]\n{text[:3500]}"}]
    try:
        r = client.client.chat.completions.create(model=client.model, messages=msgs, max_tokens=300, temperature=0)
        raw = r.choices[0].message.content or ""
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:100]}"}
    m = re.search(r"\{.*\}", re.sub(r"<think>.*?</think>", "", raw, flags=re.S), re.S)
    try:
        v = json.loads(m.group(0)) if m else {}
    except ValueError:
        v = {}
    return {k: v.get(k) for k in (*RUBRIC, "까닭")} if v else {"error": "형식", "raw": raw[:200]}


def _avg(rows: list[dict]) -> dict:
    out = {}
    for k in RUBRIC:
        xs = [float(r[k]) for r in rows if isinstance(r.get(k), (int, float)) or str(r.get(k, "")).isdigit()]
        out[k] = round(sum(xs) / len(xs), 2) if xs else None
    return out


# ── 시나리오 ──
def run_variant(variant: str, label: str, tmp: Path, out_dir: Path, judge_on: bool) -> dict:
    from fastapi.testclient import TestClient

    from zzaimy.app import chat_memory, webhwp, webhwp_agent, webhwp_form_create, webhwp_templates
    from zzaimy.app import webhwp_write as ww
    from zzaimy.app.drafter import SliceDrafter
    from zzaimy.app.main import create_app
    from zzaimy.app.pipeline import DocumentProcessor

    e201 = _load201()
    d = tmp / variant
    d.mkdir(parents=True, exist_ok=True)
    src_conn = Path(os.environ.get("ZZAIMY_EVAL_CONN") or ROOT / "data" / "platform" / "llm_connections.json")
    if src_conn.exists():
        shutil.copy(src_conn, d / "llm_connections.json")
        os.chmod(d / "llm_connections.json", 0o600)
    saved_ensure = webhwp_templates.ensure_all
    if variant == "noforms":                               # 빈 문서함 — 공통 양식을 만들지 않는다(평가용 설정, 앱 코드는 그대로)
        webhwp_templates.ensure_all = lambda *a, **k: []
    try:
        app = create_app(db_path=d / "eval.db", inbox_dir=d / "inbox", processor=DocumentProcessor(), drafter=SliceDrafter(),
                         password="eval-pass-123")
        app.state.accounts["zzaimy"]["dept"] = "기획처"
        db = app.state.db
        from zzaimy.eval import model_trial
        models = model_trial.snapshot()
        webhwp.set_editor(db, "zzaimy", "webhwp")
        pid = db.create_project("grant", PROJECT, owner="zzaimy")
        proc = DocumentProcessor()
        docs = EVIDENCE_DOCS + ([FORM_DOC] if variant == "forms" else [])
        for name in docs:
            dst = d / "inbox" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(EVALDOCS / name, dst)
            did = db.add_document(name, str(dst), doc_type="grant", project_id=pid, owner="zzaimy")
            proc.process(db, did, dst, defer_review=True)
            if "서식" in name:
                db.set_document_kind(did, "form")
        print(f"[{variant}] 프로젝트 문서 {len(docs)}건 처리", flush=True)
        c = TestClient(app)
        r = c.post("/login", data={"username": "zzaimy", "pw": "eval-pass-123"}, follow_redirects=False)
        assert r.status_code == 303, "로그인 실패"
        sid = None
        turns, snaps = [], {}
        t_all = time.time()
        for k, (name, q) in enumerate(TURNS):
            t0 = time.time()
            data_ = {"question": q}
            if sid is None:
                data_["project_id"] = str(pid)
            else:
                data_["session_id"] = str(sid)
            r = c.post("/chat/send", data=data_, follow_redirects=False)
            if sid is None:
                m = re.search(r"/chat/(\d+)", r.headers.get("location", ""))
                sid = int(m.group(1)) if m else None
            sec = round(time.time() - t0, 1)
            msgs = db.list_chats(sid, limit=400)
            answered = bool(msgs) and msgs[-1]["role"] == "assistant"
            if not answered:                                # 답이 없으면 다음 턴이 막힌다 — 표시를 남기고 잇는다
                db.add_chat(sid, "assistant", "(평가: 이 턴에 답이 없었다)")
            ep = {}
            eps = sorted(d.rglob("doc_agent_episodes.jsonl"))
            if eps:
                try:
                    ep = json.loads(eps[0].read_text(encoding="utf-8").strip().splitlines()[-1])
                except (ValueError, IndexError):
                    ep = {}
            try:
                u = json.loads(db.get_setting(f"chat_ctx:{sid}", "") or "{}")
            except ValueError:
                u = {}
            # 작업 메모가 이번 턴까지 따라오기를 기다린다(배경 갱신, 사람이 다음 말을 하기까지의 틈)
            last_id = max(int(m_["id"]) for m_ in db.list_chats(sid, limit=400))
            for _ in range(90):
                if int(chat_memory.load(db, sid).get("upto") or 0) >= last_id:
                    break
                time.sleep(2)
            fid = webhwp_agent.bound(db, sid)
            f = webhwp.get(db, fid) if fid else None
            row = {"turn": name, "q": q, "sec": sec, "answered": answered,
                   "answer": next((str(m_.get("content") or "")[:2000] for m_ in reversed(db.list_chats(sid, limit=400)) if m_["role"] == "assistant"), ""),
                   "actions": [s_.get("action") for s_ in ep.get("steps") or [] if s_.get("action")] if ep.get("request", "")[:40] == q[:40] else [],
                   "stop": ep.get("stop") if ep.get("request", "")[:40] == q[:40] else None,
                   "calls": u.get("calls", 0), "peak": u.get("peak", 0), "limit": u.get("limit", 0),
                   "memo": chat_memory.load(db, sid).get("text", "")[:1200], "file": f["name"] if f else ""}
            if ep.get("request", "")[:40] == q[:40]:
                row["thoughts"] = [(s_.get("action"), str(s_.get("thought") or "")[:200]) for s_ in ep.get("steps") or [] if s_.get("action")]
                row["observations"] = [(s_.get("action"), str(s_.get("observation") or "")[:200]) for s_ in ep.get("steps") or [] if s_.get("action")][:40]
            if f:
                data = Path(f["path"]).read_bytes()
                xml = _xml(data)
                secs = [x for x in ww.fields(data) if x["kind"] == "절"]
                bodies = e201.section_bodies(xml, webhwp.meta(db, fid).get("written") or {}, {x["name"]: x["text"] for x in secs})
                snaps[name] = {"bodies": bodies, "data": data, "shape": dict(webhwp.meta(db, fid).get("shape") or {})}
                row["written"] = sum(1 for x in secs if x["text"])
                row["design"] = design_values(data)
            turns.append(row)
            print(json.dumps({k_: v_ for k_, v_ in row.items() if k_ not in ("memo", "thoughts", "observations", "answer")}, ensure_ascii=False), flush=True)
        total = round(time.time() - t_all, 1)
        res: dict = {"variant": variant, "label": label, "turns": turns, "total_sec": total, "sid": sid, "models": models}
        fid = webhwp_agent.bound(db, sid)
        if not fid:
            res["error"] = "작업본이 없다"
            return res
        f = webhwp.get(db, fid)
        data = Path(f["path"]).read_bytes()
        meta = webhwp.meta(db, fid)
        out_dir.mkdir(parents=True, exist_ok=True)
        res["hwpx"] = str(out_dir / f"{variant}-{label}.hwpx")
        Path(res["hwpx"]).write_bytes(data)
        for key, tag in (("기간·양식", "양식"), ("계속", "초안")):          # 흐름 단계별 파일(양식 → 초안 → 고쳐 쓰기 → 디자인)
            if snaps.get(key):
                res[f"hwpx_{tag}"] = str(out_dir / f"{variant}-{label}-{tag}.hwpx")
                Path(res[f"hwpx_{tag}"]).write_bytes(snaps[key]["data"])
        mid = snaps.get("고쳐 쓰기·표") or snaps.get("고쳐 쓰기·구체")
        if mid:
            res["hwpx_text"] = str(out_dir / f"{variant}-{label}-글.hwpx")
            Path(res["hwpx_text"]).write_bytes(mid["data"])
        # 양식
        src = str(meta.get("source") or "")
        pick = next(((a, t) for t_ in turns for a, t in t_.get("thoughts") or [] if a in ("hwp_start", "hwp_form_create")), ("", ""))
        res["form"] = {"name": f["name"], "source": src, "how": "만듦" if src == "양식생성" else "고름", "why": pick[1],
                       "tried_create": sum(1 for t_ in turns for a in t_.get("actions") or [] if a == "hwp_form_create"),
                       "create_log": webhwp_form_create.load_log(db, sid)}
        if meta.get("form_design"):
            res["form"]["structure"] = structure_score(meta["form_design"])
            res["form"]["outline"] = [s["heading"] for s in meta["form_design"]["sections"]]
        # 목표 이해
        res["goal"] = goal_score(chat_memory.load(db, sid).get("text", ""))
        res["goal"]["kept"] = goal_kept(turns)
        res["build"] = build_tag()
        # 쓴 절·사실·지어낸 숫자
        xml = _xml(data)
        text = _text(xml)
        norm = re.sub(r"\s+", "", text)
        fl = ww.fields(data)
        secs = [x for x in fl if x["kind"] == "절"]
        res["sections"] = f"{sum(1 for x in secs if x['text'])}/{len(secs)}"
        res["facts"] = {k: bool(re.search(p, text)) for k, p in GIVEN.items()}
        res["evidence_words"] = {w: w in norm for w in EVIDENCE_WORDS}
        given = set().union(*[_nums(q) for _, q in TURNS]) | {"2026", "2027", "2028"}
        tpl = set()
        ev_texts = [q for _, q in TURNS]
        for name in docs:
            try:
                t_ = _text(_xml((EVALDOCS / name).read_bytes()))
                tpl |= _nums(t_)
                ev_texts.append(t_)
            except (OSError, zipfile.BadZipFile):
                pass
        if src.startswith("양식복사:") and src[5:].isdigit() and webhwp.get(db, int(src[5:])):
            t_ = _text(_xml(Path(webhwp.get(db, int(src[5:]))["path"]).read_bytes()))
            tpl |= _nums(t_)
            ev_texts.append(t_)
        if meta.get("form_design"):
            tpl |= _nums(json.dumps(meta["form_design"], ensure_ascii=False))
            ev_texts.append(json.dumps(meta["form_design"], ensure_ascii=False))
        if snaps.get("기간·양식"):                              # 빈 양식(만든 직후)의 글 — 양식에 있던 숫자는 지어낸 것이 아니다
            ev_texts.append(_text(_xml(snaps["기간·양식"]["data"])))
        # 표면형만 견준 옛 집계(단위 바꿈도 지어낸 숫자로 잡힘)와 수치 대조기로 같은 값을 인정한 집계를 함께 남긴다
        res["invented_surface"] = sorted(n for n in _nums(text) - given - tpl if not n.startswith("20"))[:20]
        res["invented"] = invented_numbers(text, ev_texts)
        shapes = meta.get("shape") or {}
        res["tables"] = sum(v.get("tables", 0) for v in shapes.values())
        res["diagrams"] = sum(v.get("diagrams", 0) for v in shapes.values())
        need_n = sum(len(v.get("need") or []) for v in shapes.values())
        res["needs_met"] = f"{need_n - sum(len(v.get('missing') or []) for v in shapes.values())}/{need_n}"
        # 고쳐 쓰기 전후
        before = snaps.get("계속") or snaps.get("절 쓰기") or {"bodies": {}}
        rw = {}
        for nm, after_key in (("구체", "고쳐 쓰기·구체"), ("표", "고쳐 쓰기·표")):
            prev = before if nm == "구체" else (snaps.get("고쳐 쓰기·구체") or before)
            aft = snaps.get(after_key) or {"bodies": {}}
            changed = [k for k in aft["bodies"] if aft["bodies"].get(k) != prev["bodies"].get(k)]
            # 표·도식 수는 쓰기 때 남긴 모양 기록(meta shape)에서 — 작업본 XML 글에는 마크다운 표 꼴이 남지 않는다
            def stat(snap, k):
                st = body_stats(snap["bodies"].get(k, ""))
                sh = (snap.get("shape") or {}).get(k) or {}
                st.update(tables=sh.get("tables", 0), diagrams=sh.get("diagrams", 0))
                return st
            rw[nm] = {"changed": [re.sub(r"^(절|칸|표\d+):", "", k)[:30] for k in changed],
                      "before": [stat(prev, k) for k in changed[:4]], "after": [stat(aft, k) for k in changed[:4]]}
        # 일정 표 — 「표로」 턴에서 바뀐 자리에 표가 새로 생겼나(절 이름은 양식마다 달라 바뀐 자리로 본다)
        rw["표"]["schedule_table"] = any(a_["tables"] > b_["tables"] for a_, b_ in zip(rw["표"]["after"], rw["표"]["before"]))
        # 반영 — 「더 구체적으로」 는 바뀐 자리가 있고 그 가운데 하나라도 글자가 1.15배 넘게 늘었나, 「표로」 는 바뀐 자리에 표가 새로 생겼나
        rw["구체"]["reflected"] = any(a_["chars"] > b_["chars"] * 1.15 for a_, b_ in zip(rw["구체"]["after"], rw["구체"]["before"]))
        rw["표"]["reflected"] = rw["표"]["schedule_table"]
        rw["actions"] = {t_["turn"]: t_.get("actions") for t_ in turns if t_["turn"].startswith("고쳐 쓰기")}
        res["rewrite"] = rw
        res["delegate_log"] = _delegate_log(db, sid)
        # 디자인 — 턴마다 값과 끝 값
        dv = design_values(data)
        base_d = next((t_["design"] for t_ in reversed(turns) if t_.get("design") and not t_["turn"].startswith("디자인")), None)
        res["design"] = {"values": dv, "before": base_d, "score": design_score(dv, base_d),
                         "by_turn": {t_["turn"]: design_score(t_["design"], base_d) for t_ in turns if t_.get("design") and t_["turn"].startswith("디자인")}}
        res["broken"] = broken(data)
        res["peak"] = max((t_.get("peak") or 0) for t_ in turns)
        res["limit"] = max((t_.get("limit") or 0) for t_ in turns)
        res["calls"] = sum((t_.get("calls") or 0) for t_ in turns)
        # 글 품질 채점(review 연결 27B) — 쓴 자리 가운데 긴 것부터 6개, 고쳐 쓴 절은 전후
        if judge_on:
            try:
                jc = model_trial.fixed_client("review")
                bodies = (snaps.get("고쳐 쓰기·표") or snaps.get("계속") or {"bodies": {}})["bodies"]
                picks = sorted(bodies.items(), key=lambda kv: -len(kv[1]))[:6]
                scores = [{"section": k, **judge(jc, k, v)} for k, v in picks if len(v) > 40]
                res["judge"] = {"scores": scores, "avg": _avg(scores), "system": JUDGE_SYSTEM, "rubric": RUBRIC,
                                "model": getattr(jc, "model", ""), "note": "쓰는 모델과 같은 모델이 채점 — 자기 평가 한계, 판·회차 비교용"}
                ch = rw["구체"]["changed"][:1]
                if ch:
                    key = next((k for k in before["bodies"] if re.sub(r"^(절|칸|표\d+):", "", k)[:30] == ch[0]), None)
                    aft = (snaps.get("고쳐 쓰기·구체") or {"bodies": {}})["bodies"]
                    if key and key in aft:
                        res["judge"]["rewrite"] = {"section": key, "before": judge(jc, key, before["bodies"][key]), "after": judge(jc, key, aft[key])}
            except Exception as e:
                res["judge"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
        return res
    finally:
        webhwp_templates.ensure_all = saved_ensure


def _delegate_log(db, sid: int) -> dict:
    """서브 기록 요약 — 맡긴 절 수, 고쳐 쓰기 수, 자기 점검 판정, 검색어 수, 동시에 맡긴 묶음(같은 걸음의 자리)."""
    from zzaimy.app import delegate
    log = delegate.load_log(db, sid)
    secs = [r for r in log if r.get("kind") != "revise"]
    rev = [r for r in log if r.get("kind") == "revise"]
    return {"sections": len(secs), "written": sum(1 for r in secs if r.get("written")),
            "check": {v: sum(1 for r in secs if (r.get("check") or {}).get("verdict") == v) for v in ("통과", "고침", "모름", "실패")},
            "check_used": sum(1 for r in secs if (r.get("check") or {}).get("used")),
            "queries": sum(len(r.get("queries") or []) for r in secs),
            "lanes": {k: sum(1 for r in secs if r.get("lane") == k) for k in {str(r.get("lane") or "") for r in secs}},
            "revise": [{"field": r.get("field"), "written": r.get("written"), "before": r.get("before_chars"), "after": r.get("after_chars"),
                        "plan": r.get("plan"), "left": r.get("left"), "tries": len(r.get("tries") or [])} for r in rev]}


def build_tag() -> str:
    """판 표시 — 새 기능 설정(쓰기 품질·고쳐 쓰기·동시 맡기기·메모 목표 줄)이 켜졌나."""
    from zzaimy.app import chat_memory, delegate
    on = {"품질": delegate.quality_on(), "고쳐쓰기": delegate.revise_on(), "동시": delegate.parallel_on(), "목표줄": chat_memory.goal_lock()}
    return "new" if all(on.values()) else "old" if not any(on.values()) else ",".join(k for k, v in on.items() if v)


OLD_ENV = {"ZZAIMY_DRAFT_QUALITY": "0", "ZZAIMY_REVISE": "0", "ZZAIMY_DELEGATE_PARALLEL": "0", "ZZAIMY_MEMO_GOAL_LOCK": "0"}


# ── 여러 회 모아 보기(평균·범위) ──
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def metrics(r: dict) -> dict:
    """한 회차의 견줄 수치 — 고쳐 쓰기 반영(0~2), 절 채움 비율, 지어낸 숫자(보정 뒤·표면형), 채점 4항목, 목표, 목표 유지, 시간."""
    if r.get("error") and not r.get("form"):
        return {}
    a, b = (r.get("sections") or "0/0").split("/")
    rw = r.get("rewrite") or {}
    j = (r.get("judge") or {}).get("avg") or {}
    g = (r.get("goal") or {}).get("goal") or {}
    kept = (r.get("goal") or {}).get("kept") or ""
    out = {"고쳐 쓰기 반영": int(bool((rw.get("구체") or {}).get("reflected"))) + int(bool((rw.get("표") or {}).get("reflected"))),
           "절 채움": round(int(a) / max(1, int(b)), 2), "쓴 절": int(a),
           "지어낸 숫자": len(r.get("invented") or []), "지어낸 숫자(표면형)": len(r.get("invented_surface") or r.get("invented") or []),
           "목표": sum(1 for v in g.values() if v), "시간(초)": r.get("total_sec")}
    if kept and "/" in kept:
        k1, k2 = kept.split("/")
        out["목표 유지"] = round(int(k1) / max(1, int(k2)), 2)
    for k in RUBRIC:
        out[k] = j.get(k)
    return out


def aggregate(rows: list[dict]) -> str:
    """판(variant)·판본(build)마다 평균(최소~최대) 표."""
    keys = ["고쳐 쓰기 반영", "절 채움", "쓴 절", "지어낸 숫자", "지어낸 숫자(표면형)", "목표", "목표 유지", *RUBRIC, "시간(초)"]
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r.get("variant"), r.get("build") or "old"), []).append(metrics(r))
    out = ["| 판 | 판본 | 회수 | " + " | ".join(keys) + " |", "|" + "---|" * (len(keys) + 3)]
    for (v, b), ms in sorted(groups.items(), key=lambda x: (str(x[0][0]), str(x[0][1]))):
        ms = [m for m in ms if m]
        cells = []
        for k in keys:
            xs = [_num(m.get(k)) for m in ms if _num(m.get(k)) is not None]
            cells.append(f"{round(sum(xs) / len(xs), 2)} ({min(xs):g}~{max(xs):g})" if xs else "-")
        out.append(f"| {v} | {b} | {len(ms)} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def rescore(paths: list[Path]) -> list[dict]:
    """저장된 결과(json)를 다시 센다 — 지어낸 숫자를 지금 수치 대조기로(빈 양식 파일·근거 문서·담당자 말을 근거로), 고쳐 쓰기 반영·목표 유지도.
    이전 판 결과(이 집계가 없던 때)를 같은 잣대로 견주려고 쓴다."""
    out = []
    for p in paths:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(r, dict) or not r.get("variant"):
            continue
        hx = Path(r.get("hwpx") or "")
        if not hx.exists() and r.get("hwpx"):
            hx = p.parent / Path(r["hwpx"]).name
        if hx.exists():
            text = _text(_xml(hx.read_bytes()))
            ev = [q for _, q in TURNS]
            for name in EVIDENCE_DOCS + ([FORM_DOC] if r["variant"] == "forms" else []):
                try:
                    ev.append(_text(_xml((EVALDOCS / name).read_bytes())))
                except (OSError, zipfile.BadZipFile):
                    pass
            blank = Path(r.get("hwpx_양식") or "")
            if not blank.exists() and r.get("hwpx_양식"):
                blank = p.parent / Path(r["hwpx_양식"]).name
            if blank.exists():
                ev.append(_text(_xml(blank.read_bytes())))
            if (r.get("form") or {}).get("structure"):
                ev.append(json.dumps(r["form"], ensure_ascii=False))
            r.setdefault("invented_surface", r.get("invented") or [])
            r["invented"] = invented_numbers(text, ev)
        rw = r.get("rewrite") or {}
        if rw and "reflected" not in (rw.get("구체") or {}):
            c = rw.get("구체") or {}
            c["reflected"] = any((a_.get("chars") or 0) > (b_.get("chars") or 0) * 1.15 for a_, b_ in zip(c.get("after") or [], c.get("before") or []))
            (rw.get("표") or {})["reflected"] = bool((rw.get("표") or {}).get("schedule_table"))
        if r.get("goal") is not None and r.get("turns"):
            r["goal"].update({k: v for k, v in goal_score(r["turns"][-1].get("memo") or "").items() if k in ("final",)})
            r["goal"]["kept"] = goal_kept(r["turns"])
        r.setdefault("build", "old")
        out.append(r)
    return out


# ── 양식 만들기 묶음(--forms) ──
# 목적마다 학교 서식에서 흔히 쓰는 절 이름 갈래(정규식) — 포함률로 센다(정답 절 목록을 채우는 것이 아니다)
FORM_CASES = [
    {"name": "사업계획서", "q": "내년 신규 국고 사업 사업계획서를 써야 하는데 쓸 양식부터 만들어 줘.",
     "expect": [r"배경|필요성", r"목표", r"추진\s*(?:내용|전략|과제)|세부\s*내용", r"추진\s*체계|조직", r"일정", r"예산|사업비", r"성과|기대\s*효과"]},
    {"name": "결과보고서", "q": "올해 사업이 끝나서 결과보고서를 써야 해. 결과보고서 양식부터 만들어 줘.",
     "expect": [r"개요", r"추진\s*(?:실적|내용)|운영\s*결과|주요\s*실적", r"성과|목표\s*대비|달성", r"예산|집행", r"문제점|개선|환류", r"향후|계획"]},
    {"name": "회의록", "q": "다음 주 사업운영위원회 회의록을 남겨야 해. 회의록 양식부터 만들어 줘.",
     "expect": [r"회의\s*개요|일시|개요", r"참석", r"안건", r"논의|토의|심의", r"의결|결정|결과", r"향후|후속|조치"]},
    {"name": "출장 결과 보고", "q": "이번 출장 다녀온 결과를 보고해야 해. 출장 결과 보고서 양식부터 만들어 줘.",
     "expect": [r"개요|목적", r"일정|기간|출장지", r"주요\s*내용|결과|활동", r"시사점|후속|활용|조치", r"경비|비용"]},
    {"name": "내부결재 기안문", "q": "장비 구입 건으로 내부결재 기안문을 올려야 해. 기안문 양식부터 만들어 줘.",
     "expect": [r"관련|근거", r"목적|배경|필요성", r"내용|계획|추진", r"예산|소요|금액", r"붙임|첨부"]},
    {"name": "예산 변경 신청", "q": "사업비 비목 간 변경을 신청해야 해. 예산 변경 신청서 양식부터 만들어 줘.",
     "expect": [r"변경\s*사유|사유|필요성", r"변경\s*내역|내역", r"당초|변경\s*전|증감", r"영향|효과|성과지표"]},
    {"name": "행사 운영 계획", "q": "가을에 학생 대상 진로 박람회를 열 거야. 행사 운영 계획서 양식부터 만들어 줘.",
     "expect": [r"목적|개요|배경", r"일시|장소|일정", r"대상|참가|참여", r"프로그램|진행|세부\s*내용", r"운영\s*(?:인력|체계)|역할|추진\s*체계|조직", r"예산|소요", r"홍보", r"안전|유의|위험"]},
    {"name": "만족도 조사 결과 보고", "q": "프로그램 만족도 조사를 마쳤어. 만족도 조사 결과 보고서 양식부터 만들어 줘.",
     "expect": [r"조사\s*개요|개요|목적", r"응답|참여|대상", r"결과|분석", r"문항|영역|항목", r"개선|시사점|제언", r"환류|반영|향후"]},
]
_SOURCE_WORD = re.compile(r"근거|공고|지침|자료|출처|결과|실적|증빙|기준|계획서|규정|조사")


def form_scores(case: dict, design: dict) -> dict:
    from zzaimy.app import webhwp_form_create as fc
    secs = design.get("sections") or []
    titles = " ".join(s["title"] for s in secs)
    hit = [bool(re.search(p, titles)) for p in case["expect"]]
    leaves = [s for s in secs if not s["parent"]]
    guides = [s.get("guide") or "" for s in leaves]
    return {"include": f"{sum(hit)}/{len(hit)}", "include_rate": round(sum(hit) / len(hit), 2),
            "missing": [p for p, h in zip(case["expect"], hit) if not h],
            "numbering": all(re.match(r"^(\d+\.|[가-하]\.|\d+\))\s", s["heading"]) for s in secs),
            "guide_cover": round(sum(1 for g in guides if g) / max(1, len(leaves)), 2),
            "guide_len": round(sum(len(g) for g in guides) / max(1, len(guides))),
            "guide_source": round(sum(1 for g in guides if _SOURCE_WORD.search(g)) / max(1, len(leaves)), 2),
            "length_cover": round(sum(1 for s in leaves if s.get("length")) / max(1, len(leaves)), 2),
            **fc.shape(design)}


FORM_MODES = {"참고 없음": (False, False), "참고": (True, False), "참고+검토": (True, True)}


def run_forms(label: str, tmp: Path, out_dir: Path, only: str = "", modes: tuple[str, ...] = tuple(FORM_MODES),
              agent: bool = False) -> list[dict]:
    """양식 만들기 묶음. 문서함에는 공통 양식과 공고 서식(반입 문서)을 두되 hwp_forms 목록에서는 숨긴다(고르지 못하게 — 참고 양식으로만 읽힘).
    목적마다 판(modes: 참고 없음·참고·참고+검토)별로 같은 설계 도구를 직접 불러 설계 품질을 견주고, agent 면 에이전트 대화 한 턴(기본 설정)도 돌린다."""
    from fastapi.testclient import TestClient

    from zzaimy.app import webhwp, webhwp_agent, webhwp_form_create, webhwp_templates
    from zzaimy.app.drafter import SliceDrafter
    from zzaimy.app.main import create_app
    from zzaimy.app.pipeline import DocumentProcessor
    from zzaimy.eval import model_trial
    from zzaimy.generate.client import VllmClient

    d = tmp / "forms-only"
    d.mkdir(parents=True, exist_ok=True)
    src_conn = Path(os.environ.get("ZZAIMY_EVAL_CONN") or ROOT / "data" / "platform" / "llm_connections.json")
    if src_conn.exists():
        shutil.copy(src_conn, d / "llm_connections.json")
        os.chmod(d / "llm_connections.json", 0o600)
    saved_forms = webhwp_agent.HwpTools.forms

    def hidden_forms(self, a):                                 # 기존 양식을 숨긴 판 — 찾아본 표시만 남기고 후보는 없다고 돌려준다
        webhwp_form_create.mark_seen(self.db, self.sid)
        return "맞는 양식이 없다 — 검색어를 바꿔 한 번 더 찾거나, 목적에 맞는 양식을 hwp_form_create 로 설계해 만든다."
    webhwp_agent.HwpTools.forms = hidden_forms
    rows = []
    try:
        app = create_app(db_path=d / "eval.db", inbox_dir=d / "inbox", processor=DocumentProcessor(), drafter=SliceDrafter(),
                         password="eval-pass-123")
        app.state.accounts["zzaimy"]["dept"] = "기획처"
        db = app.state.db
        base = Path(db.path).parent
        webhwp_templates.ensure_all(db, base)                  # 참고 양식 문고(공통 양식)
        if (EVALDOCS / FORM_DOC).exists():                     # 공고 서식 — 반입 문서(양식)로
            dst = d / "inbox" / FORM_DOC
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(EVALDOCS / FORM_DOC, dst)
            did = db.add_document(FORM_DOC, str(dst), doc_type="grant", owner="zzaimy")
            DocumentProcessor().process(db, did, dst, defer_review=True)
            db.set_document_kind(did, "form")
        models = model_trial.snapshot()
        kd = webhwp_form_create.kind_dict(db, base)
        print(f"[forms] 참고 양식 {kd.get('inputs')}건 · 갈래 사전 {', '.join(f'{k}({v['n']})' for k, v in kd['kinds'].items())}", flush=True)
        webhwp.set_editor(db, "zzaimy", "webhwp")
        c = TestClient(app)
        assert c.post("/login", data={"username": "zzaimy", "pw": "eval-pass-123"}, follow_redirects=False).status_code == 303
        out_dir.mkdir(parents=True, exist_ok=True)
        client = VllmClient(role="answer")
        for case in FORM_CASES:
            if only and case["name"] != only:
                continue
            runs = [(m, "직접") for m in modes] + ([("참고+검토", "에이전트")] if agent else [])
            for mode, path in runs:
                t0 = time.time()
                row: dict = {"name": case["name"], "q": case["q"], "mode": mode, "path": path, "models": models}
                data = None
                design = None
                if path == "에이전트":
                    r = c.post("/chat/send", data={"question": case["q"]}, follow_redirects=False)
                    m = re.search(r"/chat/(\d+)", r.headers.get("location", ""))
                    sid = int(m.group(1)) if m else None
                    eps = sorted(d.rglob("doc_agent_episodes.jsonl"))
                    try:
                        ep = json.loads(eps[0].read_text(encoding="utf-8").strip().splitlines()[-1]) if eps else {}
                    except (ValueError, IndexError):
                        ep = {}
                    row["actions"] = [s_.get("action") for s_ in ep.get("steps") or [] if s_.get("action")]
                    fid = webhwp_agent.bound(db, sid) if sid else None
                    design = webhwp.meta(db, fid).get("form_design") if fid else None
                    row["log"] = webhwp_form_create.load_log(db, sid) if sid else []
                    if design:
                        data = Path(webhwp.get(db, fid)["path"]).read_bytes()
                    row["made_by"] = "에이전트" if design else "만들지 않음"
                else:
                    sid = db.create_chat_session(title=case["q"][:40], owner="zzaimy")
                    db.add_chat(sid, "user", case["q"])
                    hw = webhwp_agent.HwpTools(db, base, sid, "zzaimy", "기획처", None, lambda: [])
                    hw.client = client
                    refs, review = FORM_MODES[mode]
                    fcr = webhwp_form_create.FormCreator(client, db, sid, hw, case["q"], visible=lambda d_: True, refs=refs, review=review)
                    clean, probs, tries = fcr.design({"purpose": case["q"], "kind": case["name"]})
                    row["tries"] = tries
                    row["refs"] = [r_["name"] for r_ in fcr.refs]
                    if clean:
                        design = {**clean, "shape": webhwp_form_create.shape(clean)}
                        row["made_by"] = "직접"
                        try:
                            data = webhwp_form_create.build(clean)
                        except Exception as e:
                            row["build_error"] = str(e)[:200]
                    else:
                        row["made_by"] = "실패"
                        row["problems"] = probs
                row["sec"] = round(time.time() - t0, 1)
                if design:
                    row["scores"] = form_scores(case, design)
                    row["outline"] = [s_["heading"] for s_ in design["sections"]]
                    row["summary"] = design.get("summary")
                    row["why"] = design.get("why")
                if data:
                    row["broken"] = broken(data)
                    tag = f"{mode}-{path}".replace("+", "_").replace(" ", "")
                    row["hwpx"] = str(out_dir / f"forms-{label}-{case['name'].replace(' ', '_')}-{tag}.hwpx")
                    Path(row["hwpx"]).write_bytes(data)
                rows.append(row)
                print(json.dumps({k: row.get(k) for k in ("name", "mode", "path", "made_by", "sec", "actions", "scores", "broken")},
                                 ensure_ascii=False), flush=True)
    finally:
        webhwp_agent.HwpTools.forms = saved_forms
    return rows


FORMS_HEAD = ("| 목적 | 회차 | 판 | 길 | 절(쓸 자리) | 기준 절 포함 | 번호 체계 | 작성방법 덮음·평균 글자·근거 언급 | 분량 안내 | 요약 표 칸 | 표·도식 자리 | 깨짐 | 걸린 시간(초) |\n"
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|")


def forms_row(r: dict, label: str) -> str:
    sc = r.get("scores") or {}
    head = f"| {r['name']} | {label} | {r.get('mode', '-')} | {r.get('path', '-')}{'' if r.get('made_by') in ('직접', '에이전트') else '·' + str(r.get('made_by'))} |"
    if not sc:
        return head + " - | - | - | - | - | - | - | - | " + f"{r.get('sec', '-')} |"
    b = r.get("broken") or {}
    return (head + f" {sc['sections']}({sc['slots']}) | {sc['include']} | {'O' if sc['numbering'] else 'x'} | "
            f"{sc['guide_cover']}·{sc['guide_len']}·{sc['guide_source']} | {sc['length_cover']} | {sc['summary']} | {sc['tables']}·{sc['diagrams']} | "
            f"{b.get('check_n', '-')}·{b.get('field_unpaired', '-')} | {r['sec']} |")


def forms_summary(rows: list[dict], label: str) -> str:
    """판별 평균 — 기준 절 포함률·안내 덮음·근거 언급·분량 안내·요약 칸·깨짐 합·시간."""
    out = ["| 판·길 | 회차 | 건수 | 기준 절 포함률 | 번호 체계 | 안내 덮음 | 안내 평균 글자 | 근거 언급 | 분량 안내 | 요약 칸 | 깨짐 합 | 평균 시간(초) |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    keys = list(dict.fromkeys((r.get("mode"), r.get("path")) for r in rows))
    for mode, path in keys:
        rs = [r for r in rows if r.get("mode") == mode and r.get("path") == path]
        sc = [r["scores"] for r in rs if r.get("scores")]
        avg = lambda k: round(sum(float(x[k]) for x in sc) / len(sc), 2) if sc else "-"
        brk = sum((r.get("broken") or {}).get("check_n", 0) + (r.get("broken") or {}).get("field_unpaired", 0) for r in rs)
        out.append(f"| {mode}·{path} | {label} | {len(sc)}/{len(rs)} | {avg('include_rate')} | {sum(1 for x in sc if x['numbering'])}/{len(sc)} | "
                   f"{avg('guide_cover')} | {avg('guide_len')} | {avg('guide_source')} | {avg('length_cover')} | {avg('summary')} | {brk} | "
                   f"{round(sum(r.get('sec', 0) for r in rs) / max(1, len(rs)), 1)} |")
    return "\n".join(out)


def table_row(r: dict) -> str:
    if r.get("error") and not r.get("form"):
        return f"| {r['variant']} | {r['label']} | 오류: {r['error'][:60]} |" + " |" * 12
    fm = r["form"]
    st = (fm.get("structure") or {}).get("score", "-")
    g = r["goal"]["goal"]
    ds = r["design"]["score"]
    j = (r.get("judge") or {}).get("avg") or {}
    rw = r["rewrite"]
    return (f"| {r['variant']} | {r['label']} | {fm['how']}: {fm['name'][:28]} | {st} | "
            f"{''.join('O' if g[k] else 'x' for k in GIVEN)} | {r['sections']} | {sum(r['facts'].values())}/{len(r['facts'])} | "
            f"{len(r['invented'])} | {r['tables']}·{r['diagrams']} | {r['needs_met']} | "
            f"{len(rw['구체']['changed'])}·{'O' if rw['표'].get('schedule_table') else 'x'} | "
            f"{''.join('O' if v else 'x' for v in ds.values())} | {r['broken']['check_n']}·{r['broken']['field_unpaired']} | "
            f"{r['total_sec']} | {r['peak']}/{r['limit']} | "
            f"{'/'.join(str(j.get(k)) for k in RUBRIC) if j else '-'} |")


HEAD = ("| 판 | 회차 | 양식 | 구조 점수 | 목표(대상·예산·기간) | 쓴 절 | 준 값 반영 | 지어낸 숫자 | 표·도식 | 작성방법 요구 반영 | 고쳐 쓴 자리·일정 표 | "
        "디자인(가로·여백·머리줄·쪽 번호) | 깨짐(점검·필드) | 걸린 시간(초) | 가장 큰 호출/한도 | 채점(구체·논리·문체·근거) |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=("forms", "noforms", "both", "none"), default="both", help="시나리오 판(none 이면 시나리오를 건너뛴다)")
    ap.add_argument("--forms", action="store_true", help="양식 만들기 묶음(목적 여덟 가지)도 돌린다")
    ap.add_argument("--only-form", default="", help="양식 만들기 묶음에서 이 목적만")
    ap.add_argument("--form-modes", default=",".join(FORM_MODES), help="양식 만들기 판(쉼표): 참고 없음·참고·참고+검토")
    ap.add_argument("--forms-agent", action="store_true", help="양식 만들기 묶음에서 에이전트 대화 한 턴(기본 설정)도 돌린다")
    ap.add_argument("--label", default=time.strftime("%m%d-%H%M"))
    ap.add_argument("--out", default=str(ROOT / "data" / "eval" / "webhwp_scenario"))
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--candidate-conn", default="", help="후보 연결 id(운영 용도 지정은 바꾸지 않는다)")
    ap.add_argument("--candidate-model", default="", help="후보 서빙 이름(LoRA 어댑터 이름 등)")
    ap.add_argument("--repeat", type=int, default=1, help="같은 요청을 이만큼 되풀이(회차 이름 <label>-1 …) — 평균·범위 표")
    ap.add_argument("--old", action="store_true", help="이전 판 — 새 기능 설정(쓰기 품질·고쳐 쓰기·동시 맡기기·메모 목표 줄)을 끈다")
    ap.add_argument("--rescore", nargs="*", default=None, help="저장된 결과 json 을 다시 세어 평균·범위 표만(모델을 부르지 않는다)")
    a = ap.parse_args()
    if a.rescore is not None:
        rows = rescore([Path(x) for x in a.rescore])
        md = aggregate(rows)
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{time.strftime('%Y%m%d-%H%M%S')}-rescore-{a.label}.json").write_text(
            json.dumps([{"variant": r.get("variant"), "label": r.get("label"), "build": r.get("build"), **metrics(r),
                         "invented": r.get("invented"), "invented_surface": r.get("invented_surface")} for r in rows],
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(md, flush=True)
        return 0
    if a.old:
        os.environ.update(OLD_ENV)
    from zzaimy.eval import model_trial
    model_trial.apply(a.candidate_conn.strip(), a.candidate_model.strip())
    os.environ.pop("ZZAIMY_PLATFORM_SQLITE_PATH", None)        # 임시 DB 경로라 운영 PostgreSQL 로 가지 않는다
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="webhwp-scenario-"))
    res = []
    md = ""
    try:
        for i in range(max(1, a.repeat)):
            label = a.label if a.repeat <= 1 else f"{a.label}-{i + 1}"
            for v in {"both": ("forms", "noforms"), "none": ()}.get(a.variant, (a.variant,)):
                try:
                    r = run_variant(v, label, tmp / label, out, not a.no_judge)
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    r = {"variant": v, "label": label, "error": f"{type(e).__name__}: {e}"[:300]}
                r.setdefault("build", build_tag())
                res.append(r)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                (out / f"{stamp}-{v}-{label}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
                print(json.dumps({k: r.get(k) for k in ("variant", "build", "form", "goal", "sections", "facts", "invented", "design", "broken", "total_sec")},
                                 ensure_ascii=False, default=str)[:3000], flush=True)
        if res:
            md += HEAD + "\n" + "\n".join(table_row(r) for r in res) + "\n"
            md += "\n" + aggregate(res) + "\n"
        if a.forms:
            try:
                fr = run_forms(a.label, tmp, out, a.only_form, tuple(m.strip() for m in a.form_modes.split(",") if m.strip() in FORM_MODES),
                               a.forms_agent)
            except Exception as e:
                import traceback
                traceback.print_exc()
                fr = [{"name": "(묶음)", "sec": 0, "error": f"{type(e).__name__}: {e}"[:300]}]
            (out / f"{time.strftime('%Y%m%d-%H%M%S')}-formsset-{a.label}.json").write_text(
                json.dumps(fr, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            md += "\n" + forms_summary(fr, a.label) + "\n\n" + FORMS_HEAD + "\n" + "\n".join(forms_row(r, a.label) for r in fr) + "\n"
        (out / f"{time.strftime('%Y%m%d-%H%M%S')}-{a.label}.md").write_text(md, encoding="utf-8")
        print(md, flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)                 # 임시 DB·복사한 연결 설정을 지운다
    print("EVAL_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
