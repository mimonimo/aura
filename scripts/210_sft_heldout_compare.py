#!/usr/bin/env python3
"""학습셋 보류분(held-out) 견주기 — 같은 문항을 두 서빙 이름(운영 Writer·파인튜닝 어댑터 등)으로 생성하고, 채점자가 눈가림 쌍대 비교로 고른다.

저장소 코드에 기대지 않는 홑 파일이다(표준 라이브러리만) — 운영 폴더 밖(~/ops/sft_eval 등)에 복사해 바로 돌릴 수 있다.
연결 장부(llm_connections.json)를 거치지 않고 주소·서빙 이름을 직접 받는다. 결과에는 서빙 이름만 남기고 주소는 남기지 않는다.

입력: 학습 꼴 jsonl(한 줄 = {"prompt": [메시지…], "completion": [{"role": "assistant", "content": 정답}], "chat_template_kwargs": {…}})
      와 같은 순서의 색인 jsonl({"job", "track", "turn"} — 갈래별 표에 쓴다, 없으면 갈래 「전체」 하나).
걸음(--phase):
- gen    두 모델로 문항마다 생성(같은 메시지·온도·최대 토큰, 생각 모드 끔). gen-<모델>.jsonl 에 쌓고 이어 돌리면 빈 것만 채운다.
- judge  채점자에게 답 둘을 A·B 로 보여 고르게 한다 — 순서를 바꿔 두 번(위치 치우침 상쇄), 두 판정을 더해 이김·비김·짐.
         모델이 셋 이상이면 짝마다(기본: 첫 모델 대 나머지, 그 뒤 후보끼리) judge-<기준>__<후보>.jsonl.
         기준표(JUDGE_RUBRIC): 지시 충실·형식·근거 충실·문체·완결성. 정답은 채점자에게 보여 주지 않는다(정답 꼴을 익힌 쪽에 기울지 않게).
- report 기계 지표(정답 대비 chrF·ROUGE-L, 질문에 없는 숫자, 형식 표시, 모름 답, 잘림, 외국 글자, 길이·시간·토큰)와
         쌍대 판정(이김·비김·짐, 이김 비율 95% 신뢰구간 — 부트스트랩)을 갈래(track × 첫 턴/이어지는 턴)별로 report.json·report.md 에.
         원문은 넣지 않고 문항 번호만 남긴다.
- score  팔마다 답 하나씩 항목 1~5점(지시 충실·맥락 이해·형식·근거 충실·문체·완결성) — 팔을 나중에 더해도 그대로 견준다.
- speed  앞 --speed-n 문항을 동시 1로 흘려 받아 첫 토큰 시간·토큰/초·전체 시간.
- goldlp 앞 --goldlp-n 문항의 정답 토큰당 평균 로그확률(vLLM) — 학습이 실제로 얹혔는지(어댑터·병합본) 샘플링 잡음 없이 본다.
- all    생성·점수·쌍대·보고를 차례로(speed·goldlp 는 따로).
팔(arm): --models 의 「팔:서빙 이름=주소」. 결과 파일은 팔 이름으로 — 운영 교체 뒤 같은 서빙 이름이 다른 모델을 가리켜도 섞이지 않는다.

사용(VM — 결과·사본은 저장소 밖에):
  python3 210_sft_heldout_compare.py --data ~/ops/sft_eval/test.jsonl --index ~/ops/sft_eval/test-index.jsonl \\
    --base-url http://<서빙>:8001/v1 --models zzaimy-writer,writer-sft --judge-url http://<채점 서빙>:8001/v1 --judge-model zzaimy-writer \\
    --out ~/ops/sft_eval/test-run --concurrency 2
운영 서빙이므로 동시 호출은 기본 2 로 묶는다.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

JUDGE_RUBRIC = {
    "지시 충실": "마지막 질문과 앞 대화의 조건(범위·하지 말 것·연습 조건)을 따랐는가",
    "맥락 이해": "질문 의도를 바로 짚고 주어진 원문·앞 대화의 내용(앞서 준 값·결정)을 제대로 끌어 썼는가",
    "형식": "대학 행정 문서 꼴 — 개조식·표·절 구조(제목·번호)가 내용에 맞게 쓰였는가",
    "근거 충실": "주어진 원문·자료에 없는 숫자·기관·사실·승인 상태를 지어내지 않았는가, 모르는 것은 모른다고 했는가",
    "문체": "문장 끝 「~함」「~임」 같은 행정 문체가 일관되고 구어·군더더기가 없는가",
    "완결성": "물은 것을 빠짐없이 다루고 중간에 끊기지 않았는가",
}
CRITERIA = list(JUDGE_RUBRIC)
JUDGE_SYSTEM = """당신은 대학 행정 문서 작성 도우미의 답 두 개(A, B)를 견주는 채점자다. 주어진 대화 맥락만 근거로 삼고 기준표 밖의 생각은 넣지 않는다.
답의 길이나 순서(A가 먼저)로 판단하지 않는다. 둘이 비슷하면 「같음」.
[기준표]
{rubric}
답은 JSON 하나만: {{"지시 충실": "A|B|같음", "맥락 이해": "A|B|같음", "형식": "A|B|같음", "근거 충실": "A|B|같음", "문체": "A|B|같음", "완결성": "A|B|같음", "종합": "A|B|같음", "까닭": "한 문장"}}"""

SCORE_SYSTEM = """당신은 대학 행정 문서 작성 도우미의 답 하나를 기준표로 채점한다. 주어진 대화 맥락만 근거로 삼고 기준표 밖의 생각은 넣지 않는다.
항목마다 1~5점(1=매우 나쁨, 3=보통, 5=매우 좋음). 길이가 길다고 점수를 주지 않는다.
[기준표]
{rubric}
답은 JSON 하나만: {{"지시 충실": 1~5, "맥락 이해": 1~5, "형식": 1~5, "근거 충실": 1~5, "문체": 1~5, "완결성": 1~5, "까닭": "한 문장"}}"""

UNKNOWN = re.compile(r"확인되지\s*않|확인할\s*수\s*없|자료에\s*없|근거가\s*없|제시되지\s*않|알\s*수\s*없|주어지지\s*않|사용자\s*입력으로|확인이\s*필요|확인\s*후")
HAN = re.compile(r"[一-鿿]")
# 학습셋 틀(「근거 설명: … 답변: …」, 「○○ 원문: …」) — 플랫폼은 이 틀을 요청하지 않는다. 학습 꼴이 서빙 답에 새어 나오는지 센다.
# 틀의 표지는 줄머리 「답변:」 — 학습셋 정답은 모두 이 줄을 갖고, 플랫폼 답에는 쓸 일이 없다(「근거:」 만은 정상 답에도 나와 세지 않는다).
FRAME = re.compile(r"(?:^|\n)\s*(?:\*\*)?답변(?:\*\*)?\s*:")
LATIN_WORD = re.compile(r"\b[A-Za-z]{4,}\b")


# ── 호출 ──
def chat(base_url: str, model: str, messages: list[dict], max_tokens: int, temperature: float, timeout: float = 600,
         kwargs: dict | None = None, seed: int | None = None) -> dict:
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
            "chat_template_kwargs": dict(kwargs or {}, enable_thinking=False)}
    if seed is not None:
        body["seed"] = seed
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            ch = d["choices"][0]
            return {"text": ch["message"].get("content") or "", "finish": ch.get("finish_reason"),
                    "usage": d.get("usage") or {}, "sec": round(time.time() - t0, 2)}
        except Exception as e:  # 잠깐 끊김은 다시
            last = f"{type(e).__name__}: {e}"[:200]
            time.sleep(5 * (attempt + 1))
    return {"text": "", "error": last, "sec": round(time.time() - t0, 2)}


def chat_stream(base_url: str, model: str, messages: list[dict], max_tokens: int, temperature: float, timeout: float = 600,
                kwargs: dict | None = None, seed: int | None = None) -> dict:
    """흘려 받기 — 첫 토큰까지 시간(ttft)·생성 속도(토큰/초)·전체 시간. 요청 본문은 chat 과 같다(stream 만 더함)."""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stream": True,
            "stream_options": {"include_usage": True}, "chat_template_kwargs": dict(kwargs or {}, enable_thinking=False)}
    if seed is not None:
        body["seed"] = seed
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    ttft, parts, usage, finish = None, [], {}, None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                d = json.loads(data)
                if d.get("usage"):
                    usage = d["usage"]
                for ch in d.get("choices") or []:
                    piece = (ch.get("delta") or {}).get("content") or ""
                    if piece and ttft is None:
                        ttft = time.time() - t0
                    parts.append(piece)
                    finish = ch.get("finish_reason") or finish
    except Exception as e:
        return {"text": "".join(parts), "error": f"{type(e).__name__}: {e}"[:200], "sec": round(time.time() - t0, 2)}
    total = time.time() - t0
    n = usage.get("completion_tokens") or 0
    tps = (n - 1) / (total - ttft) if ttft is not None and n > 1 and total > ttft else 0.0
    return {"text": "".join(parts), "finish": finish, "usage": usage, "sec": round(total, 2),
            "ttft": round(ttft or 0.0, 3), "tok_per_sec": round(tps, 2)}


# ── 입력 ──
def load_items(data: Path, index: Path | None, limit: int = 0) -> list[dict]:
    rows = [json.loads(x) for x in data.read_text(encoding="utf-8").splitlines() if x.strip()]
    idx = [json.loads(x) for x in index.read_text(encoding="utf-8").splitlines() if x.strip()] if index and index.exists() else []
    out = []
    for n, r in enumerate(rows):
        meta = idx[n] if n < len(idx) else {}
        gold = (r.get("completion") or [{}])[-1].get("content", "")
        out.append({"id": f"{data.stem}-{n:03d}", "n": n, "messages": r["prompt"], "gold": gold,
                    "kwargs": r.get("chat_template_kwargs") or {}, "track": meta.get("track", "전체"),
                    "turn": int(meta.get("turn") or 1), "job": str(meta.get("job", ""))[:10]})
    return out[:limit] if limit else out


TRACK_NAMES = {"institutional_policy": "규정·제도 문서", "program_retrieval": "사업 자료 활용"}


def group_of(it: dict) -> str:
    return f"{TRACK_NAMES.get(it['track'], it['track'])}·{'첫 질문' if it['turn'] <= 1 else '이어지는 질문'}"


def read_cache(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                if not d.get("error"):
                    out[d["key"]] = d
    return out


def append(path: Path, rec: dict, lock) -> None:
    with lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── 기계 지표 ──
def _ngrams(s: str, n: int) -> Counter:
    return Counter(s[i:i + n] for i in range(len(s) - n + 1))


def chrf(hyp: str, ref: str, n_max: int = 6, beta: float = 2.0) -> float:
    """문자 n-gram F-점수(chrF, 공백 뺌) 0~100."""
    h, r = re.sub(r"\s+", "", hyp or ""), re.sub(r"\s+", "", ref or "")
    if not h or not r:
        return 0.0
    ps, rs = [], []
    for n in range(1, n_max + 1):
        hn, rn = _ngrams(h, n), _ngrams(r, n)
        if not hn or not rn:
            continue
        m = sum((hn & rn).values())
        ps.append(m / sum(hn.values()))
        rs.append(m / sum(rn.values()))
    if not ps:
        return 0.0
    p, rc = sum(ps) / len(ps), sum(rs) / len(rs)
    if p + rc == 0:
        return 0.0
    b2 = beta * beta
    return round(100 * (1 + b2) * p * rc / (b2 * p + rc), 2)


def rouge_l(hyp: str, ref: str) -> float:
    """어절 단위 ROUGE-L F 0~100(최장 공통 부분열)."""
    h, r = (hyp or "").split(), (ref or "").split()
    if not h or not r:
        return 0.0
    prev = [0] * (len(r) + 1)
    for x in h:
        cur = [0]
        for j, y in enumerate(r):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    lcs = prev[-1]
    if not lcs:
        return 0.0
    p, rc = lcs / len(h), lcs / len(r)
    return round(100 * 2 * p * rc / (p + rc), 2)


def numbers(text: str) -> set[str]:
    """두 자리 이상 숫자(쉼표 뺌). 줄머리 번호(「1.」「2)」)는 한 자리라 빠진다."""
    return {re.sub(r",", "", n).rstrip(".") for n in re.findall(r"\d[\d,.]*\d", text or "")}


HEAD_NUM = re.compile(r"^[\s#*>|-]*(?:[가-하]\.\s*)?(\d{1,2}(?:\.\d{1,2})+)\.?\s", re.M)


def invented_numbers(answer: str, messages: list[dict]) -> list[str]:
    """답의 숫자 가운데 질문·앞 대화에 없는 것. 절 번호(줄머리 「3.1 」)와 연도 자리표(「202X」)는 숫자 값이 아니라 뺀다."""
    ctx = " ".join(m.get("content", "") for m in messages)
    have = numbers(ctx)
    heads = set(HEAD_NUM.findall(answer or ""))
    body = re.sub(r"\d+(?=[Xx○●])", " ", answer or "")
    return sorted(n for n in numbers(body) if n not in have and n not in heads)


def endings(text: str) -> dict:
    """줄·문장 끝 갈래 — 명사형(~함·~임·~음·~됨 등) / 서술형(~다) / 그 밖."""
    out = Counter()
    for seg in re.split(r"[\n]|(?<=[.!?])\s", text or ""):
        s = re.sub(r"[\s|*#>\-·•○□■◦]+$", "", seg.strip()).rstrip(".")
        if len(s) < 6 or s.startswith("|"):
            continue
        if re.search(r"(함|임|음|됨|필요|예정|요망|바람|없음|있음|계획|추진|검토|확인|운영|수립|마련|구성|개선|강화|제고|지원)$", s):
            out["명사형"] += 1
        elif re.search(r"다$|요$", s):
            out["서술형"] += 1
        else:
            out["기타"] += 1
    return dict(out)


def format_marks(text: str) -> dict:
    lines = [x for x in (text or "").splitlines() if x.strip()]
    bullets = sum(bool(re.match(r"\s*([-*•·○□■◦]|\d+[.)]|[가-하][.)]|\(\d+\))\s", x)) for x in lines)
    return {"table": bool(re.search(r"^\s*\|?\s*:?-{3,}", text or "", re.M)),
            "headings": sum(bool(re.match(r"\s*(#{1,4}\s|[가-하]\.\s|\d+\.\s\S.{0,30}$)", x)) for x in lines),
            "bullet_ratio": round(bullets / len(lines), 3) if lines else 0.0,
            "gold_frame": bool(re.search(r"근거\s*설명\s*:", text or "")) and bool(re.search(r"답변\s*:", text or "")),
            "frame_prefix": bool(FRAME.search(text or ""))}


def mech(it: dict, g: dict) -> dict:
    t = g.get("text", "")
    e = endings(t)
    tot = sum(e.values()) or 1
    inv = invented_numbers(t, it["messages"])
    u = g.get("usage") or {}
    return {"chrf": chrf(t, it["gold"]), "rougeL": rouge_l(t, it["gold"]), "chars": len(t),
            "len_ratio": round(len(t) / max(1, len(it["gold"])), 3), "invented": len(inv),
            "nominal_ratio": round(e.get("명사형", 0) / tot, 3), "plain_ratio": round(e.get("서술형", 0) / tot, 3),
            **format_marks(t), "unknown": bool(UNKNOWN.search(t)), "truncated": g.get("finish") == "length",
            "han": len(HAN.findall(t)), "latin_words": len(LATIN_WORD.findall(t)), "empty": not t.strip(),
            "sec": g.get("sec", 0), "out_tokens": u.get("completion_tokens", 0), "in_tokens": u.get("prompt_tokens", 0)}


# ── 채점 ──
def judge_context(messages: list[dict], cap_turn: int = 1500, cap_last: int = 8000) -> str:
    parts = []
    for i, m in enumerate(messages):
        c = m.get("content", "")
        last = i == len(messages) - 1
        cap = cap_last if last else cap_turn
        if len(c) > cap:
            c = c[:cap] + " …(줄임)"
        parts.append(f"[{ {'system': '시스템', 'user': '사용자', 'assistant': '도우미'}.get(m.get('role'), m.get('role')) }{' — 이번 질문' if last else ''}]\n{c}")
    return "\n\n".join(parts)


def parse_verdict(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    out = {}
    for k in CRITERIA + ["종합"]:
        v = str(d.get(k, "")).strip()
        out[k] = "A" if v.startswith("A") else "B" if v.startswith("B") else "같음"
    out["까닭"] = str(d.get("까닭", ""))[:200]
    return out


def side_score(verdict: dict, a_is: str, model: str, key: str) -> int:
    """판정 하나를 model 쪽 점수로: 이김 +1, 같음 0, 짐 -1."""
    v = verdict.get(key, "같음")
    if v == "같음":
        return 0
    winner = a_is if v == "A" else ("other" if a_is == model else model)
    return 1 if winner == model else -1


def combine(verdicts: list[tuple[dict, str]], model: str, key: str) -> str:
    s = sum(side_score(v, a_is, model, key) for v, a_is in verdicts)
    return "win" if s > 0 else "loss" if s < 0 else "tie"


# ── 통계 ──
def bootstrap_ci(scores: list[float], iters: int = 2000, seed: int = 7) -> tuple[float, float]:
    if not scores:
        return (0.0, 0.0)
    rnd = random.Random(seed)
    n = len(scores)
    means = sorted(sum(scores[rnd.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return (round(means[int(0.025 * iters)], 3), round(means[int(0.975 * iters) - 1], 3))


def wlt_summary(outcomes: list[str]) -> dict:
    c = Counter(outcomes)
    sc = [1.0 if o == "win" else 0.5 if o == "tie" else 0.0 for o in outcomes]
    rate = round(sum(sc) / len(sc), 3) if sc else 0.0
    return {"n": len(outcomes), "win": c["win"], "tie": c["tie"], "loss": c["loss"], "score": rate, "ci95": bootstrap_ci(sc)}


def mean(xs) -> float:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else 0.0


# ── 걸음 ──
def pair_file(out: Path, base: str, cand: str) -> Path:
    return out / f"judge-{base}__{cand}.jsonl"


def phase_gen(items, a, out: Path, lock) -> None:
    for model in a.gen_models:
        path = out / f"gen-{model}.jsonl"
        done = read_cache(path)
        todo = [it for it in items if it["id"] not in done]
        print(f"[gen] {model}: {len(done)} 있음, {len(todo)} 남음", flush=True)

        def one(it, model=model, path=path):
            g = chat(a.urls[model], a.served[model], it["messages"], a.max_tokens, a.temperature, kwargs=it["kwargs"], seed=a.seed)
            append(path, {"key": it["id"], "arm": model, "model": a.served[model], **g}, lock)
            return g

        with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
            for k, g in enumerate(ex.map(one, todo), 1):
                if k % 10 == 0 or g.get("error"):
                    print(f"  {model} {k}/{len(todo)} {g.get('sec')}초 {g.get('error') or ''}", flush=True)


def phase_judge(items, a, out: Path, lock) -> None:
    gens = {m: read_cache(out / f"gen-{m}.jsonl") for m in a.models}
    sysmsg = JUDGE_SYSTEM.format(rubric="\n".join(f"- {k}: {v}" for k, v in JUDGE_RUBRIC.items()))
    for m1, m2 in a.pairs:
        path = pair_file(out, m1, m2)
        done = read_cache(path)
        jobs = []
        for it in items:
            if it["id"] not in gens[m1] or it["id"] not in gens[m2]:
                continue
            for order in ((m1, m2), (m2, m1)):
                key = f"{it['id']}|{order[0]}"
                if key not in done:
                    jobs.append((it, order, key))
        print(f"[judge] {m2} 대 {m1}: {len(done)} 있음, {len(jobs)} 남음", flush=True)

        def one(job, path=path):
            it, (ma, mb), key = job
            user = (f"[대화 맥락]\n{judge_context(it['messages'])}\n\n[답 A]\n{gens[ma][it['id']]['text'][:6000]}\n\n"
                    f"[답 B]\n{gens[mb][it['id']]['text'][:6000]}\n\n기준표대로 견주어 JSON 하나만 답하라.")
            r = chat(a.judge_url, a.judge_model, [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}],
                     400, 0.0)
            v = parse_verdict(r.get("text", ""))
            rec = {"key": key, "item": it["id"], "a_is": ma, "verdict": v, "sec": r.get("sec")}
            if v is None:
                rec["error"] = r.get("error") or "판정 못 읽음"
            append(path, rec, lock)
            return rec

        with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
            for k, rec in enumerate(ex.map(one, jobs), 1):
                if k % 20 == 0 or rec.get("error"):
                    print(f"  judge {k}/{len(jobs)} {rec.get('error') or ''}", flush=True)


def parse_scores(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    out = {}
    for k in CRITERIA:
        try:
            v = int(round(float(d.get(k))))
        except Exception:
            return None
        out[k] = min(5, max(1, v))
    out["까닭"] = str(d.get("까닭", ""))[:200]
    return out


def phase_score(items, a, out: Path, lock) -> None:
    """팔마다 답 하나씩 1~5점(항목 여섯) — 다른 팔 답을 보지 않으므로 팔을 나중에 더해도(운영 교체 뒤) 그대로 견줄 수 있다."""
    sysmsg = SCORE_SYSTEM.format(rubric="\n".join(f"- {k}: {v}" for k, v in JUDGE_RUBRIC.items()))
    for arm in a.gen_models:
        gens = read_cache(out / f"gen-{arm}.jsonl")
        path = out / f"score-{arm}.jsonl"
        done = read_cache(path)
        jobs = [it for it in items if it["id"] in gens and it["id"] not in done]
        print(f"[score] {arm}: {len(done)} 있음, {len(jobs)} 남음", flush=True)

        def one(it, arm=arm, gens=gens, path=path):
            user = (f"[대화 맥락]\n{judge_context(it['messages'])}\n\n[답]\n{gens[it['id']]['text'][:6000]}\n\n기준표대로 채점해 JSON 하나만 답하라.")
            r = chat(a.judge_url, a.judge_model, [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}], 300, 0.0)
            v = parse_scores(r.get("text", ""))
            rec = {"key": it["id"], "arm": arm, "scores": v, "sec": r.get("sec")}
            if v is None:
                rec["error"] = r.get("error") or "점수 못 읽음"
            append(path, rec, lock)
            return rec

        with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
            for k, rec in enumerate(ex.map(one, jobs), 1):
                if k % 20 == 0 or rec.get("error"):
                    print(f"  score {arm} {k}/{len(jobs)} {rec.get('error') or ''}", flush=True)


def phase_speed(items, a, out: Path, lock) -> None:
    """속도 — 앞 --speed-n 문항을 하나씩(동시 1) 흘려 받아 첫 토큰 시간·토큰/초·전체 시간. 생성 설정은 gen 과 같다."""
    for arm in a.gen_models:
        path = out / f"speed-{arm}.jsonl"
        done = read_cache(path)
        todo = [it for it in items[:a.speed_n] if it["id"] not in done]
        print(f"[speed] {arm}: {len(done)} 있음, {len(todo)} 남음", flush=True)
        for it in todo:
            g = chat_stream(a.urls[arm], a.served[arm], it["messages"], a.max_tokens, a.temperature, kwargs=it["kwargs"], seed=a.seed)
            append(path, {"key": it["id"], "arm": arm, "model": a.served[arm], **{k: v for k, v in g.items() if k != "text"},
                          "chars": len(g.get("text", ""))}, lock)


def gold_logprob(base_url: str, model: str, it: dict, timeout: float = 600) -> dict:
    """정답(학습 꼴 완성본)의 토큰당 평균 로그확률 — 같은 정답에 대해 모델이 얼마나 「익숙한가」. 학습이 들어간 모델이면 학습 손실 근처로 내려간다.
    vLLM 전용(prompt_logprobs·continue_final_message·/tokenize). 샘플링 잡음이 없어 어댑터가 실제로 얹혔는지 가르는 데 쓴다."""
    kw = dict(it.get("kwargs") or {}, enable_thinking=False)
    root = re.sub(r"/v1/?$", "", base_url.rstrip("/"))

    def post(url, body):
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    try:
        n_prompt = post(root + "/tokenize", {"model": model, "messages": it["messages"], "add_generation_prompt": True,
                                             "chat_template_kwargs": kw})["count"]
        d = post(base_url.rstrip("/") + "/chat/completions", {
            "model": model, "messages": it["messages"] + [{"role": "assistant", "content": it["gold"]}], "max_tokens": 1,
            "temperature": 0, "prompt_logprobs": 0, "add_generation_prompt": False, "continue_final_message": True,
            "chat_template_kwargs": kw})
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:200]}
    vals = [next(iter(x.values()))["logprob"] for x in (d.get("prompt_logprobs") or [])[n_prompt:] if x]
    if not vals:
        return {"error": "로그확률 없음"}
    return {"mean_lp": round(sum(vals) / len(vals), 5), "first_lp": round(vals[0], 5), "n_tokens": len(vals)}


def phase_goldlp(items, a, out: Path, lock) -> None:
    for arm in a.gen_models:
        path = out / f"goldlp-{arm}.jsonl"
        done = read_cache(path)
        todo = [it for it in items[:a.goldlp_n] if it["id"] not in done]
        print(f"[goldlp] {arm}: {len(done)} 있음, {len(todo)} 남음", flush=True)
        for it in todo:
            append(path, {"key": it["id"], "arm": arm, **gold_logprob(a.urls[arm], a.served[arm], it)}, lock)


MECH_KEYS = ("chrf", "rougeL", "chars", "len_ratio", "invented", "nominal_ratio", "plain_ratio", "bullet_ratio",
             "headings", "table", "gold_frame", "frame_prefix", "unknown", "truncated", "han", "latin_words", "empty", "sec", "out_tokens")


def phase_report(items, a, out: Path) -> dict:
    """기계 지표는 모델마다, 쌍대 판정은 짝(기준 m1 · 후보 m2 — m2 쪽 이김·짐)마다."""
    gens = {m: read_cache(out / f"gen-{m}.jsonl") for m in a.models}
    rows = []
    for it in items:
        if not all(it["id"] in gens[m] for m in a.models):
            continue
        rows.append({"id": it["id"], "group": group_of(it), "track": it["track"], "turn": it["turn"], "job": it["job"],
                     "mech": {m: mech(it, gens[m][it["id"]]) for m in a.models}, "pairs": {}})
    byid = {r["id"]: r for r in rows}
    groups = ["전체"] + sorted({r["group"] for r in rows})
    rep = {"models": a.models, "judge_model": a.judge_model, "n": len(rows), "temperature": a.temperature,
           "max_tokens": a.max_tokens, "data": Path(a.data).name, "mech": {}, "pairs": {}}
    for g in groups:
        rs = [r for r in rows if g == "전체" or r["group"] == g]
        rep["mech"][g] = {"n": len(rs), **{m: {k: mean([float(r["mech"][m][k]) for r in rs]) for k in MECH_KEYS}
                                           | {"invented_any": mean([float(r["mech"][m]["invented"] > 0) for r in rs])}
                                           for m in a.models}}
    rep["scores"], rep["speed"] = {}, {}
    for arm in a.models:
        sc = {k: v["scores"] for k, v in read_cache(out / f"score-{arm}.jsonl").items() if v.get("scores")}
        for r in rows:
            if r["id"] in sc:
                r.setdefault("scores", {})[arm] = sc[r["id"]]
        for g in groups:
            rs = [r for r in rows if (g == "전체" or r["group"] == g) and arm in r.get("scores", {})]
            rep["scores"].setdefault(g, {})[arm] = {"n": len(rs), **{k: mean([r["scores"][arm][k] for r in rs]) for k in CRITERIA}}
        gl = [v for v in read_cache(out / f"goldlp-{arm}.jsonl").values() if v.get("mean_lp") is not None]
        if gl:
            rep.setdefault("goldlp", {})[arm] = {"n": len(gl), "mean_lp": mean([x["mean_lp"] for x in gl]), "first_lp": mean([x["first_lp"] for x in gl]),
                                                 "ids": sorted(x["key"] for x in gl)}
        sp = [v for v in read_cache(out / f"speed-{arm}.jsonl").values() if v.get("ttft") is not None]
        if sp:
            rep["speed"][arm] = {"n": len(sp), "ttft": mean([x["ttft"] for x in sp]), "tok_per_sec": mean([x["tok_per_sec"] for x in sp]),
                                 "sec": mean([x["sec"] for x in sp]), "out_tokens": mean([(x.get("usage") or {}).get("completion_tokens", 0) for x in sp]),
                                 "in_tokens": mean([(x.get("usage") or {}).get("prompt_tokens", 0) for x in sp])}
    for m1, m2 in a.pairs:
        name = f"{m2} 대 {m1}"
        judged = defaultdict(list)
        for d in read_cache(pair_file(out, m1, m2)).values():
            if d.get("verdict"):
                judged[d["item"]].append((d["verdict"], d["a_is"]))
        prs = []
        if not judged:
            continue
        for iid, vs in judged.items():
            r = byid.get(iid)
            if r is None or len(vs) != 2:
                continue
            r["pairs"][name] = {"result": {k: combine(vs, m2, k) for k in CRITERIA + ["종합"]},
                                "position_A_wins": sum(v.get("종합") == "A" for v, _ in vs),
                                "flip": vs[0][0].get("종합") == vs[1][0].get("종합") != "같음",   # 두 순서에서 같은 자리를 고름 = 모델로는 엇갈림
                                "reasons": [v.get("까닭", "") for v, _ in vs]}
            prs.append(r)
        p = {"baseline": m1, "candidate": m2, "n": len(prs), "groups": {},
             "position_bias_A_share": round(sum(r["pairs"][name]["position_A_wins"] for r in prs) / max(1, 2 * len(prs)), 3),
             "flip_rate": round(sum(r["pairs"][name]["flip"] for r in prs) / max(1, len(prs)), 3)}
        for g in groups:
            gp = [r for r in prs if g == "전체" or r["group"] == g]
            p["groups"][g] = {k: wlt_summary([r["pairs"][name]["result"][k] for r in gp]) for k in ["종합"] + CRITERIA}
        # 문항 번호 목록(원문 없이)
        p["ids"] = {
            "candidate_loss": sorted(r["id"] for r in prs if r["pairs"][name]["result"]["종합"] == "loss"),
            "candidate_win": sorted(r["id"] for r in prs if r["pairs"][name]["result"]["종합"] == "win"),
            "candidate_invented_more": [r["id"] for r in rows if r["mech"][m2]["invented"] > r["mech"][m1]["invented"]],
            "candidate_truncated": [r["id"] for r in rows if r["mech"][m2]["truncated"]],
            "baseline_truncated": [r["id"] for r in rows if r["mech"][m1]["truncated"]],
        }
        rep["pairs"][name] = p
    (out / "report.json").write_text(json.dumps({**rep, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    md = to_md(rep, a.models)
    (out / "report.md").write_text(md, encoding="utf-8")
    print(md, flush=True)
    return rep


def to_md(rep: dict, models: list[str]) -> str:
    L = [f"# 보류분 견주기 — {rep['data']} {rep['n']}문항", "",
         f"모델 {' · '.join(models)} · 채점자 {rep['judge_model']}(순서 바꿔 두 번) · 온도 {rep['temperature']} · 최대 {rep['max_tokens']}토큰", "",
         "## 쌍대 판정(후보 기준 이김/비김/짐, 점수 = (이김 + 비김/2)/n, 95% 부트스트랩 구간)", ""]
    for name, p in rep["pairs"].items():
        L += [f"### {name}", "", f"채점자 A 쪽 고름 비율 {p['position_bias_A_share']} · 두 순서 판정 엇갈림 {p['flip_rate']}", "",
              "| 갈래 | n | 항목 | 이김 | 비김 | 짐 | 점수 | 95% 구간 |", "|---|---|---|---|---|---|---|---|"]
        for g, d in p["groups"].items():
            for k, x in d.items():
                if g != "전체" and k != "종합":
                    continue
                L.append(f"| {g} | {x['n']} | {k} | {x['win']} | {x['tie']} | {x['loss']} | {x['score']} | {x['ci95'][0]}~{x['ci95'][1]} |")
        L.append("")
    if rep.get("scores"):
        L += ["## 항목 점수(채점자 1~5, 팔마다 따로)", "", "| 갈래 | 팔 | n | " + " | ".join(CRITERIA) + " |", "|---|---|---|" + "---|" * len(CRITERIA)]
        for g, d in rep["scores"].items():
            for arm, x in d.items():
                if x["n"]:
                    L.append(f"| {g} | {arm} | {x['n']} | " + " | ".join(str(x[k]) for k in CRITERIA) + " |")
        L.append("")
    if rep.get("goldlp"):
        L += ["## 정답 로그확률(토큰당 평균, 높을수록 학습 꼴에 익숙)", "", "| 비교 대상 | 문항 | 정답 평균 | 정답 첫 토큰 |", "|---|---|---|---|"]
        for arm, x in rep["goldlp"].items():
            L.append(f"| {arm} | {x['n']} | {x['mean_lp']} | {x['first_lp']} |")
        L.append("")
    if rep.get("speed"):
        L += ["## 속도(흘려 받기, 동시 1)", "", "| 팔 | n | 첫 토큰(초) | 토큰/초 | 전체(초) | 출력 토큰 | 입력 토큰 |", "|---|---|---|---|---|---|---|"]
        for arm, x in rep["speed"].items():
            L.append(f"| {arm} | {x['n']} | {x['ttft']} | {x['tok_per_sec']} | {x['sec']} | {x['out_tokens']:.0f} | {x['in_tokens']:.0f} |")
        L.append("")
    L += ["## 기계 지표(평균)", "", "| 갈래 | 모델 | chrF | ROUGE-L | 글자 | 정답 대비 길이 | 지어낸 숫자 | 숫자 지어낸 답 비율 | 명사형 끝 | 서술형 끝 | "
          "표 | 정답 꼴(근거 설명·답변) | 모름 표시 | 잘림 | 한자 | 초 | 출력 토큰 |", "|" + "---|" * 17]
    for g, d in rep["mech"].items():
        for m in models:
            x = d[m]
            L.append(f"| {g} | {m} | {x['chrf']} | {x['rougeL']} | {x['chars']:.0f} | {x['len_ratio']} | {x['invented']} | {x['invented_any']} | "
                     f"{x['nominal_ratio']} | {x['plain_ratio']} | {x['table']} | {x['gold_frame']} | {x['unknown']} | {x['truncated']} | "
                     f"{x['han']} | {x['sec']} | {x['out_tokens']:.0f} |")
    return "\n".join(L) + "\n"


def parse_models(spec: str, base_url: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """「[팔:]서빙 이름[=주소]」 를 쉼표로. 팔(arm)은 결과 파일·표의 이름 — 서빙 이름이 바뀌어도(운영 교체 뒤 같은 이름) 결과가 섞이지 않게
    팔 이름으로 남긴다. 팔을 빼면 서빙 이름, 주소를 빼면 --base-url."""
    names, urls, served = [], {}, {}
    for part in (x.strip() for x in spec.split(",")):
        if not part:
            continue
        head, _, url = part.partition("=")
        arm, _, model = head.partition(":") if ":" in head else ("", "", head)
        arm, model = (arm.strip() or model.strip()), model.strip()
        names.append(arm)
        urls[arm] = url.strip() or base_url
        served[arm] = model
    return names, urls, served


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="학습셋 보류분 모델 견주기(생성·눈가림 쌍대 채점·기계 지표)")
    ap.add_argument("--data", required=True)
    ap.add_argument("--index", default="")
    ap.add_argument("--base-url", default="", help="모델을 내어 주는 서빙 주소(/v1 까지) — 모델마다 다르면 --models 에 이름=주소")
    ap.add_argument("--models", default="zzaimy-writer,writer-sft", help="기준,후보[,후보…] 서빙 이름(쉼표, 이름=주소 가능)")
    ap.add_argument("--pairs", default="", help="견줄 짝 「기준:후보」 쉼표(기본 첫 모델 대 나머지, 그 뒤 후보끼리)")
    ap.add_argument("--gen-only", default="", help="이 모델들만 생성(쉼표) — 다른 서빙에 부하를 더하지 않을 때")
    ap.add_argument("--judge-url", default="")
    ap.add_argument("--judge-model", default="zzaimy-writer")
    ap.add_argument("--out", required=True)
    ap.add_argument("--phase", choices=("gen", "judge", "score", "speed", "goldlp", "report", "all"), default="all")
    ap.add_argument("--goldlp-n", type=int, default=40, help="정답 로그확률을 잴 앞 문항 수")
    ap.add_argument("--speed-n", type=int, default=20, help="속도 걸음에서 흘려 받을 앞 문항 수")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args(argv)
    a.models, a.urls, a.served = parse_models(a.models, a.base_url)
    if not a.models or len(set(a.models)) != len(a.models) or (len(a.models) < 2 and a.phase in ("judge", "all")):
        ap.error("--models 는 서로 다른 이름(쌍대 판정은 둘 이상)")
    if a.pairs:
        a.pairs = [tuple(x.split(":", 1)) for x in a.pairs.split(",") if ":" in x]
        if any(m not in a.models for pr in a.pairs for m in pr):
            ap.error("--pairs 의 이름이 --models 에 없다")
    else:
        a.pairs = [(a.models[i], a.models[j]) for i in range(len(a.models)) for j in range(i + 1, len(a.models))]
    a.gen_models = [m.strip() for m in a.gen_only.split(",") if m.strip()] or a.models
    if a.phase in ("gen", "speed", "goldlp", "all") and not all(a.urls.get(m) for m in a.gen_models):
        ap.error("생성할 모델의 주소가 없다(--base-url 또는 이름=주소)")
    if a.phase in ("judge", "score", "all") and not a.judge_url:
        ap.error("--judge-url 이 필요하다")
    a.concurrency = max(1, min(a.concurrency, 4))
    items = load_items(Path(a.data).expanduser(), Path(a.index).expanduser() if a.index else None, a.limit)
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    import threading
    lock = threading.Lock()
    if a.phase in ("gen", "all"):
        phase_gen(items, a, out, lock)
    if a.phase == "speed":
        phase_speed(items, a, out, lock)
    if a.phase == "goldlp":
        phase_goldlp(items, a, out, lock)
    if a.phase in ("score", "all"):
        phase_score(items, a, out, lock)
    if a.phase in ("judge", "all"):
        phase_judge(items, a, out, lock)
    if a.phase in ("report", "all"):
        phase_report(items, a, out)
    print("HELDOUT_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
