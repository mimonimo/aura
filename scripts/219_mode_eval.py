#!/usr/bin/env python3
"""모드별 질문 시험 — 일반·RAG·인터넷 × 빠르게·꼼꼼하게 질문을 운영 데이터로 보내 답·시간을 잰다(10-30).

운영 DB 를 읽고 「[시험]」 대화를 만들었다가 끝나면 지운다. 배경 작업(재색인·드라이브 점검 등)은 띄우지 않는다.
결과는 줄마다 JSON(case·ok·turns[sec, answer]). 인자로 이름 일부를 주면 그 경우만(예: 총장 일반).
고칠 때마다 같은 질문으로 다시 재서 회귀를 본다.
사용(VM, 운영 폴더에서 .env.local 과 함께):
  cd ~/zzaimy-capstone && set -a && . ./.env.local; set +a; PYTHONPATH=src .venv/bin/python scripts/219_mode_eval.py [이름 일부 …]
"""
import json, re, sys, threading, time
from pathlib import Path
_o = threading.Thread.start
_SKIP = {"auto-reindex", "drive-share-sweep", "drive-share", "weekly-schedule", "org-reconcile", "requeue-stalled", "warm-nouns", "warm-pages"}
threading.Thread.start = lambda self: None if self.name in _SKIP else _o(self)
from fastapi.testclient import TestClient
from zzaimy.app.main import create_app
from zzaimy.app.drafter import SliceDrafter
from zzaimy.app.pipeline import DocumentProcessor
app = create_app(db_path=Path("data/platform/platform.db"), inbox_dir=Path("data/platform/inbox"),
                 processor=DocumentProcessor(), drafter=SliceDrafter(), responder=None, password=None)
db = app.state.db
c = TestClient(app)
# (이름, [(질문, web, effort)], 맞으면 답에 들어 있어야 할 글)
CASES = [
    ("RAG 정년·빠르게", [("우리대학의 직원의 정년퇴임 연령은 몇살이야?", "", "fast")], "61"),
    ("RAG 총장 임기·빠르게", [("영남이공대학교 총장 임기는?", "", "fast")], "4년"),
    ("RAG 총장 임기·꼼꼼하게", [("영남이공대학교 총장 임기는?", "", "deep")], "4년"),
    ("일반 이어진 질문", [("qwen 27b 모델 컨텍스트 윈도우 알려줘", "model", "fast"), ("K로 변환하면 얼마야 그럼?", "model", "fast")], "K"),
    ("인터넷 정년", [("우리대학 직원 정년퇴임 연령은 몇살이야?", "1", "fast")], "정년"),
    ("B 연구년", [("교원 연구년 신청 자격이 어떻게 돼?", "", "fast")], ""),
    ("B 승진연수", [("직원 승진에 필요한 최저 근무 연수는?", "", "fast")], ""),
    ("B 출장 숙박", [("국내 출장 숙박비 상한액은 얼마야?", "", "fast")], ""),
    ("B 산단장 이어짐", [("산학협력단장 임기는?", "", "fast"), ("그럼 연임도 돼?", "", "fast")], ""),
    ("B 사업 기간", [("LINC 3.0 사업 기간은 언제부터 언제까지야?", "", "fast")], ""),
    ("B 사업 예산", [("현장실습 지원비는 학생 1인당 얼마까지 줄 수 있어?", "", "fast")], ""),
    ("B 일반 공문", [("공문서에서 날짜는 어떻게 표기해?", "model", "fast")], ""),
    ("B 규정 비교", [("교원과 직원 정년이 각각 몇 살이고 왜 달라?", "", "fast")], ""),
]
only = sys.argv[1:] or None
sids = []
out = []
try:
    for name, turns, expect in CASES:
        if only and not any(o in name for o in only):
            continue
        sid = db.create_chat_session(f"[시험] 모드별 {name}", owner="zzaimy")
        sids.append(sid)
        rec = {"case": name, "turns": []}
        for q, web, eff in turns:
            n0 = len(db.list_chats(sid, limit=400))
            t = time.time()
            c.post("/chat/send", data={"question": q, "session_id": str(sid), "web": web, "effort": eff}, follow_redirects=False)
            ans = None
            while time.time() - t < 900:
                msgs = db.list_chats(sid, limit=400)
                if len(msgs) >= n0 + 2 and msgs[-1]["role"] == "assistant":
                    ans = msgs[-1]["content"]; break
                time.sleep(2)
            sec = round(time.time() - t, 1)
            run = None
            with db._conn() as cc:
                r = cc.execute("select steps, timing from answer_runs where session_id=? order by id desc limit 1", (sid,)).fetchone()
                if r:
                    try:
                        run = len(json.loads(r[0] or "[]"))
                    except Exception:
                        run = None
            rec["turns"].append({"q": q, "web": web, "effort": eff, "sec": sec, "steps": run, "answer": (ans or "(답 없음)")[:700]})
        last = rec["turns"][-1]["answer"]
        rec["ok"] = (expect in last) if expect else None
        out.append(rec)
        print(json.dumps(rec, ensure_ascii=False), flush=True)
finally:
    for sid in sids:
        try:
            db.delete_chat_session(sid)
        except Exception:
            pass
