"""플랫폼 안내 자료 — 플랫폼 업무 도우미(오른쪽 옆 패널)가 「어디서 무엇을 누르면 되는가」 를 답할 때 근거로 쓰는 화면 설명.

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
    mark: str = ""                # 화면에서 가리킬 요소의 속성(예: data-crit="student") — 도우미가 커서·강조로 짚는다
    mark_in: str = ""             # 그 요소가 화면 템플릿이 아닌 곳(base.html 사이드바 등)에 있을 때
    auto: bool = False            # 도우미가 바로(now) 커서로 눌러도 되는가 — 열기·찾기·칸 채우기처럼 자료를 바꾸지 않는 요소만 True.
                                  # 보안: 자료를 바꾸는 단추(학생 공개·검색에 추가 등)는 False 로 두어 [실행] 확인을 거치게 한다(문서 속 글이 모델을 꾀어 누르게 하지 못하게)
    final: bool = False           # 마지막 단추(저장·보내기·지우기·등록) — 담당자가 이번 요청에 직접 친 글에 verbs 가운데 하나가 있을 때만 커서가 누른다
    verbs: tuple[str, ...] = ()   # 마지막 단추를 시키는 말(활용형까지, 예: 「저장해」) — platform_ops.commanded 가 사람이 친 글에서만 찾는다

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
         (Task("질문하기·문서 작업 맡기기(에이전트 대화)", "입력 칸에 묻는다. 파일을 끌어 놓으면 첨부되어 함께 읽는다. 사업 문서 내용 질문과 문서 작성은 여기서 한다. 긴 문서는 AURA 가 절마다 나눠 맡겨(그 절에 필요한 메모·근거만 담아) 동시에 쓰고 모아 넣는다."),
          Task("프로젝트 만들기", "사이드바 「프로젝트」 옆 + (프로젝트 추가) — 업무 영역·이름·마감을 적는다. 도우미에게 문서를 쓰고 싶다고 하면 프로젝트를 만들고 첫 요청까지 보내 준다.",
               op="project.start", mark='data-modal-open="#projectModal"', auto=True, mark_in="base.html"),
          Task("새 프로젝트 업무 고르기", "프로젝트 추가 창의 「업무」 — 도우미에게 맡기면 text 에 업무 이름을 넣어 고른다.", mark='data-aura="project-sector"', auto=True,
               mark_in="_project_modal.html"),
          Task("새 프로젝트 이름 칸 채우기", "프로젝트 추가 창의 이름 칸 — 도우미에게 맡기면 text 에 이름을 넣는다.", mark='data-aura="project-name"', auto=True,
               mark_in="_project_modal.html"),
          Task("새 프로젝트 만들기 단추", "프로젝트 추가 창 아래 「만들기」", mark='data-aura="project-create"', final=True,
               verbs=("만들어", "만들고", "생성해", "추가해"), mark_in="_project_modal.html"),
          Task("일반 모드로 묻기", "입력 칸 오른쪽 「일반」 — 문서 없이 모델이 바로 답한다. 확인이 필요한 답이면 끝에 「인터넷에서 찾아보기」 선택지가 붙고, "
               "누르면 같은 질문을 인터넷 검색으로 다시 묻는다(질문 글만 밖으로 나가고 문서·첨부는 나가지 않는다).", mark='data-aura="answer-general"', auto=True),
          Task("RAG 모드로 묻기", "입력 칸 오른쪽 「RAG」 — 내 계정의 RAG(공통·부서) 문서를 근거로 답한다. 프로젝트 대화와 첨부가 있으면 처음부터 RAG 다.",
               mark='data-aura="answer-rag"', auto=True),
          Task("쓸 RAG 좁히기", "RAG 를 고른 뒤 옆의 범위 단추(처음은 「전체」) — 쓸 RAG 만 체크한다. 내 계정 범위 밖은 고를 수 없다. 고른 값은 계정에 기억된다.",
               mark='data-aura="answer-rag-pick"', auto=True),
          Task("답변 깊이 고르기(빠르게·꼼꼼하게)", "입력 칸 오른쪽 깊이 단추(처음은 「빠르게」)를 누르고 「빠르게」 또는 「꼼꼼하게」 — 빠르게는 생각 단계 없이 바로 답하고, "
               "꼼꼼하게는 생각을 거쳐 자료를 넓게 보고 맡긴 절을 한 번 더 점검한다(느리다). 고른 값은 계정에 기억되고 도우미 답에도 같이 쓴다. "
               "기본이 아닌 깊이로 보낸 질문은 머리에 「· 꼼꼼하게」 가 붙는다. 학생 계정에는 없다.",
               mark='data-aura="answer-effort"', auto=True),
          Task("문서 작업", "프로젝트 대화에서 「○○ 서식으로 작업하자」처럼 말하면 작업본을 만들고 절 쓰기·검토·한글 내보내기를 이어 한다.")),
         buttons=("작업 패널",), roles=("dev", "staff", "student"), name="새 채팅"),
    Page("/?type=all", "index.html", "라이브러리 — 들어온 문서 전부를 찾고 폴더로 묶는 곳. 사업 문서와 기준 문서(규정·지침·공고)를 함께 본다.",
         (Task("문서 올리기(사업 문서)", "쓰임새 줄 오른쪽 「문서 올리기」 — 파일(여러 개 가능)을 선택하고, 폴더(없으면 「폴더 없이」)·검색 범위(정하지 않으면 공통 RAG)·"
               "사업(선택하면 그 사업 연도 묶음에 들어감)·사업 연도·업무 갈래(모르면 「자동으로 정하기」)를 선택한 뒤 「올리기」. 올린 문서는 처리 중 → 처리 완료로 바뀐다.",
               mark='data-modal-open="#libUploadModal"', auto=True),
          Task("문서 올리기 창의 폴더 고르기", "문서 올리기 창의 「폴더」 — 도우미에게 맡기면 text 에 폴더 이름을 넣어 고른다.", mark='data-aura="lib-up-folder"', auto=True),
          Task("문서 올리기 창의 사업 고르기", "문서 올리기 창의 「사업」 — 도우미에게 맡기면 text 에 사업 이름을 넣어 고른다.", mark='data-aura="lib-up-prog"', auto=True),
          Task("문서 올리기 창의 사업 연도 칸", "문서 올리기 창의 사업 연도 — 도우미에게 맡기면 text 에 연도(예: 2026)를 넣는다.", mark='data-aura="lib-up-year"', auto=True),
          Task("문서 올리기 창의 업무 갈래 고르기", "문서 올리기 창의 업무 갈래 — 모르면 「자동으로 정하기」.", mark='data-aura="lib-up-kind"', auto=True),
          Task("문서 올리기 창의 올리기 단추", "파일을 고른 뒤 「올리기」 — 파일 고르기는 담당자가 한다.", mark='data-aura="lib-up-submit"', final=True,
               verbs=("올려", "올리고", "업로드해")),
          Task("새 규정·공고·지침 파일을 기준 문서로 올리기", "쓰임새에서 「기준」(주소 /?type=all&use=criteria)을 선택하면 나오는 「기준 등록」 — 업무를 선택하고 파일을 올리면 원문 그대로 규정 검색에 들어간다. "
               "이미 반입된 자료 속 규정은 다시 올리지 않고 같은 자리의 「새 판·미등록」에서 고른다(관리자).", mark='data-modal-open="#criteriaUploadModal"', auto=True),
          Task("학생 RAG 에 규정 넣기(학생 공개 지정)", "관리자 — 「기준」 칩 목록에서 규정 줄 끝 「…」 › 「학생 공개로」(여럿이면 체크 뒤 작업 줄의 「학생 공개」). 되돌리기는 「교직원만」. "
               "공개 대상 칸으로 학생 공개·교직원만을 거른다.", "", "regs.audience", 'data-crit="student"'),
          Task("반입 자료의 규정을 기준 문서로 올리기", "관리자 — 「기준」 칩의 「새 판·미등록」 창에서 체크 뒤 「검색에 추가」. 학교 원본에서 들어온 새 판은 저절로 올라가고, 사용자가 올린 문서는 여기서 고른다.",
               "", "regs.register", 'data-crit="register"'),
          Task("규정을 검색에서 빼기", "관리자 — 「기준」 칩 목록에서 줄 끝 「…」 › 「검색에서 빼기」(여럿이면 체크 뒤 작업 줄) — 일반 문서로 돌아간다.", mark='data-crit="unregister"'),
          Task("쓰임새로 거르기(기준 문서만 보기 등)", "목록 위 쓰임새 칩 — 「기준」 은 규정·지침·공고, 「작성·결과」 는 그 밖. 도우미에게 맡기면 text 에 칩 이름을 넣어 누른다.",
               mark='data-aura="lib-use"', auto=True),
          Task("문서 이름으로 찾기", "거르기 줄의 찾을 낱말 칸 — 제목·접수번호(「본문까지」 를 켜면 본문 낱말)로 찾는다. 도우미에게 맡기면 text 에 낱말을 넣어 찾는다.",
               mark='data-aura="lib-q"', auto=True),
          Task("지난 사업 찾기", "위 「사업 찾기」 칸에 사업 이름이나 별칭을 넣고 「찾기」 — 하나로 좁혀지면 그 사업 한눈에 보기(연도별 서류)로 간다. "
               "도우미에게 맡기면 찾을 말을 넣어 찾아 준다.", mark='id="libFind"', auto=True),
          Task("올린 문서가 어떻게 됐는지 보기", "라이브러리에서 이름으로 찾거나 문서를 열어 상태(처리 중·처리 완료·읽지 못함)를 본다. 도우미에게 물으면 최근 올린 문서 상태를 알려 준다."),
          Task("읽지 못한 문서 다시 하기", "목록 줄의 「읽지 못함 · 사유」 옆 단추나 문서 화면 머리에서 — 암호 문서는 「암호를 푼 파일로 바꾸기」, 스캔본은 「글자 읽기 맡기기」"
               "(판독 서버가 닿으면 읽음), 빈 파일·형식 불일치는 「다른 파일로 바꾸기」(한글은 「기안기로 열어 다시 저장」), 그 밖은 「다시 읽기」, 필요 없으면 「지우기」. "
               "읽지 못하면 올린 사람의 종 알림에 한 번 뜬다. 학교 원본은 관리자가 원본 관리(개발 메뉴) 「정리」 카드의 「처리 실패」 목록에서 골라 「다시 시도」한다."),
          Task("원본 파일 보기·내려받기", "목록 줄의 「원본」 — 새 창에서 원본을 연다(한글·워드·엑셀·PPT는 PDF로 바꿔 보여 줌). 내려받기는 문서 화면의 「내려받기」 › 「원본 파일」. 볼 수 있는 문서의 원본만 열린다."),
          Task("폴더 만들기·폴더째 부서 RAG 로 두기", "폴더 목록의 「+ 폴더 만들기」, 폴더를 선택한 뒤 「공개 범위 바꾸기…」 — 앞으로 그 폴더로 들어오는 문서도 같은 부서로 간다.")),
         buttons=("문서 올리기", "기준 등록", "공개 범위 바꾸기…", "+ 폴더 만들기", "폴더 없이", "자동으로 정하기", "올리기",
                  "새 판·미등록", "학생 공개", "교직원만", "검색에서 빼기", "검색에 추가"), roles=("dev", "staff"), name="라이브러리",
         terms=(("검색에 쓰는 규정(기준 문서)", "규정·지침·공고처럼 조항 단위로 찾는 문서. 학교 원본에서 들어온 규정은 저절로 기준 문서가 되고, 사용자가 올린 규정은 「새 판·미등록」에서 검색에 추가한다."),
                ("학생 공개", "기준 문서 가운데 학생 계정도 찾는 규정(학생 RAG). 나머지는 교직원만 찾는다."))),
    Page("/project/{id}", "project.html", "프로젝트(사업 하나) — 사업 문서·작성 원칙·기준 문서·관련 지난 사업을 모아 두고 대화로 작업하는 곳. 사이드바 「프로젝트」 에서 연다.",
         (Task("프로젝트에 문서 넣기", "「프로젝트 문서」 카드의 「문서 추가」 — 내 컴퓨터에서 올리기·문서함·구글 드라이브에서 가져오기·기준으로 연결 가운데 고른다.",
               mark='data-aura="project-add"', auto=True),
          Task("문서함·드라이브에서 가져오기", "「문서 추가」 › 「문서함·구글 드라이브에서 가져오기」 — 파일 찾기 창이 뜬다.", mark='data-aura="project-add-finder"', auto=True),
          Task("공유 창 열기", "머리의 공유 단추(나만 보기·공유 표시) — 부서 전체나 사람을 더하고 「저장」.", mark='data-modal-open="#shareModal"', auto=True,
               mark_in="_project_head.html"),
          Task("공유 저장", "공유 창 아래 「저장」 — 작업 문서의 구글 권한도 따라 바뀐다.", mark='data-aura="share-save"', final=True,
               verbs=("저장해", "저장하고", "저장까지", "공유해", "공유하고")),
          Task("진행 현황 보기", "머리 아래 탭의 「진행 현황」 — 사업 책임자 이상에게 보인다.", mark='data-aura="project-activity"', auto=True,
               mark_in="_project_head.html"),
          Task("이 사업의 기준 문서 정하기", "「기준 문서 선택」 카드에서 선택해 저장한다."),
          Task("이 프로젝트로 대화 시작", "「대화 시작」"),
          Task("끝난 사업 보관", "「프로젝트 관리」 의 「보관」 — 「지난 사업」(/projects/archived)에서 다시 본다.")),
         buttons=("문서 추가", "대화 시작", "보관"), roles=("dev", "staff"), name="프로젝트"),
    Page("/hwp", "hwp.html", "한글 문서함 — 한글 문서를 부서·공통·내 폴더에 두고 웹 한글 기안기로 브라우저에서 바로 열어 고치는 곳. 문서 작성의 주 경로다.",
         (Task("새 한글 문서 만들기", "「새 문서」 — 지금 폴더에 빈 문서를 기안기로 연다(처음 저장할 때 파일이 생긴다)", mark='data-aura="hwp-new"', auto=True),
          Task("폴더 열기", "왼쪽 위치 목록의 부서 폴더·내 폴더, 또는 목록의 폴더 이름을 누른다. 도우미에게 맡기면 text 에 폴더 이름을 넣어 연다.",
               mark='data-aura="hwp-folder"', auto=True),
          Task("문서 찾아 열기", "폴더를 연 뒤 목록에서 문서 이름을 누르면 기안기로 열린다. 도우미에게 맡기면 text 에 문서 이름을 넣어 연다.",
               mark='data-aura="hwp-file"', auto=True),
          Task("양식으로 시작", "「양식으로 시작」 — 공통 양식 목록이 뜨고, 양식 이름 칸에 낱말을 넣어 거른다.", mark='data-modal-open="#hwTpl"', auto=True),
          Task("양식 이름으로 거르기", "「양식으로 시작」 창의 양식 이름 칸 — 도우미에게 맡기면 text 에 찾을 말을 넣는다.", mark='data-aura="hwp-tpl-find"', auto=True),
          Task("가진 한글 파일 올리기", "「올리기」 — hwp·hwpx 를 고른 폴더에 둔다"),
          Task("여럿이 함께 고치기", "같은 문서를 여러 사람이 열면 칸(누름틀)마다 먼저 고친 사람이 쓰고 다른 칸은 동시에 고친다. 고치는 사람 이름이 칸에 보인다."),
          Task("AURA 에게 절 쓰기 맡기기", "문서를 연 채 채팅에서 「이 절 써 줘」 — AURA 가 그 절을 쓰면 열린 화면이 새 판을 불러온다"),
          Task("다른 문서 바로 열기", "기안기 머리의 「열기」 — 최근 문서·부서·내 폴더·드라이브 파일 창에서 골라 같은 화면에서 연다"),
          Task("폴더 정리", "「새 폴더」, 지운 문서는 부서 휴지통으로")),
         buttons=("새 문서", "새 폴더", "올리기"), roles=("dev", "staff"), name="한글 문서함",
         terms=(("칸(누름틀)", "양식에서 글을 넣는 자리 — 공동 편집은 칸 단위로 주고받는다"),
                ("새 판", "다른 사람이나 AURA 가 문서 구조를 바꿔 저장한 판 — 열린 화면이 저절로 다시 연다"))),
    Page("/team", "team.html", "조직 관리 — 왼쪽 부서 트리(맨 위 「전체 사용자」), 오른쪽 고른 것의 상세. 부서를 고르면 구성원(옮기기·등급 바꾸기·빼기·사람 추가)과 "
         "부서 설정(이름·상위 부서·하위 부서 추가·지우기·부서 RAG 문서 수·최소 등급·다른 부서에 열기·부서 드라이브), 「전체 사용자」 는 계정 목록(관리자), 관리자에게는 아래에 학교 계정 연결 현황(구글 앱 등록). "
         "관리자는 전부, 총괄 책임자는 맡은 처·단 아래만 고친다.",
         (Task("부서 찾기", "트리 위 「부서 찾기」 칸에 이름을 넣으면 트리가 걸러진다 — 도우미에게 맡기면 text 에 부서 이름을 넣는다.", mark="data-om-find", auto=True),
          Task("부서 고르기", "트리에서 부서 이름을 누르면 오른쪽에 구성원·부서 설정이 나온다 — 도우미에게 맡기면 text 에 부서 이름을 넣어 고른다.",
               mark='data-aura="team-dept"', auto=True, mark_in="../static/org-chart.js"),
          Task("부서 만들기(부서 RAG 가 함께 생김)", "맨 위는 트리 위 「부서」, 아래 부서는 그 부서의 「부서 설정」 › 「하위 부서 추가」", "", "dept_rag.create", "data-org-add"),
          Task("부서 옮기기", "트리에서 부서를 다른 부서 위로 끌어다 놓거나 「부서 설정」 › 「상위 부서 옮기기」"),
          Task("계정 만들기·고치기", "트리 맨 위 「전체 사용자」 › 「계정 추가」, 줄 끝 … › 「편집」"),
          Task("비밀번호 잊은 계정", "전체 사용자에서 계정을 열어 「임시 비밀번호 만들기」"),
          Task("부서 드라이브 연결", "그 부서의 「부서 설정」 › 부서 드라이브의 「구글 드라이브 연결」 — 총괄 책임자와 관리자"),
          Task("구글 앱 등록(처음 한 번)·학교 계정 연결 현황", "관리자만 — 화면 아래 「학교 계정 연결 현황」 의 앱 등록, 허용 도메인, 연결 삭제. "
               "등록 뒤에는 사용자마다 설정 화면에서 자기 학교 계정을 잇는다.", "google-links")),
         roles=("dev", "staff"), name="조직 관리"),
    Page("/settings", "settings.html", "내 설정 — 프로필(성명·계정·소속 부서)·보안(비밀번호)·구글 계정 연결. 사용자 메뉴의 「설정」. 한글 작업은 기본으로 웹 한글 기안기에서 연다.",
         (Task("성명 칸 채우기", "「프로필」 의 성명 칸 — 고치면 「저장」 이 켜진다. 도우미에게 맡기면 text 에 새 성명을 넣는다.", mark='data-aura="settings-name"', auto=True),
          Task("성명 저장", "「프로필」 아래 「저장」", mark="data-ms-save", final=True, verbs=("저장해", "저장하고", "저장까지", "바꿔 줘", "바꿔줘", "고쳐 줘", "고쳐줘")),
          Task("비밀번호 바꾸기", "「비밀번호 변경」 을 누르면 뜨는 창에서 — 비밀번호 칸은 담당자가 직접 친다(도우미는 창만 연다).",
               mark='data-aura="settings-pw"', auto=True),
          Task("내 학교 구글 계정 잇기", "「구글 계정」 카드의 「구글 계정 연결」 — 드라이브 가져오기와 보조 편집기(구글 독스)에 쓴다. 한글 문서는 한글 문서함에서 바로 고친다.")),
         buttons=("저장", "비밀번호 변경", "구글 계정 연결"), roles=("dev", "staff"), name="내 설정"),

    # ---- 개발(관리자) 화면 — 개발 메뉴 순서 ----
    Page("/dev", "dev.html", "개발 현황 — 등록 문서 한 줄·시스템 구성(장비 갈래 색), 기능별 구축 현황과 할 일(완료는 「완료 n개도 보기」), 모델 개발·학습 현황을 보는 첫 화면. 단계별 문서 수와 공동 편집 처리 방식은 처리 흐름에."),
    Page("/dev/coedit-demo", "coedit_demo.html", "공동 편집 시연 — 시연 계정 세 명이 같은 한글 문서를 나란히 열고, AURA 가 빈 문서에 가상 사업의 양식을 만들어 넣고 절을 차례로 쓰는 모습을 보여 주는 곳.",
         (Task("양식 만들기", "빈 문서에서 「AURA 양식 만들기」 — 27B 가 시연용 가상 사업의 짧은 사업계획서 양식을 설계해 이 문서에 넣고(세 화면에 함께 뜬다) 요약 칸을 가상 사업 값으로 채운다"),
          Task("AURA 가 쓰는 모습 보기", "양식을 넣은 뒤 「AURA 이어 쓰기」 — 27B 가 빈 절을 1절부터 차례로 세 개 쓰며, 머리줄에 지금 쓰는 절이 보이고 세 화면이 그 자리로 따라가 써 내려가는 것이 보인다"),
          Task("세 사람이 동시에 쓰는 모습 보기", "칸마다 아무 쪽이나 고치면(서식 포함) 다른 칸에 바로 보이고, 가만히 있는 화면은 쓰는 자리로 따라간다"),
          Task("처음 상태로", "「새 빈 문서」 — 한 번 더 눌러야 지운다")),
         buttons=("AURA 양식 만들기", "새 빈 문서")),
    Page("/dev/flow", "dev_flow.html", "처리 흐름 — 문서가 들어와 검색·답변·초안이 되고 학습 데이터가 되기까지 단계 한눈 그림(누르면 그 단계)과 단계별 지금 수치, 설명은 「자세히」. 「반입 점검」 칸에서 학교 원본(DGX) → 라이브러리까지의 수와 빈틈(빠진 원본)을 보고 목록을 내려받는다 — 점검·다시 들이기는 자동, 읽지 못한 원본은 원본 관리 「정리」 카드의 「처리 실패」 로."),
    Page("/dev/archive", "archive.html", "원본 관리 — 학교 원본 파일 전부(문서가 되기 전 것 포함)를 사업 → 연도 → 종류 → 처리 상태로 훑고, 처리하지 않는 원본을 정리하고, "
         "읽지 못한 원본을 다시 읽게 하는 곳. 교직원은 쓰지 않는다(문서는 라이브러리, 원본 파일은 문서의 「원본」).",
         (Task("처리하지 않는 원본 정리", "위 「정리」 카드의 「펼치기」 — 갈래(사진·압축·범위 밖·중복·크기 초과)를 체크하면 작업 줄의 「정리함으로」 또는 「남기기」. "
               "정리함은 원본 목록·건수에서만 빼고 되돌릴 수 있다.", "archive-cleanup"),
          Task("처리 실패 원본 다시 읽기", "「정리」 카드 안 「처리 실패」 의 「목록 보기」 — 줄을 체크하면 「다시 시도」(스캔본은 글자 읽기 맡기기). 다음 동기화 때 다시 읽는다.", "archive-cleanup"),
          Task("원본 찾기", "왼쪽 「사업별로 보기」 에서 사업을 고르고 연도·종류·처리 상태로 좁히거나, 검색 칸에 파일 이름·폴더를 넣는다. 줄의 「원본 열기」 로 파일을, 「등록 문서 보기」 로 이어진 문서를 연다. 경로를 누르면 장부 값(분류 상태·같은 파일 대표·처리 기록·목록에서 본 때)과 「반입 결과」(문서 하나의 개요·마스킹 기록·조각·검색 색인·그래프 연관, /dev/archive/doc/{id})가 펼쳐지고, 「범위」 를 장부 전체로 하면 플랫폼 사본·변환 부산물·정리함까지 보인다.",
               "archive-search")),
         buttons=("정리함으로", "남기기", "다시 시도", "목록 보기")),
    Page("/dev/rag", "dev_rag.html", "RAG 공간 — 답변할 때 찾는 자료 묶음(공통·부서·학생 RAG)과 그 검색 범위를 정하는 곳. RAG 카드나 부서 줄을 누르면 오른쪽에 상세(구성·최소 등급·쓰는 계정)가 나온다. "
         "규정의 학생 공개·등록은 라이브러리 「기준」 칩에서 한다.",
         (Task("학생 RAG 에 규정 넣기(학생 공개 지정)", "라이브러리 「기준」 칩(주소 /?type=all&use=criteria)에서 한다 — 규정 줄 끝 「…」 › 「학생 공개로」."),
          Task("부서 RAG 만들기", "조직 관리에서 부서를 더하면 부서 RAG 가 함께 생긴다. RAG 목록에서 문서 수·쓰는 계정을 본다.",
               "dept-rags", "dept_rag.create"),
          Task("부서 RAG 를 다른 부서에 열기", "부서 줄을 누른 뒤 상세의 「다른 부서에 열기」 칸에서 부서를 고른다(칩 × 로 거둠). 받는 쪽은 늘 부서 — "
               "그 부서 교직원이 이 부서 사업 문서·규정을 함께 찾고 본다(고치지는 못함). 관리자와 이 부서 총괄 책임자(조직 관리 › 부서 설정)가 한다.",
               "dept-rags", "dept_rag.lend"),
          Task("한 사람을 다른 부서에도 두기(겸직)", "조직 관리 › 그 부서 › 구성원의 「사람 추가」(겸직으로), 또는 전체 사용자에서 계정 편집 › 겸직 부서."),
          Task("이 계정이 이 규정을 찾는지 확인", "화면 끝 「검색 범위 시험」 을 눌러 계정이나 공간과 검색어를 넣는다.", "try")),
         buttons=("검색 범위 시험", "부서 더하기", "내려받기", "기준 문서"),
         terms=(("공통 RAG", "전 교직원이 찾는 기본 자료 묶음. 부서를 정하지 않은 문서는 모두 여기 든다."),
                ("부서 RAG", "그 부서 계정만 공통 RAG 와 함께 찾는 묶음. 문서 화면·문서 올리기에서 공개 범위를 그 부서로 선택하면 들어간다."),
                ("학생 RAG", "학생 계정이 찾는 묶음 — 학생 공개로 지정한 기준 문서(규정)만 든다. 사업 문서는 들어가지 않는다. 지정은 라이브러리 「기준」 칩에서."),
                ("겸직 부서", "교직원 계정에 소속 부서 말고 더 둔 부서 — 그 부서 문서도 찾고 작업한다. 조직 관리에서 정한다."),
                ("다른 부서에 열기", "부서 RAG 를 다른 부서가 함께 찾게 하는 것 — 부서 단위로만 준다(계정 하나에 주지 않는다). 받은 부서는 보기·검색만."))),
    Page("/graph/explore", "kg_explore.html", "그래프 탐색 — 사업·연차·문서·절 사이 관계(지식그래프)와 온톨로지를 그림으로 본다.",
         buttons=("지식그래프", "온톨로지", "전체 보기")),
    Page("/dev/train", "dev_data.html", "데이터·학습 — 탭 둘. 「검수·학습」: 문답 검수(Label Studio 프로젝트마다 완료/전체, 1분마다 갱신)와 「검수 가이드」, 학습셋 구성(갈래 × 학습·검증·시험 수, 갈래별 표본), 학습·평가(학습셋 표 — 상태·수·날짜·파일, 학습 상태·TensorBoard). "
         "「수집 자료」(/dev/train?tab=collect): 베타·실사용에서 쌓인 대화·답 실행 기록·답 평가·에이전트 작업 기록의 건수·기간을 보고, "
         "기간·항목을 골라 가린 사본 묶음(zip)으로 한 번에 내려받는 곳. 사람은 가명, 줄마다 train·eval·heldout 나눔.",
         (Task("검수 방법 보기", "「문답 검수」 카드 오른쪽 위 「검수 가이드」 — 라벨 스튜디오에서 채택·수정·폐기를 정하는 순서와 화면 그림", mark="data-review-guide", auto=True),
          Task("검수 진행 보기", "「문답 검수」 표 — 프로젝트마다 완료/전체와 진행 막대, 「열기 ↗」 로 그 프로젝트"),
          Task("학습셋 표본 보기", "「학습셋 구성」 표의 갈래 줄 끝 「표본 n건」 — 질문과 학습할 답이 줄 아래에 펼쳐진다"),
          Task("수집 자료 탭 열기", "화면 위 탭 줄의 「수집 자료」", mark='data-train-tab="collect"', auto=True),
          Task("개선용 자료 한꺼번에 내려받기", "「수집 자료」 탭에서 항목을 고르고 기간을 정한 뒤 「선택 항목 내려받기 묶음 만들기」, 끝나면 「만든 묶음」에서 「내려받기」. "
               "묶음에는 항목마다 같은 필드 차례의 JSONL 과 필드 뜻·가림 규칙을 적은 manifest.json 이 든다"),
          Task("항목의 필드와 표본 보기", "「수집 자료」 탭의 항목 줄 오른쪽 「구조·표본 보기」 — 필드 이름·형식·뜻과 가린 앞 3건")),
         buttons=("검수·학습", "수집 자료", "검수 가이드", "선택 항목 내려받기 묶음 만들기", "내려받기", "구조·표본 보기")),
    Page("/dev/tools", "dev_tools.html", "도구·모델 연결 — 학습 도구 주소·계정, LLM 연결과 단계별 모델, 외부 연결(웹 검색 제공자).",
         (Task("모델 서버 연결 확인", "LLM 연결 카드의 「연결 확인」"),
          Task("문서 작업 기본 서버 바꾸기", "LLM 연결 카드의 「기본 서버로 지정」"))),
    Page("/dev/quality", "dev_quality.html", "품질·성능 — 맨 위 요약 타일 넷(규정 검색 운영 구성 R@1·MRR@10, 사업 문서 검색 정답 문서 상위 5건의 운영 방식 대 풀텍스트, 모델 비교 결론, 추출 품질 남은 신고)과 "
         "영역마다 짧은 카드(핵심 수치와 핵심 줄 몇 개). 전체 표·측정 방법·비교 도구·설명은 카드마다 「자세히」 하나로 연다.",
         (Task("규정 검색 방식별 전체 표 보기", "「규정 검색 품질」 카드의 「자세히」 — 방식별 R@1·R@5·R@10·MRR@10 전체와 측정 방법 메모, 구성이 달라진 지난 측정"),
          Task("질문 하나로 검색 방식 비교", "「사업 문서 검색」 카드의 「자세히」 → 「질문 하나로 비교」 — 질문과 문서 수(1~20)를 넣고 「비교」, 풀텍스트·온톨로지·27B 질문 읽기 세 칸에 걸린 시간과 찾은 문서가 나란히 나온다"),
          Task("GraphRAG 가 왜 느린지·왜 직접 만들었는지 보기", "「사업 문서 검색」 카드의 「자세히」 화면 아래 두 카드"),
          Task("모델 비교 보기", "「모델 비교」 카드에서 결론과 핵심 줄을 보고, 「자세히」 로 분석·묶음별 표·표본·만든 문서·지난 실행을 연다", "model-compare"),
          Task("추출 문제 처리 완료 적기", "남은 신고가 있을 때 「추출 품질 백로그」 카드에서 처리 내용을 적고 「처리 완료」")),
         buttons=("자세히", "처리 완료")),
    Page("/dev/quality/regulation", "dev_quality_regulation.html", "규정 검색 품질 — 품질·성능 「규정 검색 품질」 카드의 자세히. 방식별 전체 표(운영 구성 줄 강조), 측정 방법 메모, 지금 색인과 구성이 다른 지난 측정은 회색으로."),
    Page("/dev/quality/grant-search", "dev_quality_grant.html", "사업 문서 검색 — 품질·성능 「사업 문서 검색」 카드의 자세히. 풀텍스트 RAG 와 GraphRAG 비교 전체 표, 질문 하나로 세 방식 비교, 왜 느린가·왜 기성 도구 대신 직접 만들었나.",
         (Task("질문 하나로 검색 방식 비교", "「질문 하나로 비교」 카드에 질문과 문서 수(1~20)를 넣고 「비교」", "rcForm"),),
         buttons=("비교",)),
    Page("/dev/models/compare", "dev_model_compare.html", "모델 비교 — 품질·성능 「모델 비교」 카드의 자세히. 비교 실행마다 결론 한 줄과 「분석」(문제·까닭·다음 방향, 실행 폴더의 analysis.md), 둘 중 나은 답 고르기·항목별 평균·지표·속도·문항 종류별 표, 표본 출력과 판정 이유, 갈래별로 만든 문서 내려받기.",
         buttons=("내려받기",)),
    Page("/dev/pii", "dev_pii.html", "개인정보·반출 점검 — 처리 원칙(ADR-0006) 한 줄, 내부 마스킹 설정, 잔여 검사·자가 점검.",
         (Task("가림 뒤 남은 개인정보 찾기", "「점검 도구」 탭의 「저장 본문 잔여 검사」 결과 — 일요일 새벽에 자동으로 돈다"),
          Task("마스킹 설정 바꾸기", "「내부 마스킹 설정」 에서 고친 뒤 「설정 저장」")),
         buttons=("설정 저장",)),
    Page("/dev/docs", "dev_docs.html", "논문·설계 문서 — 설계 결정(ADR)·논문 자료·주간 보고를 찾아본다.",
         buttons=("보고서 생성",)),
)

BY_PATH = {p.path: p for p in PAGES}


def page_for(path: str) -> Page | None:
    """주소(경로)에 맞는 항목 — /project/7 은 /project/{id}, /dev/rag/… 처럼 하위 경로는 위 화면."""
    path = (path or "").split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
    if path == "/":
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
        kind = (" · 마지막 단추(담당자가 " + "·".join(f"「{v}」" for v in t.verbs[:3]) + " 처럼 시켰을 때만 바로 누름)") if t.final else \
               (" · 바로 할 수 있음" if t.auto and t.selector else "")
        lines.append(f"- {t.what}: {t.how}{where}{el}{kind}{can}")
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
