"""문서 작성 시험 묶음 실행 — 빈 양식·근거로 새 프로젝트를 만들고 시험 요청을 차례로 보낸 뒤 결과 작업본·대화·걸음 기록을 받아 둔다.

  python scripts/225_eval_set_run.py data/eval_sets/<이름> --init        # 묶음 틀 만들기
  python scripts/225_eval_set_run.py data/eval_sets/<이름> --base https://192.168.16.226 --user aura_test1
    비밀번호는 환경 변수 AURA_EVAL_PW 로(인자·저장소·기록에 남기지 않는다). 계정은 시연 계정(aura_demo) 말고 시험 계정을 쓴다.
    결과: <묶음>/runs/<시각>/ 에 result.hwpx(대화의 지금 작업본), work_<id>.hwpx(대화에 이은 작업본 전부), messages.json,
          run.json(요청마다 걸린 시간·끝난 상태), episodes.jsonl(--episodes-ssh 를 주었을 때만, 에이전트 걸음 기록)
    --compare 를 주면 끝난 뒤 226 비교 리포트(report.md)까지 만든다.
운영에서 돌린다 — 시험 계정·새 프로젝트로만 쓰고 기존 문서는 건드리지 않는다. 자체 서명 인증서라 기본은 인증서 확인을 끈다(--verify 로 켬).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.eval.eval_set import EvalSet  # noqa: E402

BUSY = re.compile(r"처리 중|대기 중|읽는 중|반입 중|올리는 중")


def log(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class _Retry:
    """httpx.Client 감싸개 — 연결이 끊기면(VPN 끊김 등) 기다렸다 다시 한다. 몇 시간짜리 시험이 잠깐 끊김에 멈추지 않게(11-12 시범이 끊김 한 번에 멈춤).
    올리기(POST /projects/bundle)·요청 보내기(POST /chat/send)는 두 번 들어가면 안 되므로 연결 전 실패(ConnectError)만 다시 한다."""

    def __init__(self, http, tries: int = 40, wait: float = 30.0):
        self.http, self.tries, self.wait = http, tries, wait

    @property
    def cookies(self):
        return self.http.cookies

    def _call(self, meth: str, *a, **k):
        import httpx
        safe = meth == "get"
        for n in range(self.tries):
            try:
                return getattr(self.http, meth)(*a, **k)
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                err = e
            except (httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ReadError) as e:
                if not safe:
                    raise
                err = e
            log(f"연결 끊김({type(err).__name__}) — {self.wait:.0f}초 뒤 다시({n + 1}/{self.tries})")
            time.sleep(self.wait)
        raise err

    def get(self, *a, **k):
        return self._call("get", *a, **k)

    def post(self, *a, **k):
        return self._call("post", *a, **k)


class Client:
    def __init__(self, base: str, verify: bool, timeout: float = 120.0):
        import httpx
        self.http = _Retry(httpx.Client(base_url=base.rstrip("/"), verify=verify, timeout=timeout, follow_redirects=False))

    def login(self, user: str, pw: str) -> None:
        r = self.http.post("/login", data={"username": user, "pw": pw})
        if "zz_session" not in self.http.cookies:
            raise SystemExit(f"로그인 실패({r.status_code}) — 계정·비밀번호(AURA_EVAL_PW)를 확인하세요")

    def create_project(self, name: str, sector: str, files: list[Path]) -> int:
        up = [("file", (p.name, p.read_bytes(), "application/octet-stream")) for p in files]
        r = self.http.post("/projects/bundle", data={"name": name, "sector": sector}, files=up)
        m = re.search(r"/project/(\d+)", r.headers.get("location", ""))
        if not m:
            raise SystemExit(f"프로젝트를 만들지 못했습니다({r.status_code}): {r.text[:200]}")
        return int(m.group(1))

    def wait_intake(self, pid: int, limit: float) -> float:
        t0 = time.time()
        while time.time() - t0 < limit:
            r = self.http.get(f"/project/{pid}")
            if r.status_code == 200 and not BUSY.search(re.sub(r"<[^>]+>", " ", r.text)):
                return time.time() - t0
            time.sleep(10)
        log(f"자료 읽기가 {int(limit)}초 안에 끝나지 않았습니다 — 그대로 진행")
        return time.time() - t0

    def send(self, text: str, effort: str, pid: int, sid: int | None) -> int:
        data = {"question": text, "effort": effort}
        if sid:
            data["session_id"] = str(sid)
        else:
            data["project_id"] = str(pid)
        r = self.http.post("/chat/send", data=data)
        m = re.search(r"/chat/(\d+)", r.headers.get("location", ""))
        if not m:
            raise SystemExit(f"요청을 보내지 못했습니다({r.status_code}): {r.text[:200]}")
        return int(m.group(1))

    def wait(self, sid: int, limit: float, every: float = 5.0) -> tuple[float, dict]:
        t0, last = time.time(), {}
        time.sleep(3)
        while time.time() - t0 < limit:
            try:
                st = self.http.get(f"/chat/{sid}/status").json()
            except Exception:
                st = {"waiting": True}
            if st.get("progress"):
                last = st["progress"]
            if not st.get("waiting"):
                return time.time() - t0, last
            time.sleep(every)
        return -1.0, last

    def messages(self, sid: int) -> dict:
        return self.http.get(f"/chat/{sid}/messages").json()

    def work_files(self, sid: int) -> dict:
        return self.http.get(f"/api/chat/{sid}/hwp").json()

    def download(self, fid: int) -> bytes:
        r = self.http.get(f"/hwp/{fid}/file")
        r.raise_for_status()
        return r.content


def fetch_episodes(host: str, sid: int, data_dir: str) -> list[dict]:
    """운영 VM 의 에이전트 걸음 기록(doc_agent_episodes.jsonl)에서 이 대화 것만 — ssh 로 읽기만 한다."""
    code = ("import sys,json\n"
            f"for l in open({json.dumps(data_dir + '/doc_agent_episodes.jsonl')},'rb'):\n"
            "    try: e=json.loads(l.decode('utf-8','replace'))\n"
            "    except Exception: continue\n"
            f"    if str(e.get('session'))=={json.dumps(str(sid))}: print(json.dumps(e,ensure_ascii=False))\n")
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", host, "python3", "-"], input=code, capture_output=True, text=True, timeout=300)
    return [json.loads(x) for x in out.stdout.splitlines() if x.strip()]


def episode_summary(eps: list[dict]) -> list[dict]:
    out = []
    for e in eps:
        st = e.get("steps") or []
        out.append({"at": e.get("at"), "stop": e.get("stop"), "writes": e.get("writes"), "steps": len(st),
                    "decide_s": round(sum(float(s.get("decide_s") or 0) for s in st), 1),
                    "run_s": round(sum(float(s.get("run_s") or 0) for s in st), 1),
                    "actions": [s.get("action") for s in st if s.get("action")]})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("set_dir", type=Path)
    ap.add_argument("--base", default=os.environ.get("AURA_EVAL_BASE", "https://192.168.16.226"))
    ap.add_argument("--user", default=os.environ.get("AURA_EVAL_USER", ""))
    ap.add_argument("--verify", action="store_true", help="인증서 확인(기본 끔 — 운영은 자체 서명)")
    ap.add_argument("--project", default="", help="프로젝트 이름(기본: meta.json 의 project, 없으면 묶음 이름 + 시각)")
    ap.add_argument("--intake-wait", type=float, default=1800, help="자료 읽기 기다리는 최대 초")
    ap.add_argument("--turn-wait", type=float, default=3600, help="요청 하나 기다리는 최대 초")
    ap.add_argument("--episodes-ssh", default="", help="걸음 기록을 받을 운영 VM(예: aura@192.168.16.226)")
    ap.add_argument("--data-dir", default="~/zzaimy-capstone/data/platform", help="VM 의 데이터 폴더")
    ap.add_argument("--compare", action="store_true", help="끝나면 정답과 비교 리포트까지")
    ap.add_argument("--steps", default="", help="보낼 요청 차례만(예: 1-4, 5-20) — 기본 전부")
    ap.add_argument("--project-id", type=int, default=0, help="이미 만든 프로젝트에 이어서(올리기·읽기 기다림 건너뜀)")
    ap.add_argument("--session", type=int, default=0, help="이미 있는 대화에 이어서 보냄(--project-id 와 함께)")
    ap.add_argument("--snapshots", action="store_true", help="요청마다 작업본 사본(turn_NN.hwpx)을 남긴다 — 과정 기록용")
    ap.add_argument("--init", action="store_true", help="묶음 폴더를 틀(scripts/eval_set_template)로 만들기만 한다")
    a = ap.parse_args(argv)
    if a.init:
        import shutil
        if a.set_dir.exists():
            raise SystemExit(f"{a.set_dir} 가 이미 있습니다")
        shutil.copytree(ROOT / "scripts" / "eval_set_template", a.set_dir)
        log(f"묶음 틀을 만들었습니다: {a.set_dir} — blank.hwpx·evidence/·answer.hwpx 를 넣고 prompts.txt 를 고치세요")
        return 0

    es = EvalSet(a.set_dir)
    pw = os.environ.get("AURA_EVAL_PW", "")
    if not a.user or not pw:
        raise SystemExit("계정은 --user(또는 AURA_EVAL_USER), 비밀번호는 환경 변수 AURA_EVAL_PW 로 주세요")
    if a.user == "aura_demo":
        log("주의: 시연 계정입니다 — 시험은 aura_test1 같은 시험 계정을 권합니다")
    prompts = es.prompts()
    first = 1
    if a.steps:
        lo, _, hi = a.steps.partition("-")
        first, last_ = int(lo), int(hi or lo)
        prompts = prompts[first - 1:last_]
    files = [es.blank] + es.evidence + es.materials
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = a.set_dir / "runs" / stamp
    out.mkdir(parents=True, exist_ok=True)
    name = a.project or es.meta.get("project") or f"{a.set_dir.name} 시험 {stamp}"

    c = Client(a.base, a.verify)
    c.login(a.user, pw)
    log("로그인", a.user)
    if a.project_id:
        pid, intake_s = a.project_id, 0.0
        log(f"프로젝트 {pid} 에 이어서")
    else:
        pid = c.create_project(name, es.meta.get("sector", "grant"), files)
        log(f"프로젝트 {pid} 「{name}」 · 파일 {len(files)}개 올림")
        intake_s = c.wait_intake(pid, a.intake_wait)
        log(f"자료 읽기 {intake_s:.0f}초")
    run = {"set": a.set_dir.name, "base": a.base, "user": a.user, "project_id": pid, "project": name,
           "intake_seconds": round(intake_s), "turns": [], "started": stamp}
    sid = a.session or None
    for i, p in enumerate(prompts, first):
        log(f"요청 {i}/{first + len(prompts) - 1} 보냄: {p['text'][:60]}")
        sid = c.send(p["text"], p["effort"], pid, sid)
        sec, prog = c.wait(sid, a.turn_wait)
        msgs = c.messages(sid).get("messages") or []
        last = next((m["content"] for m in reversed(msgs) if m["role"] == "assistant"), "")
        run["turns"].append({"n": i, "name": p.get("name", ""), "text": p["text"], "effort": p["effort"], "seconds": round(sec),
                             "timed_out": sec < 0, "answer": last, "answer_head": last[:300], "progress": prog})
        if a.snapshots:
            try:
                cur_ = (c.work_files(sid).get("file") or {}).get("id")
                if cur_:
                    (out / f"turn_{i:02d}.hwpx").write_bytes(c.download(int(cur_)))
            except Exception as e:                                   # 사본을 못 받아도 시험은 이어 간다
                log(f"요청 {i} 작업본 사본 실패: {e}")
        run["session_id"] = sid
        log(f"요청 {i} 끝 {sec:.0f}초" if sec >= 0 else f"요청 {i} 시간 초과")
        (out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")

    (out / "messages.json").write_text(json.dumps(c.messages(sid), ensure_ascii=False, indent=1), encoding="utf-8")
    wf = c.work_files(sid)
    cur = (wf.get("file") or {}).get("id")
    for f in wf.get("files") or []:
        (out / f"work_{f['id']}.hwpx").write_bytes(c.download(int(f["id"])))
    if cur:
        (out / "result.hwpx").write_bytes(c.download(int(cur)))
    run["work_files"] = [{"id": f["id"], "name": f["name"]} for f in wf.get("files") or []]
    run["result_file"] = cur
    if a.episodes_ssh:
        eps = fetch_episodes(a.episodes_ssh, sid, a.data_dir)
        with open(out / "episodes.jsonl", "w", encoding="utf-8") as fh:
            for e in eps:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        run["episodes"] = episode_summary(eps)
    (out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"결과 → {out}")

    if a.compare and cur and es.answer:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "226_eval_set_compare.py"), str(a.set_dir), "--run", str(out)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
