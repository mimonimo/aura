"""사업 목록의 대·중·소 분류 — 라이브러리 「사업별로 보기」와 그래프 탐색 왼쪽 목록이 같은 나무를 쓴다.

기준은 외부 확인 장부(kg_external.json)다. 장부의 사업 항목은 학교의 재정지원사업 현황(연도별 엑셀, scripts/186)과
공개 출처로 채워져 있고, 항목마다 지원기관(ministry)과 재정지원 체계 갈래(category)를 적는다.
- 대분류: 지원 주체 — 교육부 재정지원 / 다른 부처 / 지자체·교육청 / 공공기관·기업 등. 지원기관 이름의 꼴(「…부」「…광역시」
  「…구청」)로 가른다 — 사업마다 적어 두지 않는다
- 중분류: 교육부는 재정지원 체계 갈래(장부 categories 의 이름), 나머지는 지원기관
- 소분류: 사업. 보일 이름은 장부의 정식 이름(엑셀에 적힌 이름) — 문서에서 뽑힌 이름의 띄어쓰기·약칭·오타 차이를 지운다
장부에 없는 이름은 「장부에 없는 이름」으로 따로 모아 확인할 수 있게 둔다(지어낸 갈래에 넣지 않는다).
"""
from __future__ import annotations

import re

from zzaimy.app.program_names import group_key

L1_EDU, L1_GOV, L1_LOCAL, L1_ORG, L1_OTHER, L1_UNKNOWN = (
    "교육부 재정지원", "다른 부처 사업", "지자체·교육청 사업", "공공기관·기업 등", "그 밖의 사업", "장부에 없는 이름 (확인 필요)")
ORDER = (L1_EDU, L1_GOV, L1_LOCAL, L1_ORG, L1_OTHER, L1_UNKNOWN)
EDU_CATS = ("일반재정지원", "RISE·앵커", "특수목적")
EDU_REST = "그 밖의 교육부 사업(장학·학생 지원 등)"

_EDU = re.compile(r"^(교육부|교육과학기술부|교육인적자원부)")
_LOCAL = re.compile(r"(특별시|광역시|특별자치|도청|구청|군청|시청|교육청|^경상북도|^경상남도|^경기도|^전라|^충청|^강원)|(?:[가-힣]+(?:시|군|구))$")
_GOV = re.compile(r"^(?!.*(?:본부|사령부)$)[가-힣]{2,12}(부|처)$")      # 중앙 부처(○○부·○○처) — 「…본부」「…사령부」는 기관
_MIN_KEY = 4                                            # 장부 표기가 이름 속에 들어 있다고 볼 최소 열쇠 길이


def ministry_of(e: dict) -> str:
    """지원기관 첫 이름 — 「교육부 (한국연구재단)」→ 교육부, 「교육부, 대구광역시」→ 교육부."""
    m = re.sub(r"^\s*[(（][^)）]*[)）]\s*", "", str(e.get("ministry") or ""))        # 「(재)○○」 앞 법인 표기
    m = re.split(r"[,(（/]", m)[0]
    return " ".join(m.split())


def place(e: dict | None, categories: dict | None = None) -> tuple[str, str]:
    """장부 항목 → (대분류, 중분류)."""
    if not e:
        return L1_UNKNOWN, ""
    cats = categories or {}
    cat = e.get("category") or ""
    m = ministry_of(e)
    if _EDU.match(m) or (not m and cat in EDU_CATS):
        return L1_EDU, ((cats.get(cat) or {}).get("label") or cat) if cat in EDU_CATS else EDU_REST
    if not m:
        return L1_OTHER, ""
    if _LOCAL.search(m):
        return L1_LOCAL, m
    if _GOV.search(m):
        return L1_GOV, m
    return L1_ORG, m


class Ledger:
    """장부 항목 찾기 — 이름의 열쇠(group_key)가 항목의 이름·별칭 열쇠와 같으면 그 항목, 아니면 이름 속에 든 가장 긴 별칭."""

    def __init__(self, ledger: dict | None):
        ledger = ledger or {}
        self.categories = ledger.get("categories") or {}
        self.exact: dict[str, dict] = {}
        self.keys: list[tuple[str, dict]] = []
        for e in ledger.get("programs") or []:
            if e.get("category") == "사업 아님":
                continue
            for t in [e.get("name") or "", *(e.get("terms") or [])]:
                k = group_key(t)
                if k:
                    self.exact.setdefault(k, e)
                    if len(k) >= _MIN_KEY:
                        self.keys.append((k, e))
        self.keys.sort(key=lambda kv: -len(kv[0]))

    def find(self, name: str) -> dict | None:
        k = group_key(name)
        if not k:
            return None
        if k in self.exact:
            return self.exact[k]
        for t, e in self.keys:
            if t in k:
                return e
        return None


def build(items: list[dict], ledger: dict | None) -> list[dict]:
    """items: [{label, n, …}] → [{label, n, groups:[{label, n, progs:[…]}]}]. 항목의 label 은 장부 정식 이름으로 바꾸고 원래 이름(raw_label)·in_ledger 를 단다.
    대분류는 정한 순서, 중분류·사업은 문서 많은 순."""
    lg = Ledger(ledger)
    tree: dict[str, dict[str, list[dict]]] = {}
    for it in items:
        e = lg.find(it.get("label") or "")
        l1, l2 = place(e, lg.categories)
        row = {**it, "raw_label": it.get("label"), "label": (e.get("name") if e else None) or it.get("label"),
               "in_ledger": e is not None}
        tree.setdefault(l1, {}).setdefault(l2, []).append(row)
    out = []
    for l1 in ORDER:
        if l1 not in tree:
            continue
        groups = []
        for l2, rows in tree[l1].items():
            rows.sort(key=lambda r: -int(r.get("n") or 0))
            groups.append({"label": l2, "n": sum(int(r.get("n") or 0) for r in rows), "progs": rows})
        groups.sort(key=lambda g: (g["label"] == EDU_REST, -g["n"]))
        out.append({"label": l1, "n": sum(g["n"] for g in groups), "groups": groups, "unknown": l1 == L1_UNKNOWN})
    return out
