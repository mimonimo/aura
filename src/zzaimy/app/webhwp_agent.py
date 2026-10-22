"""채팅 에이전트의 한글 기안기 도구 — 편집기를 「웹 한글 기안기」 로 고른 사람의 대화에서 구글 문서 도구 대신 쓴다(흐름은 같고 편집기만 다르다).

도구(판단은 에이전트, 코드는 실행·관문·기록 — agent-not-pipeline):
- hwp_forms {query}: 쓸 양식 후보 — 이 프로젝트의 한글 양식(사업이 준 서식)이 먼저, 그다음 한글 문서함의 잠긴 양식(공문·국고 사업·학교 양식).
- hwp_start {form, title}: 고른 양식으로 작업본을 부서 디렉터리(「작업/<프로젝트>」)에 만들고 이 대화에 잇는다. 한글 원본은 hwpx 로 바꾸고 쓸 자리 필드를 박는다.
- hwp_fields {}: 작업본의 필드와 채움 상태(빈 필드가 먼저).
- hwp_fill {values: {필드: 값}}: 칸·표 필드 값 넣기.
- hwp_write {field, text}: 절 필드에 본문 쓰기(번호 문단 + 마크다운 표). 같은 절을 다시 쓰면 앞 내용을 지우고 새로.
쓰기는 서버에서 파일을 고치고 판을 남긴다(webhwp.save, 고친 사람 「AURA」). 사람이 그 문서를 편집 중(잠금)이면 쓰지 않고 알린다.
쓰는 동안 「AURA 작성 중 · 필드」 를 남겨 편집 화면이 AURA 커서·안내를 띄운다(webhwp_activity:<id>).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from zzaimy.app import storage, webhwp


def bound(db, session_id: int) -> int | None:
    v = (db.get_setting(f"chat_hwp:{int(session_id)}", "") or "").strip()
    return int(v) if v.isdigit() else None


def activity(db, fid: int) -> dict:
    try:
        a = json.loads(db.get_setting(f"webhwp_activity:{int(fid)}", "{}") or "{}")
    except ValueError:
        return {}
    return a if a and time.time() - float(a.get("at") or 0) < 120 else {}


class HwpTools:
    def __init__(self, db, base: Path, session_id: int, owner: str, dept: str, project: dict | None, project_docs):
        self.db, self.base, self.sid, self.owner = db, Path(base), int(session_id), owner
        self.dept = dept or webhwp.COMMON
        self.project = project
        self.project_docs = project_docs            # () -> 프로젝트·대화 문서 목록(dict)
        self.writes = 0

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
                    "(프로젝트에 사업이 준 한글 양식이 있으면 그것을 쓴다. 후보가 여럿이라 정하기 어렵거나 담당자가 정할 일이면 ask_user 로 묻는다)."]
        f = webhwp.get(self.db, fid)
        fl = self._fields()
        empty = [x["name"] for x in fl if not x["text"]]
        lines = [f"편집기: 웹 한글 기안기 — 이 대화의 한글 작업본: 「{Path(f['name']).stem}」(필드 {len(fl)}개, 빈 필드 {len(empty)}개)"]
        lines.append("필드(이름 — 상태): " + "; ".join(f"{x['name']} — {'비어 있음' if not x['text'] else '채움'}" for x in fl[:80]))
        held = webhwp.lock_holder(self.db, fid)
        if held and held["user"] != "AURA":
            lines.append(f"지금 {held['user']} 님도 이 작업본을 편집 중이다 — 칸은 그 화면에 바로 들어가니 그대로 쓴다.")
        if empty:
            lines.append("작성 순서: ① 담당자가 알려 준 값·프로젝트 정보(프로젝트 이름은 사업명 칸)로 채울 수 있는 칸을 hwp_fill 한 번에 모두 채운다 "
                         "② 빈 절을 하나씩 hwp_write 로 쓴다 ③ 그래도 모르는 값만 마지막에 ask_user 로 한 번에 묻는다 — 칸 하나 때문에 쓰기를 멈추지 않는다.")
        return lines

    # ── 도구 ──
    def forms(self, a: dict) -> str:
        q = str(a.get("query") or "").strip()
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
        return ("양식 후보(form 에 앞의 id 를 쓴다):\n" + "\n".join(out)) if out else "맞는 양식이 없다 — 검색어를 바꾸거나 담당자에게 묻는다."

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
        self.db.set_setting(f"chat_hwp:{self.sid}", str(fid))
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
        # 사람이 같은 문서를 편집 중이어도 쓴다 — 칸 글은 실시간 공유로 그 화면에 바로 들어가고(live_put), 저장 때 서로 합친다
        return fid, webhwp.get(self.db, fid)

    def _mark(self, fid: int, field: str) -> None:
        self.db.set_setting(f"webhwp_activity:{fid}", json.dumps({"by": "AURA", "field": field, "at": time.time()}, ensure_ascii=False))

    def fill(self, a: dict) -> str:
        from zzaimy.app import webhwp_write as ww
        g = self._guard()
        if isinstance(g, str):
            return g
        fid, f = g
        values = a.get("values") if isinstance(a.get("values"), dict) else {}
        values = {str(k): str(v)[:2000] for k, v in values.items() if not str(k).startswith("절:")}
        if not values:
            return "values 가 비었다(절 필드는 hwp_write 로 쓴다)."
        self._mark(fid, next(iter(values)))
        data, done = ww.set_fields(Path(f["path"]).read_bytes(), values)
        if not done:
            return "그런 필드가 없다 — hwp_fields 의 이름을 그대로 쓴다."
        webhwp.save(self.db, self.base, fid, data, user="AURA")
        for k in done:                                      # 열린 편집 화면의 같은 칸에 바로(다시 불러오지 않고)
            webhwp.live_put(self.db, fid, "AURA", k, values[k])
        self.writes += 1
        miss = [k for k in values if k not in done]
        return f"채웠다: {', '.join(done)}" + (f" · 없는 이름: {', '.join(miss)}" if miss else "")

    def write(self, a: dict) -> str:
        from zzaimy.app import webhwp_write as ww
        g = self._guard()
        if isinstance(g, str):
            return g
        fid, f = g
        field = str(a.get("field") or "").strip()
        text = str(a.get("text") or "").strip()
        if not field.startswith("절:"):
            field = "절:" + field if not field.startswith(("칸:", "표")) else field
        if not text:
            return "text 가 비었다."
        m = webhwp.meta(self.db, fid)
        written = m.get("written") or {}
        self._mark(fid, field)
        try:
            data, put = ww.write_section(Path(f["path"]).read_bytes(), field, ww.parse_body(text), written)
        except KeyError:
            names = [x["name"] for x in self._fields() if x["kind"] == "절"]
            return f"그런 절 필드가 없다. 절 필드: {', '.join(names[:30])}"
        webhwp.save(self.db, self.base, fid, data, user="AURA")
        m = webhwp.meta(self.db, fid)
        written[field] = put
        m["written"] = written
        self.db.set_setting(f"webhwp_meta:{fid}", json.dumps(m, ensure_ascii=False))
        self.writes += 1
        return f"「{field[2:]}」 을 썼다(문단·표 {len(put) + 1}개)."
