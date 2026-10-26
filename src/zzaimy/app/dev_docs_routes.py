"""문서·보고(/dev/docs·/dev/doc/·/dev/paper·/dev/history·/dev/weekly*) — 논문 자료·설계 결정·기술 검토·측정 기록 목록과 열람,
변경 이력(깃 커밋), 주간 보고서(자동 작성·피드백 반영·내려받기).

- 개발자(dev)만 — /dev 경로는 main.check_auth 가 막는다.
- 화면 틀·화면 공통 맥락(page_ctx)과 문서 도우미 묶음(docs — make_docs_helpers 가 앱마다 만든다)은
  main.create_app 이 app.state 에 올려 둔 것을 읽는다. 개발 현황(/dev)과 함께 쓰는 도우미(docs/ 읽기·결정/메모 목록·
  논문 자료 목록·마지막 변경일·dev-now 나누기)는 개발 현황 묶음(app/dev_overview_routes)에, 마크다운 렌더는 main 에 두고
  묶음에 넘겨 받는다.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from zzaimy.app import storage

from zzaimy.app.dev_overview_routes import PAPER_PLANNING

router = APIRouter()


# 문서 한 줄 설명 — 목록에서 제목 아래에 보인다. 없는 파일은 설명 없이 제목만.
_DOC_DESC = {
    "paper/논문/README.md": "장별 원고 — 웹 기안기 논문 문서로 옮기는 원본, 장마다 상태와 그림·표 목록",
    "paper/프로젝트-기획서.md": "제출용 기획서 — 목표·개발 내용·장비·일정·기대 효과",
    "paper/논문-원재료.md": "먼저 읽을 문서 — 문서 처리 → 검색·지식(RAG·온톨로지·GraphRAG) → 모델 → 화면 → 학습 → 개인정보 → 수치, 어떻게 구현했는지",
    "paper/실험-로그.md": "실험 시계열 — 무엇을 어떻게 재서 어떤 판정을 했는지",
    "paper/논문-양식-가이드.md": "논문집 형식과 목차 대응",
    "paper/중간-발표자료.md": "중간 발표 — 개요·계획·진척도·결과물·변경·남은 일·일정·Q&A",
    "paper/발표-자료-모음.md": "PPT 장마다 넣을 수치·비교 표·화면 캡처와 그 화면 위치",
    "paper/제안발표-내용.md": "제안 발표 슬라이드 원고",
    "HANDOFF.md": "장비·접속 요령, 사람 손이 필요한 남은 일",
    "dev-now.md": "지금 하는 일·다음 일·최근 작업",
    "architecture.md": "지금 구조의 정본 — 데이터 흐름·검색·27B 문서 작업 에이전트·웹 기안기 공동 편집(칸 글 DB → hwpx 써 넣기)·권한",
    "model-plan.md": "모델 4종의 학습 데이터·순서·채택 기준",
    "eval-plan.md": "평가 계획 — 평가셋·지표·베이스라인 절차",
    "capstone-plan.md": "13주 실행 계획 — 주차별 목표와 현황",
    "pilot-plan.md": "실물 문서 파일럿 계획",
    "risks.md": "위험 관리 — 개인정보·환각·파싱·운영",
    "quality-system.md": "품질 5계층 체계 — 문제를 어느 계층에서 막는가",
    "workflow.md": "저장소·결정·보고 방식",
    "retrieval-baseline-mini.md": "검색 기준선(9/4) — 어휘·임베딩·하이브리드",
    "retrieval-weight-sweep.md": "하이브리드 가중 스윕(9/4)",
    "rerank-baseline.md": "리랭커 학습 전 기준선(9/4)",
    "llm-rerank-eval.md": "리랭커 학습본·LLM 리랭킹 비교(9/20)",
    "embed-v0-report.md": "임베딩 v0 리허설(9/3, 미배포)",
    "ocr-cer-bench.md": "OCR 어절 정확도 벤치(9/7)",
    "ocr-duel.md": "OCR 엔진 대결(9/8)",
    "model-cards/zzaimy-embed-v2.md": "운영 임베딩 학습본 카드",
    "model-cards/zzaimy-rerank-v1.md": "운영 리랭커 학습본 카드",
    "notes/2026-09-22-external-split-and-blockchain.md": "외부 모델 반분 위탁·블록체인 — 채택 안 함(이유·대안)",
    "notes/2026-09-22-vllm-vs-ollama.md": "서빙 엔진 비교 — 실측과 vLLM 통일 이유",
    "notes/2026-09-22-access-controlled-knowledge-base.md": "권한별 지식 베이스 — 권한 밖 질문(학생·타 부서 개인정보)에 어떻게 응답하나, 열람 등급·부서 범위",
    "notes/meeting-w2-20260908.md": "2주차 미팅 메모와 정합 분석",
    "notes/2026-09-02-ocr-refs.md": "OCR 엔진 후보·스캔 전처리·고도화 레퍼런스 조사",
    "notes/2026-09-02-external-doc-sources.md": "공개 문서 소스 조사",
    "notes/table-layout.md": "표·레이아웃 원본 불일치 원인 진단",
    "notes/automation-pipeline.md": "자동화 파이프라인 목표와 현재 위치",
    "notes/backlog.md": "개선 백로그(9/8 실사용 리뷰)",
    "notes/uiux-audit.md": "UI/UX 전수 조사",
    "notes/ui-glossary.md": "화면 문구 용어집",
    "notes/hwpx-agent-skills.md": "한글 에이전트 스킬 수집 노트",
}
# 기술 검토(조사·판단 기록)와 작업 메모(백로그·용어집 같은 내부 메모)를 나눈다
_NOTE_MEMO = {"backlog.md", "ui-glossary.md", "uiux-audit.md", "hwpx-agent-skills.md",
              "automation-pipeline.md"}


def _doc_topics(docs_dir: Path) -> dict[str, tuple[str, str]]:
    """주제별 묶음 — docs/ 기준 경로 → (주제, 지금 상태).

    원본은 결정 기록 색인(decisions/README.md 의 「주제별 묶음」, 표의 셋째 칸이 지금 상태)과
    기록 자료 색인(notes/README.md). 두 색인의 「### 주제」 아래 링크를 읽는다 — 목록 화면은 묶음 이름을 설명 앞에 붙인다.
    """
    out: dict[str, tuple[str, str]] = {}
    for sub in ("decisions", "notes"):
        try:
            text = (docs_dir / sub / "README.md").read_text(encoding="utf-8")
        except OSError:
            continue
        topic = ""
        for ln in text.splitlines():
            if ln.startswith("### "):
                topic = ln[4:].strip()
                continue
            if ln.startswith("## "):
                topic = ""
            if not topic:
                continue
            for link in re.findall(r"\]\(([^)#\s]+\.md)\)", ln):
                rel = os.path.normpath(f"{sub}/{link}").replace(os.sep, "/")
                cells = [c.strip() for c in ln.strip().strip("|").split("|")] if ln.lstrip().startswith("|") else []
                out.setdefault(rel, (topic, cells[2] if len(cells) >= 3 else ""))
    return out


_WEEKLY_PROMPT = """너는 대학 캡스톤 프로젝트(행정문서 AI 플랫폼)의 주간 업무 보고를 쓴다. 독자는 지도교수이고
개발자가 아니다. 아래 원자료를 바탕으로 쓰되 원자료를 옮겨 적지 말고, 무엇을 했고 무엇이 되었는지만 짧게 쓴다.
피드백(미팅 결정·지도교수 의견)이 있으면 그 내용을 먼저 반영하고 그에 대해 한 일을 잇는다.

{sections}

쓰는 법:
- 【이번 주 한 일】은 아래 [기준 틀]의 큰 축(플랫폼·모델(LLM)·데이터 같은 것) 단위로 3~5개. 큰 항목은 "무엇 — 결과" 한 줄
  (예: "서빙 장비 구성 완료 — 젯슨 토르 2대에 27B 모델, DGX는 학습 전용"). 항목은 굵직한 구축 단위로만 잡는다 —
  장비 구성, 플랫폼 구축, 문서 반입 체계, 검색 구축, 모델 준비처럼 "무엇을 구축·준비·구성했다"로 끝나는 제목 한 줄.
  항목은 5~6개, 세부 줄은 항목당 2~3줄. 개조식으로 쓴다 — 번호는 "1)" 꼴, 세부는 "- " 로 시작, 문장은 "~했습니다" 대신
  명사형이나 "~함/~완료/~확인" 으로 끝맺고 한 줄 40자 안팎으로 짧게 끊는다(좁은 화면에서도 한눈에 들어오게).
  예)
  1) 서빙 장비 구성 완료 — 젯슨 토르 2대 + DGX 학습 전용
  - 27B 양자화 판 적용, 원본 대비 3~4배 속도 확인
  - 검토·판독은 토르 03, 대화·초안은 토르 02로 분담
  짧게 줄이려고 사실을 빼지 말고, 늘리려고 없는 말을 넣지도 않는다.
  항목 순서는 장비 구성(토르 2대·DGX) → 표·이미지 OCR 고도화 작업(판독 테스트와 측정 결과 — 무엇으로 시험했고
  속도·정확도가 어땠는지) → 플랫폼 구축(화면·자동화·보고 기능) → 문서 반입·검색 구축(반입 체계, 검색 모델 학습본,
  정확도) → 모델 준비(모델 통일·베이스라인·학습 경로). 그 주에 해당 없는 항목은 뺀다.
  항목 제목은 짧은 명사구에 결과를 붙인다 — "토르·DGX 장비 구성 완료", "표·이미지 OCR 판독 테스트 및 측정"처럼.
  과정 서술(원인을 파악해 무효 처리했다, 보고 방식을 개선했다, 규칙을 고쳤다 같은 말)은
  쓰지 않는다 — 읽는 사람은 결과만 안다. 수치는 항목당 하나까지, 그것도 뜻이 바로 보이는 것만
  (예: "검색 정확도 75%" 는 되고 "R@1 0.750"·"MRR" 은 안 된다). 원자료에 없는 수치·기간·효과("업무 시간 50% 단축" 따위)는
  절대 만들지 않는다 — 수치가 없으면 수치 없이 쓴다.
- 【다음 주 계획】은 3~4개, "1)" 번호로 각 한 줄, 명사구 목표로("실물 문서 반입 및 파일럿", "파인튜닝 착수", "규정 RAG 구축"처럼).
  [다음에 할 일] 원자료에서만 고른다.
- 【이슈/건의사항】은 실제 막힌 것·요청할 것만. 없으면 "없음".
- 피드백에 질문이 있으면(예: "왜 이 모델인가", "표·이미지는 어떻게 처리하나") 【이번 주 한 일】 맨 앞에
  "피드백 답변" 항목을 두고 질문마다 "- " 한두 줄로 원자료에 근거해 답한다 — 모델 선정 이유는 [기준 틀]의 근거(멀티모달·학습 경로·
  벤치마크 수치와 그 한계)를 그대로 쓴다. 라이선스·명명 같은 말은 쓰지 않는다.
  표·이미지 같은 처리 방식 질문에는 "표는 구조를 유지한 채 읽고, 스캔·이미지는 27B 모델이 판독하며, 품질은 원본 대조와
  자가 점검으로 확인한다" 수준으로 답한다 — 내부 결함이나 고친 과정(글자층·잡음·제한 시간·비율 같은 말)은 쓰지 않는다. 질문을 이슈나 건의사항으로 되돌리지 않는다.
- 쓰지 않는 것: 파일·스크립트·함수 이름, 결정 번호(ADR 등)·요청 번호(K61 같은 것)·작업자 별칭, 영문 약어·개발 용어(폴백·타임아웃·리랭커·임베딩 같은 말),
  내부에서만 쓰는 낱말(잎 절·필드·칸 이름·v1/v2 같은 판 이름 등) — 지도교수가 처음 읽어도 뜻이 통하는 말로만. 「~처리 중」 처럼 상태가 애매한 말 대신
  무엇이 되었는지(사용자가 무엇을 할 수 있게 됐는지)를 쓴다. 결정한 일은 결정과 까닭을 한 줄로(예: 「1차 학습본은 운영에 넣지 않기로 — 문서 작성 기능이 동작하지 않음」).
  꼭 필요하면 우리말로 풀어 쓴다(예: "검색 결과를 다시 줄 세우는 모델").
- 숫자는 원자료에 있는 것만. 과장·수식어·볼드·이모지 금지. 【】 제목은 양식 그대로.

[기준 틀 — 기획서 개발 내용]
{frame}

[이번 주 피드백·미팅 결정]
{feedback}

[지금 하는 일·이번 주 한 일 — 현재 상태 문서]
{now}

[다음에 할 일 — 현재 상태 문서]
{plans}

[참고: 이번 주 작업 기록 제목만]
{changelog}"""

_WEEKLY_SECTIONS_DEFAULT = """마크다운으로, 아래 섹션 구성 그대로:
## 요약
이번 주 작업을 3~4문장으로. 무엇이 가장 큰 진전인지부터.
## 주요 성과
4~7개 항목. 각 항목은 "무엇을 어떻게 해서 무엇이 개선되었다" 형태의 1~2문장.
전문용어는 괄호로 짧게 풀어 쓴다. 예: "스캔 문서에 투명 글자층(이미지 위에 보이지 않는
글자를 입혀 복사·검색이 되게 하는 방식)을 입혀, 스캔본에서도 본문을 긁어 쓸 수 있게 했다."
## 문제와 대응
이번 주 발생한 문제 1~3건과 어떻게 해결했는지.
## 다음 주 계획
3~5개 항목."""


def make_docs_helpers(_DOCS_DIR: Path, db_path, *, _dev_read, _md_view, _data_overview, _dev_doc_list, _dev_papers,
                      _PAPER_LABELS: dict, _doc_last_changed, _dev_now_parts) -> SimpleNamespace:
    """앱마다 하나 — 깃 이력 캐시·주간 보고 작성 상태(뒤에서 도는 작성 스레드)를 앱 안에 둔다."""
    from zzaimy.app.main import _tidy_weekly_md   # 시험이 main 에서 바로 부른다 — 정본은 main

    def _eval_md(name: str) -> str:
        """기계가 쓴 사본(data/platform/eval/) 먼저, 없으면 저장소 사본(docs/)."""
        from zzaimy.eval.retrieval_eval import MARKDOWN_PATH

        try:
            path = MARKDOWN_PATH.parent / name
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            pass
        return _dev_read(name)

    # 커밋이 건드린 파일의 영역 — 변경 이력 줄마다 붙는 표식(어디를 고쳤는지 한눈에)
    _AREA_RULES = (
        ("src/zzaimy/app/templates/", "화면"), ("src/", "코드"), ("scripts/", "스크립트"),
        ("tests/", "테스트"), ("docs/decisions/", "결정"), ("docs/", "문서"), ("configs/", "설정"),
    )
    _git_cache: dict = {}

    def _commit_areas(paths: list[str]) -> list[str]:
        seen: list[str] = []
        for path in paths:
            area = next((a for pre, a in _AREA_RULES if path.startswith(pre)), "기타")
            if area not in seen:
                seen.append(area)
        return seen

    def _git_history() -> list[dict]:
        """커밋 이력을 깃에서 직접 읽는다 — 한 줄 = {day, time, subject, hash, areas, files}.
        깃이 정본이다(손으로 쓰던 사본은 낡아 지웠다).
        HEAD 가 같으면 캐시를 쓴다."""
        import subprocess

        root = _DOCS_DIR.parent
        try:
            head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                  timeout=5, cwd=str(root)).stdout.strip()
            if not head:
                return []
            if _git_cache.get("head") == head:
                return _git_cache["items"]
            r = subprocess.run(
                ["git", "log", "--no-merges", "--name-only", "--date=format:%Y-%m-%d %H:%M",
                 "--format=%x00%h\t%ad\t%s"],
                capture_output=True, text=True, timeout=15, cwd=str(root))
        except Exception:
            return []
        items: list[dict] = []
        for blk in (r.stdout or "").split("\x00")[1:]:
            head_ln, _, rest = blk.partition("\n")
            parts = head_ln.split("\t", 2)
            if len(parts) < 3:
                continue
            h, when, subject = parts
            files = [ln.strip() for ln in rest.splitlines() if ln.strip()]
            items.append({"hash": h, "day": when[:10], "time": when[11:16], "subject": subject,
                          "files": len(files), "areas": _commit_areas(files)})
        _git_cache.update(head=head, items=items)
        return items

    def _dev_history() -> list[dict]:
        """변경 이력 — 커밋을 날짜별로 묶는다(깃이 정본 — 손으로 쓰던 사본 dev-changelog.md 는 09-21 에 멈춰 10-18 지웠다)."""
        from collections import OrderedDict

        days: OrderedDict[str, list[dict]] = OrderedDict()
        items = _git_history()
        for it in items:
            days.setdefault(it["day"], []).append(it)
        return [{"day": d, "items": items} for d, items in days.items()]

    def _dev_doc_view(text: str, fname: str) -> dict:
        """열람 화면(dev_paper.html)용 — _md_view 는 허브 작업 기록과 공용이라 손대지 않고,
        문서 열람에만 필요한 손질을 앞뒤로 붙인다.

        앞: 줄바꿈으로 감싼(hard-wrap) 문단을 한 줄로 합쳐 문단 하나가 <p> 하나가 되게 한다
            (펜스 안·표·목록·제목·인용·구분선은 그대로).
        뒤: 첫 제목(# )은 머리에 보이므로 본문에서 빼고, `인라인 코드`·> 인용을 살리며,
            ##·### 제목에 id 를 달아 목차를 만든다.
        """
        import html as _html

        from markupsafe import Markup as _M

        block = re.compile(r"^(\s*[-*+]\s|#{1,6}\s|\s*\||>|\s*[-*_]{3,}\s*$)")
        para_start = re.compile(r"^\s*\d+[.)]\s")
        joined: list[str] = []
        buf: list[str] = []
        fence = False
        for ln in text.splitlines():
            if ln.lstrip().startswith("```"):
                fence = not fence
            if fence or ln.lstrip().startswith("```") or not ln.strip() or block.match(ln):
                if buf:
                    joined.append(" ".join(buf))
                    buf = []
                joined.append(ln)
            elif para_start.match(ln):
                if buf:
                    joined.append(" ".join(buf))
                buf = [ln.strip()]
            else:
                buf.append(ln.strip())
        if buf:
            joined.append(" ".join(buf))

        code_re = re.compile(r"`([^`<>\n]+)`")
        tag_re = re.compile(r"<[^>]+>")
        out: list[str] = []
        toc: list[dict] = []
        quote: list[str] = []
        title = ""
        in_pre = False
        for raw in str(_md_view("\n".join(joined))).split("\n"):
            if in_pre or raw.startswith("<pre"):
                out.append(raw)
                in_pre = "</pre>" not in raw
                continue
            m = re.match(r'<p style="[^"]*">&gt;\s?(.*)</p>$', raw)
            if m:
                quote.append("<p>" + code_re.sub(r"<code>\1</code>", m.group(1)) + "</p>")
                continue
            if quote:
                out.append("<blockquote>" + "".join(quote) + "</blockquote>")
                quote = []
            raw = code_re.sub(r"<code>\1</code>", raw)
            if re.match(r'<p style="[^"]*">[-*_]{3,}</p>$', raw):
                out.append("<hr>")
                continue
            if raw.startswith('<div class="table-scroll">'):
                # 표 칸은 _md_view 가 굵게를 살리지 않는다 — 여기서만 살린다
                raw = re.sub(r"\*\*([^<>*]+?)\*\*", r"<b>\1</b>", raw)
            m = re.match(r"<h3 [^>]*>(.*)</h3>$", raw)
            if m and not title and not out:
                title = _html.unescape(tag_re.sub("", m.group(1)))
                continue
            m = re.match(r"<(h4|h5)\b([^>]*)>(.*)</\1>$", raw)
            if m:
                sid = f"s{len(toc) + 1}"
                toc.append({
                    "id": sid, "level": 2 if m.group(1) == "h4" else 3,
                    "text": _html.unescape(tag_re.sub("", m.group(3))),
                })
                raw = f'<{m.group(1)} id="{sid}"{m.group(2)}>{m.group(3)}</{m.group(1)}>'
            out.append(raw)
        if quote:
            out.append("<blockquote>" + "".join(quote) + "</blockquote>")
        return {"title": title or fname, "html": _M("\n".join(out)), "toc": toc}

    _WEEKLY_TEMPLATE = _DOCS_DIR / "weekly" / "양식.md"


    def _weekly_sections(template: Path | None = None) -> tuple[str, bool]:
        """보고서 항목 구성 — 양식 파일(docs/weekly/양식.md)이 있으면 그 구성을 그대로, 없으면 기본."""
        f = template or _WEEKLY_TEMPLATE
        try:
            text = f.read_text(encoding="utf-8").strip() if f.exists() else ""
        except OSError:
            text = ""
        if text:
            return ("마크다운으로, 아래 양식의 항목 구성·순서·제목을 그대로 따른다. 양식의 안내문은 지침이지 본문이 아니다:\n"
                    + text), True
        return _WEEKLY_SECTIONS_DEFAULT, False

    def _weekly_feedback_path(monday: str) -> Path:
        return _weekly_dir() / f"{monday}.feedback.md"

    def _weekly_feedback(monday: str) -> str:
        f = _weekly_feedback_path(monday)
        try:
            return f.read_text(encoding="utf-8").strip() if f.exists() else ""
        except OSError:
            return ""

    def _weekly_dir() -> Path:
        return storage.report_dir(Path(db_path).parent, "주간")

    def _project_week(day: str):
        """날짜가 사업 기간의 몇 주차인지 — (주차, 그 주 첫날, 끝날). 시작일은 실행 계획(capstone-plan.md)의 「가용 기간」에서
        읽는다. 계획에 기간이 없거나 시작 전 날짜면 None."""
        from datetime import date as _date
        from datetime import timedelta as _td
        try:
            got = re.search(r"가용 기간[^\n]*?(\d{4})-(\d{2})-(\d{2})", _dev_read("capstone-plan.md"))
            start = _date(int(got.group(1)), int(got.group(2)), int(got.group(3))) if got else None
            d = _date.fromisoformat(day)
        except (ValueError, TypeError, AttributeError):
            return None
        if start is None or d < start:
            return None
        n = (d - start).days // 7 + 1
        first = start + _td(days=7 * (n - 1))
        return n, first, first + _td(days=6)

    def _weekly_list() -> list[dict]:
        """지금까지의 주간 보고 — 생성본(data/platform/weekly)과 수기본(docs/weekly)을 한 목록으로, 최근 것부터."""
        out: list[dict] = []
        seen: set[str] = set()
        for src, folder in (("생성", _weekly_dir()), ("수기", _DOCS_DIR / "weekly")):
            if not folder.exists():
                continue
            for f in folder.glob("*.md"):
                if f.name == "양식.md" or f.name.endswith(".feedback.md") or f.stem in seen:
                    continue
                m = re.search(r"\d{4}-\d{2}-\d{2}", f.stem)
                title = f.stem
                try:
                    first = next((ln[2:].strip() for ln in f.read_text(encoding="utf-8").splitlines()
                                  if ln.startswith("# ")), "")
                except OSError:
                    first = ""
                seen.add(f.stem)
                wk = _project_week(m.group(0)) if m else None
                # 표시 제목은 파일 날짜에서 같은 규칙으로 — 본문 첫 제목은 쓰는 사람마다 꼴이 달라 목록이 들쭉날쭉했다
                shown = (f"{wk[0]}주차 주간 보고 ({wk[1]:%m-%d} ~ {wk[2]:%m-%d})" if wk
                         else first or f"주간 보고 {title}")
                out.append({"href": f"/dev/weekly/{f.stem}", "title": shown, "heading": first,
                            "num": "", "status": src, "date": m.group(0) if m else "",
                            "stem": f.stem, "kind": src})
        out.sort(key=lambda d: d["date"], reverse=True)
        return out

    def _compose_weekly(monday: str, today: str, fresh: bool = False,
                        revision: str = "", feedback: str = "") -> str:
        """주간 보고 본문 — LLM이 원자료를 읽고 문장으로 쓴다. 결과는 캐시."""
        cache_dir = _weekly_dir()
        cache_dir.mkdir(exist_ok=True)
        cache = cache_dir / f"{monday}.md"
        if cache.exists() and not fresh:
            return cache.read_text(encoding="utf-8")

        # 원자료는 사람이 읽는 현재 상태 문서가 먼저다 — 커밋 기록은 제목만, 이번 주 것만 참고로 준다.
        dn = _dev_read("dev-now.md")

        def _section(text: str, head: str) -> str:
            if head not in text:
                return ""
            return text.split(head, 1)[1].split("\n## ", 1)[0].strip()

        now_text = _section(dn, "## 지금 하는 일")
        recent = _section(dn, "## 최근 작업")
        # 이번 주 날짜(월요일 이후)의 '### YYYY-MM-DD' 묶음만
        week_parts = []
        for chunk in recent.split("\n### ")[1:] if recent else []:
            day = chunk[:10]
            if day >= monday:
                week_parts.append("### " + chunk.strip())
        if week_parts:
            now_text = (now_text + "\n\n이번 주 한 일:\n" + "\n".join(week_parts)).strip()
        plans = _section(dn, "## 다음에 할 일")
        # 기준 틀 — 기획서의 개발 내용(플랫폼·모델·데이터 축). 보고는 이 축의 높이에서 쓴다.
        plan_doc = _dev_read("paper/프로젝트-기획서.md")
        frame = (_section(plan_doc, "## 개발 내용")[:2500]
                 + "\n\n[추진 일정]\n" + _section(plan_doc, "## 추진 일정")[:1200])   # 일정 질문에도 답할 수 있게
        week_lines = [f"- {it['day'][5:]} {it['subject']}" for it in _git_history() if it["day"] >= monday]
        changelog = "\n".join(week_lines[:60])[:3000]
        body: str | None = None
        try:
            from zzaimy.generate.client import VllmClient

            client = VllmClient(role="answer")
            resp = client.client.chat.completions.create(
                model=client.model,
                messages=[{"role": "user", "content": (
                    "다음 주간 보고서를 수정 요청에 맞게 개선하세요. 보고 기간과 기존 사실·수치를 유지하고, "
                    "근거 없는 실적을 추가하지 마세요. 설명 없이 수정된 보고서 전체를 마크다운으로 반환하세요.\n\n"
                    f"[기존 보고서]\n{revision}\n\n[수정 요청]\n{feedback}"
                ) if revision else _WEEKLY_PROMPT.format(
                    sections=_weekly_sections()[0],
                    frame=frame or "(없음)",
                    feedback=_weekly_feedback(monday) or "(없음)",
                    now=now_text[:5000] or "(없음)",
                    plans=plans[:2000] or "(없음)",
                    changelog=changelog or "(없음)",
                )}],
                temperature=0.3, max_tokens=1600,
                extra_body=getattr(client, "_extra", {}),
                timeout=float(os.environ.get("ZZAIMY_REVIEW_TIMEOUT", "600")),   # 1,600 토큰은 3분을 넘긴다
            )
            body = (resp.choices[0].message.content or "").strip()
        except Exception:
            body = None
        if not body:
            # 모델이 없으면 원자료(현재 상태 문서)를 그대로 보인다 — 커밋 목록을 쏟지 않는다
            body = ("(모델이 응답하지 않아 자동 작성을 못 했습니다. 아래는 현재 상태 문서의 내용입니다 — "
                    "모델 연결 뒤 '다시 만들기'를 누르십시오.)\n\n" + (now_text or "(기록 없음)"))
            return body                      # 실패본은 캐시하지 않는다

        # 정량 지표는 결정론으로 그대로 붙인다.
        # 검색 품질 표는 기계가 마지막으로 쓴 사본을 먼저 본다 — 평가는 운영에서 돌고
        # 저장소 사본은 --report 로만 갱신되므로, 산출물 폴더가 최신이다.
        base_tbl = "\n".join(
            ln for ln in _eval_md("retrieval-baseline-mini.md").splitlines()
            if ln.startswith("|")
        )
        embed_tbl = "\n".join(
            ln for ln in _dev_read("embed-v0-report.md").splitlines()
            if ln.startswith("|")
        )
        scale = " · ".join(f"{t['label']} {t['value']}" for t in _data_overview()["tiles"])
        # 양식 밖의 것은 붙이지 않는다(읽기 쉽게). 기간은 제목에 있다.
        full = _tidy_weekly_md(body) + "\n"
        _ = (base_tbl, embed_tbl, scale)      # 지표는 화면(측정 기록)에서 본다
        if cache.exists():
            import shutil
            from datetime import datetime as _dt
            history = cache_dir / "history" / monday
            history.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cache, history / f"{_dt.now():%Y%m%d-%H%M%S-%f}.md")
        pending = cache.with_suffix(".tmp")
        pending.write_text(full, encoding="utf-8")
        pending.replace(cache)
        return full

    # 자동 작성 — 논문 자료 화면을 열면 이번 주 보고서가 없을 때 뒤에서 만든다.
    # 피드백 저장·다시 만들기도 뒤에서 돌고, 화면은 끝나면 스스로 새로 고친다.
    _weekly_state: dict = {"monday": "", "running": False, "started": "", "error": ""}
    import threading as _weekly_threading
    _weekly_lock = _weekly_threading.Lock()

    def _weekly_can_auto() -> bool:
        """답변 용도로 부를 모델 연결이 있는가 — 없으면 자동 작성을 시도하지 않고 화면에 그 사실을 적는다."""
        from zzaimy.generate import llm_connections as lc

        try:
            return bool(lc.role_conn("answer") or lc.active())
        except Exception:
            return False

    def _weekly_kick(monday: str, today: str, fresh: bool = False,
                     revision: str = "", feedback: str = "") -> bool:
        """이번 주 보고서를 뒤에서 만든다. 이미 만드는 중이면 False."""
        import threading
        from datetime import datetime as _dt

        with _weekly_lock:
            if _weekly_state["running"]:
                return False
            _weekly_state.update(monday=monday, running=True, started=_dt.now().strftime("%H:%M"), error="")

        def run():
            try:
                body = _compose_weekly(monday, today, fresh=fresh, revision=revision, feedback=feedback)
                if body.startswith("(모델이 응답하지 않아"):
                    _weekly_state["error"] = "모델이 응답하지 않아 작성하지 못했습니다"
            except Exception:
                _weekly_state["error"] = "작성 중 오류가 났습니다"
            finally:
                _weekly_state["running"] = False

        threading.Thread(target=run, daemon=True, name="weekly-report").start()
        return True

    def _weekly_card(monday: str, today: str) -> dict:
        """주간 보고서 칸의 상태 — 있으면 미리보기, 없으면 자동 작성을 시작한다."""
        from datetime import datetime as _dt

        cache = _weekly_dir() / f"{monday}.md"
        auto = _weekly_can_auto()
        info = {"exists": cache.exists(), "running": _weekly_state["running"] and _weekly_state["monday"] == monday,
                "started": _weekly_state["started"], "error": _weekly_state["error"],
                "auto": auto, "preview": "", "made_at": "", "stem": monday}
        if cache.exists():
            text = cache.read_text(encoding="utf-8")
            info["preview"] = text[:1800] + ("\n\n…" if len(text) > 1800 else "")
            info["made_at"] = _dt.fromtimestamp(cache.stat().st_mtime).strftime("%m-%d %H:%M")
        return info

    def _weekly_payload(title: str, body: str, fmt: str):
        from fastapi.responses import Response
        from urllib.parse import quote as _q

        if fmt == "md":
            payload: bytes | None = f"# {title}\n\n{body}\n".encode()
            media = "text/markdown; charset=utf-8"
        elif fmt == "docx":
            from zzaimy.app.draft_export import build_draft_docx

            payload = build_draft_docx(title, body)
            media = ("application/vnd.openxmlformats-officedocument"
                     ".wordprocessingml.document")
        elif fmt == "hwpx":
            from zzaimy.app.draft_export import build_draft_hwpx

            payload = build_draft_hwpx(title, body)
            media = "application/hwp+zip"
        else:
            raise HTTPException(404)
        if payload is None:
            raise HTTPException(500, "보고서 생성에 실패했습니다")
        return Response(payload, media_type=media, headers={
            "Content-Disposition": "attachment; filename*=UTF-8\'\'" + _q(f"{title}.{fmt}"),
        })

    def _weekly_file(stem: str) -> Path:
        if "/" in stem or "\\" in stem or ".." in stem or not stem or stem.endswith(".feedback"):
            raise HTTPException(404)
        for folder in (_weekly_dir(), _DOCS_DIR / "weekly"):
            f = folder / f"{stem}.md"
            if f.exists() and f.name != "양식.md":
                return f
        raise HTTPException(404)

    return SimpleNamespace(
        docs_dir=_DOCS_DIR, dev_read=_dev_read, dev_doc_list=_dev_doc_list, dev_papers=_dev_papers,
        paper_labels=_PAPER_LABELS, doc_last_changed=_doc_last_changed, dev_now_parts=_dev_now_parts,
        dev_doc_view=_dev_doc_view, git_history=_git_history, dev_history=_dev_history,
        weekly_sections=_weekly_sections, weekly_feedback_path=_weekly_feedback_path, weekly_feedback=_weekly_feedback,
        weekly_dir=_weekly_dir, weekly_list=_weekly_list, compose_weekly=_compose_weekly, weekly_state=_weekly_state,
        weekly_can_auto=_weekly_can_auto, weekly_kick=_weekly_kick, weekly_card=_weekly_card,
        weekly_payload=_weekly_payload, weekly_file=_weekly_file,
    )


@router.get("/dev/doc/{name:path}", response_class=HTMLResponse)
def dev_doc(request: Request, name: str):
    h = request.app.state.docs
    allowed = {
        "embed-v0-report.md", "retrieval-baseline-mini.md", "llm-rerank-eval.md", "ocr-duel.md",
        "rerank-baseline.md", "retrieval-weight-sweep.md", "ocr-cer-bench.md",
        "quality-system.md", "HANDOFF.md", "dev-now.md", "model-plan.md", "architecture.md",
        "eval-plan.md", "capstone-plan.md", "pilot-plan.md", "risks.md", "workflow.md",
    } | {
        f"{d['sub']}/{d['file']}"
        for sub in ("decisions", "notes") for d in h.dev_doc_list(sub)
    } | {f"model-cards/{p.name}" for p in (h.docs_dir / "model-cards").glob("*.md")}
    if name not in allowed:
        raise HTTPException(404)
    fname = name.rsplit("/", 1)[-1]
    return request.app.state.templates.TemplateResponse(request, "dev_paper.html", request.app.state.page_ctx(request, {
        "fname": fname,
        "doc": h.dev_doc_view(h.dev_read(name), fname),
        "papers": h.dev_papers(),
        # 설계·기술 문서는 논문 자료가 아니라 hwpx·docx 내보내기가 없다
        "show_export": False, "page_title": "설계·기술 문서",
    }))


# 논문 문서(웹 한글 기안기, ADR-0072) — /dev/paper/{name} 보다 먼저 둔다(「hwp」 가 원재료 문서 이름으로 잡히지 않게)
@router.get("/dev/paper/hwp")
def dev_paper_hwp_open(request: Request):
    """논문 문서를 기안기로 연다 — 없으면 논문 자료 목록으로(그 화면에 「논문 문서 만들기」)."""
    from fastapi.responses import RedirectResponse

    from zzaimy.app import paper_hwp
    from zzaimy.app.webhwp_routes import EDIT_PAGE
    f = paper_hwp.current(request.app.state.db)
    if not f:
        return RedirectResponse("/dev/docs#paper-hwp", status_code=303)
    return RedirectResponse(f"{EDIT_PAGE}?id={int(f['id'])}", status_code=303)


@router.post("/dev/paper/hwp")
def dev_paper_hwp_create(request: Request):
    """논문 양식으로 논문 문서를 만든다(이미 있으면 그것) — 한글 문서함 「공통/논문」, 교직원 모두 함께 고친다."""
    from fastapi.responses import RedirectResponse

    from zzaimy.app import paper_hwp
    from zzaimy.app.webhwp_routes import EDIT_PAGE
    st = request.app.state
    src = Path(request.app.state.docs.docs_dir) / "paper" / "논문"
    fid, _ = paper_hwp.create(st.db, Path(st.data_dir), getattr(request.state, "user", "") or "zzdev", src)
    url = f"{EDIT_PAGE}?id={fid}"
    if "application/json" in request.headers.get("accept", ""):       # 화면의 만들기 단추는 답을 받아 직접 옮겨 간다
        return {"id": fid, "url": url}
    return RedirectResponse(url, status_code=303)


# 논문 문서의 목차 쪽 번호(ADR-0072) — 쪽은 기안기만 안다. 편집 화면(static/paper-toc.js)이 칸마다 쪽을 재 보내면 목차 줄을 다시 쓴다.
# 논문은 교직원이 함께 쓰므로 /dev 밖 경로(교직원·관리자, 문서를 열 수 있는 사람)
@router.get("/hwp/{fid}/paper-toc")
def hwp_paper_toc_fields(request: Request, fid: int):
    """목차 줄마다 쪽을 잴 필드 — 차례대로, 겹치지 않게."""
    from zzaimy.app import paper_hwp
    from zzaimy.app.webhwp_routes import _file, _need_staff
    _need_staff(request)
    f = _file(request, fid)
    if not paper_hwp.is_paper(request.app.state.db, fid):
        raise HTTPException(404, "논문 문서가 아닙니다")
    seen: list[str] = []
    for e in paper_hwp.toc_entries(Path(f["path"]).read_bytes()):
        if e["field"] and e["field"] not in seen:
            seen.append(e["field"])
    return {"fields": seen}


@router.post("/hwp/{fid}/paper-toc")
async def hwp_paper_toc_set(request: Request, fid: int):
    """잰 쪽({pages: {필드: 쪽}})으로 목차 쪽 번호를 쓴다 — 필드 밖만 고치고 앞 판은 .판/ 에, 열린 화면은 저장 알림으로 새 판."""
    from starlette.concurrency import run_in_threadpool

    from zzaimy.app import paper_hwp
    from zzaimy.app.webhwp_routes import _file, _need_staff, _writable
    _need_staff(request)
    _file(request, fid)
    _writable(request, fid)
    st = request.app.state
    if not paper_hwp.is_paper(st.db, fid):
        raise HTTPException(404, "논문 문서가 아닙니다")
    try:
        body = await request.json()
    except Exception:
        body = {}
    pages = body.get("pages") if isinstance(body, dict) and isinstance(body.get("pages"), dict) else {}
    if not pages:
        raise HTTPException(400, "잰 쪽이 없습니다")
    user = getattr(request.state, "user", "")
    r = await run_in_threadpool(lambda: paper_hwp.toc_refresh(st.db, Path(st.data_dir), fid, pages, user=user))
    if not r.get("ok"):
        why = {"struct": "문서 틀을 고치는 사람이 있어 쓰지 않았습니다", "check": "고친 파일이 점검을 통과하지 못했습니다",
               "fields": "칸 글이 달라져 쓰지 않았습니다"}.get(r.get("why"), "쓰지 않았습니다")
        raise HTTPException(409, why)
    return {"ok": True, "changed": r.get("changed", False), "note": r.get("note", "")}


@router.get("/dev/paper/{name}/export.{fmt}")
def dev_paper_export(request: Request, name: str, fmt: str):
    """논문 자료 문서를 hwpx·docx 파일로 내려받기."""
    h = request.app.state.docs
    from urllib.parse import quote as _q

    from fastapi.responses import Response

    if name not in {p["file"] for p in h.dev_papers()}:
        raise HTTPException(404)
    text = h.dev_read(f"paper/{name}")
    title = h.paper_labels.get(name, name.replace(".md", ""))
    body = "\n".join(
        ln for ln in text.splitlines() if not ln.startswith("# ")
    )
    payload: bytes | None
    if fmt == "docx":
        from zzaimy.app.draft_export import build_draft_docx

        payload = build_draft_docx(title, body)
        media = ("application/vnd.openxmlformats-officedocument"
                 ".wordprocessingml.document")
    elif fmt == "hwpx":
        from zzaimy.app.draft_export import build_draft_hwpx

        payload = build_draft_hwpx(title, body)
        media = "application/hwp+zip"
    else:
        raise HTTPException(404)
    if payload is None:
        raise HTTPException(500, "내보내기에 실패했습니다")
    return Response(payload, media_type=media, headers={
        "Content-Disposition": "attachment; filename*=UTF-8''"
        + _q(f"{title}.{fmt}"),
    })


@router.get("/dev/paper/{name:path}", response_class=HTMLResponse)
def dev_paper(request: Request, name: str):
    """논문 자료 문서 열람 — 개발자 전용(경로 가드). 논문 원고는 한 단계 아래 「논문/」 폴더다."""
    h = request.app.state.docs
    if not re.fullmatch(r"(논문/)?[^/]+\.md", name) or ".." in name:
        raise HTTPException(404)
    f = h.docs_dir / "paper" / name
    if not f.exists():
        raise HTTPException(404)
    return request.app.state.templates.TemplateResponse(request, "dev_paper.html", request.app.state.page_ctx(request, {
        "fname": name, "doc": h.dev_doc_view(f.read_text(encoding="utf-8"), name),
        "papers": h.dev_papers(),
        "show_export": "/" not in name,
    }))


def _paper_hwp(request: Request) -> dict:
    """논문 문서 줄 — {id, name, saved}(없으면 빈 사전)."""
    from zzaimy.app import paper_hwp, webhwp
    f = paper_hwp.current(request.app.state.db)
    if not f:
        return {}
    m = webhwp.meta(request.app.state.db, f["id"])
    return {"id": f["id"], "name": Path(f["name"]).stem,
            "saved": (m.get("saved") or m.get("created") or "")[:16].replace("T", " ")}


@router.get("/dev/docs", response_class=HTMLResponse)
def dev_docs(request: Request, view: str = "current", q: str = "", page: int = 1):
    """논문 자료·설계 결정·기술 검토·측정 기록 목록."""
    h = request.app.state.docs
    if view not in {"current", "decisions", "records"}:
        view = "current"
    page = max(1, page)
    date_re = re.compile(r"\d{4}-\d{2}-\d{2}")
    head_re = re.compile(r"^-\s*\**(상태|날짜)\**\s*:\s*(.+)$")
    topics = _doc_topics(h.docs_dir)

    def entry(rel: str, href: str, title: str | None = None) -> dict:
        """문서 머리(첫 8줄)에서 뽑은 한 줄 정보 — ADR 은 '- 상태:'·'- 날짜:' 줄,
        그 밖에는 머리에 적힌 첫 날짜. 없으면 비워 둔다."""
        meta = {"status": "", "date": ""}
        first_date = ""
        try:
            head = (h.docs_dir / rel).read_text(encoding="utf-8").splitlines()[:8]
        except OSError:
            head = []
        for ln in head:
            if title is None and ln.startswith("# "):
                title = ln[2:].strip()
            m = head_re.match(ln.strip())
            if m:
                key = "status" if m.group(1) == "상태" else "date"
                meta[key] = m.group(2).strip()
            elif not first_date and (d := date_re.search(ln)):
                first_date = d.group(0)
            # 최신 ADR은 한 줄에 날짜·상태를 기록한다.
            if inline := re.search(r"(?:^|·)\s*상태:\s*([^·]+)", ln):
                meta["status"] = inline.group(1).strip()
        # 대체 고지가 과거의 '확정' 메타데이터보다 우선한다.
        if any(re.match(r"^>\s*(대체됨|폐기됨)", ln) for ln in head):
            meta["status"] = "대체됨"
        if meta["date"] and (d := date_re.search(meta["date"])):
            meta["date"] = d.group(0)
        if not meta["date"]:
            # 계속 고쳐 쓰는 문서(인수인계·계획·진행 현황 …)는 머리에 적힌 옛 날짜가 아니라 마지막으로 바뀐 날 —
            # 머리 날짜를 쓰면 10-09 에 고친 문서가 「10-01」 로 보였다. 결정 기록(ADR)은 머리의 「날짜:」 를 그대로 쓴다
            first_date = h.doc_last_changed(rel) or first_date
        desc_auto = ""
        if not _DOC_DESC.get(rel):
            for ln in (h.docs_dir / rel).read_text(encoding="utf-8").splitlines()[1:30] if (h.docs_dir / rel).is_file() else []:
                t = ln.strip()
                if not t or t[0] in "#->|*`" or re.match(r"^(상태|날짜|작성|기준)\s*[:：]", t) or date_re.fullmatch(t):
                    continue
                t = re.sub(r"[*`]|\[([^\]]+)\]\([^)]*\)", r"\1", t)
                desc_auto = t if len(t) <= 90 else t[:88].rstrip() + "…"
                break
        status = re.split(r"\s*[(（]", meta["status"], maxsplit=1)[0].strip()
        num = ""
        title = title or rel.rsplit("/", 1)[-1]
        # ADR 제목은 '# NNNN. 제목' 이 규칙이지만 'ADR-NNNN' · 'NNNN ·' 꼴도 번호로 읽는다
        if m := re.match(r"^(?:ADR-)?(\d{4})\s*[.·:]?\s+(.+)$", title):
            num, title = m.group(1), m.group(2).lstrip("—– ")
        desc = _DOC_DESC.get(rel, "") or desc_auto
        # 주제별 묶음 — ADR 은 「주제 · 지금 상태(유효가 아닐 때)」, 기록 자료는 「주제 · 설명」
        if rel in topics:
            topic, now = topics[rel]
            if rel.startswith("decisions/"):
                desc = topic + (f" · {now}" if now and now != "유효" else "")
                if now.startswith("대체됨"):
                    status = "대체됨"
            else:
                desc = f"{topic} · {desc}" if desc else topic
        return {"href": href, "title": title, "num": num, "status": status,
                "date": meta["date"] or first_date, "desc": desc}

    from datetime import date as _date, timedelta as _td

    _today = _date.today()
    return request.app.state.templates.TemplateResponse(request, "dev_docs.html", request.app.state.page_ctx(request, {
        "docs_view": view, "docs_query": q.strip(), "docs_page": page,
        "weekly_docs": h.weekly_list(),
        "weekly_template": h.weekly_sections()[1],
        "weekly_monday": (_today - _td(days=_today.weekday())).isoformat(),
        "weekly_feedback": h.weekly_feedback((_today - _td(days=_today.weekday())).isoformat()),
        "weekly": h.weekly_card((_today - _td(days=_today.weekday())).isoformat(), _today.isoformat()),
        "papers": [entry(f"paper/{p['file']}", f"/dev/paper/{p['file']}", p["label"])
                   for p in h.dev_papers() if p["file"] not in PAPER_PLANNING],
        # 기획 단계 자료(기획서·제안 발표·논문 양식 분석)는 기록·자료 목록의 한 묶음으로
        "planning_docs": [entry(f"paper/{p['file']}", f"/dev/paper/{p['file']}", p["label"])
                          for p in h.dev_papers() if p["file"] in PAPER_PLANNING],
        "paper_hwp": _paper_hwp(request),
        "adr_docs": [entry(f"decisions/{d['file']}", f"/dev/doc/decisions/{d['file']}",
                           d["title"]) for d in h.dev_doc_list("decisions")],
        # 기술 검토(조사·판단)와 작업 메모(내부 메모)를 나눠 보인다
        "note_docs": sorted([entry(f"notes/{d['file']}", f"/dev/doc/notes/{d['file']}", d["title"])
                             for d in h.dev_doc_list("notes") if d["file"] not in _NOTE_MEMO],
                            key=lambda e: e["date"], reverse=True),
        "memo_docs": sorted([entry(f"notes/{d['file']}", f"/dev/doc/notes/{d['file']}", d["title"])
                             for d in h.dev_doc_list("notes") if d["file"] in _NOTE_MEMO],
                            key=lambda e: e["date"], reverse=True),
        # 현황(먼저 볼 것) · 설계·계획 · 측정 기록 · 모델 카드 — docs/ 바로 아래 파일들
        # dev-now 는 머리 제목 없이 절(## 지금 하는 일)로 시작한다 — 파일 이름 대신 화면 이름으로
        "status_docs": [entry("dev-now.md", "/dev/doc/dev-now.md", "진행 현황"), entry("HANDOFF.md", "/dev/doc/HANDOFF.md")],
        # 지금도 정본인 설계·계획 — 시스템 설계(27B 문서 작업·웹 기안기 공동 편집까지)와 모델 개발 계획.
        # 나머지 9월 설계·계획 문서는 이력 쪽으로
        "plan_docs": [entry("architecture.md", "/dev/doc/architecture.md"), entry("model-plan.md", "/dev/doc/model-plan.md")],
        "old_plan_docs": [entry(f, f"/dev/doc/{f}") for f in (
            "eval-plan.md", "capstone-plan.md",
            "pilot-plan.md", "risks.md", "quality-system.md", "workflow.md")],
        "bench_docs": [entry(f, f"/dev/doc/{f}") for f in (
            "llm-rerank-eval.md", "retrieval-baseline-mini.md", "retrieval-weight-sweep.md",
            "rerank-baseline.md", "embed-v0-report.md", "ocr-cer-bench.md", "ocr-duel.md")],
        "card_docs": [entry(f"model-cards/{f}", f"/dev/doc/model-cards/{f}") for f in
                      sorted(p.name for p in (h.docs_dir / "model-cards").glob("*.md"))],
    }))


@router.get("/dev/history", response_class=HTMLResponse)
def dev_history_page(request: Request):
    """변경 이력 — 작업 기록(날짜별) + 커밋 이력 전체(깃에서 직접, 영역 표식).
    주간 보고서 요약은 논문 자료 화면의 주간 보고서 칸에 있다."""
    h = request.app.state.docs
    days = []
    for blk in re.split(r"\n(?=### )", h.dev_now_parts()["recent_all"]):
        if blk.strip().startswith("### "):
            head, _, body = blk.strip().partition("\n")
            days.append({"day": head[4:].strip(), "body": body.strip()})
    history = [d for d in h.dev_history() if re.fullmatch(r"(\d{4}-)?\d{2}-\d{2}", d["day"])]
    area_count: dict[str, int] = {}
    for d in history:
        for it in d["items"]:
            for a in it["areas"]:
                area_count[a] = area_count.get(a, 0) + 1
    return request.app.state.templates.TemplateResponse(request, "dev_history.html", request.app.state.page_ctx(request, {
        "history": history,
        "history_count": sum(len(d["items"]) for d in history),
        "history_areas": sorted(area_count.items(), key=lambda kv: -kv[1]),
        "history_source": "깃 커밋" if h.git_history() else "깃 기록 없음",
        "dev_now_days": days,
    }))


@router.get("/dev/weekly/status")
def dev_weekly_status(request: Request):
    h = request.app.state.docs
    from datetime import date as _date, timedelta as _td

    today = _date.today()
    monday = (today - _td(days=today.weekday())).isoformat()
    return {"running": h.weekly_state["running"], "error": h.weekly_state["error"],
            "exists": (h.weekly_dir() / f"{monday}.md").exists()}


@router.post("/dev/weekly/generate")
def dev_weekly_rebuild(request: Request):
    """다시 만들기 — 뒤에서 새로 쓰고 화면으로 돌아간다."""
    h = request.app.state.docs
    from datetime import date as _date, timedelta as _td
    from fastapi.responses import RedirectResponse

    today = _date.today()
    monday = (today - _td(days=today.weekday())).isoformat()
    if not (h.weekly_dir() / f"{monday}.md").exists():
        h.weekly_kick(monday, today.isoformat())
    return RedirectResponse("/dev/docs#weekly", status_code=303)


@router.get("/dev/weekly.{fmt}")
def dev_weekly_report(request: Request, fmt: str, fresh: int = 0):
    """캡스톤 주간 보고서 — LLM 작성 성과 서술 + 결정론 지표 표."""
    h = request.app.state.docs
    from datetime import date, timedelta
    from urllib.parse import quote as _q

    from fastapi.responses import Response

    today = date.today()
    monday = today - timedelta(days=today.weekday())
    body = h.compose_weekly(
        monday.isoformat(), today.isoformat(), fresh=bool(fresh)
    )
    title = f"주간업무보고 ({monday.isoformat()} ~ {today.isoformat()})"
    if fmt == "md":
        payload: bytes | None = f"# {title}\n\n{body}\n".encode()
        media = "text/markdown; charset=utf-8"
    elif fmt == "docx":
        from zzaimy.app.draft_export import build_draft_docx

        payload = build_draft_docx(title, body)
        media = ("application/vnd.openxmlformats-officedocument"
                 ".wordprocessingml.document")
    elif fmt == "hwpx":
        from zzaimy.app.draft_export import build_draft_hwpx

        payload = build_draft_hwpx(title, body)
        media = "application/hwp+zip"
    else:
        raise HTTPException(404)
    if payload is None:
        raise HTTPException(500, "보고서 생성에 실패했습니다")
    return Response(payload, media_type=media, headers={
        "Content-Disposition": "attachment; filename*=UTF-8\'\'"
        + _q(f"주간보고_{monday.isoformat()}.{fmt}"),
    })


@router.post("/dev/weekly/feedback")
async def dev_weekly_feedback(request: Request):
    """선택한 보고서의 기존 내용에 수정 요청을 적용한다. 실패해도 원본은 남긴다."""
    h = request.app.state.docs
    from datetime import date as _date
    from fastapi.responses import RedirectResponse

    form = await request.form()
    stem = str(form.get("stem") or "")
    report = h.weekly_file(stem)
    text = str(form.get("feedback") or "").strip()
    if not text or len(text) > 10000:
        raise HTTPException(400, "수정 요청은 1~10,000자로 입력해 주세요")
    if not h.weekly_kick(stem, _date.today().isoformat(), fresh=True,
                        revision=report.read_text(encoding="utf-8"), feedback=text):
        raise HTTPException(409, "보고서를 작성 중입니다. 완료 후 다시 요청해 주세요")
    f = h.weekly_feedback_path(stem)
    f.write_text(text, encoding="utf-8")
    return RedirectResponse(f"/dev/weekly/{stem}", status_code=303)


@router.get("/dev/weekly/{stem}.{fmt}")
def dev_weekly_past(request: Request, stem: str, fmt: str):
    """지난 주간 보고를 파일로 — 생성본이든 수기본이든 같은 형식으로."""
    h = request.app.state.docs
    f = h.weekly_file(stem)
    text = f.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), f"주간 보고 {stem}")
    body = "\n".join(ln for ln in lines if not ln.startswith("# ")).strip()
    return h.weekly_payload(title, body, fmt)


@router.get("/dev/weekly/{stem}", response_class=HTMLResponse)
def dev_weekly_view(request: Request, stem: str):
    """지난 주간 보고 열람 — 논문 자료 열람 화면과 같은 틀."""
    h = request.app.state.docs
    f = h.weekly_file(stem)
    return request.app.state.templates.TemplateResponse(request, "dev_paper.html", request.app.state.page_ctx(request, {
        "fname": f.name, "doc": h.dev_doc_view(f.read_text(encoding="utf-8"), f.name),
        "papers": [], "show_export": False, "page_title": "주간 보고서",
        "weekly_stem": stem,
        "weekly_feedback": h.weekly_feedback(stem),
        "weekly_running": h.weekly_state["running"] and h.weekly_state["monday"] == stem,
        "weekly_error": h.weekly_state["error"] if h.weekly_state["monday"] == stem else "",
    }))
