#!/usr/bin/env python3
"""서류 갈래 표본 대결 — 27B 판정(app/kind_read)과 제목 낱말 규칙(doc_routing.guess_kind)을 같은 문서로 견준다.

DGX 의 처리 결과(scripts/167 parsed-*.jsonl: 파일 이름·본문·규칙 갈래 doc_kind)에서 표본을 고르고, 27B 로 판정한다
(--role review 면 운영 연결 — 토르 03, 아니면 --url 의 Ollama. 10-18 부터 운영 판정은 토르 03).
표본은 두 묶음: 규칙이 갈래를 붙인 문서(27B 가 같은 답을 내는지 — 엉뚱한 판정 점검)와 규칙이 비워 둔 문서(27B 가 얼마나 채우는지).
운영 데이터는 바꾸지 않는다. 결과 JSONL(문서 경로·제목·두 판정·이유·걸린 시간 — 본문은 남기지 않음)과 요약을 낸다.
사용(DGX): PYTHONPATH=src python3 scripts/190_kind_read_sample.py --parsed ~/parsed --n 150 --out ~/kind_read/sample.jsonl
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from zzaimy.app import kind_read  # noqa: E402


def ollama(url: str, model: str):
    def complete(messages: list[dict]) -> str:
        body = {"model": model, "messages": messages, "stream": False, "think": False, "format": "json",
                "options": {"temperature": 0, "num_predict": 160}}
        req = urllib.request.Request(url.rstrip("/") + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode("utf-8"))["message"]["content"]
    return complete


def via_client(client, max_tokens: int = 160):
    """VM 등 OpenAI 호환 연결(VllmClient — 생각 끄기·구조화 출력 물러남·외부 가림 관문이 거기 있다)로 판정한다."""
    def complete(messages: list[dict]) -> str:
        r = client.client.chat.completions.create(model=client.model, messages=messages, temperature=0.0,
                                                  max_tokens=max_tokens, response_format={"type": "json_object"},
                                                  extra_body=client._extra)
        return r.choices[0].message.content or ""
    return complete


def make_complete(url: str, model: str, role: str = ""):
    """(complete, 모델 정보). role 을 주면 그 용도의 연결(VM 의 review 등)로, 아니면 주소의 Ollama 로."""
    from zzaimy.generate import model_info
    if role:
        from zzaimy.generate.client import VllmClient
        c = VllmClient(role=role)
        return via_client(c), model_info.stamp(c)
    return ollama(url, model), model_info.describe_url(url, model)


def latest(parsed_dir: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in sorted(glob.glob(str(Path(parsed_dir) / "parsed-*.jsonl"))):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("rel") and d.get("state") == "parsed" and (d.get("masked_text") or "").strip():
                    out[d["rel"]] = d
    return out


def summarize(rows: list[dict]) -> dict:
    """묶음별 요약 — 교체 점검(scripts/195)이 기준과 견주는 수치."""
    out: dict = {"n": len(rows), "fail": sum(1 for r in rows if r["model"] is None)}
    for key, group in (("ruled", "규칙 있음"), ("empty", "규칙 비움")):
        g = [r for r in rows if r["group"] == group]
        if not g:
            continue
        ok = [r for r in g if r["model"] is not None]
        out[key] = {"n": len(g), "fail": len(g) - len(ok),
                    "same_as_rule": round(sum(1 for r in ok if r["model"] == r["rule"]) / len(g), 3),
                    "filled": round(sum(1 for r in ok if r["model"]) / len(g), 3)}
    secs = sorted(r["sec"] for r in rows)
    out["sec_median"] = secs[len(secs) // 2] if secs else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsed", default=str(Path.home() / "parsed"))
    ap.add_argument("--n", type=int, default=150, help="묶음마다(규칙 있음·규칙 비움) 표본 수")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--model", default="qwen3.8:27b")
    ap.add_argument("--role", default="", help="이 용도(review·answer)의 연결로 판정한다 — VM 에서 .env.local 과 함께(--url·--model 은 무시)")
    ap.add_argument("--seed", type=int, default=20261014)
    ap.add_argument("--rels", default="", help="이 목록(JSONL 의 rel, 또는 한 줄에 경로 하나)만 다시 판정 — 갈래 설명을 고친 뒤 엇갈린 것만 볼 때")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    docs = latest(args.parsed)
    rng = random.Random(args.seed)
    ruled = sorted(r for r, d in docs.items() if d.get("doc_kind"))
    empty = sorted(r for r, d in docs.items() if not d.get("doc_kind"))
    pick = [("규칙 있음", r) for r in rng.sample(ruled, min(args.n, len(ruled)))] + \
           [("규칙 비움", r) for r in rng.sample(empty, min(args.n, len(empty)))]
    if args.rels:
        wanted = []
        for line in Path(args.rels).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                wanted.append(json.loads(line)["rel"] if line.startswith("{") else line)
        pick = [("규칙 있음" if docs[r].get("doc_kind") else "규칙 비움", r) for r in wanted if r in docs]
    print(f"문서 {len(docs)} · 규칙 있음 {len(ruled)} · 규칙 비움 {len(empty)} · 표본 {len(pick)}", flush=True)
    complete, llm = make_complete(args.url, args.model, args.role)
    from zzaimy.generate import model_info
    print(f"판정 모델: {model_info.label(llm)}", flush=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with open(args.out, "w", encoding="utf-8") as fo:
        for i, (group, rel) in enumerate(pick, 1):
            d = docs[rel]
            title = Path(d.get("filename") or rel).stem
            folder = str(Path(rel).parent)
            t0 = time.time()
            got = kind_read.read_kind(complete, title, d.get("masked_text") or "", folder)
            row = {"group": group, "rel": rel, "title": title, "rule": d.get("doc_kind") or "",
                   "model": (got[0] if got else None), "why": (got[1] if got else "실패"), "sec": round(time.time() - t0, 1),
                   "llm": llm}
            rows.append(row)
            fo.write(json.dumps(row, ensure_ascii=False) + "\n")
            if i % 20 == 0:
                print(f"  {i}/{len(pick)}", flush=True)
    for group in ("규칙 있음", "규칙 비움"):
        g = [r for r in rows if r["group"] == group]
        fail = sum(1 for r in g if r["model"] is None)
        same = sum(1 for r in g if r["model"] is not None and r["model"] == r["rule"])
        filled = sum(1 for r in g if r["model"])
        print(f"[{group}] {len(g)}건 · 실패 {fail} · 규칙과 같음 {same} · 모델이 갈래를 붙임 {filled} · "
              f"모델 갈래 {Counter(r['model'] for r in g if r['model']).most_common(6)}", flush=True)
    secs = sorted(r["sec"] for r in rows)
    print(f"한 건 중앙값 {secs[len(secs) // 2] if secs else 0}초 · 전체 {sum(secs):.0f}초", flush=True)
    print("KIND_READ " + json.dumps(summarize(rows), ensure_ascii=False), flush=True)
    print("KIND_READ_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
