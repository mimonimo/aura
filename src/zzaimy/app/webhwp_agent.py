"""채팅 에이전트의 한글 기안기 도구 — 편집기를 「웹 한글 기안기」 로 고른 사람의 대화에서 구글 문서 도구 대신 쓴다(흐름은 같고 편집기만 다르다).

도구(판단은 에이전트, 코드는 실행·관문·기록 — agent-not-pipeline):
- hwp_forms {query}: 쓸 양식 후보 — 이 프로젝트의 한글 양식(사업이 준 서식)이 먼저, 그다음 한글 문서함의 잠긴 양식(공문·국고 사업·학교 양식).
- hwp_form_create {purpose, kind, must, title, cover}: 맞는 양식이 없을 때만 — 같은 모델이 목적에 맞는 양식을 설계하고 코드가 공통 양식과 같은
  빌더로 만들어 작업본으로 연다(app/webhwp_form_create). 이 대화에서 hwp_forms 로 먼저 찾아본 뒤에만 돈다.
- hwp_start {form, title}: 고른 양식으로 작업본을 부서 디렉터리(「작업/<프로젝트>」)에 만들고 이 대화에 잇는다. 한글 원본은 hwpx 로 바꾸고 쓸 자리 필드를 박는다.
- hwp_fields {}: 작업본의 필드와 채움 상태(빈 필드가 먼저).
- hwp_fill {values: {필드: 값}}: 칸·표 필드 값 넣기.
- hwp_write {field, text}: 절 필드에 본문 쓰기(번호 문단 + 마크다운 표). 같은 절을 다시 쓰면 앞 내용을 지우고 새로.
- 편집 도구(webhwp_edit_ops.TOOLS — hwp_page_setup·hwp_table_insert·hwp_table_view·hwp_table_edit·hwp_page_number·hwp_header_footer·
  hwp_page_break·hwp_text_style·hwp_footnote·hwp_picture·hwp_replace·hwp_undo·hwp_live): 기안기 기능을 파일 직접 고치기·열린 기안기 명령으로.
- delegate_section {section, gist, form, length} 또는 {sections: […]}: 긴 절을 같은 모델의 깨끗한 문맥(서브)에 맡긴다 — 여럿이면 동시에(app/delegate, ADR-0065 4번, ZZAIMY_DELEGATE).
- hwp_revise {field, instruction}: 이미 쓴 절을 담당자 지시대로 고친다 — 서브가 지시를 점검표로 만들고 코드가 셀 수 있는 것만 점검(app/delegate.Reviser, ADR-0068).
절 쓰기는 메인이 직접 쓰든 서브가 쓰든 같은 관문(check_body)을 지난다 — 양식 예시 글자(○○·000), 근거에 없는 수치, 길이 상한.
남은 문맥이 모자란데 긴 본문을 직접 쓰려 하면 거절하고 절 맡기기를 안내한다(관문만 — 맡길지는 에이전트가 정한다).
쓰기는 공동 편집(webhwp_coedit, ADR-0070)의 한 참여자로 한다 — 칸 값은 사람과 같은 길(임대·판 번호·알림)로 넣고 바로 임대를 놓는다,
절 본문은 서버에서 파일을 고치고 판을 남긴다(webhwp.save, 고친 사람 「AURA」). 사람이 임대를 쥔 칸(지금 그 칸을 쓰는 중)은 쓰지 않고 알린다 —
다른 칸은 사람이 문서를 열어 두었어도 쓴다(열린 화면에 바로 들어간다).
쓰는 동안 「AURA 작성 중 · 필드」 를 남겨 편집 화면이 AURA 표시를 띄운다(webhwp_activity:<id>, 알림 흐름 「aura」, 마지막 쓰기 뒤 aura_ttl 초).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from zzaimy.app import storage, webhwp


def bound(db, session_id: int) -> int | None:
    v = (db.get_setting(f"chat_hwp:{int(session_id)}", "") or "").strip()
    return int(v) if v.isdigit() else None


def files_of(db, session_id: int) -> list[int]:
    """이 대화로 가져온 한글 파일(가져오기·양식으로 시작·완성본) — 문서 패널 「작업 문서」 에 서는 목록. 지금 이어진 것이 맨 앞."""
    try:
        ids = [int(x) for x in json.loads(db.get_setting(f"chat_hwp_files:{int(session_id)}", "[]") or "[]")]
    except (ValueError, TypeError):
        ids = []
    cur = bound(db, session_id)
    if cur and cur not in ids:
        ids.insert(0, cur)
    return [i for i in ids if webhwp.get(db, i)]


def attach(db, session_id: int, fid: int) -> None:
    """한글 파일을 이 대화의 작업 문서로 잇는다(지금 작업본으로 두고 목록 맨 앞에)."""
    ids = [i for i in files_of(db, session_id) if i != int(fid)]
    db.set_setting(f"chat_hwp_files:{int(session_id)}", json.dumps([int(fid), *ids][:30]))
    db.set_setting(f"chat_hwp:{int(session_id)}", str(int(fid)))


def detach(db, session_id: int, fid: int) -> None:
    """목록에서 뺀다(파일은 부서 폴더에 그대로). 지금 작업본이었으면 다음 것으로."""
    ids = [i for i in files_of(db, session_id) if i != int(fid)]
    db.set_setting(f"chat_hwp_files:{int(session_id)}", json.dumps(ids))
    if bound(db, session_id) == int(fid):
        db.set_setting(f"chat_hwp:{int(session_id)}", str(ids[0]) if ids else "")


def activity(db, fid: int) -> dict:
    try:
        a = json.loads(db.get_setting(f"webhwp_activity:{int(fid)}", "{}") or "{}")
    except ValueError:
        return {}
    from zzaimy.app import webhwp_coedit as ce
    return a if a and time.time() - float(a.get("at") or 0) < ce.conf(db, "aura_ttl") else {}


def _too_long(v: str) -> bool:
    v = v.strip()
    return v.count("\n") >= 2 or bool(re.search(r"^\s*\|.*\|\s*$", v, re.M)) or len(v) > 150


def _placeholder(v: str) -> bool:
    """양식 예시 글자 — ○○·OO·ㅇㅇ·000·□□ 가 든 값(「○○대학교 총장」 같은)."""
    return bool(re.search(r"○○|OO|ㅇㅇ|□□|◯◯|(?<![\d,])000(?![\d,])|XXX|xxx", v))


BODY_SHARE = 0.25                 # 절 본문 하나의 길이 상한 — 문맥 한도(토큰)의 몫
LONG_SHARE = 0.04                 # 이보다 긴 본문(문맥 한도 몫)은 「긴 본문」 — 남은 문맥이 적으면 직접 쓰지 않고 맡긴다
LOW_HEADROOM = 0.40               # 메인의 남은 문맥 몫이 이 아래면 긴 본문을 직접 쓰지 않는다


def _ph_lines(text: str) -> list[str]:
    """절 본문에서 양식 예시 글자(○○·000 등)가 남은 줄."""
    return [ln.strip()[:60] for ln in (text or "").splitlines() if _placeholder(ln)]


def check_body(text: str, evidence: list[str] | None, limit: int) -> str | None:
    """절 본문 품질 관문 — 메인 직접 쓰기와 서브(절 맡기기)가 같은 것을 지난다. 통과면 None, 아니면 거절 까닭.
    - 양식 예시 글자(○○·OO·000·□□)가 남은 줄
    - 근거(요청·찾은 근거·작업본의 양식 글·담당자 글)에 없는 수치 — evidence 가 None 이면(근거를 모르는 호출부) 보지 않는다
    - 길이: 문맥 한도의 BODY_SHARE 를 넘는 본문"""
    from zzaimy.app import llm_budget
    from zzaimy.verify.numbers import mask_unsupported

    ph = _ph_lines(text)
    if ph:
        return ("양식 예시 글자(○○·000 등)가 남았다 — 모르는 값은 그 줄을 빼고 「확인 필요」 로 돌린다: " + " / ".join(ph[:4]))
    if llm_budget.estimate_tokens(text) > int(limit * BODY_SHARE):
        return f"본문이 너무 길다(문맥 한도의 {int(BODY_SHARE * 100)}% 넘음) — 절을 나눠 쓰거나 줄인다."
    if evidence is not None:
        _, bad = mask_unsupported(text, [e for e in evidence if e])
        if bad:
            # 막힌 수치가 든 줄을 함께 보여 준다 — 같은 본문을 되풀이하지 않고 그 줄만 고치게(일정이면 기간 안의 날짜를 지어내지 않고 단계 이름으로)
            lines = [ln.strip()[:70] for ln in (text or "").splitlines() if any(b in ln for b in bad[:8])][:3]
            return ("근거에 없는 수치: " + ", ".join(dict.fromkeys(bad[:8]))
                    + (" (든 줄: " + " / ".join(lines) + ")" if lines else "")
                    + " — 찾은 근거·담당자가 알려 준 값에 있는 수치만 쓴다. 그 줄만 고쳐 다시 쓴다: 모르는 값은 「확인 필요」 로 바꾸고, "
                      "일정은 담당자가 준 시작·끝만 날짜로 쓰고 그 사이는 단계 이름(준비·운영·평가 등)으로 나눈다. 같은 본문을 그대로 다시 내지 않는다.")
    return None


def length_hint(guide: str) -> str:
    """양식 안내 글의 분량 안내(「2쪽 이내」「500자 내외」) — 없으면 빈 글."""
    m = re.search(r"(\d+(?:\.\d+)?\s*(?:쪽|페이지|매|자|줄))\s*(?:이내|내외|정도|분량|이하)?", guide or "")
    return m.group(0).strip() if m else ""


# 작성방법 글이 요구하는 본문 요소 — 서식 일반의 낱말로 읽는다(특정 양식·사업의 규칙이 아니다)
_GUIDE_MARK = re.compile(r"작성\s*방법|작성\s*요령|작성\s*지침|작성\s*가이드|※\s*작성")
_NEEDS = (("표", re.compile(r"(?<![가-힣])표(?:로|에|를|와|\s|\(|$)|일정표|내역|연차별|월별|분기별|[가-힣]+\s*현황표")),
          ("도식", re.compile(r"도식|체계도|조직도|흐름도|모식도|구성도|그림|추진\s*체계")),
          ("출처", re.compile(r"출처")))


def guide_of(context: str) -> str:
    """필드 앞뒤 양식 글에서 작성방법 상자 글만 — 마지막 작성방법 표시부터 쓸 자리까지. 표시가 없으면 빈 글(앞 절 본문을 안내로 읽지 않게)."""
    head = (context or "").split("[이 자리에 쓴다]")[0]
    marks = list(_GUIDE_MARK.finditer(head))
    return head[marks[-1].start():].strip() if marks else ""


def needs_of(guide: str) -> list[str]:
    """작성방법이 요구한 요소(표·도식·출처)."""
    return [k for k, pat in _NEEDS if pat.search(guide or "")]


def body_shape(text: str) -> dict:
    """쓴 본문의 모양 — 표·도식 블록 수, 번호 체계(1) 가) (1)), 글자 수, 출처 표기."""
    from zzaimy.app import webhwp_write as ww
    items = ww.parse_body(text)
    plain = "\n".join(str(v) for k, v in items if k == "text")
    return {"tables": sum(1 for k, _ in items if k == "table"), "diagrams": sum(1 for k, _ in items if k == "diagram"),
            "numbered": bool(re.search(r"(^|\n)\s*(\d+\)|[가-하]\)|\(\d+\))", plain)),
            "chars": len(re.sub(r"\s+", "", text or "")),
            "sources": bool(re.search(r"출처|자료\s*[:：]|《[^》]+》", text or ""))}


def missing_needs(need: list[str], shape: dict) -> list[str]:
    have = {"표": shape.get("tables", 0) > 0, "도식": shape.get("diagrams", 0) > 0, "출처": shape.get("sources", False)}
    return [n for n in need if not have.get(n, True)]


class HwpTools:
    def __init__(self, db, base: Path, session_id: int, owner: str, dept: str, project: dict | None, project_docs):
        self.db, self.base, self.sid, self.owner = db, Path(base), int(session_id), owner
        self.dept = dept or webhwp.COMMON
        self.project = project
        self.project_docs = project_docs            # () -> 프로젝트·대화 문서 목록(dict)
        self.writes = 0
        self.evidence = None                        # () -> [근거 글] — 수치 관문에 쓰는 근거(요청·찾은 근거·담당자 글). None 이면 수치 관문 끔
        self.client = None                          # 문맥 한도를 읽을 연결(관문의 길이 상한) — None 이면 기본 한도
        self.delegating = False                     # 절 맡기기 도구가 켜졌는가(상태 글·직접 쓰기 관문의 안내)
        self.need_notes: dict[str, int] = {}        # 요구 요소 빠짐을 알린 횟수(필드마다 한 번만 — 고쳐 쓰기 1회)
        self.form_search = None                     # (query) -> [{"title","text"}] — 양식 설계 재료(공고의 작성·평가 항목) 찾기
        self.request = ""                           # 이번 요청 글(양식 설계의 담당자 인용)
        self.doc_visible = None                     # (doc) -> bool — 참고 양식으로 볼 반입 문서의 열람 범위(없으면 반입 문서는 참고하지 않음)
        self.revising = False                       # 고쳐 쓰기 도구(hwp_revise)가 켜졌는가(상태 글의 안내)
        self.delegate_cap = 1                       # 한 번에 맡길 수 있는 절 수(서브 연결 자리 수 — 상태 글의 안내)

    def limit(self) -> int:
        from zzaimy.app import llm_budget
        try:
            return llm_budget.context_len(self.client) if self.client is not None else int(os.environ.get("ZZAIMY_WRITER_CONTEXT", llm_budget.DEFAULT_CONTEXT))
        except Exception:
            return llm_budget.DEFAULT_CONTEXT

    # ── 상태 ──
    def fid(self) -> int | None:
        f = bound(self.db, self.sid)
        return f if f and webhwp.get(self.db, f) else None

    def _fields(self) -> list[dict]:
        from zzaimy.app import webhwp_write as ww
        f = webhwp.get(self.db, self.fid() or 0)
        return ww.fields(Path(f["path"]).read_bytes()) if f else []

    def state_lines(self) -> list[str]:
        fid = self.fid()
        if not fid:
            return ["편집기: 웹 한글 기안기 — 이 대화의 한글 작업본: 없음. 담당자가 문서를 쓰자고 하면 hwp_forms 로 양식을 고르고 hwp_start 로 만든다"
                    "(프로젝트에 사업이 준 한글 양식이 있으면 그것을 쓴다. 후보가 여럿이라 정하기 어렵거나 담당자가 정할 일이면 ask_user 로 묻는다). "
                    "찾아본 양식 가운데 목적에 맞는 것이 없으면 hwp_form_create 로 양식부터 설계해 만든다."]
        f = webhwp.get(self.db, fid)
        fl = self._fields()
        empty = [x["name"] for x in fl if not x["text"]]
        lines = [f"편집기: 웹 한글 기안기 — 이 대화의 한글 작업본: 「{Path(f['name']).stem}」(필드 {len(fl)}개, 빈 필드 {len(empty)}개)"]
        needs = self.needs_map()
        lines.append("필드(이름 — 상태): " + "; ".join(
            f"{x['name']} — {'비어 있음' if not x['text'] else '채움'}" + (f"(작성방법 요구: {'·'.join(needs[x['name']])})" if needs.get(x["name"]) and not x["text"] else "")
            for x in fl[:80]))
        held = webhwp.lock_holder(self.db, fid)
        if held and held["user"] != "AURA":
            lines.append(f"지금 {held['user']} 님도 이 작업본을 편집 중이다 — 칸은 그 화면에 바로 들어가니 그대로 쓴다.")
        if empty:
            lines.append("작성 순서: ① 담당자가 알려 준 값·프로젝트 정보(프로젝트 이름은 사업명 칸)로 채울 수 있는 칸을 hwp_fill 한 번에 모두 채운다 "
                         "② 빈 절을 하나씩 hwp_write 로 쓴다 ③ 그래도 모르는 값만 마지막에 ask_user 로 한 번에 묻는다 — 칸 하나 때문에 쓰기를 멈추지 않는다.")
        lines.append("쓰기·고쳐 쓰기 요청(「채워」「계속 써」「더 구체적으로」「표로」)이면 이번 요청에서 먼저 쓴다: 지금 있는 근거·담당자 값으로 hwp_write(또는 "
                     "delegate_section)를 하고, 모르는 값은 본문에 「확인 필요」 로 남긴 뒤 묻는다 — 값이 모자라다는 까닭으로 쓰지 않고 묻기만 하고 끝내지 않는다. "
                     + ("담당자가 이미 쓴 절을 고쳐 달라고 하면(더 구체적으로·표로·줄여·문체·항목 더하기) 다시 찾거나 묻지 말고 먼저 hwp_revise {field, instruction: 담당자 지시 그대로} "
                        "— 서브가 지금 글·지시·그 절의 근거를 보고 고친다(「N번 절」 은 필드 이름의 번호, 「일정은 표로」 처럼 절을 짚지 않으면 그 내용이 든 절)."
                        if self.revising else
                        "고쳐 쓰기는 같은 field 로 다시 쓰면 앞 내용이 바뀐다(「N번 절」 은 필드 이름의 번호로 짚는다)."))
        if self.delegating:
            lines += self.budget_lines(fl)
        lines.append("모양 고치기(페이지 설정·표 만들기·행·열·칸 합치기·쪽 번호·머리말·글꼴·각주·그림·찾아 바꾸기)는 hwp_page_setup·hwp_table_* 같은 "
                     "편집 도구로 — 표 번호는 hwp_tables, 칸 자리는 hwp_table_view 로 확인한다.")
        return lines

    def needs_map(self) -> dict[str, list[str]]:
        """필드마다 작성방법이 요구한 요소(표·도식·출처) — 작업본의 작성방법 상자 글에서 읽는다."""
        from zzaimy.app import webhwp_write as ww
        f = webhwp.get(self.db, self.fid() or 0)
        if not f:
            return {}
        try:
            ctx = ww.section_contexts(Path(f["path"]).read_bytes())
        except Exception:
            return {}
        return {k: n for k, v in ctx.items() if (n := needs_of(guide_of(v)))}

    def budget_lines(self, fl: list[dict] | None = None) -> list[str]:
        """절 맡기기 판단 재료 — 남은 문맥 %, 빈 절 수와 절별 분량 안내, 남은 단계 수(판단은 에이전트가 한다)."""
        from zzaimy.app import doc_agent, llm_budget
        from zzaimy.app import webhwp_write as ww
        fl = fl if fl is not None else self._fields()
        f = webhwp.get(self.db, self.fid() or 0)
        data = Path(f["path"]).read_bytes() if f else b""
        empty = [x["name"] for x in fl if x["kind"] == "절" and not x["text"]]
        hr = llm_budget.headroom()
        st = doc_agent.step_info()
        sizes = []
        for n in empty[:12]:
            try:
                h = length_hint(ww.section_context(data, n)) if data else ""
            except Exception:
                h = ""
            sizes.append(f"{n[2:]}({h or '분량 안내 없음'})")
        out = [("문맥: 남은 약 " + (f"{int(hr[0] * 100)}%(한도 {hr[1]} 토큰)" if hr else "알 수 없음(첫 단계)")
                + (f" · 남은 단계 {max(0, st[1] - st[0])}/{st[1]}" if st else "")
                + f" · 빈 절 {len(empty)}개")]
        if sizes:
            out.append("빈 절과 분량 안내: " + "; ".join(sizes) + (" …" if len(empty) > 12 else ""))
        out.append("절 맡기기(delegate_section): 긴 절·근거가 많은 절·남은 문맥이 적을 때 같은 모델에 깨끗한 문맥으로 그 절 하나를 맡긴다. "
                   "짧은 절·칸 몇 개·서식 고치기는 직접 한다. 돌아온 보고(쓴 값·확인 필요)를 보고 모자라면 한 번 다시 맡긴다.")
        if self.delegate_cap > 1:
            low = hr is not None and hr[0] < 0.5
            out.append(f"동시 맡기기: 서로 다른 빈 절을 sections 로 묶어 한 번에 {self.delegate_cap}개까지 맡기면 동시에 쓴다(한 걸음에 여러 절)."
                       + (f" 지금 빈 절 {len(empty)}개" + (" · 남은 문맥이 절반 아래" if low else "")
                          + " — 직접 하나씩 쓰면 이번 요청의 단계·문맥 안에 다 못 쓴다. 빈 절을 묶어 맡기는 것이 낫다." if len(empty) >= 4 or low else ""))
        return out

    def step_budget(self) -> int:
        """이번 요청의 걸음 한도 — 기본 20, 작업본의 빈 절이 많으면 늘린다(절 하나에 쓰기 한 걸음 + 가끔 찾기), 최대 48."""
        try:
            empty = sum(1 for x in self._fields() if x["kind"] == "절" and not x["text"]) if self.fid() else 20
        except Exception:
            empty = 12
        return max(20, min(48, 12 + empty))

    def resolve(self, name: str, kinds: tuple[str, ...] = ("칸", "표", "절")) -> str | None:
        """에이전트가 쓴 필드 이름 → 작업본의 실제 이름. 접두사(칸:·절:·표n:)를 빠뜨렸거나 띄어쓰기가 달라도 하나로 맞으면 그것."""
        fl = [x["name"] for x in self._fields()]
        if name in fl:
            return name
        key = lambda n: re.sub(r"\s+", "", re.sub(r"^(칸|절|표\d+):", "", n))
        hits = [n for n in fl if key(n) == key(name) and (n.split(":")[0].rstrip("0123456789") in kinds)]
        return hits[0] if len(hits) == 1 else None

    # ── 도구 ──
    def forms(self, a: dict) -> str:
        from zzaimy.app import webhwp_form_create
        q = str(a.get("query") or "").strip()
        webhwp_form_create.mark_seen(self.db, self.sid)
        try:                                                # 공통 양식이 아직 없으면 먼저 만든다(정의가 같으면 그대로 — 빠르다)
            from zzaimy.app import webhwp_templates
            webhwp_templates.ensure_all(self.db, self.base)
        except Exception:
            pass
        out = []
        for d in self.project_docs() or []:
            if storage.original_ext(d) in (".hwp", ".hwpx"):
                out.append(f"doc:{d['id']} 「{storage.title_of(d.get('filename') or '')[:50]}」(이 프로젝트 문서{' · 양식' if d.get('kind') == 'form' else ''})")
        words = [w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", q)]
        scored = []
        for r in self.db.list_files(kind="webhwp", limit=8000):
            if not webhwp.locked(self.db, r["id"]) or not Path(r["path"]).exists():
                continue
            name = Path(r["name"]).stem
            group = webhwp.folder_of(self.base, r["path"])
            hit = sum(1 for w in words if w in name or w in group)
            if words and not hit:
                continue
            pri = 0 if group.startswith("공통 양식") else 1
            scored.append((-hit, pri, name, f"tpl:{r['id']} 「{name}」({group})"))
        scored.sort()
        out += [x[3] for x in scored[:15]]
        return (("양식 후보(form 에 앞의 id 를 쓴다 — 목적에 맞는 것이 없으면 hwp_form_create 로 새로 설계한다):\n" + "\n".join(out)) if out
                else "맞는 양식이 없다 — 검색어를 바꿔 한 번 더 찾거나, 목적에 맞는 양식을 hwp_form_create 로 설계해 만든다.")

    def form_create(self, a: dict) -> str:
        """맞는 양식이 없을 때 양식 설계·만들기(app/webhwp_form_create) — 설계는 같은 모델, 관문·빌드·기록은 코드."""
        from zzaimy.app import webhwp_form_create
        return webhwp_form_create.FormCreator(self.client, self.db, self.sid, self, self.request, search=self.form_search,
                                              visible=self.doc_visible).run(a)

    def start(self, a: dict) -> str:
        from zzaimy.app import webhwp_convert
        form = str(a.get("form") or "").strip()
        title = re.sub(r"[\\/:*?\"<>|]", " ", str(a.get("title") or "").strip())[:60]
        folder = f"작업/{storage.safe_name(self.project['name'])[:40]}" if self.project else "작업"
        if form.startswith("tpl:") and form[4:].isdigit():
            t = webhwp.get(self.db, int(form[4:]))
            if not t or not webhwp.locked(self.db, t["id"]):
                return "그런 양식이 없다 — hwp_forms 결과의 id 를 쓴다."
            data, fields = webhwp_convert.work_form(Path(t["path"]).read_bytes(), ".hwpx")
            name = title or Path(t["name"]).stem.replace(" 공통 양식", "")
            src = f"양식복사:{t['id']}"
        elif form.startswith("doc:") and form[4:].isdigit():
            d = next((x for x in self.project_docs() or [] if int(x["id"]) == int(form[4:])), None)
            if not d:
                return "이 대화에서 볼 수 있는 문서가 아니다 — hwp_forms 결과의 id 를 쓴다."
            from zzaimy.app.webhwp_routes import _original_bytes

            class _St:
                db = self.db
            data, fields = webhwp_convert.work_form(_original_bytes(_St, d), storage.original_ext(d))
            name = title or (storage.title_of(d.get("filename") or "") + " 작업본")
            src = f"doc:{d['id']}"
        else:
            return "form 은 hwp_forms 결과의 id(tpl:… 또는 doc:…)다."
        fid = webhwp.add(self.db, self.base, self.dept, f"{name}.hwpx", data, owner=self.owner, source=src, folder=folder)
        m = webhwp.meta(self.db, fid)
        m["session"] = self.sid
        self.db.set_setting(f"webhwp_meta:{fid}", json.dumps(m, ensure_ascii=False))
        attach(self.db, self.sid, fid)
        self.writes += 1
        kinds = {}
        for x in fields:
            kinds[x["kind"]] = kinds.get(x["kind"], 0) + 1
        return (f"작업본 「{name}」 을 {self.dept}/{folder} 에 만들고 이 대화에 이었다(필드 {len(fields)}개: "
                + ", ".join(f"{k} {v}" for k, v in kinds.items()) + "). hwp_fields 로 빈 필드를 보고 채운다.")

    def fields(self, a: dict) -> str:
        if not self.fid():
            return "이 대화에 한글 작업본이 없다 — hwp_forms → hwp_start."
        fl = sorted(self._fields(), key=lambda x: bool(x["text"]))
        return "\n".join(f"{x['name']} — {'(비어 있음)' if not x['text'] else x['text'][:60]}" for x in fl[:120])

    def _guard(self) -> tuple[int, dict] | str:
        fid = self.fid()
        if not fid:
            return "이 대화에 한글 작업본이 없다 — hwp_forms → hwp_start."
        # 사람이 같은 문서를 편집 중이어도 쓴다 — 칸 글은 공동 편집(webhwp_coedit)의 같은 길로 그 화면에 바로 들어간다.
        # 다만 사람이 임대를 쥔 칸(지금 그 칸을 쓰는 중)은 건드리지 않는다
        return fid, webhwp.get(self.db, fid)

    def _mark(self, fid: int, field: str) -> None:
        from zzaimy.app import webhwp_coedit as ce
        self.db.set_setting(f"webhwp_activity:{fid}", json.dumps({"by": "AURA", "field": field, "at": time.time()}, ensure_ascii=False))
        ce.emit(self.db, fid, "aura", {"field": field, "ttl": ce.conf(self.db, "aura_ttl"), "by": self.owner})   # 누가 시킨 AURA 인지(화면에 「AURA · 이름」)

    def fill(self, a: dict) -> str:
        from zzaimy.app import webhwp_write as ww
        g = self._guard()
        if isinstance(g, str):
            return g
        fid, f = g
        values = a.get("values") if isinstance(a.get("values"), dict) else {}
        values = {(self.resolve(str(k)) or str(k)): str(v)[:2000] for k, v in values.items()}
        values = {k: v for k, v in values.items() if not k.startswith("절:")}
        long_, fake = {}, []
        for k, v in list(values.items()):
            if _too_long(v):                                # 여러 줄·표·긴 글은 칸 값이 아니다 — 절 쓰기로(칸 안에 문단·표로 들어간다)
                long_[k] = values.pop(k)
            elif _placeholder(v):                           # 양식의 예시 글자(○○·000)를 그대로 넣지 않는다 — 모르면 비워 두고 묻는다
                fake.append(k)
                values.pop(k)
        if not values:
            return ("넣은 것 없음." + (f" 긴 글·표는 hwp_write(field=칸 이름)로 쓴다: {', '.join(long_)}." if long_ else "")
                    + (f" 예시 글자 그대로라 넣지 않았다(모르면 비워 두고 ask_user): {', '.join(fake)}." if fake else "")
                    + ("" if long_ or fake else " values 가 비었다(절 필드는 hwp_write 로 쓴다)."))
        from zzaimy.app import webhwp_coedit as ce
        have = {x["name"] for x in ww.fields(Path(f["path"]).read_bytes())}
        miss = [k for k in values if k not in have]
        values = {k: v for k, v in values.items() if k in have}
        if not values:
            return "그런 필드가 없다 — hwp_fields 의 이름을 그대로 쓴다."
        # 사람과 같은 길 — 임대 확인·판 번호·알림(열린 편집 화면의 같은 칸에 바로) 뒤 한글 파일에 써 넣는다(고친 사람 AURA)
        done, held = ce.put(self.db, fid, ce.AGENT, values, base=self.base, client="aura-agent")
        if not done and not held:
            return "그런 필드가 없다 — hwp_fields 의 이름을 그대로 쓴다."
        if done:                                            # AURA 작성 표시는 실제로 쓴 칸에만(사람이 쥐어 못 쓴 칸에는 띄우지 않는다)
            self._mark(fid, done[-1])
        self.writes += 1 if done else 0
        return (f"채웠다: {', '.join(done) or '없음'}" + (f" · 없는 이름: {', '.join(miss)}" if miss else "")
                + (f" · 담당자가 지금 쓰는 칸이라 넣지 않음(끝나면 다시): {', '.join(held)}" if held else "")
                + (f" · 긴 글·표라 넣지 않음 — hwp_write(field=칸 이름)로: {', '.join(long_)}" if long_ else "")
                + (f" · 예시 글자라 넣지 않음(모르면 ask_user): {', '.join(fake)}" if fake else ""))

    def write(self, a: dict, by: str = "메인") -> str:
        from zzaimy.app import webhwp_write as ww
        g = self._guard()
        if isinstance(g, str):
            return g
        fid, f = g
        field = str(a.get("field") or "").strip()
        text = str(a.get("text") or "").strip()
        field = self.resolve(field) or (field if field.startswith(("절:", "칸:", "표")) else "절:" + field)
        if not text:
            return "text 가 비었다."
        why = self.gate(text, by=by)                       # by 는 코드가 정한다(모델 인자로 바꿀 수 없다)
        if why:
            return "쓰지 않았다 — " + why
        from zzaimy.app import webhwp_coedit as ce
        who = ce.holder(self.db, fid, field)
        if who and who != ce.AGENT:
            return f"쓰지 않았다 — {who} 님이 지금 「{field}」 칸을 쓰는 중이다. 다른 절을 먼저 쓰고 나중에 다시 한다."
        m = webhwp.meta(self.db, fid)
        written = m.get("written") or {}
        self._mark(fid, field)
        from zzaimy.app import webhwp_dress
        try:
            raw = Path(f["path"]).read_bytes()
            others = [v for k, v in ww.field_values(raw).items() if k != field]
            items, look = webhwp_dress.dress_items(ww.parse_body(text), others)   # 참고 상자(> 줄)·표 제목(<표> 줄)은 꾸밈 스타일로
            data, put = ww.write_section(raw, field, items, written)
            data = webhwp_dress.apply(data, put, look)
        except KeyError:
            names = [x["name"] for x in self._fields() if x["kind"] == "절"]
            return f"그런 절 필드가 없다. 절 필드: {', '.join(names[:30])}"
        webhwp.save(self.db, self.base, fid, data, user="AURA")
        inner = ww.field_values(data).get(field)
        if inner is not None:                               # 절 필드 안 글도 칸 글 표에 — 써 넣기(overlay)가 AURA 글을 옛 글로 되돌리지 않게
            ce.put(self.db, fid, ce.AGENT, {field: inner}, client="aura-agent")
        m = webhwp.meta(self.db, fid)
        written[field] = put
        m["written"] = written
        gist = m.get("gist") if isinstance(m.get("gist"), dict) else {}
        gist[field] = re.sub(r"\s+", " ", text)[:300]       # 절 요지 — 다음 절을 맡길 때 「앞 절 요지」 로 쓴다
        m["gist"] = gist
        bodies = m.get("body") if isinstance(m.get("body"), dict) else {}
        bodies[field] = text[:20000]                         # 쓴 본문 그대로(마크다운 표·도식) — 고쳐 쓰기(hwp_revise)의 「지금 글」
        m["body"] = bodies
        need = needs_of(guide_of(ww.section_context(data, field)))
        shape = body_shape(text)
        shape.update(need=need, missing=missing_needs(need, shape), by=by)
        shapes = m.get("shape") if isinstance(m.get("shape"), dict) else {}
        shapes[field] = shape                                # 쓴 모양 기록 — 평가(scripts/201)·화면이 읽는다
        m["shape"] = shapes
        self.db.set_setting(f"webhwp_meta:{fid}", json.dumps(m, ensure_ascii=False))
        self.writes += 1
        msg = f"「{field[2:]}」 을 썼다(문단·표 {len(put) + 1}개)."
        if shape["missing"] and self.need_notes.get(field, 0) < 1:
            self.need_notes[field] = self.need_notes.get(field, 0) + 1
            msg += (f" 다만 작성방법이 요구한 {'·'.join(shape['missing'])} 이(가) 본문에 없다 — 근거가 있으면 같은 field 로 한 번 고쳐 쓴다"
                    "(표는 마크다운 표, 도식은 ```도식 블록, 출처는 근거 문서 이름). 근거가 없어 못 넣으면 그대로 두고 다음으로.")
        return msg

    def resolve_number(self, name: str) -> str | None:
        """「2번 절」「2」 처럼 번호로 짚은 절 → 그 번호로 시작하는 절 필드(위 단계 「2.」 가 먼저). 하나로 정해지지 않으면 None."""
        m = re.search(r"(\d+)", name or "")
        if not m or re.sub(r"[\d\s번절장.:)]", "", re.sub(r"^(절|칸):", "", name or "")):
            return None
        n = m.group(1)
        secs = [x["name"] for x in self._fields() if x["kind"] == "절"]
        for pat in (rf"^절:\s*{n}\.\s", rf"^절:\s*{n}\)\s", rf"^절:\s*{n}\D"):
            hits = [x for x in secs if re.match(pat, x)]
            if len(hits) == 1:
                return hits[0]
        return None

    def current_body(self, field: str) -> str:
        """절의 지금 글 — AURA 가 쓴 본문 기록(마크다운 그대로)이 작업본의 첫 줄과 맞으면 그것, 아니면(사람이 고쳤거나 기록 없음) 작업본 글."""
        f = webhwp.get(self.db, self.fid() or 0)
        if not f:
            return ""
        m = webhwp.meta(self.db, f["id"])
        data = Path(f["path"]).read_bytes()
        from zzaimy.app import webhwp_write as ww
        first = next((x["text"] for x in ww.fields(data) if x["name"] == field), "")
        rec = (m.get("body") or {}).get(field) if isinstance(m.get("body"), dict) else None
        if rec:
            head = re.sub(r"\s+", "", re.sub(r"^\s*(?:\d+\)|[가-하]\)|\(\d+\))\s*", "", rec.strip().splitlines()[0]))[:20]
            if not first or head[:12] in re.sub(r"\s+", "", first):
                return rec
        pids = ((m.get("written") or {}).get(field) or []) if isinstance(m.get("written"), dict) else []
        import io
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(data))
        xml = "".join(z.read(n).decode("utf-8", "ignore") for n in sorted(z.namelist()) if re.match(r"Contents/section\d+\.xml$", n))
        parts = [first]
        for pid in pids:
            mm = re.search(rf'<hp:p id="{re.escape(str(pid))}"[^>]*>', xml)
            if not mm:
                continue
            depth = 0
            for t in re.finditer(r"<hp:p\b|</hp:p>", xml[mm.start():]):
                depth += 1 if t.group(0) != "</hp:p>" else -1
                if depth == 0:
                    parts.append(re.sub(r"<[^>]+>", " ", xml[mm.start():mm.start() + t.end()]).strip())
                    break
        return "\n".join(p for p in parts if p.strip())

    def gate(self, text: str, by: str = "메인") -> str | None:
        """절 본문 관문 — check_body(메인·서브 같음) + 메인만: 남은 문맥이 모자란데 긴 본문을 직접 쓰면 거절하고 맡기기 안내."""
        from zzaimy.app import llm_budget
        limit = self.limit()
        ev = None
        if self.evidence is not None:
            try:
                ev = list(self.evidence()) + [self.form_text()]
            except Exception:
                ev = None
        why = check_body(text, ev, limit)
        if why:
            return why
        if by == "메인" and self.delegating:
            hr = llm_budget.headroom()
            if hr and hr[0] < LOW_HEADROOM and llm_budget.estimate_tokens(text) > int(hr[1] * LONG_SHARE):
                return (f"남은 문맥이 약 {int(hr[0] * 100)}% 라 긴 본문을 직접 쓰지 않는다 — delegate_section 으로 이 절을 맡긴다"
                        "(절 이름·요지·형식·분량만 주면 지시서의 나머지는 코드가 채운다).")
        return None

    def form_text(self) -> str:
        """작업본에 원래 있는 글(양식·예시·쓴 절) — 양식에 있는 수치는 지어낸 것이 아니다."""
        f = webhwp.get(self.db, self.fid() or 0)
        if not f:
            return ""
        import io
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(Path(f["path"]).read_bytes()))
        return re.sub(r"<[^>]+>", " ", "".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist()
                                              if re.match(r"Contents/section\d+\.xml$", n)))

    def tables(self, a: dict) -> str:
        from zzaimy.app import webhwp_style
        if not self.fid():
            return "이 대화에 한글 작업본이 없다 — hwp_forms → hwp_start."
        f = webhwp.get(self.db, self.fid())
        tl = webhwp_style.tables(Path(f["path"]).read_bytes())
        return "\n".join(f"표{t['no']} — {t['rows']}행×{t['cols']}열 · 첫 행: {t['head'] or '(빈 칸)'}" for t in tl[:80]) or "표가 없다."

    def style(self, a: dict) -> str:
        """담당자의 표 서식 요청(음영·선 굵기·이중선·글자 크기·굵게·정렬)을 작업본 표에 입힌다."""
        from zzaimy.app import webhwp_style
        g = self._guard()
        if isinstance(g, str):
            return g
        fid, f = g
        which = [int(x) for x in (a.get("tables") or []) if str(x).isdigit()]
        keys = ("head_fill", "label_fill", "outer", "inner", "head_bottom", "font_pt", "head_bold", "head_align", "body_align")
        opts = {k: a[k] for k in keys if k in a and a[k] not in (None, "")}
        for k in ("head_align", "body_align"):
            if k in opts:
                opts[k] = webhwp_style.ALIGNS.get(str(opts[k]), "")
        if "head_bottom" in opts:
            opts["head_bottom"] = "DOUBLE" if "이중" in str(opts["head_bottom"]) or str(opts["head_bottom"]).upper() == "DOUBLE" else "SOLID"
        self._mark(fid, f"표 서식 {', '.join(map(str, which)) or '전부'}")
        data = webhwp_style.apply(Path(f["path"]).read_bytes(), which or None, opts)
        webhwp.save(self.db, self.base, fid, data, user="AURA")
        self.writes += 1
        return f"표 서식을 입혔다({'표 ' + ', '.join(map(str, which)) if which else '모든 표'}; " + \
            (", ".join(f"{k}={v}" for k, v in opts.items()) or "학교 표 기본 모양") + ")."
