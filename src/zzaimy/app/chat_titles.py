"""대화 제목 짧게 짓기(11-12) — 새 대화의 제목은 첫 요청 앞 60자라 목록에서 늘 잘렸다(「올린 「03 사업계획서 작성 서식」으로 한글 작업본을 만…」).

새 대화를 만들면 뒤에서 검토 연결(서빙 장비 둘째)에 요청을 18자 안팎 명사구 제목으로 물어 바꾼다. 답을 기다리지 않고, 실패하면 처음 제목 그대로.
담당자가 그사이 제목을 바꿨으면(지금 제목이 처음 제목과 다르면) 손대지 않는다. 프로젝트 대화의 「[프로젝트 이름 앞부분] 」 접두어는 지킨다.
"""
from __future__ import annotations

import logging
import re
import threading

log = logging.getLogger(__name__)

PROMPT = ("다음은 대학 행정 업무 도우미에게 보낸 첫 요청이다. 대화 목록에 보일 제목을 한국어 명사구 하나로 짓는다.\n"
          "- 8~20자, 문장 끝·따옴표·마침표 없이. 무엇을 하는 대화인지(문서 이름·업무)가 드러나게.\n"
          "- 요청에 없는 말을 지어 넣지 않는다.\n"
          "요청:\n{q}\n\n제목만 한 줄로 답한다:")
MAX = 30
_PREFIX = re.compile(r"^\s*(\[[^\]]{1,20}\])\s*")


def clean(t: str) -> str:
    """모델 답 → 제목 한 줄(따옴표·머리표·끝 마침표 뗌). 못 쓰는 답이면 빈 글."""
    t = (t or "").strip().splitlines()[0] if (t or "").strip() else ""
    t = re.sub(r"^(제목\s*[:：]\s*)", "", t)
    t = t.strip(" \"'「」『』<>*#-·.。")
    if not t or len(t) > MAX or len(t) < 4:
        return ""
    return t


def shorten(db, sid: int, question: str, original: str, client=None) -> str:
    """제목을 지어 바꾼다 — 바꾼 제목(못 바꾸면 빈 글)."""
    q = re.sub(r"\s+", " ", question or "").strip()
    body = _PREFIX.sub("", original or "")
    if len(body) <= 22 or not q:                          # 이미 짧은 제목은 그대로
        return ""
    try:
        if client is None:
            from zzaimy.generate.client import VllmClient
            client = VllmClient(role="review", timeout=20, max_retries=0)
        r = client.client.chat.completions.create(model=client.model, temperature=0.0, max_tokens=40, extra_body=client._extra,
                                                  messages=[{"role": "user", "content": PROMPT.format(q=q[:600])}])
        title = clean(r.choices[0].message.content or "")
    except Exception as e:                                 # 서버가 없거나 늦으면 처음 제목 그대로
        log.info("대화 제목 짓기 건너뜀: %s", e)
        return ""
    if not title:
        return ""
    s = db.get_chat_session(int(sid)) or {}
    if (s.get("title") or "") != (original or "")[:60]:   # 그사이 담당자가 바꿨다
        return ""
    m = _PREFIX.match(original or "")
    new = f"{m.group(1)} {title}" if m else title
    db.rename_chat_session(int(sid), new)
    return new


def schedule(db, sid: int, question: str, original: str) -> None:
    """뒤에서 짓는다 — 요청 처리를 붙잡지 않는다. ZZAIMY_CHAT_TITLES=0 이면 끈다(모델 서버 없는 시험)."""
    import os
    if os.environ.get("ZZAIMY_CHAT_TITLES", "1") == "0":
        return
    threading.Thread(target=shorten, args=(db, sid, question, original), daemon=True, name=f"chat-title-{sid}").start()
