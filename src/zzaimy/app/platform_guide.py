"""플랫폼 안내 자료 — 플랫폼 업무 도우미(오른쪽 아래 떠 있는 창)가 「어디서 무엇을 누르면 되는가」 를 답할 때 근거로 쓰는 화면 설명.

화면마다 한 항목(PAGES): 무엇을 하는 곳인지, 할 수 있는 일과 그 자리(카드·단추), 관련 플랫폼 동작(platform_ops).
화면 이름은 개발 메뉴(dev_nav)에서, 카드 제목은 템플릿의 <h2> 에서, 처리 단계는 처리 흐름(process_flow)에서 읽는다 —
손으로 쓰는 것은 설명 글과 단추 이름뿐이다. 화면을 고치면 여기 항목도 같이 고친다: tests/test_platform_agent.py 가
「개발 메뉴의 화면은 모두 항목이 있다」, 「적어 둔 단추 이름·자리(#id)가 그 화면 템플릿에 실제로 있다」 를 확인한다.

찾기(lookup)는 질문과 항목 글의 글자 겹침으로 후보를 고를 뿐이다 — 어느 항목으로 답할지는 도우미(27B)가 정한다.
문서 이름·내용은 담지 않는다(누가 물어도 같은 화면 설명만 나온다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from zzaimy.app import dev_nav

TEMPLATES = Path(__file__).parent / "templates"

ROLE_NAMES = {"dev": "관리자", "staff": "교직원", "student": "학생"}


@dataclass(frozen=True)
class Task:
    what: str                     # 할 수 있는 일
    how: str                      # 어디서 무엇을 누르는가
    anchor: str = ""              # 화면 안 자리(#id) — 링크에 붙인다
    op: str = ""                  # 같은 일을 하는 플랫폼 동작(platform_ops) — 있으면 도우미가 대신 할 수 있다
    mark: str = ""                # 화면에서 가리킬 요소의 속성(예: data-rr="student") — 도우미가 커서·강조로 짚는다
    mark_in: str = ""             # 그 요소가 화면 템플릿이 아닌 곳(base.html 사이드바 등)에 있을 때

    @property
    def selector(self) -> str:
        return f"[{self.mark}]" if self.mark else ""


@dataclass(frozen=True)
class Page:
    path: str
    template: str
    purpose: str
    tasks: tuple[Task, ...] = ()
    buttons: tuple[str, ...] = ()           # 설명에 나오는 단추 이름 — 템플릿에 그대로 있어야 한다(테스트)
    roles: tuple[str, ...] = ("dev",)
    name: str = ""                          # 개발 메뉴에 없는 화면만 적는다
    terms: tuple[tuple[str, str], ...] = field(default=())   # 이 화면에서 쓰는 말 (이름, 뜻)

    @property
    def title(self) -> str:
        return self.name or dev_nav.NAMES.get(self.path, self.path)


PAGES: tuple[Page, ...] = (
    # ---- 업무 화면 ----
    Page("/chat", "chat_workspace.html", "에이전트와 대화하며 질문하고 문서 작업을 맡기는 곳. 프로젝트 안에서 시작한 대화는 그 프로젝트의 문서·기준 문서를 근거로 쓴다.",
         (Task("질문하기·문서 작업 맡기기(에이전트 대화)", "입력 칸에 묻는다. 파일을 끌어 놓으면 첨부되어 함께 읽는다. 사업 문서 내용 질문과 문서 작성은 여기서 한다."),
          Task("프로젝트 만들기", "사이드바 「프로젝트」 옆 + (프로젝트 추가) — 업무 영역·이름·마감을 적는다. 도우미에게 문서를 쓰고 싶다고 하면 프로젝트를 만들고 첫 요청까지 보내 준다.",
               op="project.start", mark='data-modal-open="#projectModal"', mark_in="base.html"),
          Task("일반 질문을 웹으로 찾기", "입력 칸의 외부 검색 모드(웹)를 켠다 — 질문 글만 밖으로 나가고 문서·첨부는 나가지 않는다."),
          Task("문서 작업", "프로젝트 대화에서 「○○ 서식으로 작업하자」처럼 말하면 작업본을 만들고 절 쓰기·검토·한글 내보내기를 이어 한다.")),
         buttons=("작업 패널",), roles=("dev", "staff", "student"), name="새 채팅"),
    Page("/?type=all", "index.html", "라이브러리 — 들어온 문서 전부를 찾고 폴더로 묶는 곳. 사업 문서와 기준 문서(규정·지침·공고)를 함께 본다.",
         (Task("문서 올리기(사업 문서)", "쓰임새 줄 오른쪽 「문서 올리기」 — 파일(여러 개 가능)을 선택하고, 폴더(없으면 「폴더 없이」)·검색 범위(정하지 않으면 공통 RAG)·"
               "사업(선택하면 그 사업 연도 묶음에 들어감)·사업 연도·업무 갈래(모르면 「자동으로 정하기」)를 선택한 뒤 「올리기」. 올린 문서는 처리 중 → 처리 완료로 바뀐다.",
               mark='data-modal-open="#libUploadModal"'),
          Task("새 규정·공고·지침 파일을 기준 문서로 올리기", "쓰임새에서 「기준」(주소 /?type=all&use=criteria)을 선택하면 나오는 「기준 등록」 — 업무를 선택하고 파일을 올리면 원문 그대로 규정 검색에 들어간다. "
               "이미 반입된 자료 속 규정은 다시 올리지 않고 RAG 공간에서 선택해 올린다(관리자).", mark='data-modal-open="#criteriaUploadModal"'),
          Task("올린 문서가 어떻게 됐는지 보기", "라이브러리에서 이름으로 찾거나 문서를 열어 상태(처리 중·처리 완료·실패)를 본다. 도우미에게 물으면 최근 올린 문서 상태를 알려 준다."),
          Task("폴더 만들기·폴더째 부서 RAG 로 두기", "폴더 목록의 「+ 폴더 만들기」, 폴더를 선택한 뒤 「공개 범위 바꾸기…」 — 앞으로 그 폴더로 들어오는 문서도 같은 부서로 간다.")),
         buttons=("문서 올리기", "기준 등록", "공개 범위 바꾸기…", "+ 폴더 만들기", "폴더 없이", "자동으로 정하기", "올리기"), roles=("dev", "staff"), name="라이브러리"),
    Page("/project/{id}", "project.html", "프로젝트(사업 하나) — 사업 문서·작성 원칙·기준 문서·관련 지난 사업을 모아 두고 대화로 작업하는 곳. 사이드바 「프로젝트」 에서 연다.",
         (Task("프로젝트에 문서 넣기", "「문서 추가」"),
          Task("이 사업의 기준 문서 정하기", "「기준 문서 선택」 카드에서 선택해 저장한다."),
          Task("이 프로젝트로 대화 시작", "「대화 시작」"),
          Task("끝난 사업 보관", "「프로젝트 관리」 의 「보관」 — 사이드바의 「보관된 사업」 에서 다시 본다.")),
         buttons=("문서 추가", "대화 시작", "보관"), roles=("dev", "staff"), name="프로젝트"),
    Page("/settings", "settings.html", "내 설정 — 성명·비밀번호·구글 계정 연결. 오른쪽 위 사용자 메뉴의 「내 설정」. 답 지침은 프로젝트의 「작성 원칙」 에 적는다.",
         (Task("성명 바꾸기", "「프로필」 의 성명을 고치고 「저장」"),
          Task("비밀번호 바꾸기", "「비밀번호 변경」 을 누르면 뜨는 창에서"),
          Task("내 학교 구글 계정 잇기", "「구글 계정」 카드의 「구글 계정 연결」 — 독스 작업본·드라이브 가져오기에 쓴다.")),
         buttons=("저장", "비밀번호 변경", "구글 계정 연결"), roles=("dev", "staff"), name="내 설정"),

    # ---- 개발(관리자) 화면 — 개발 메뉴 순서 ----
    Page("/dev", "dev.html", "개발 현황 — 기능별 진행 상태와 구축 현황을 보는 첫 화면. 규정이 바뀌면 검색 색인은 몇 분 안에 자동으로 다시 만든다."),
    Page("/dev/flow", "dev_flow.html", "처리 흐름 — 문서가 들어와 검색·답변·초안이 되고 학습 데이터가 되기까지 단계별 설명과 지금 수치."),
    Page("/dev/intake", "dev_intake.html", "문서 반입 — 원본 → 처리 대상 → 라이브러리 → 검색까지의 수와 빈틈(놓친 원본·실패)을 보고, Google Drive 가져오기 탭이 있다.",
         (Task("구글 드라이브 폴더에서 가져오기", "문서 반입 화면의 Google Drive 탭"),)),
    Page("/dev/db", "dev_db.html", "원천 데이터 — 운영 DB 의 반입 문서와 검색 자료를 장부 값 그대로 찾아본다.",
         buttons=("찾기",)),
    Page("/dev/rag", "dev_rag.html", "RAG 공간 — 답변할 때 찾는 자료 묶음(공통 RAG·부서 RAG·학생 RAG)과 기준 문서, 계정별 추가 권한을 관리하는 곳.",
         (Task("학생 RAG 에 규정 넣기(학생 공개 지정)", "「규정·지침」 카드의 「검색에 쓰는 규정」 탭에서 규정 줄의 「학생 공개로」(여럿이면 체크 뒤 아래 「학생 공개」) — 되돌리려면 같은 줄의 「교직원만으로」.",
               "student-regs", "regs.audience", 'data-rr="student"'),
          Task("반입 자료의 규정을 기준 문서로 올리기", "같은 카드의 「추가할 수 있는 규정」 탭에서 찾아 줄의 「검색에 추가」. 추가하면 몇 분 안에 자동으로 뜻 검색에 들어간다(바로 넣으려면 「지금 바로 만들기」).",
               "student-regs", "regs.register", 'data-rr="register"'),
          Task("규정을 검색에서 빼기", "「검색에 쓰는 규정」 탭에서 줄의 「빼기」(여럿이면 「검색에서 빼기」) — 일반 문서로 돌아간다.", "student-regs"),
          Task("부서 RAG 만들기", "사용자·계정 화면의 「부서 추가」 — 부서를 만들면 부서 RAG 가 함께 생긴다. RAG 목록에서 문서 수·쓰는 계정을 본다.",
               "dept-rags", "dept_rag.create"),
          Task("다른 부서 문서를 더 찾게 하기", "「계정별 추가 권한」 표에서 계정의 「설정」 을 눌러 부서를 선택하고 저장.", "grants"),
          Task("이 계정이 이 규정을 찾는지 확인", "「검색 범위 시험」 을 펼쳐 계정이나 공간과 검색어를 넣는다.", "try")),
         buttons=("학생 공개", "교직원만", "검색에서 빼기", "검색에 추가", "지금 바로 만들기", "설정", "검색 범위 시험"),
         terms=(("공통 RAG", "전 교직원이 찾는 기본 자료 묶음. 부서를 정하지 않은 문서는 모두 여기 든다."),
                ("부서 RAG", "그 부서 계정만 공통 RAG 와 함께 찾는 묶음. 문서 화면·문서 올리기에서 공개 범위를 「부서만」으로 선택하면 들어간다."),
                ("학생 RAG", "학생 계정이 찾는 묶음 — 학생 공개로 지정한 기준 문서(규정)만 든다. 사업 문서는 들어가지 않는다."),
                ("검색에 쓰는 규정(기준 문서)", "규정·지침·공고처럼 조항 단위로 찾는 문서. 반입 자료 속 규정은 처음엔 일반 문서로 들어오므로 「추가할 수 있는 규정」 탭에서 검색에 추가해야 한다."),
                ("추가 권한", "부서가 정해진 교직원 계정에 다른 부서 RAG 의 사업 문서 검색을 더해 주는 것."))),
    Page("/graph/explore", "kg_explore.html", "그래프 탐색 — 사업·연차·문서·절 사이 관계(지식그래프)와 온톨로지를 그림으로 본다.",
         buttons=("지식그래프", "온톨로지", "전체 보기")),
    Page("/dev/train", "dev_train.html", "데이터·학습 — 학습 도구(Label Studio 등) 상태, 단계별 모델, LLM 연결 추가·기본 서버 지정.",
         (Task("모델 서버 연결 확인", "연결 카드의 「연결 확인」"),
          Task("문서 작업 기본 서버 바꾸기", "「기본 서버로 지정」")),
         buttons=("연결 확인", "기본 서버로 지정")),
    Page("/dev/tools", "dev_tools.html", "도구·모델 연결 — 플랫폼 서버의 외부 도구 CLI 설치·구독 인증 상태.",
         buttons=("연결 상태 확인",)),
    Page("/dev/quality", "dev_quality.html", "품질·성능 — 검색 품질 측정(GraphRAG 와 풀텍스트 비교)과 담당자가 신고한 추출 문제."),
    Page("/dev/pii", "dev_pii.html", "개인정보·반출 점검 — 내부 마스킹 설정, 유형별 처리 정책, 잔여 검사·자가 점검, 외부 구독 연결.",
         (Task("가림 뒤 남은 개인정보 찾기", "「잔여 검사 실행」"),
          Task("마스킹 설정 바꾸기", "「내부 마스킹 설정」 에서 고친 뒤 「설정 저장」")),
         buttons=("잔여 검사 실행", "자가 점검 실행", "설정 저장")),
    Page("/dev/users", "dev_users.html", "사용자·계정 — 계정 추가·역할·부서, 부서 목록(부서 RAG), 구글 계정과 문서 저장 위치.",
         (Task("계정 만들기", "「사용자 추가」"),
          Task("부서 만들기(부서 RAG 가 함께 생김)", "「부서」 카드의 「부서 추가」", "depts", "dept_rag.create", 'data-modal-open="#deptCreate"'),
          Task("비밀번호 잊은 계정", "계정을 열어 「임시 비밀번호 만들기」")),
         buttons=("사용자 추가", "부서 추가", "임시 비밀번호 만들기")),
    Page("/dev/docs", "dev_docs.html", "논문·설계 문서 — 설계 결정(ADR)·논문 자료·주간 보고를 찾아본다.",
         buttons=("보고서 생성",)),
)

BY_PATH = {p.path: p for p in PAGES}


def page_for(path: str) -> Page | None:
    """주소(경로)에 맞는 항목 — /project/7 은 /project/{id}, /dev/rag/… 처럼 하위 경로는 위 화면."""
    path = (path or "").split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
    if path in ("/", "/inbox", "/criteria"):
        return BY_PATH["/?type=all"]
    if re.fullmatch(r"/project/\d+", path):
        return BY_PATH["/project/{id}"]
    if re.fullmatch(r"/chat(/\d+)?", path):
        return BY_PATH["/chat"]
    best = None
    for p in PAGES:
        base = p.path.split("?", 1)[0]
        if base.startswith("/") and "{" not in base and (path == base or path.startswith(base + "/")):
            if best is None or len(base) > len(best.path):
                best = p
    return best


@lru_cache(maxsize=None)
def headings(template: str) -> tuple[str, ...]:
    """템플릿의 카드 제목(<h2>) — 틀 문법이 섞인 제목은 뺀다."""
    try:
        src = (TEMPLATES / template).read_text(encoding="utf-8")
    except OSError:
        return ()
    out = []
    for m in re.finditer(r"<h2[^>]*>(.*?)</h2>", src, re.S):
        t = " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())
        if t and "{" not in t and t not in out:
            out.append(t)
    return tuple(out)


def _flow_stages() -> list[tuple[str, str, str]]:
    """처리 흐름의 단계 — (이름, 하는 일, 화면 주소)."""
    try:
        from zzaimy.app import process_flow
        return [(s["name"], s["does"], (s.get("link") or ("", ""))[0]) for s in process_flow.STAGES]
    except Exception:
        return []


def link(page: Page, anchor: str = "") -> str:
    return page.path.replace("{id}", "번호") + (f"#{anchor}" if anchor else "")


def render(page: Page, role: str = "dev") -> str:
    """항목 하나를 도우미에 건넬 글로."""
    who = ", ".join(ROLE_NAMES.get(r, r) for r in page.roles)
    lines = [f"■ {page.title} — 주소 {link(page)} (여는 계정: {who})" + ("" if role in page.roles else " · 지금 계정으로는 열 수 없음")]
    lines.append(page.purpose)
    heads = headings(page.template)
    if heads:
        lines.append("화면의 카드: " + " · ".join(heads[:8]))
    for t in page.tasks:
        where = f" (자리 {link(page, t.anchor)})" if t.anchor else ""
        can = f" · 도우미가 대신 할 수 있음(propose_action op={t.op})" if t.op else ""
        el = f" · 가리킬 요소 {t.selector}" if t.selector else ""
        lines.append(f"- {t.what}: {t.how}{where}{el}{can}")
    for name, what in page.terms:
        lines.append(f"· {name}: {what}")
    return "\n".join(lines)


def _grams(text: str) -> set[str]:
    s = re.sub(r"[^0-9A-Za-z가-힣]", "", (text or "").lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _haystack(page: Page) -> str:
    return " ".join([page.title, page.purpose, *headings(page.template), *(t.what + " " + t.how for t in page.tasks),
                     *(n + " " + w for n, w in page.terms), *page.buttons])


def lookup(query: str, *, current: str = "", role: str = "dev", limit: int = 3, budget: int = 1500) -> str:
    """질문과 글자가 많이 겹치는 화면 항목 몇 개 — 지금 보는 화면을 먼저(겹침이 조금이라도 있으면).
    처리 흐름 단계가 더 맞으면 그 줄도 덧붙인다. 결과는 근거 글이고 답은 에이전트가 쓴다."""
    q = _grams(query)
    here = page_for(current) if current else None
    scored = []
    for p in PAGES:
        hay = _grams(_haystack(p))
        s = len(q & hay) / (1 + len(q)) if q else 0.0
        if here is not None and p.path == here.path:
            s += 0.15
        scored.append((s, p))
    scored.sort(key=lambda x: -x[0])
    picked = [p for s, p in scored[:limit] if s > 0.05] or ([here] if here else [])
    parts: list[str] = []
    used = 0
    for p in picked:
        txt = render(p, role)
        if used + len(txt) > budget and parts:
            break
        parts.append(txt[:budget])
        used += len(txt)
    stages = [(n, d, l) for n, d, l in _flow_stages() if q and len(q & _grams(n + d)) / (1 + len(q)) > 0.25]
    if stages and used < budget:
        parts.append("처리 흐름(/dev/flow)에서 맞는 단계:\n" + "\n".join(f"- {n}: {d}" + (f" (화면 {l})" if l else "") for n, d, l in stages[:2]))
    if not parts:
        return "안내 자료에서 맞는 화면을 찾지 못했다. 화면 목록: " + ", ".join(f"{p.title}({link(p)})" for p in PAGES)
    return "\n\n".join(parts)


def where_line(path: str) -> str:
    """도우미 상태에 넣는 한 줄 — 지금 보는 화면."""
    p = page_for(path)
    if p is None:
        return f"지금 보는 화면: {path[:80]}" if path else ""
    return f"지금 보는 화면: {p.title} ({path[:80]}) — {p.purpose}"
