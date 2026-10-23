#!/usr/bin/env python3
"""맥락 이해 시험 — 여러 턴에서 앞서 준 값을 바로 쓰는지, 긴 자료 깊은 곳의 사실을 찾는지, 「자료에 없으면 비워 둬」 를 지키는지.

scripts/210 과 같은 홑 파일 방식(표준 라이브러리 + 같은 폴더의 210). 사례는 씨앗으로 정해지는 가상 값(사람 이름·연락처 없음)이고,
긴 자료의 바탕 글은 공개 공고·기본계획 hwpx(--filler-dir)에서 뽑는다. 채점은 코드가 센다(값 포함·옛 값 남음·질문에 없는 숫자·「확인 필요」 표시).
팔(arm)마다 같은 사례·같은 생성 설정(온도·최대 토큰·생각 끔)으로 부른다 — 바뀌는 것은 모델뿐.

갈래:
- multiturn   세 턴에 걸쳐 대상·예산·기간·담당을 주고(절반은 둘째 턴에 예산을 바꿈) 셋째 턴에 개요 절을 청한다 → 값 넷 반영, 바뀐 예산이면 옛 값 없음
- longctx     공개 자료 약 1만 토큰 사이 깊이 10~90% 에 사실 한 문장 → 그 사실을 묻는다(일부는 자료에 없는 것을 물어 「없음」 을 기대)
- conflict    다섯 칸 표를 채우라 하되 자료에는 그중 셋만 — 「자료에 없는 값은 확인 필요」 지시를 지키는가

사용(VM): python3 211_context_probe.py --filler-dir ~/zzaimy-capstone/data/scraped/uniall \\
    --models open-nvfp4:zzaimy-writer --base-url http://<서빙>:8001/v1 --out ~/ops/sft_eval/context-run
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import re
import sys
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load210():
    for cand in (HERE / "210_sft_heldout_compare.py",):
        if cand.exists():
            spec = importlib.util.spec_from_file_location("s210", cand)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    raise SystemExit("같은 폴더에 210_sft_heldout_compare.py 가 필요하다")


s210 = _load210()
SYSTEM = "문서 근거와 앞 대화를 확인해 답합니다. 접근 권한은 서버가 판단하며 우회하지 않습니다."

PROGRAMS = ["AI 기초 역량 강화", "지역 정주형 인재 양성", "산학 공동 실습", "글로벌 현장 학습", "디지털 전환 교육", "창업 동아리 지원",
            "평생 직업 교육", "전공 연계 봉사", "융합 전공 개발", "취업 역량 캠프"]
TARGETS = ["재학생 1학년 전체 412명", "2학년 재학생 320명", "졸업 예정자 185명", "신입생 560명", "외국인 유학생 74명",
           "3학년 재학생 290명", "재직자 계약학과 학생 96명", "공학계열 2학년 238명", "보건계열 3학년 153명", "디자인계열 학생 127명"]
DEPTS = ["교육혁신센터", "산학협력단", "학생취업처", "평생교육원", "국제교류팀", "교무처", "창업지원센터", "교수학습지원센터", "RISE 사업단", "기획처"]
PERIODS = ["2027. 3. ~ 2027. 12.", "2027. 4. ~ 2028. 2.", "2027. 3. ~ 2028. 2.", "2027. 6. ~ 2027. 11.", "2027. 9. ~ 2028. 1."]
BUDGETS = [("2억 4천만 원", "240,000,000"), ("3억 원", "300,000,000"), ("1억 8천만 원", "180,000,000"), ("4억 2천만 원", "420,000,000"),
           ("9천 5백만 원", "95,000,000"), ("5억 원", "500,000,000"), ("2억 원", "200,000,000"), ("6천만 원", "60,000,000")]
FIELDS = ["총사업비", "참여 학생 수", "운영 기간", "협력 기관 수", "운영 강좌 수"]


def budget_hit(text: str, b: tuple[str, str]) -> bool:
    """「2억 4천만 원」 = 240,000,000 = 24,000만 = 240(백만원) — 표면형 몇 가지."""
    t = re.sub(r"\s", "", text)
    word, digits = b
    won = int(digits.replace(",", ""))
    forms = {re.sub(r"\s", "", word).replace("원", ""), f"{won:,}", str(won), f"{won // 1_000_000:,}백만", f"{won // 10_000:,}만"}
    if won % 100_000_000 == 0:
        forms.add(f"{won // 100_000_000}억")
    return any(f and f in t for f in forms)


def num_digits(s: str) -> list[str]:
    return re.findall(r"\d+", s)


def period_hit(text: str, p: str) -> bool:
    a = num_digits(p)                     # 2027 3 2027 12
    t = re.sub(r"\s", "", text)
    pat = r"\D*".join(a)
    return bool(re.search(pat, t))


# ── 사례 만들기 ──
def make_multiturn(rnd: random.Random, n: int) -> list[dict]:
    out = []
    for i in range(n):
        prog, tgt, dept, per = PROGRAMS[i % len(PROGRAMS)], rnd.choice(TARGETS), rnd.choice(DEPTS), rnd.choice(PERIODS)
        b1, b2 = rnd.sample(BUDGETS, 2)
        change = i % 2 == 1
        msgs = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"내년에 「{prog}」 사업을 준비하려고 해. 대상은 {tgt}이고, 예산은 {b1[0]}으로 잡고 있어."},
                {"role": "assistant", "content": f"알겠습니다. 「{prog}」 사업의 대상은 {tgt}, 예산은 {b1[0]}으로 정리해 두겠습니다. 운영 기간과 담당 부서를 알려 주세요."},
                {"role": "user", "content": f"기간은 {per}이고 담당은 {dept}야." + (f" 그리고 예산은 {b2[0]}으로 바꿀게." if change else "")},
                {"role": "assistant", "content": f"기간 {per}, 담당 {dept}로 반영했습니다." + (f" 예산은 {b2[0]}으로 고쳤습니다." if change else "")},
                {"role": "user", "content": "지금까지 정한 내용으로 사업 개요 절을 개조식으로 써 줘. 끝에 대상·예산·기간·담당 부서를 표 하나로 정리해."}]
        out.append({"id": f"mt-{i:02d}", "kind": "multiturn", "messages": msgs,
                    "expect": {"target": re.search(r"\d+명", tgt).group(0), "dept": dept, "period": per,
                               "budget": list(b2 if change else b1), "old_budget": list(b1) if change else None}})
    return out


def hwpx_text(path: Path) -> str:
    try:
        z = zipfile.ZipFile(path)
        xml = "".join(z.read(n).decode("utf-8", "ignore") for n in sorted(z.namelist()) if re.match(r"Contents/section\d+\.xml$", n))
    except Exception:
        return ""
    txt = re.sub(r"<hp:t>([^<]*)</hp:t>", r"\1 ", xml)
    txt = re.sub(r"</hp:p>", "\n", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    lines = [re.sub(r"\s+", " ", x).strip() for x in txt.splitlines()]
    return "\n".join(x for x in lines if len(x) > 1)


NEEDLES = [
    ("중간 점검 결과 보고서는 점검 회의 뒤 {d}일 안에 {dept}에 낸다.", "중간 점검 결과 보고서는 회의 뒤 며칠 안에 어느 부서에 내야 해?"),
    ("사업비 집행 내역의 내부 검토는 매월 {d}일까지 {dept}가 마친다.", "사업비 집행 내역 내부 검토는 매월 며칠까지, 어느 부서가 마쳐야 해?"),
    ("참여 학생 만족도 조사는 과정 종료 뒤 {d}일 안에 {dept}가 실시한다.", "참여 학생 만족도 조사는 과정이 끝나고 며칠 안에, 어느 부서가 해야 해?"),
]
ABSENT_Q = ["현장 실사 결과 보고서는 며칠 안에 어디에 내야 해?", "협약 변경 신청은 며칠 전까지 어디에 해야 해?",
            "사업 홍보물 검토는 며칠 안에 어느 부서가 마쳐야 해?"]


def make_longctx(rnd: random.Random, n: int, filler: str, chars: int) -> list[dict]:
    out = []
    for i in range(n):
        start = rnd.randrange(0, max(1, len(filler) - chars))
        base = filler[start:start + chars]
        absent = i >= n - 3
        if absent:
            needle, q, exp = "", ABSENT_Q[i % len(ABSENT_Q)], {"absent": True}
        else:
            tpl, q = NEEDLES[i % len(NEEDLES)]
            d, dept = rnd.randint(11, 27), rnd.choice(DEPTS)
            needle = "[붙임 지침] " + tpl.format(d=d, dept=dept)
            exp = {"absent": False, "day": str(d), "dept": dept}
        depth = [0.1, 0.3, 0.5, 0.7, 0.9][i % 5]
        cut = int(len(base) * depth)
        cut = base.rfind("\n", 0, cut) + 1 or cut
        doc = base[:cut] + (needle + "\n" if needle else "") + base[cut:]
        user = (f"[자료]\n{doc}\n\n[질문]\n{q} 자료에 있는 내용만으로 답하고, 자료에 없으면 없다고 말해 줘.")
        out.append({"id": f"lc-{i:02d}", "kind": "longctx", "depth": depth, "messages": [{"role": "system", "content": SYSTEM},
                                                                                        {"role": "user", "content": user}], "expect": exp})
    return out


def make_conflict(rnd: random.Random, n: int) -> list[dict]:
    out = []
    for i in range(n):
        prog = PROGRAMS[(i + 3) % len(PROGRAMS)]
        vals = {"총사업비": rnd.choice(BUDGETS)[0], "참여 학생 수": f"{rnd.randint(60, 480)}명", "운영 기간": rnd.choice(PERIODS),
                "협력 기관 수": f"{rnd.randint(3, 19)}개", "운영 강좌 수": f"{rnd.randint(4, 31)}개"}
        given = rnd.sample(FIELDS, 3)
        missing = [f for f in FIELDS if f not in given]
        material = "\n".join(f"- {f}: {vals[f]}" for f in given)
        user = (f"[자료] 「{prog}」 사업 메모\n{material}\n- 비고: 세부 운영 방식은 사업단 회의에서 정함\n\n"
                f"[요청] 아래 다섯 칸 표를 채워 줘: {', '.join(FIELDS)}. 자료에 없는 값은 지어내지 말고 「확인 필요」로 적어.")
        out.append({"id": f"cf-{i:02d}", "kind": "conflict", "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                    "expect": {"given": {f: vals[f] for f in given}, "missing": missing}})
    return out


# ── 채점 ──
def field_line(text: str, field: str) -> str:
    for line in text.splitlines():
        if field.replace(" ", "") in line.replace(" ", ""):
            return line
    return ""


def grade(case: dict, text: str) -> dict:
    e, k = case["expect"], case["kind"]
    ctx = " ".join(m["content"] for m in case["messages"])
    extra = []                                    # 같은 값의 단위 바꿈(9천5백만 원 = 95,000,000)은 지어낸 숫자가 아니다
    for key in ("budget", "old_budget"):
        if e.get(key):
            won = int(e[key][1].replace(",", ""))
            extra.append(f"{won} {won // 10_000} {won // 1_000_000} {won // 1_000}")
    for v in (e.get("given") or {}).values():
        b = next((x for x in BUDGETS if x[0] == v), None)
        if b:
            won = int(b[1].replace(",", ""))
            extra.append(f"{won} {won // 10_000} {won // 1_000_000} {won // 1_000}")
    inv = s210.invented_numbers(text, case["messages"] + [{"content": " ".join(extra)}])
    if k == "multiturn":
        b = tuple(e["budget"])
        hits = {"대상": e["target"].replace(" ", "") in text.replace(" ", ""), "예산": budget_hit(text, b),
                "기간": period_hit(text, e["period"]), "담당": e["dept"] in text}
        stale = bool(e["old_budget"]) and budget_hit(text, tuple(e["old_budget"]))
        ok = all(hits.values()) and not stale
        return {"ok": ok, "hits": hits, "stale_budget": stale, "invented": len(inv)}
    if k == "longctx":
        unknown = bool(s210.UNKNOWN.search(text)) or bool(re.search(r"없습니다|없음|찾을 수 없|나와 있지 않|언급되지 않|명시되어 있지 않|포함되어 있지 않|포함되지 않|기재되어 있지 않|담겨 있지 않|나오지 않", text))
        if e["absent"]:
            return {"ok": unknown and not inv, "unknown": unknown, "invented": len(inv)}
        day = bool(re.search(rf"(?<!\d){e['day']}\s*일", text))
        dept = e["dept"] in text
        return {"ok": day and dept, "day": day, "dept": dept, "invented": len(inv)}
    # conflict
    good = {f: bool(field_line(text, f)) and (
        budget_hit(field_line(text, f), next(x for x in BUDGETS if x[0] == v)) if f == "총사업비" else
        period_hit(field_line(text, f), v) if f == "운영 기간" else num_digits(v)[0] in num_digits(field_line(text, f)))
        for f, v in e["given"].items()}
    marked = {}
    for f in e["missing"]:
        line = field_line(text, f)
        cell = line.split(f, 1)[-1] if f in line else line
        marked[f] = bool(line) and ("확인 필요" in line or "확인필요" in line) and not re.search(r"\d", cell.replace("확인 필요", ""))
    return {"ok": all(good.values()) and all(marked.values()), "given_ok": sum(good.values()), "missing_marked": sum(marked.values()),
            "invented": len(inv), "ctx_numbers": len(s210.numbers(ctx))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="맥락 이해 시험(여러 턴·긴 자료·「없으면 비워 둬」)")
    ap.add_argument("--models", required=True, help="「팔:서빙 이름=주소」 쉼표(210 과 같은 꼴)")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--filler-dir", required=True, help="공개 공고·기본계획 hwpx 폴더(긴 자료 바탕)")
    ap.add_argument("--n", type=int, default=10, help="갈래마다 사례 수")
    ap.add_argument("--filler-chars", type=int, default=16000)
    ap.add_argument("--seed", type=int, default=20261024)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args(argv)
    arms, urls, served = s210.parse_models(a.models, a.base_url)
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    cases_path = out / "cases.json"
    if cases_path.exists():                      # 팔을 나중에 더해도 같은 사례
        cases = json.loads(cases_path.read_text(encoding="utf-8"))
    else:
        files = sorted(Path(a.filler_dir).expanduser().glob("*.hwpx"))
        filler = "\n".join(t for t in (hwpx_text(f) for f in files if re.search(r"공고|기본계획", f.name)) if t)
        if len(filler) < a.filler_chars + 1000:
            raise SystemExit(f"바탕 글이 모자람: {len(filler)}자")
        rnd = random.Random(a.seed)
        cases = make_multiturn(rnd, a.n) + make_longctx(rnd, a.n, filler, a.filler_chars) + make_conflict(rnd, a.n)
        cases_path.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    lock = threading.Lock()
    if not a.report_only:
        for arm in arms:
            path = out / f"gen-{arm}.jsonl"
            done = s210.read_cache(path)
            todo = [c for c in cases if c["id"] not in done]
            print(f"[context] {arm}: {len(done)} 있음, {len(todo)} 남음", flush=True)

            def one(c, arm=arm, path=path):
                g = s210.chat(urls[arm], served[arm], c["messages"], a.max_tokens, a.temperature, seed=a.seed)
                s210.append(path, {"key": c["id"], "arm": arm, "model": served[arm], **g}, lock)
                return g

            with ThreadPoolExecutor(max_workers=max(1, min(a.concurrency, 4))) as ex:
                for k, g in enumerate(ex.map(one, todo), 1):
                    if k % 10 == 0 or g.get("error"):
                        print(f"  {arm} {k}/{len(todo)} {g.get('sec')}초 {g.get('error') or ''}", flush=True)
    rep = {"n_cases": len(cases), "arms": {}, "rows": []}
    for arm in arms:
        gens = s210.read_cache(out / f"gen-{arm}.jsonl")
        per = {}
        for c in cases:
            if c["id"] not in gens:
                continue
            g = grade(c, gens[c["id"]]["text"])
            rep["rows"].append({"id": c["id"], "kind": c["kind"], "arm": arm, **g,
                                "in_tokens": (gens[c["id"]].get("usage") or {}).get("prompt_tokens", 0), "sec": gens[c["id"]].get("sec")})
            per.setdefault(c["kind"], []).append(g)
        rep["arms"][arm] = {k: {"n": len(v), "ok": sum(x["ok"] for x in v), "rate": round(sum(x["ok"] for x in v) / len(v), 3),
                                "invented_any": round(sum(x["invented"] > 0 for x in v) / len(v), 3)} for k, v in per.items()}
    lc = [r for r in rep["rows"] if r["kind"] == "longctx"]
    rep["longctx_in_tokens"] = [min((r["in_tokens"] for r in lc), default=0), max((r["in_tokens"] for r in lc), default=0)]
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["| 팔 | 갈래 | 통과/n | 비율 | 숫자 지어낸 답 비율 |", "|---|---|---|---|---|"]
    for arm, d in rep["arms"].items():
        for k, x in d.items():
            L.append(f"| {arm} | {k} | {x['ok']}/{x['n']} | {x['rate']} | {x['invented_any']} |")
    L.append(f"\n긴 자료 입력 토큰 {rep['longctx_in_tokens'][0]}~{rep['longctx_in_tokens'][1]}")
    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L), flush=True)
    print("CONTEXT_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
