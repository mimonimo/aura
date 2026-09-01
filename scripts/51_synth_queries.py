"""합성 질의 생성 배치 (야간 자동화 / model-plan §1 Embed 데이터 파이프라인의 축소판).

규정 저장소의 각 조각에 대해 vLLM으로 검색 질의 3종(실무형·요구사항형·키워드형)을
생성해 JSONL로 쌓는다. 재시작해도 이어서 돈다(이미 처리한 조각은 건너뜀).
/tmp/stop-overnight 파일이 생기면 우아하게 종료한다.

실행(Spark): .venv/bin/python scripts/51_synth_queries.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from zzaimy.app.db import Database

OUT = Path("data/interim/synth_queries.jsonl")
STOP = Path("/tmp/stop-overnight")

_PROMPT = """다음은 교내 규정·지침 문서의 한 조각이다. 행정 담당자가 이 조각을 찾으려고
검색할 법한 질의를 3개 만들어라.
- practical: 실무형 자연어 질문
- requirement: 요구사항 확인형 질문
- keyword: 짧은 키워드형 질의

규칙: 반드시 아래 본문에 실제로 있는 내용으로만 만들어라. 본문이 표지·목차 등
실질 내용이 없는 조각이면 세 값을 모두 빈 문자열로 하라.

출처: {title} / {heading}
본문:
{content}"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "practical": {"type": "string"},
        "requirement": {"type": "string"},
        "keyword": {"type": "string"},
    },
    "required": ["practical", "requirement", "keyword"],
}


def main() -> None:
    from zzaimy.generate.client import VllmClient, _strip_fences

    db = Database("data/platform/platform.db")
    chunks = db.list_regulation_chunks()
    done: set[int] = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["chunk_id"])
            except (json.JSONDecodeError, KeyError):
                pass
    todo = [c for c in chunks if c["id"] not in done]
    print(f"조각 {len(chunks)}개 중 {len(todo)}개 생성 예정 (완료 {len(done)})", flush=True)

    client = VllmClient()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    with OUT.open("a", encoding="utf-8") as f:
        for i, c in enumerate(todo):
            if STOP.exists():
                print("stop 파일 감지 — 종료", flush=True)
                break
            try:
                resp = client.client.chat.completions.create(
                    model=client.model,
                    messages=[{
                        "role": "user",
                        "content": _PROMPT.format(
                            title=c["reg_title"], heading=c["heading"],
                            content=c["content"][:1500],
                        ),
                    }],
                    temperature=0.7,
                    max_tokens=300,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {"name": "queries", "schema": _SCHEMA},
                    },
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
                data = json.loads(_strip_fences(resp.choices[0].message.content or ""))
                if not any(str(v).strip() for v in data.values()):
                    ok += 1  # 실질 내용 없음 — 정상 스킵
                    continue
                f.write(json.dumps({"chunk_id": c["id"], **data}, ensure_ascii=False) + "\n")
                f.flush()
                ok += 1
            except Exception as e:
                fail += 1
                print(f"조각 {c['id']} 실패: {type(e).__name__}", flush=True)
                time.sleep(2)
            if (i + 1) % 25 == 0:
                print(f"진행 {i + 1}/{len(todo)} (성공 {ok}, 실패 {fail})", flush=True)
    print(f"완료: 성공 {ok}, 실패 {fail}", flush=True)


if __name__ == "__main__":
    main()
