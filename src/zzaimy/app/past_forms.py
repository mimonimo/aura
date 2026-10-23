"""지난 사업 문서·공통 양식 — 지난 문서에서 모은 한글 양식의 분류·점검 규칙(문서함 공통 부서).

왜(10-23 사용자): 기안기 양식 모음(scripts/200 register)은 처음에 「공통/양식/<종류>」 에 1천여 건을 쌓았다. 지난 문서에서 모은 것이라
- 어디서나 쓸 빈 양식(특정 사업 이름이 두드러지지 않는 것)은 「공통 양식/<목적>」 — 플랫폼이 만든 공통 양식과 한자리
- 특정 사업 양식은 「지난 사업 문서/<사업>/<목적>」(별칭은 장부의 한 사업으로 묶는다, 양식이 적은 사업은 목적 폴더 없이)
- 쓸데없는 것은 휴지통(되살릴 수 있게, 까닭·잰 값은 계획에): 개인 서류·채워진 제출본·한 번 쓴 기록·쓸모 적음·변환 품질 낮음·
  깨짐·빈 파일·중복(같은 본문, 같은 자리의 같은 제목 — 가장 나은 하나만 남긴다)
classify() 가 이 판정 하나를 돌려준다 — 정리(scripts/208)·새 양식 등록(scripts/200 register)·앞으로의 올리기 경로가 같이 쓴다.

규칙은 특정 파일·사업을 적지 않는다 — 장부(사업 이름·별칭), 제목 낱말, 본문에서 기계로 잰 값(빈 칸 비율·개인 정보 꼴·
양식 예시 글자 수·사업 이름 수)만 본다. 잰 값과 문턱은 계획 파일(scripts/208 plan)에 그대로 남는다.
"""
from __future__ import annotations

import difflib
import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path

from zzaimy.app import storage

ROOT = "지난 사업 문서"            # 특정 사업 양식 뿌리 폴더(공통 부서 아래)
COMMON_ROOT = "공통 양식"          # 어디서나 쓰는 양식 — 플랫폼이 만든 것 + 지난 문서의 빈 공통 양식
OLD_ROOTS = ("양식", "지난 문서 양식")   # 옛 자리(처음 register 가 쓴 「양식」, 중간 이름)
SMALL_PROGRAM = 3                  # 사업의 양식이 이 수 이하이면 목적 폴더를 만들지 않고 사업 폴더에 바로 둔다

# ── 문턱(계획 md 에 그대로 적힌다) ──
NEAR_EMPTY_CHARS = 20              # 보이는 글자가 이보다 적고 표가 없으면 빈 파일(휴지통)
FILLED_BLANK_SHARE = 0.15          # 표 칸 가운데 빈 칸 비율이 이보다 낮고
FILLED_CONCRETE = 3                # 구체 값(사람 이름·전화·전자우편·날짜·서명·금액) 수가 이 이상이고
TEMPLATE_SIGNALS_MAX = 2           # 양식 예시 글자·작성 안내 수가 이 이하이면 채워진 제출본(휴지통)
FILLED_PROSE_CHARS = 6000          # 긴 글 — 이 글자 수 이상 + 날짜 FILLED_PROSE_DATES 이상 + 안내 적음이면 제출본(휴지통)
FILLED_PROSE_DATES = 3
RECORD_BLANK_SHARE = 0.25          # 회의록·명단 갈래에서 빈 칸 비율이 이보다 낮고
RECORD_CONCRETE = 3                # 날짜·이름·채운 행 합이 이 이상이면 한 번 쓴 기록(휴지통)
LOW_VALUE_BLANK = 0.20             # 목적을 모르고(기타) 양식 표시 없고 빈 칸이 이보다 적으면 쓸모 적음(휴지통)
GARBLED_SHARE = 0.05               # 깨진 글자(대체 문자·사용자 영역·제어 문자) 비율이 이 이상이면 변환 품질 낮음(휴지통)
PROGRAM_HITS_MIN = 2               # 본문에 한 사업 이름·별칭이 이만큼 나오거나 제목에 나오면 그 사업 전용
NEAR_DUP_RATIO = 0.90              # 같은 자리·같은 제목 묶음에서 본문 유사도가 이 이상이면 중복(휴지통)
DUP_TEXT_CAP = 6000                # 유사도는 앞 글자 이만큼만 비교(속도)

TRASH, KEEP = "trash", "keep"

# 목적 — 제목의 낱말로 고른다(긴 낱말 먼저). 목적 이름이 폴더 이름이 된다
PURPOSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("공문", ("공문", "시행문", "기안문", "협조전", "협조문", "알림문")),
    ("결과보고서", ("결과보고", "실적보고", "완료보고", "성과보고", "결과서", "실적서")),
    ("계획서", ("계획서", "계획(안)", "계획안", "기획안", "사업계획", "운영계획", "추진계획", "계획")),
    ("신청서", ("신청서", "지원서", "신청", "요청서", "추천서", "응모")),
    ("명단", ("명단", "서명부", "참석자", "명부", "목록", "대장", "출석부")),
    ("서약·동의서", ("서약서", "동의서", "각서", "확약서", "서약", "동의")),
    ("확인서", ("확인서", "증명서", "확인", "증빙")),
    ("회의록", ("회의록", "회의결과", "협의록", "회의")),
    ("예산", ("예산", "정산", "명세서", "내역서", "집행", "지출", "산출", "견적", "청구")),
    ("평가", ("평가표", "평가서", "체크리스트", "심사", "설문", "평가", "만족도")),
    ("협약·계약", ("협약서", "계약서", "협약", "계약", "업무협약")),
    ("보고서", ("보고서", "일지", "현황", "의견서", "검토서", "보고")),
    ("이력·소개서", ("이력서", "자기소개서", "경력", "소개서")),
)
OTHER = "기타"

# 개인 서류 갈래(제목) — 이 갈래이고 본문에 개인 정보가 하나라도 있으면 개인 문서
PERSONAL_WORDS = ("이력서", "자기소개서", "경력증명", "재직증명", "졸업증명", "성적증명", "통장", "신분증", "가족관계", "등본",
                  "초본", "건강진단", "자격증", "학생증", "여권")
NAME_LABELS = ("성명", "이름", "작성자", "담당자", "책임자", "대표자", "신청인", "신청자", "지도교수", "학생명", "교수명", "확인자",
               "예금주", "보호자", "참석자", "강사명", "참여자", "서명자", "기안자", "결재자")
ADDRESS_LABELS = ("주소", "현주소", "거주지", "자택", "주민등록상주소")
ACCOUNT_LABELS = ("계좌", "계좌번호", "입금계좌", "통장번호")
# 흔한 성 — 이름 꼴(성 + 이름 한두 글자) 판정에 쓴다(특정 사람이 아니라 한국 이름의 짜임)
SURNAMES = set("김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진나지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예봉경봉사부")
# 양식에 흔히 쓰는 보기 이름·일반어 — 사람 이름으로 세지 않는다
NOT_NAMES = {"홍길동", "김철수", "이영희", "김영희", "아무개", "미정", "없음", "해당없음", "본인", "대표", "교수", "직원", "학생",
             "담당", "책임", "기관", "학과", "부서", "소속", "직위", "직급", "서명", "날인", "성명", "이름", "연락처", "비고",
             "합계", "소계", "구분", "내용", "일시", "장소", "주관", "주최", "참석", "확인", "제출", "신청", "총장", "학장",
             "처장", "팀장", "과장", "부장", "원장", "단장", "센터장", "위원장", "위원", "간사", "회장"}

_T = re.compile(r"<hp:t\b[^>]*>(.*?)</hp:t>", re.S)
_TAG = re.compile(r"<[^>]+>")
_LEAF_P = re.compile(r"<hp:p\b[^>]*>(?:(?!<hp:p\b).)*?</hp:p>", re.S)
_TBL = re.compile(r"<hp:tbl\b.*?</hp:tbl>", re.S)
_TR = re.compile(r"<hp:tr\b.*?</hp:tr>", re.S)
_TC = re.compile(r"<hp:tc\b.*?</hp:tc>", re.S)
_SEC = re.compile(r"Contents/section\d+\.xml$")

_RRN = re.compile(r"(?<!\d)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\s*-\s*[1-4]\d{6}(?!\d)")
_MOBILE = re.compile(r"(?<!\d)01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)")
_PHONE = re.compile(r"(?<!\d)0(?:2|[3-6][1-5])[-)\s.]\s?\d{3,4}[-.\s]\d{4}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.]+")
_DATE = re.compile(r"(?:19|20)\d{2}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}")
_AMOUNT = re.compile(r"(?<![\d,])\d{1,3}(?:,\d{3})+(?![\d,])")
_SIGNED = re.compile(r"([가-힣]{2,4})\s*\(\s*(?:인|서명|印)\s*\)")
_ACCOUNT = re.compile(r"(?<![\d-])\d{2,6}-\d{2,6}-\d{2,8}(?:-\d{1,4})?(?![\d-])")
_ADDRESS = re.compile(r"[가-힣]+(?:특별시|광역시|시|도)\s+[가-힣]+(?:시|군|구)\s+[가-힣0-9]+(?:로|길|동|읍|면)\s*\d")
_PLACEHOLDER = re.compile(r"○+|◯{2,}|ㅇㅇ|OO|oo|0{3,}|□{2,}|△△|XX|xx|홍길동|20\s*\.\s+\.|20\s*년\s+월\s+일|(?<!\d)년\s+월\s+일")
_GUIDANCE = re.compile(r"작성\s*(?:요령|방법|예시|시\s*유의)|기재|작성하|※|예\)|예시|해당\s*란|작성란")


def norm_title(t: str) -> str:
    """같은 양식 판정 열쇠 — 연도·번호·괄호·○○·양식 꼬리표를 뗀 제목(scripts/200 select 와 같은 꼴)."""
    t = storage.title_of(t or "")
    t = re.sub(r"[\(\[\{（【<「][^\)\]\}）】>」]*[\)\]\}）】>」]", "", t)
    t = re.sub(r"20\d\d|\d+\s*(차년도|차|회차|년|월|일|호|번)?", "", t)
    t = re.sub(r"(ㅇ|○|◯|o{2,}|O{2,})+", "", t)
    t = re.sub(r"(양식|서식|최종|수정|붙임|별지|별첨|제출용|작성용)", "", t)
    return re.sub(r"[^가-힣A-Za-z]", "", t)


def purpose_of(title: str, text: str = "") -> str:
    """목적 — 제목의 가장 긴 목적 낱말. 제목에 없으면 본문 앞 200자에서 한 번 더."""
    for src in (title or "", (text or "")[:200]):
        t = re.sub(r"\s+", "", src)
        best, which = "", ""
        for name, words in PURPOSES:
            for w in words:
                if w in t and len(w) > len(best):
                    best, which = w, name
        if which:
            return which
    return OTHER


def _under(folder: str, root: str) -> bool:
    return folder == root or folder.startswith(root + "/")


def folder_for(program: str = "", purpose: str = "", small: bool = False) -> str:
    """분류 → 공통 부서 안 폴더.
    - 사업 전용 아님: 「공통 양식/<목적>」
    - 사업 전용: 「지난 사업 문서/<사업>/<목적>」, 양식이 적은 사업(small)은 「지난 사업 문서/<사업>」"""
    purp = storage.safe_name(purpose or OTHER, 20)
    if not program:
        return f"{COMMON_ROOT}/{purp}"
    prog = storage.safe_name(program, 40)
    return f"{ROOT}/{prog}" if small else f"{ROOT}/{prog}/{purp}"


def platform_made(source: str) -> bool:
    """플랫폼이 만든 공통 양식(webhwp_templates) — 지난 문서에서 모은 것(양식:doc-·양식:common-)이 아닌 잠긴 양식."""
    s = str(source or "")
    return s.startswith("양식:") and doc_id_of(s) is None


def in_scope(folder: str, source: str) -> str:
    """정리 대상인가 — 지난 문서에서 모은 양식(source 양식:doc-·양식:common-)이 옛 자리·지난 사업 문서·공통 양식에 있으면
    그 자리 이름, 아니면 빈 글. 플랫폼이 만든 공통 양식과 사람이 만든 문서는 대상이 아니다."""
    f = folder or ""
    if doc_id_of(source) is None:
        return ""
    for r in (*OLD_ROOTS, ROOT, COMMON_ROOT):
        if _under(f, r):
            return r
    return ""


def doc_id_of(source: str) -> int | None:
    m = re.match(r"양식:(?:doc|common)-(\d+)$", str(source or ""))
    return int(m.group(1)) if m else None


# ── 사업 이름 ──
def _flat(t: str) -> str:
    return re.sub(r"\s+", "", str(t or "")).lower()


class Programs:
    """문서 → 사업. 원본 보관소 장부(archive_files.program·program_name) → 프로젝트 사업(projects.program) →
    문서 정체(identity.program) 순으로 열쇠·이름을 찾고, 사업 관리 장부(program_admin.merged_rows, 합친 줄은 합쳐 받은 줄로)의
    이름으로 바꾼다. 본문·제목의 사업 이름·별칭 수도 센다(사업 전용 판정)."""

    def __init__(self, db, data_dir: Path | None = None, rows: list[dict] | None = None):
        self.db = db
        if rows is None:
            try:
                from zzaimy.app import program_admin
                rows = program_admin.merged_rows(data_dir)
            except Exception:
                rows = []
        self.rows = rows
        by_key = {r["key"]: r for r in rows}
        self.target: dict[str, dict] = {}
        self.by_id: dict[str, dict] = {}
        for r in rows:
            tgt, seen = r, 0
            while tgt.get("merged_into") and tgt["merged_into"] in by_key and seen < 5:
                tgt, seen = by_key[tgt["merged_into"]], seen + 1
            self.target[r["key"]] = tgt
            for i in r.get("ids") or ():
                self.by_id.setdefault(i, tgt)
        try:
            from zzaimy.graph.programs import vague_name
        except Exception:
            def vague_name(_n):
                return False
        # 이름·별칭 → 줄(별칭 여럿이 한 사업으로 모인다). 너무 짧거나 일반어뿐인 별칭은 세지 않는다
        terms: dict[str, dict] = {}
        for r in rows:
            tgt = self.target[r["key"]]
            for t in [r.get("name") or "", *(r.get("terms") or [])]:
                f = _flat(t)
                if len(f) >= 3 and not vague_name(t):
                    terms.setdefault(f, tgt)
        self.terms = sorted(terms.items(), key=lambda kv: -len(kv[0]))
        try:
            from zzaimy.app.program_tree import Ledger
            from zzaimy.app.program_admin import load_ledger
            self.ledger = Ledger(load_ledger(data_dir))
        except Exception:
            self.ledger = None
        self._doc_cache: dict[int, tuple[str, dict | None]] = {}

    @staticmethod
    def name_of(r: dict) -> str:
        from zzaimy.app.program_names import display_name
        return display_name(r.get("name") or "") or r.get("name") or ""

    def stage(self, r: dict | None) -> str:
        """현행(current)·지난(past)·기간 모름(unknown)·장부에 없음(none) — program_admin.stage 와 같은 규칙."""
        if not r:
            return "none"
        try:
            from zzaimy.app.program_admin import stage
            return stage(r, int(time.strftime("%Y")))
        except Exception:
            return "unknown"

    def _resolve(self, key: str, name: str = "") -> tuple[str, dict | None]:
        from zzaimy.app.program_admin import _norm
        from zzaimy.app.program_names import display_name, is_program_name
        key = (key or "").split("|")[0]
        if key in ("none", "nonprogram"):
            key = ""
        for k in ([key] if key else []) + ([f"program:{_norm(name)}"] if name else []):
            if k in self.by_id:
                r = self.by_id[k]
                return self.name_of(r), r
        if name and self.ledger is not None:
            e = self.ledger.find(name)
            if e:
                nm = " ".join(str(e.get("name") or "").split())
                r = self.by_id.get(f"program:{_norm(nm)}")
                return (self.name_of(r), r) if r else (display_name(nm), None)
        if name and is_program_name(name, classified=bool(key)):
            return display_name(name), None
        return "", None

    def of_doc(self, doc_id: int | None) -> tuple[str, dict | None]:
        """문서가 이어진 사업 (보일 이름, 장부 줄 또는 None). 모르면 ("", None)."""
        if not doc_id:
            return "", None
        if int(doc_id) in self._doc_cache:
            return self._doc_cache[int(doc_id)]
        arch = proj = None
        with self.db._conn() as c:
            d = c.execute("SELECT * FROM documents WHERE id = ?", (int(doc_id),)).fetchone()
            d = dict(d) if d else {}
            sp = str(d.get("stored_path") or "")
            try:
                arch = c.execute("SELECT program, program_name FROM archive_files WHERE doc_id = ? OR rel = ? "
                                 "ORDER BY (doc_id = ?) DESC LIMIT 1",
                                 (int(doc_id), sp[6:] if sp.startswith("dgx://") else "\0", int(doc_id))).fetchone()
            except Exception:
                arch = None
            if d.get("project_id"):
                try:
                    proj = c.execute("SELECT program FROM projects WHERE id = ?", (int(d["project_id"]),)).fetchone()
                except Exception:
                    proj = None
        got: tuple[str, dict | None] = ("", None)
        if arch is not None:
            got = self._resolve(arch[0] or "", arch[1] or "")
        if not got[0] and proj is not None and proj[0]:
            got = self._resolve(proj[0], "")
        if not got[0]:
            try:
                ident = json.loads(d.get("identity") or "{}")
            except (ValueError, TypeError):
                ident = {}
            if isinstance(ident, dict) and ident.get("program"):
                got = self._resolve("", str(ident["program"]))
        self._doc_cache[int(doc_id)] = got
        return got

    def hits(self, title: str, text: str) -> list[dict]:
        """제목·본문에 나온 사업 — [{name, row, title_hit, text_hits}] 많이 나온 순. 긴 별칭이 먼저 잡히고 그 자리는 다시 세지 않는다."""
        ft, fx = _flat(title), _flat(text)
        acc: dict[str, dict] = {}
        for term, r in self.terms:
            n = fx.count(term)
            th = term in ft
            if not n and not th:
                continue
            fx = fx.replace(term, "\0")
            if th:
                ft = ft.replace(term, "\0")
            a = acc.setdefault(r["key"], {"name": self.name_of(r), "row": r, "title_hit": False, "text_hits": 0})
            a["text_hits"] += n
            a["title_hit"] = a["title_hit"] or th
        return sorted(acc.values(), key=lambda a: (-(a["title_hit"]), -a["text_hits"]))

    def specific(self, title: str, text: str, linked: tuple[str, dict | None] = ("", None)) -> dict:
        """사업 전용인가 — 제목에 사업 이름이 있거나 본문에 PROGRAM_HITS_MIN 번 이상 나온 사업(가장 많이 나온 것).
        이어진 사업(linked)은 본문·제목에 한 번이라도 나오면 그 사업으로 본다(별칭이 장부에 없는 사업도).
        돌려주는 것 {program, stage, title_hit, text_hits, linked}. 전용이 아니면 program 빈 글."""
        hs = self.hits(title, text)
        lname, lrow = linked
        out = {"program": "", "stage": "", "title_hit": False, "text_hits": 0, "linked": lname}
        top = hs[0] if hs else None
        if lname:
            mine = next((h for h in hs if lrow is not None and h["row"]["key"] == lrow["key"]), None)
            if mine is None and lrow is None:                    # 장부에 없는 사업 — 그 이름을 그대로 센다
                f = _flat(lname)
                n = _flat(text).count(f) if len(f) >= 3 else 0
                mine = {"name": lname, "row": None, "title_hit": len(f) >= 3 and f in _flat(title), "text_hits": n}
            if mine and (mine["title_hit"] or mine["text_hits"] >= 1):
                top = mine
        if top and (top["title_hit"] or top["text_hits"] >= PROGRAM_HITS_MIN or (lname and top["name"] == lname)):
            if top["title_hit"] or top["text_hits"] >= 1:
                out.update(program=top["name"], stage=self.stage(top["row"]), title_hit=top["title_hit"],
                           text_hits=top["text_hits"])
        return out


# ── 본문 재기 ──
def _unesc(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


def _text(frag: str) -> str:
    """조각의 보이는 글 — <hp:t> 안 글만(누름틀 안내 글·명령은 빼고)."""
    return re.sub(r"\s+", " ", _unesc(_TAG.sub("", " ".join(_T.findall(frag))))).strip()


def _is_name(v: str) -> bool:
    v = v.replace(" ", "")
    return bool(re.fullmatch(r"[가-힣]{2,4}", v)) and v[0] in SURNAMES and v not in NOT_NAMES


def read_sections(data: bytes) -> str:
    from zzaimy.app.webhwp_fields import check_zip
    z = zipfile.ZipFile(io.BytesIO(data))
    check_zip(z)
    secs = sorted((n for n in z.namelist() if _SEC.search(n)), key=lambda s: int(re.search(r"(\d+)\.xml$", s).group(1)))
    if not secs:
        raise ValueError("본문 절(section)이 없음")
    out = []
    import xml.etree.ElementTree as ET
    for n in secs:
        x = z.read(n).decode("utf-8")
        ET.fromstring(x.encode("utf-8"))                   # 웰폼드가 아니면 여기서 ParseError
        out.append(x)
    return "".join(out)


def measure(data: bytes) -> dict:
    """한 양식 파일을 잰다 — 열리는가, 글자·표·칸·빈 칸, 개인 정보 꼴, 구체 값, 양식 예시 글자·안내 수, 본문 지문."""
    m: dict = {"ok": True}
    try:
        xml = read_sections(data)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:80]}"}
    paras = [_text(p) for p in _LEAF_P.findall(xml)]
    text = "\n".join(p for p in paras if p)
    m["chars"] = len(re.sub(r"\s", "", text))
    m["paragraphs"] = sum(1 for p in paras if p)
    tables = _TBL.findall(xml)
    m["tables"] = xml.count("<hp:tbl")
    cells = empty = pairs = filled_pairs = 0
    filled_rows = 0
    names: list[str] = []
    addresses = accounts = 0
    for tb in tables:
        rows_filled = 0
        for i, tr in enumerate(_TR.findall(tb)):
            cs = [_text(c) for c in _TC.findall(tr)]
            cells += len(cs)
            empty += sum(1 for c in cs if not c)
            if i and cs and sum(1 for c in cs if c) >= max(2, 0.6 * len(cs)):
                rows_filled += 1
            for a, b in zip(cs, cs[1:]):
                lab = re.sub(r"[\s:：()]", "", a)
                if not lab or len(lab) > 15 or not re.search(r"[가-힣]", lab) or _PLACEHOLDER.search(b or ""):
                    continue
                pairs += 1
                if b:
                    filled_pairs += 1
                if any(lab.endswith(x) or lab == x for x in NAME_LABELS):
                    for v in re.split(r"[,·/]\s*", b):
                        if _is_name(v) and v.replace(" ", "") not in names:
                            names.append(v.replace(" ", ""))
                if any(x in lab for x in ADDRESS_LABELS) and re.search(r"(시|도|군|구)\s", b) and re.search(r"\d", b):
                    addresses += 1
                if any(x in lab for x in ACCOUNT_LABELS) and len(re.sub(r"\D", "", b)) >= 10:
                    accounts += 1
        filled_rows = max(filled_rows, rows_filled)
    for g in _SIGNED.finditer(text):
        if _is_name(g.group(1)) and g.group(1) not in names:
            names.append(g.group(1))
    m["signed"] = sum(1 for g in _SIGNED.finditer(text) if _is_name(g.group(1)))
    m["cells"], m["empty_cells"] = cells, empty
    m["blank_share"] = round(empty / cells, 3) if cells else None
    m["pairs"], m["filled_pairs"], m["filled_rows"] = pairs, filled_pairs, filled_rows

    def _real(s: str) -> bool:
        d = re.sub(r"\D", "", s)
        return len(set(d)) >= 3

    m["rrn"] = len(_RRN.findall(text))
    m["mobiles"] = sum(1 for x in _MOBILE.findall(text) if _real(x))
    m["phones"] = sum(1 for x in _PHONE.findall(text) if _real(x))
    m["emails"] = sum(1 for x in _EMAIL.findall(text) if not re.search(r"example|○|ㅇ|xxx|abc@", x, re.I))
    m["addresses"] = addresses + len(_ADDRESS.findall(text))
    acc_text = sum(1 for mm in _ACCOUNT.finditer(text)
                   if re.search(r"계좌|예금주|은행|통장", text[max(0, mm.start() - 30):mm.start()]) and _real(mm.group(0)))
    m["accounts"] = accounts + acc_text
    m["names"] = names[:20]
    m["name_count"] = len(names)
    m["dates"] = len(_DATE.findall(text))
    m["amounts"] = len(_AMOUNT.findall(text))
    m["placeholders"] = len(_PLACEHOLDER.findall(text))
    m["guidance"] = len(_GUIDANCE.findall(text))
    begins = re.findall(r'<hp:fieldBegin\b[^>]*type="CLICK_HERE"', xml)
    m["fields"] = len(begins)
    m["text_sha"] = hashlib.sha256(re.sub(r"\s+", "", text).encode("utf-8")).hexdigest()[:16]
    m["garbled"] = round(garbled_share(text), 4)
    m["_text"] = text                                     # 유사도 비교용(계획 파일에는 쓰지 않는다)
    return m


def concrete(m: dict) -> int:
    """구체 값 수 — 사람 이름·전화·전자우편·날짜·서명·금액(금액은 셋까지만 센다)."""
    return (m.get("name_count", 0) + m.get("mobiles", 0) + m.get("phones", 0) + m.get("emails", 0) + m.get("dates", 0)
            + m.get("signed", 0) + min(m.get("amounts", 0), 3))


def template_signals(m: dict) -> int:
    return m.get("placeholders", 0) + m.get("guidance", 0)


def garbled_share(text: str) -> float:
    if not text:
        return 0.0
    bad = sum(1 for ch in text if ch == "�" or "" <= ch <= "" or (ord(ch) < 32 and ch not in "\n\t"))
    return bad / len(text)


def judge(m: dict, title: str, purpose: str) -> tuple[str, str] | None:
    """지울 까닭(갈래, 설명) — 남길 것이면 None. 앞 갈래가 먼저:
    깨짐 > 빈 파일 > 개인 서류 > 변환 품질 낮음 > 한 번 쓴 기록 > 채워진 제출본 > 쓸 자리 없음 > 쓸모 적음. 중복은 묶어 보는 쪽(scripts/208)이 판정한다."""
    if not m.get("ok"):
        return "broken", f"열리지 않음({m.get('error', '')})"
    if m["chars"] < NEAR_EMPTY_CHARS and m["tables"] == 0:
        return "empty", f"빈 파일(글자 {m['chars']}, 표 0)"
    t = re.sub(r"\s+", "", title or "")
    personal_kind = next((w for w in PERSONAL_WORDS if w in t), "")
    if m["rrn"]:
        return "personal", f"개인 서류 — 주민등록번호 꼴 {m['rrn']}"
    if m["accounts"]:
        return "personal", f"개인 서류 — 계좌번호 {m['accounts']}"
    if m["addresses"] and (m["name_count"] or m["mobiles"]):
        return "personal", f"개인 서류 — 주소 {m['addresses']} + 이름 {m['name_count']}·휴대전화 {m['mobiles']}"
    if m["mobiles"] and m["name_count"]:
        return "personal", f"개인 서류 — 휴대전화 {m['mobiles']} + 사람 이름 {m['name_count']}"
    pii = m["name_count"] + m["mobiles"] + m["emails"] + m["signed"]
    if personal_kind and pii:
        return "personal", f"개인 서류(제목 「{personal_kind}」) + 개인 정보 {pii}(이름·휴대전화·전자우편·서명)"
    g = m.get("garbled", 0.0)
    if g >= GARBLED_SHARE:
        return "garbled", f"변환 품질 낮음 — 깨진 글자 {g:.1%} ≥ {GARBLED_SHARE:.0%}"
    ts, cc, bs = template_signals(m), concrete(m), m.get("blank_share")
    if purpose in ("회의록", "명단") and ts <= TEMPLATE_SIGNALS_MAX and bs is not None and bs < RECORD_BLANK_SHARE \
            and m["dates"] + m["name_count"] + m["filled_rows"] >= RECORD_CONCRETE:
        return "record", (f"한 번 쓴 기록({purpose}) — 빈 칸 {bs:.0%} < {RECORD_BLANK_SHARE:.0%}, 날짜 {m['dates']}·이름 "
                          f"{m['name_count']}·채운 행 {m['filled_rows']}, 양식 표시 {ts}")
    if ts <= TEMPLATE_SIGNALS_MAX:
        if bs is not None and bs < FILLED_BLANK_SHARE and cc >= FILLED_CONCRETE:
            return "filled", f"채워진 제출본 — 빈 칸 {bs:.0%} < {FILLED_BLANK_SHARE:.0%}, 구체 값 {cc}, 양식 표시 {ts}"
        if m["chars"] >= FILLED_PROSE_CHARS and m["dates"] >= FILLED_PROSE_DATES:
            return "filled", f"채워진 제출본(긴 글) — 글자 {m['chars']}, 날짜 {m['dates']}, 양식 표시 {ts}"
    if not m.get("fields"):
        return "nofields", "쓸 자리 없음 — 누름틀 필드 0"
    if purpose == OTHER and ts == 0 and (bs is None or bs < LOW_VALUE_BLANK):
        return "lowvalue", f"쓸모 적음 — 목적 모름, 양식 표시 0, 빈 칸 {('-' if bs is None else f'{bs:.0%}')} < {LOW_VALUE_BLANK:.0%}"
    return None


REASON_LABEL = {"broken": "깨짐", "empty": "빈 파일", "personal": "개인 서류", "garbled": "변환 품질 낮음",
                "nofields": "쓸 자리 없음", "record": "한 번 쓴 기록", "filled": "채워진 제출본", "lowvalue": "쓸모 적음",
                "duplicate": "중복(같은 본문)", "neardup": "중복(거의 같은 본문)", "sametitle": "같은 제목(더 나은 것을 남김)"}


def classify(data: bytes, title: str, programs: "Programs | None" = None, doc_id: int | None = None) -> dict:
    """한 양식 파일의 판정 — 정리(scripts/208)·등록(scripts/200 register)·올리기 경로가 같이 쓴다.
    {action: keep|trash, reason_code, reason, program, stage, purpose, folder(작은 사업 접기 전), measure}.
    중복은 여러 파일을 함께 봐야 하므로 여기서는 보지 않는다."""
    m = measure(data)
    text = m.pop("_text", "")
    purpose = purpose_of(title, text)
    spec = {"program": "", "stage": "", "title_hit": False, "text_hits": 0, "linked": ""}
    if m.get("ok") and programs is not None:
        spec = programs.specific(title, text, programs.of_doc(doc_id) if doc_id else ("", None))
    bad = judge(m, title, purpose)
    out = {"action": TRASH if bad else KEEP, "reason_code": bad[0] if bad else "", "reason": bad[1] if bad else "",
           "purpose": purpose, **spec, "folder": "" if bad else folder_for(spec["program"], purpose), "measure": m}
    out["_text"] = text
    return out


def blank_slots(m: dict) -> int:
    return int(m.get("fields") or 0) + int(m.get("empty_cells") or 0) + int(m.get("placeholders") or 0)


def similarity(a: str, b: str) -> float:
    a, b = re.sub(r"\s+", "", a)[:DUP_TEXT_CAP], re.sub(r"\s+", "", b)[:DUP_TEXT_CAP]
    if not a or not b:
        return 0.0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if sm.real_quick_ratio() < NEAR_DUP_RATIO or sm.quick_ratio() < NEAR_DUP_RATIO:
        return 0.0
    return sm.ratio()


# ── 제목 가리기(계획 md 에 보일 제목) ──
def mask_title(title: str, names=()) -> str:
    """제목 속 사람 이름 가리기 — 본문에서 찾은 이름, 그리고 개인 서류 제목에서 성으로 시작하는 홀로 선 2~4글자 낱말.
    「박○○ 이력서」 꼴(첫 글자만 남긴다)."""
    out = title or ""
    for n in sorted({x for x in names if x}, key=len, reverse=True):
        out = out.replace(n, n[0] + "○" * (len(n) - 1))
    flat = re.sub(r"\s+", "", out)
    if any(w in flat for w in PERSONAL_WORDS) or re.search(r"서약서|동의서|확인서|증명서|신청서|지원서", flat):
        known = {w for _n, ws in PURPOSES for w in ws} | set(PERSONAL_WORDS)

        def sub(mm):
            w = mm.group(1)
            if w[0] in SURNAMES and w not in NOT_NAMES and not any(k in w for k in known) \
                    and not re.search(r"(서|표|록|부|안|문|과|처|팀|원|단|대|교|회|별|용|본|식|건)$", w):
                return w[0] + "○" * (len(w) - 1)
            return w

        out = re.sub(r"(?<![가-힣])([가-힣]{2,4})(?![가-힣])", sub, out)
    return out
