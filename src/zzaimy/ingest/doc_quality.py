"""구글 독스 작업본 품질 점검·교정 — 화면이 아니라 문서 구조(documents.get)에서 잰다.

- 표 폭: 열 너비 합이 본문 폭(쪽 폭 − 좌우 여백)을 넘으면 오른쪽 여백 밖으로 삐져나온다. 한글 서식의 좁은 여백(약 20mm) 기준
  표가 독스 여백(25.4mm)에 그대로 들어와 표 87/113개가 28pt 넘쳤다(10/11 AID 작업본). 열 비율은 지키고 본문 폭에 맞춘다
- 연속 반복 줄: 같은 글의 문단이 셋 이상 잇달아 나오면 채우기·옮기기 중복이다(「◦ 달성 계획 및 전략」 24번). 하나만 남긴다
특정 문서·서식의 글이 아니라 꼴(폭·반복)로만 판단한다."""
from __future__ import annotations

MIN_COL_PT = 14.0          # 이보다 좁히면 글자가 한 자씩 세로로 쌓인다
REPEAT_RUN = 3             # 같은 문단이 이만큼 잇달면 중복으로 본다


def _doc_tab(document: dict) -> tuple[dict, str | None]:
    tabs = document.get("tabs") or []
    if tabs:
        t = tabs[0]
        return t.get("documentTab") or {}, (t.get("tabProperties") or {}).get("tabId")
    return document, None


def content_width(doc: dict) -> float:
    st = doc.get("documentStyle") or {}
    mag = lambda k: float(((st.get(k) or {}).get("magnitude")) or 0)  # noqa: E731
    page = float((((st.get("pageSize") or {}).get("width") or {}).get("magnitude")) or 595.3)
    return page - (mag("marginLeft") or 72.0) - (mag("marginRight") or 72.0)


def _para_text(p: dict) -> str:
    return "".join((e.get("textRun") or {}).get("content", "") for e in p.get("elements", [])).strip()


def audit(document: dict) -> dict:
    """{'width': 본문 폭, 'tables': n, 'wide': [{start, cols, total}], 'repeats': [{text, count, ranges}]}"""
    doc, _tab = _doc_tab(document)
    width = content_width(doc)
    body = (doc.get("body") or {}).get("content") or []
    wide, n = [], 0
    for el in body:
        t = el.get("table")
        if not t:
            continue
        n += 1
        cols = (t.get("tableStyle") or {}).get("tableColumnProperties") or []
        ws = [float(((c.get("width") or {}).get("magnitude")) or 0) for c in cols]
        if all(ws) and sum(ws) > width + 0.5:
            wide.append({"start": el["startIndex"], "cols": ws, "total": round(sum(ws), 1)})
    repeats, run = [], []
    for el in body + [{}]:
        p = el.get("paragraph")
        txt = _para_text(p) if p else None
        if txt and run and txt == run[0][0]:
            run.append((txt, el["startIndex"], el["endIndex"]))
            continue
        if len(run) >= REPEAT_RUN:
            repeats.append({"text": run[0][0], "count": len(run), "ranges": [(s, e) for _t, s, e in run[1:]]})
        run = [(txt, el["startIndex"], el["endIndex"])] if txt else []
    return {"width": round(width, 1), "tables": n, "wide": wide, "repeats": repeats}


def fix_requests(document: dict) -> list[dict]:
    """audit 결과를 고치는 batchUpdate 요청 — 반복 줄 지우기(뒤에서부터)와 넘친 표 열 너비 맞추기."""
    doc, tab = _doc_tab(document)
    found = audit(document)
    width = found["width"]
    loc = (lambda i: {"index": i, "tabId": tab}) if tab else (lambda i: {"index": i})
    reqs: list[dict] = []
    # 지우기를 먼저, 뒤쪽부터 — 앞 위치가 밀리지 않게. 표 위치는 지운 뒤 달라지므로 지울 길이만큼 당긴다
    cuts = sorted((r for rep in found["repeats"] for r in rep["ranges"]), reverse=True)
    for s, e in cuts:
        rng = {"startIndex": s, "endIndex": e}
        if tab:
            rng["tabId"] = tab
        reqs.append({"deleteContentRange": {"range": rng}})
    for t in found["wide"]:
        shift = sum(e - s for s, e in cuts if e <= t["start"])
        scale = width / t["total"]
        new = [max(MIN_COL_PT, w * scale) for w in t["cols"]]
        over = sum(new) - width                           # 최소 폭 때문에 넘친 만큼은 넓은 열에서 덜어 낸다
        if over > 0:
            big = sum(w for w in new if w > MIN_COL_PT)
            new = [w - over * (w / big) if w > MIN_COL_PT else w for w in new]
        for i, w in enumerate(new):
            reqs.append({"updateTableColumnProperties": {
                "tableStartLocation": loc(t["start"] - shift), "columnIndices": [i],
                "tableColumnProperties": {"widthType": "FIXED_WIDTH", "width": {"magnitude": round(w, 2), "unit": "PT"}},
                "fields": "widthType,width"}})
    return reqs


def check_and_fix(email: str, doc: str, *, apply: bool = False, http=None) -> dict:
    """문서를 읽어 점검하고, apply 면 고친다. 돌려주는 것: audit 요약 + 보낸 요청 수."""
    from zzaimy.ingest import gdocs

    http = http or gdocs._http()
    r = gdocs._read(email, doc, http)
    gdocs._raise(r)
    document = r.json()
    found = audit(document)
    reqs = fix_requests(document) if apply else []
    for i in range(0, len(reqs), 400):                    # 큰 문서는 나눠 보낸다(요청 한도)
        gdocs._batch(email, gdocs.doc_id(doc), reqs[i:i + 400], http)
    return {"width": found["width"], "tables": found["tables"], "wide": len(found["wide"]),
            "repeats": [(x["text"][:30], x["count"]) for x in found["repeats"]], "requests": len(reqs)}
