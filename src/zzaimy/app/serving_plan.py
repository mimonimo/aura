"""계획서 모델 4종이 지금 어디서 어떤 상태로 돌고 있는가 — 계획 대비 실제.

왜 따로 두는가: 모델은 세 곳에 걸쳐 있다. 서빙 서비스(임베딩·리랭커), 생성 서버의 모델 목록
(문서 작업·추출·이미지 판독), 그리고 장비에 놓인 가중치(학습본). 화면마다 다른 곳을 보면
"27B 를 받아 놨는데 왜 안 쓰나" 같은 어긋남이 생긴다. 한 곳에서 모아 본다.

계획(docs/model-plan.md): ①Embed KURE-v1 ②Rerank bge-reranker-v2-m3
③Writer Qwen3.8-27B ④Extract Qwen3-4B. 학습은 DGX, 서빙은 토르가 맡는다.
"""
from __future__ import annotations

import os

PLAN = [
    {"key": "embed", "name": "①ZZAIMY-Embed", "base": "KURE-v1",
     "role": "문장 임베딩 — 조밀 검색", "kind": "service", "env": "ZZAIMY_EMBED_URL"},
    {"key": "rerank", "name": "②ZZAIMY-Rerank", "base": "bge-reranker-v2-m3",
     "role": "후보 재정렬", "kind": "service", "env": "ZZAIMY_RERANK_URL"},
    {"key": "answer", "name": "③ZZAIMY-Writer", "base": "Qwen3.8-27B",
     "role": "문서 작업 — 채팅·검토·초안", "kind": "chat"},
    {"key": "extract", "name": "④ZZAIMY-Extract", "base": "Qwen3-4B",
     "role": "실적 카드 추출", "kind": "planned",
     "note": "추출 경로는 아직 만들지 않았습니다 — 계획 단계"},
    {"key": "review", "name": "반입 검토", "base": "경량 모델",
     "role": "문서를 들일 때 요약·판정", "kind": "chat"},
    {"key": "vision", "name": "문서 이미지 판독", "base": "Qwen3.8-27B (③Writer, 멀티모달)",
     "role": "스캔·그림에서 글자 읽기 — 계획의 Writer 가 곧 판독 모델", "kind": "chat"},
    {"key": "vision_public", "name": "공개 자료 판독", "base": "외부 모델 허용",
     "role": "공개 수집 문서(국고 공고·외부 안내)만 — 지정이 없으면 위 판독 모델을 쓴다", "kind": "chat"},
]


def status(live_models=None) -> list[dict]:
    """계획 한 줄마다 (지금 쓰는 모델, 장비, 상태, 계획과 맞는지)를 채워 돌려준다.

    live_models(cid) 는 연결이 지금 내어 주는 모델 목록을 주는 함수다(화면에서 넘긴다).
    """
    from zzaimy.app import search_serving
    from zzaimy.generate import llm_connections as lc

    serving = {p["key"]: p for p in search_serving.status()}
    roles = {r["role"]: r for r in lc.roles_public()}
    conns = {c["id"]: c for c in lc.list_public()}
    active = next((c for c in conns.values() if c.get("active")), None)
    out = []
    for item in PLAN:
        row = dict(item, model="", where="", ok=False, detail="", matches_plan=False)
        if item["kind"] == "planned":
            row.update(model="", where="", ok=False, detail=item.get("note", ""))
            out.append(row)
            continue
        if item["kind"] == "service":
            part = serving.get(item["key"], {})
            row.update(model=part.get("model", ""), where=part.get("where", ""),
                       ok=bool(part.get("ok")), detail=part.get("detail", ""))
        else:
            r = roles.get(item["key"])
            conn = conns.get(r["id"]) if r and r.get("id") else active
            if conn is not None:
                model = (r.get("model") if r and r.get("id") else "") or conn.get("model") or ""
                names = [m["id"] for m in (live_models(conn["id"]) or {}).get("models", [])] \
                    if live_models else []
                row.update(model=model or "서버 기본", where=conn["name"],
                           ok=bool(names) or bool(conn.get("check_ok")),
                           detail="" if not names or not model or model in names
                                  else "이 서버에 그 모델이 지금 없습니다")
            else:
                row["detail"] = "지정된 서버가 없습니다"
        base = item["base"].lower().replace("-", "").replace(".", "")
        got = (row["model"] or "").lower().replace("-", "").replace(".", "").replace(":", "")
        row["matches_plan"] = bool(got) and (base[:8] in got or got[:8] in base
                                             or "zzaimy" in got)
        out.append(row)
    return out


def training_box() -> str:
    """학습을 맡는 장비 — 계획상 DGX."""
    return os.environ.get("ZZAIMY_TRAIN_HOST", "교내 DGX")
