"""문서 작성 시험 묶음 — 빈 양식·근거·시험 요청·정답을 넣어 두면 운영 플랫폼에서 처음부터 끝까지 돌리고 정답과 견준다(11-10).

묶음 폴더(저장소 밖 data/eval_sets/<이름>/, git 제외):
  blank.hwpx      빈 양식(정답에서 값을 지운 것)
  evidence/       근거 파일(공고·기본계획 등) — 프로젝트를 만들 때 양식과 함께 올린다
  materials/      그 밖에 함께 올리는 자료(넣을 그림 파일 등) — 같이 올리되 근거 비교에는 쓰지 않는다
  prompts.txt     한 줄에 한 요청. 차례대로 같은 대화에 보낸다. 빈 줄·「#」 줄은 건너뜀, 앞에 「[꼼꼼]」 이면 답변 깊이 꼼꼼하게
  answer.hwpx | answer.pdf   정답 문서
  meta.json       (선택) {"project": "프로젝트 이름", "sector": "grant"}

여기 있는 것은 글 뽑기·절 맞추기·채점(모두 기계 산출, 모델 채점 없음). 실행은 scripts/225_eval_set_run.py,
비교 리포트는 scripts/226_eval_set_compare.py.
"""
from __future__ import annotations

import io
import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

PENDING = re.compile(r"\[?\s*확인\s*필요\s*\]?")
_HEAD = re.compile(
    r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.．]|\d{1,2}(?:\s*\.\s*\d{1,2}){0,3}\s*[.．]?(?=\s)|[가-하]\s*[.．]|【[^】]{2,30}】)\s*\S")
_NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_UNIT = re.compile(r"\s*(?:백만\s*원|천\s*원|억\s*원|만\s*원|원|명|%|퍼센트|개|건|년|학점|시간|회|점)")


# ── 글 뽑기 ──
@dataclass
class Block:
    kind: str                        # "p" 문단 | "table" 표
    text: str = ""
    rows: list[list[str]] = field(default_factory=list)


def _t(xml: str) -> str:
    return "".join(re.findall(r"<hp:t(?:\s[^>]*)?>([^<]*)</hp:t>", xml)).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _top_tags(xml: str, tag: str) -> list[tuple[int, int]]:
    """맨 바깥 <hp:tag> 덩어리 자리(안에 든 같은 태그는 한 덩어리로)."""
    out, depth, start = [], 0, 0
    for m in re.finditer(rf"<(/?)hp:{tag}\b[^>]*?(/?)>", xml):
        if m.group(2):
            continue
        if m.group(1):
            depth -= 1
            if depth == 0:
                out.append((start, m.end()))
        else:
            if depth == 0:
                start = m.start()
            depth += 1
    return out


def _table_rows(tbl: str) -> list[list[str]]:
    rows = []
    inner = tbl[tbl.find(">") + 1:]
    for a, b in _top_tags(inner, "tr"):
        tr = inner[a:b]
        cells = []
        for c1, c2 in _top_tags(tr[tr.find(">") + 1:], "tc"):
            tc = tr[tr.find(">") + 1:][c1:c2]
            cells.append(re.sub(r"\s+", " ", _t(tc)).strip())
        rows.append(cells)
    return rows


def hwpx_blocks(data: bytes) -> list[Block]:
    """HWPX → 문단·표 차례. 표 안의 글은 표로만(문단으로 두 번 세지 않는다)."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = sorted((n for n in zf.namelist() if re.match(r"Contents/section\d+\.xml$", n)),
                   key=lambda s: int(re.search(r"(\d+)", s.rsplit("/", 1)[-1]).group(1)))
    out: list[Block] = []
    for n in names:
        xml = zf.read(n).decode("utf-8", "replace")
        body = xml[xml.find(">", xml.find("<hs:sec")) + 1:] if "<hs:sec" in xml else xml
        for a, b in _top_tags(body, "p"):
            p = body[a:b]
            tabs = _top_tags(p, "tbl")
            if not tabs:
                t = re.sub(r"\s+", " ", _t(p)).strip()
                if t:
                    out.append(Block("p", t))
                continue
            pos = 0
            for x, y in tabs:
                before = re.sub(r"\s+", " ", _t(p[pos:x])).strip()
                if before:
                    out.append(Block("p", before))
                rows = _table_rows(p[x:y])
                out.append(Block("table", " | ".join(" ".join(r) for r in rows), rows))
                pos = y
            after = re.sub(r"\s+", " ", _t(p[pos:])).strip()
            if after:
                out.append(Block("p", after))
    return out


def pdf_blocks(data: bytes) -> list[Block]:
    from pypdf import PdfReader
    out = []
    for page in PdfReader(io.BytesIO(data)).pages:
        for line in (page.extract_text() or "").splitlines():
            t = re.sub(r"\s+", " ", line).strip()
            if t:
                out.append(Block("p", t))
    return out


def read_blocks(path: Path) -> list[Block]:
    data = Path(path).read_bytes()
    ext = Path(path).suffix.lower()
    if ext == ".hwpx" or data[:2] == b"PK":
        return hwpx_blocks(data)
    if ext == ".pdf":
        return pdf_blocks(data)
    return [Block("p", t.strip()) for t in data.decode("utf-8", "replace").splitlines() if t.strip()]


# ── 절 나누기·맞추기 ──
@dataclass
class Section:
    title: str
    key: str
    blocks: list[Block] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


def norm(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def head_key(title: str) -> str:
    """절 제목 열쇠 — 번호·기호·괄호 속 꼬리·공백을 떼고 글자만."""
    t = norm(title)
    t = re.sub(r"^\s*(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.．]|\d{1,2}(?:\s*\.\s*\d{1,2}){0,3}\s*[.．]?|[가-하]\s*[.．]|【|\[)", "", t)
    t = re.sub(r"[\s\W_]+", "", t)
    return t


def is_heading(b: Block) -> bool:
    return b.kind == "p" and len(b.text) <= 60 and bool(_HEAD.match(b.text)) and not b.text.rstrip().endswith(("함.", "음.", "다.", "임."))


def sections(blocks: list[Block]) -> list[Section]:
    out = [Section("(머리)", "")]
    for b in blocks:
        if is_heading(b) and head_key(b.text):
            out.append(Section(b.text, head_key(b.text)))
        else:
            out[-1].blocks.append(b)
    return [s for s in out if s.key or s.blocks]


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)}


def match(answer: list[Section], result: list[Section], threshold: float = 0.6) -> list[tuple[Section, Section | None]]:
    """정답 절마다 결과 절 하나 — 열쇠가 같으면 그것, 아니면 글자 두 개 묶음 겹침(자카드)이 가장 큰 것(문턱 이상). 결과 절은 한 번만 쓴다."""
    used: set[int] = set()
    by_key: dict[str, list[int]] = {}
    for i, r in enumerate(result):
        by_key.setdefault(r.key, []).append(i)
    pairs = []
    for a in answer:
        if not a.key:
            continue
        hit = next((i for i in by_key.get(a.key, []) if i not in used), None)
        if hit is None:
            ab = _bigrams(a.key)
            best, score = None, 0.0
            for i, r in enumerate(result):
                if i in used or not r.key:
                    continue
                rb = _bigrams(r.key)
                j = len(ab & rb) / max(1, len(ab | rb))
                if j > score:
                    best, score = i, j
            hit = best if score >= threshold else None
        if hit is not None:
            used.add(hit)
        pairs.append((a, result[hit] if hit is not None else None))
    return pairs


# ── 채점 ──
def numbers(text: str) -> set[str]:
    """수치 — 쉼표를 떼고, 단위가 붙은 것은 단위째(「273명」「45,671,600원」「3.5%」). 한 자리 맨 수(번호·차례)는 뺀다."""
    out = set()
    t = norm(text)
    for m in _NUM.finditer(t):
        v = m.group(0).replace(",", "")
        u = _UNIT.match(t, m.end())
        unit = re.sub(r"\s+", "", u.group(0)) if u else ""
        if not unit and len(v.replace(".", "")) < 2:
            continue
        out.add(v + unit)
    return out


def _bare(n: str) -> str:
    return re.sub(r"[^\d.]", "", n)


def ngram_recall(ref: str, got: str, n: int = 3) -> float | None:
    a = re.sub(r"\s+", "", PENDING.sub("", norm(ref)))
    b = re.sub(r"\s+", "", PENDING.sub("", norm(got)))
    ga = [a[i:i + n] for i in range(len(a) - n + 1)]
    if not ga:
        return None
    gb = set(b[i:i + n] for i in range(len(b) - n + 1))
    return sum(1 for g in ga if g in gb) / len(ga)


def table_fill(blocks: list[Block]) -> tuple[int, int]:
    """표 칸 채움 — 머리줄(첫 행)을 뺀 칸 가운데 글이 있고 「확인 필요」 가 아닌 칸 수, 전체 칸 수."""
    full = total = 0
    for b in blocks:
        if b.kind != "table":
            continue
        for row in b.rows[1:]:
            for c in row:
                total += 1
                if c.strip() and not PENDING.fullmatch(c.strip()):
                    full += 1
    return full, total


def body_chars(sec: Section | None) -> int:
    if sec is None:
        return 0
    return len(re.sub(r"\s+", "", PENDING.sub("", sec.text)))


def score(answer_path: Path, result_path: Path, evidence_paths: list[Path] | None = None, fill_min: int = 30) -> dict:
    """정답과 결과를 절별로 견준다. 돌려주는 것은 {summary, sections} — 숫자는 모두 기계 산출."""
    ans_blocks, res_blocks = read_blocks(answer_path), read_blocks(result_path)
    ans, res = sections(ans_blocks), sections(res_blocks)
    ev_text = "\n".join(b.text for p in (evidence_paths or []) for b in _safe_blocks(p))
    ans_all = numbers("\n".join(b.text for b in ans_blocks))
    ev_nums = {_bare(x) for x in numbers(ev_text)}
    ans_bare = {_bare(x) for x in ans_all}
    rows = []
    for a, r in match(ans, res):
        an = numbers(a.text)
        rn = numbers(r.text) if r else set()
        rbare = {_bare(x) for x in rn}
        hit = sum(1 for x in an if x in rn or _bare(x) in rbare)
        only = sorted(x for x in rn if _bare(x) not in ans_bare and _bare(x) not in ev_nums)
        tf, tt = table_fill(r.blocks if r else [])
        rows.append({
            "title": a.title, "result_title": r.title if r else "",
            "filled": body_chars(r) >= fill_min,
            "chars": body_chars(r), "answer_chars": body_chars(a),
            "num_total": len(an), "num_hit": hit,
            "num_only": only[:12], "num_only_n": len(only),
            "pending": len(PENDING.findall(r.text)) if r else 0,
            "recall3": ngram_recall(a.text, r.text if r else ""),
            "table_full": tf, "table_cells": tt,
        })
    n = len(rows)
    matched = sum(1 for x in rows if x["result_title"])
    nt = sum(x["num_total"] for x in rows)
    rec = [x["recall3"] for x in rows if x["recall3"] is not None]
    tf, tt = sum(x["table_full"] for x in rows), sum(x["table_cells"] for x in rows)
    summary = {
        "answer_sections": n, "matched": matched, "filled": sum(1 for x in rows if x["filled"]),
        "num_total": nt, "num_hit": sum(x["num_hit"] for x in rows),
        "num_hit_rate": round(sum(x["num_hit"] for x in rows) / nt, 3) if nt else None,
        "num_only": sum(x["num_only_n"] for x in rows),
        "pending": sum(x["pending"] for x in rows),
        "recall3": round(sum(rec) / len(rec), 3) if rec else None,
        "table_fill": round(tf / tt, 3) if tt else None, "table_cells": tt,
        "result_sections": len([s for s in res if s.key]),
        "unmatched_result": [s.title for s in res if s.key and s.title not in {x["result_title"] for x in rows}][:30],
    }
    return {"summary": summary, "sections": rows}


def _safe_blocks(p: Path) -> list[Block]:
    try:
        return read_blocks(p)
    except Exception:
        return []


def _pct(v) -> str:
    return "-" if v is None else f"{v * 100:.0f}%"


def report_md(res: dict, name: str = "", run: dict | None = None) -> str:
    s = res["summary"]
    lines = [f"# 문서 작성 시험 비교{(' · ' + name) if name else ''}", ""]
    if run:
        lines += [f"- 대화 {run.get('session_id')} · 프로젝트 {run.get('project_id')} · 요청 {len(run.get('turns') or [])}개 · "
                  f"걸린 시간 합 {sum(t.get('seconds', 0) for t in run.get('turns') or [])}초", ""]
    lines += [
        "## 요약", "",
        f"- 정답 절 {s['answer_sections']}개 가운데 결과에서 찾은 절 {s['matched']}개, 글을 채운 절 {s['filled']}개",
        f"- 정답 수치 {s['num_total']}개 가운데 결과 같은 절에 나온 것 {s['num_hit']}개({_pct(s['num_hit_rate'])})",
        f"- 정답·근거 어디에도 없는 결과 수치 {s['num_only']}개(지어낸 수치 의심 — 절별 표에서 확인)",
        f"- 「확인 필요」 {s['pending']}곳",
        f"- 글 겹침(정답 문자 3글자 묶음 재현율) 평균 {_pct(s['recall3'])}",
        f"- 표 칸 채움 {_pct(s['table_fill'])} (머리줄 뺀 칸 {s['table_cells']}개)",
    ]
    if s["unmatched_result"]:
        lines.append("- 정답에 없는 결과 절: " + ", ".join(s["unmatched_result"][:12]))
    lines += ["", "## 절별", "", "| 정답 절 | 결과 절 | 채움(글자/정답) | 수치 맞음 | 근거 밖 수치 | 확인 필요 | 글 겹침 | 표 칸 |",
              "|---|---|---|---|---|---|---|---|"]
    for x in res["sections"]:
        lines.append("| {t} | {r} | {f} {c}/{ac} | {h}/{n} | {o} | {p} | {g} | {tf} |".format(
            t=x["title"][:40].replace("|", "/"), r=(x["result_title"][:40] or "(없음)").replace("|", "/"),
            f="O" if x["filled"] else "X", c=x["chars"], ac=x["answer_chars"], h=x["num_hit"], n=x["num_total"],
            o=(", ".join(x["num_only"][:4]) + (" 외" if x["num_only_n"] > 4 else "")) or "-", p=x["pending"],
            g=_pct(x["recall3"]), tf=f"{x['table_full']}/{x['table_cells']}" if x["table_cells"] else "-"))
    lines += ["", "숫자는 모두 기계 산출이다(모델 채점 없음). 절은 제목 열쇠(번호·기호 뗀 글자)로 맞추고, 같지 않으면 글자 두 개 묶음 겹침이 큰 것으로 맞춘다."]
    return "\n".join(lines) + "\n"


# ── 묶음 폴더 ──
@dataclass
class EvalSet:
    root: Path

    @property
    def blank(self) -> Path:
        return self._one("blank")

    @property
    def answer(self) -> Path | None:
        try:
            return self._one("answer")
        except FileNotFoundError:
            return None

    def _one(self, stem: str) -> Path:
        for ext in (".hwpx", ".pdf", ".hwp", ".txt"):
            p = self.root / f"{stem}{ext}"
            if p.exists():
                return p
        raise FileNotFoundError(f"{self.root}/{stem}.hwpx(또는 .pdf) 가 없습니다")

    @property
    def evidence(self) -> list[Path]:
        d = self.root / "evidence"
        return sorted(p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")) if d.is_dir() else []

    @property
    def materials(self) -> list[Path]:
        """그 밖에 담당자가 함께 올리는 자료(계획서에 넣을 그림 파일 등) — materials/ 폴더. 근거 비교(score)에는 쓰지 않는다."""
        d = self.root / "materials"
        return sorted(p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")) if d.is_dir() else []

    @property
    def meta(self) -> dict:
        p = self.root / "meta.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def prompts(self) -> list[dict]:
        """요청 목록 [{text, effort, name}].
        - prompts/ 폴더가 있으면 그 안 파일(.txt·.md, 이름 차례) 하나가 요청 하나 — 표·여러 줄이 든 긴 요청(11-11 특성화 86단계).
          파일 첫 줄이 「[꼼꼼]」 이면 답변 깊이 꼼꼼하게.
        - 없으면 prompts.txt 한 줄에 한 요청 — 빈 줄·「#」 줄은 건너뛰고, 「[꼼꼼]」·「[deep]」 머리는 답변 깊이 꼼꼼하게."""
        d = self.root / "prompts"
        if d.is_dir():
            out = []
            for f in sorted(x for x in d.iterdir() if x.suffix in (".txt", ".md") and not x.name.startswith(".")):
                t = f.read_text(encoding="utf-8").strip()
                effort = "fast"
                m = re.match(r"^\[(꼼꼼|deep|빠르게|fast)\]\s*", t)
                if m:
                    effort = "deep" if m.group(1) in ("꼼꼼", "deep") else "fast"
                    t = t[m.end():].strip()
                if t:
                    out.append({"text": t, "effort": effort, "name": f.stem})
            if not out:
                raise ValueError(f"{d} 에 요청이 없습니다")
            return out
        p = self.root / "prompts.txt"
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            effort = "fast"
            m = re.match(r"^\[(꼼꼼|deep|빠르게|fast)\]\s*", t)
            if m:
                effort = "deep" if m.group(1) in ("꼼꼼", "deep") else "fast"
                t = t[m.end():]
            out.append({"text": t, "effort": effort})
        if not out:
            raise ValueError(f"{p} 에 요청이 없습니다")
        return out
