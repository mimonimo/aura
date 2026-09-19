"""플랫폼 조작 목록 — 에이전트가 말로 받은 일을 찾아 실행할 수 있게 한다.

왜 필요한가. 기능이 화면마다 흩어져 있으면 담당자가 어디로 가서 무엇을 눌러야
하는지 알 수 없다. 조작을 한 곳에 모아 두면 에이전트가 "이 일을 하시려는 것 같다"
하고 바로 내어 줄 수 있다.

어디까지 스스로 하는가. 교내에서 끝나고 되돌릴 수 있는 일은 바로 한다(auto).
밖으로 나가거나 무겁거나 되돌리기 어려운 일은 단추로 내어 주고 담당자가 누른다.
문서를 지우는 것처럼 파괴적인 일은 목록에 아예 넣지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Action:
    key: str
    label: str                       # 담당자가 읽는 말
    url: str                         # 경로. {doc_id} 같은 자리는 맥락에서 채운다
    method: str = "get"              # get 은 이동, post 는 실행
    needs: tuple[str, ...] = ()      # 채워야 하는 값
    words: tuple[str, ...] = ()      # 이 말이 나오면 이 일을 뜻한다
    changes: bool = False            # 상태를 바꾸는 일인가
    auto: bool = False               # 물어보지 않고 바로 해도 되는 일인가


REGISTRY: tuple[Action, ...] = (
    # ---- 문서 한 건에 대한 일 ----
    Action("doc.identity", "이 문서가 무엇에 관한 것인지 읽기",
           "/doc/{doc_id}/identity", "post", ("doc_id",),
           ("정체", "무슨 문서", "사업", "어떤 문서", "파악"), True, auto=True),
    Action("doc.analyze", "이 문서 맥락 다시 분석",
           "/doc/{doc_id}/analyze", "post", ("doc_id",),
           ("분석", "검토", "다시 읽", "재처리"), True, auto=True),
    Action("doc.draft", "이 문서로 초안 만들기",
           "/doc/{doc_id}/draft", "post", ("doc_id",),
           ("초안", "작성", "만들어", "계획서", "사업계획서", "써줘"), True, auto=True),
    Action("doc.open", "문서 화면 열기", "/doc/{doc_id}", "get", ("doc_id",),
           ("열어", "보여", "확인")),

    Action("plan.start", "이 공고로 사업 준비 시작",
           "/doc/{doc_id}/plan-start", "post", ("doc_id",),
           ("사업 준비", "준비해", "프로젝트 만들", "프로젝트 생성",
            "착수", "시작해"), True, auto=True),

    # ---- 문서 모음 ----
    Action("criteria.list", "기준 문서 목록", "/criteria", "get", (),
           ("기준", "규정", "지침", "학칙")),
    Action("docs.list", "접수 문서함", "/", "get", (), ("문서함", "접수", "목록")),
    Action("graph.open", "지식 그래프", "/graph", "get", (),
           ("그래프", "연관", "관계")),

    # ---- 모델과 연결 ----
    Action("llm.page", "모델 연결 화면", "/dev/train", "get", (),
           ("모델", "연결", "서버", "학습", "gpu")),
    Action("llm.catalog", "모델 목록 새로 받기", "/dev/llm/{cid}/catalog", "post",
           ("cid",), ("모델 목록", "갱신", "새로고침"), True),
    Action("llm.test", "연결 확인", "/dev/llm/{cid}/test", "post", ("cid",),
           ("연결 확인", "접속", "살아"), True, auto=True),

    # ---- 외부 참조 ----
    Action("egress.page", "외부 참조 창구", "/dev/egress", "get", (),
           ("외부", "참조", "반출", "내보내")),
    Action("egress.on", "외부 전송 켜기", "/dev/egress/enable", "post", (),
           ("외부 전송", "외부 켜", "켜줘", "허용"), True),
    Action("egress.off", "외부 전송 끄기", "/dev/egress/disable", "post", (),
           ("외부 끄", "차단", "막아"), True),

    # ---- 자료 관리 ----
    Action("nas.page", "문서 가져오기", "/dev/nas", "get", (),
           ("가져오", "반입", "폴더", "공유")),
    Action("reindex", "검색 색인 다시 만들기", "/dev/reindex", "post", (),
           ("색인", "재색인", "검색 다시"), True),
    Action("pii.scan", "개인정보 잔여 검사", "/dev/pii/scan", "post", (),
           ("개인정보", "마스킹", "잔여"), True),
    Action("data.page", "학습 데이터 만들기", "/dev/data", "get", (),
           ("학습 데이터", "데이터 공방", "예시")),
)

_WORD = re.compile(r"[0-9A-Za-z가-힣]+")


def _fill(url: str, ctx: dict) -> str | None:
    """경로의 빈자리를 맥락으로 채운다. 못 채우면 None."""
    out = url
    for name in re.findall(r"\{(\w+)\}", url):
        val = ctx.get(name)
        if val in (None, ""):
            return None
        out = out.replace("{" + name + "}", str(val))
    return out


def context_from_page(page: str) -> dict:
    """지금 보고 있는 화면에서 얻을 수 있는 값 — 문서 번호 등."""
    ctx: dict = {}
    m = re.match(r"^/doc/(\d+)", page or "")
    if m:
        ctx["doc_id"] = int(m.group(1))
    return ctx


def _score(action: Action, words: set[str], question: str) -> int:
    """말과 얼마나 맞는지 — 구절이 통째로 들어 있으면 더 높게 본다."""
    hit = 0
    for w in action.words:
        if " " in w:
            if w in question:
                hit += 3
        elif w in words:
            hit += 2
        elif w in question:
            hit += 1
    return hit


def suggest(question: str, page: str = "", ctx: dict | None = None,
            limit: int = 4) -> list[dict]:
    """말과 화면을 보고 지금 할 수 있는 일을 고른다.

    말과 맞는 것이 우선이고, 없으면 지금 화면에서 흔히 하는 일을 낸다.
    """
    q = (question or "").lower()
    words = set(_WORD.findall(q))
    merged = dict(context_from_page(page))
    merged.update(ctx or {})

    scored: list[tuple[int, Action, str]] = []
    for a in REGISTRY:
        url = _fill(a.url, merged)
        if url is None:
            continue
        s = _score(a, words, q)
        if s:
            scored.append((s, a, url))
    scored.sort(key=lambda t: -t[0])

    if not scored and merged.get("doc_id"):
        # 말로는 못 알아들었지만 문서를 보고 있다면 흔한 일을 낸다
        for a in REGISTRY:
            if a.key.startswith("doc.") and (url := _fill(a.url, merged)):
                scored.append((0, a, url))

    out: list[dict] = []
    for _, a, url in scored[:limit]:
        out.append({"key": a.key, "label": a.label, "url": url,
                    "method": a.method, "changes": a.changes, "auto": a.auto,
                    "ctx": dict(merged)})
    return out
