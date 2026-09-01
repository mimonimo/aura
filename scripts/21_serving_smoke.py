"""vLLM 서빙 스모크 + 간단 처리량 측정 (W1-W2 TASK-02).

OpenAI 호환 엔드포인트에 스트리밍 요청을 보내 첫 토큰 지연(TTFT)과
생성 토큰/초를 측정한다. 측정 조건(배치 1, max_tokens, 프롬프트 길이)을
함께 출력한다 — 조건 없는 수치는 기록하지 않는다.

실행(Spark): .venv/bin/python scripts/21_serving_smoke.py --base-url http://localhost:8000/v1
"""

from __future__ import annotations

import argparse
import statistics
import time

from openai import OpenAI

PROMPT = "국고사업 계획서의 일반적인 구성 요소를 다섯 가지 항목으로 요약하라."


def measure_once(client: OpenAI, model: str, max_tokens: int) -> tuple[float, float, int]:
    t0 = time.perf_counter()
    ttft = None
    n_tokens = 0
    stream = client.completions.create(
        model=model,
        prompt=PROMPT,
        max_tokens=max_tokens,
        temperature=0.0,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].text:
            if ttft is None:
                ttft = time.perf_counter() - t0
            n_tokens += 1
    total = time.perf_counter() - t0
    assert ttft is not None, "토큰이 하나도 오지 않았다"
    gen_rate = (n_tokens - 1) / (total - ttft) if total > ttft and n_tokens > 1 else 0.0
    return ttft, gen_rate, n_tokens


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    client = OpenAI(base_url=args.base_url, api_key="dummy")
    model = client.models.list().data[0].id
    print(f"model: {model}")

    # 웜업 1회 (측정 제외)
    measure_once(client, model, 32)

    ttfts, rates = [], []
    for i in range(args.runs):
        ttft, rate, n = measure_once(client, model, args.max_tokens)
        ttfts.append(ttft)
        rates.append(rate)
        print(f"run {i + 1}: TTFT {ttft * 1000:.0f}ms · {rate:.1f} tok/s · {n} tokens")

    print(
        f"\n조건: 배치 1, 스트리밍, max_tokens={args.max_tokens}, temperature=0, "
        f"단일 동시 요청, 웜업 1회 제외, {args.runs}회 측정"
    )
    print(
        f"결과: TTFT 중앙값 {statistics.median(ttfts) * 1000:.0f}ms · "
        f"생성 속도 중앙값 {statistics.median(rates):.1f} tok/s"
    )


if __name__ == "__main__":
    main()
