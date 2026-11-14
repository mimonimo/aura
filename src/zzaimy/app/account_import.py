"""사용자 일괄 등록(11-15) — 엑셀·CSV 한 장으로 계정 여러 개를 만든다. 다른 기관에 설치한 뒤 첫 사용자들을 한 번에 넣는 길.

열: 아이디·이름·부서·역할·등급·상위 부서(아이디만 꼭). 역할은 교직원(기본)·관리자, 등급은 일반·행정 담당자·사업 책임자·총괄 책임자.
계정마다 임시 비밀번호를 만들고 응답에만 싣는다(저장·기록하지 않음) — 관리자가 받은 파일로 전달하고, 첫 로그인 때 본인이 바꾼다.
검사는 사용자 편집 창과 같은 user_admin._save_one 을 거친다. 없는 부서는 「없는 부서도 만들기」 를 고른 때만 만든다(조직 관리의
부서 더하기와 같은 이름 검사, 옛 권한 흔적이 남은 이름은 만들지 않음).
"""
from __future__ import annotations

import csv
import io
import secrets
import string

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from zzaimy.app import org_lock

router = APIRouter()

HEADERS = {
    "uid": ("아이디", "id", "uid", "계정", "계정 아이디"),
    "name": ("이름", "name", "성명"),
    "dept": ("부서", "dept", "소속", "소속 부서"),
    "role": ("역할", "role"),
    "grade": ("등급", "grade"),
    "parent": ("상위 부서", "처·단", "처단", "parent"),
}
ROLE_WORDS = {"": "staff", "교직원": "staff", "직원": "staff", "staff": "staff", "관리자": "dev", "dev": "dev"}
MAX_ROWS = 2000
TEMPLATE = "아이디,이름,부서,역할,등급,상위 부서\nhong01,홍길동,기획처,교직원,사업 책임자,\n"


def _cell(v) -> str:
    return " ".join(str(v if v is not None else "").split())


def parse(data: bytes, filename: str) -> list[dict]:
    """파일 → [{uid, name, dept, role, grade, parent, line}]. 첫 줄은 머리(열 이름)."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        table = [[_cell(c) for c in row] for row in wb.worksheets[0].iter_rows(values_only=True)]
    else:
        for enc in ("utf-8-sig", "cp949"):              # 엑셀이 저장한 한글 CSV 는 흔히 cp949
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("파일 글자를 읽지 못했습니다(UTF-8 또는 CP949 CSV, 또는 xlsx)")
        table = [[_cell(c) for c in row] for row in csv.reader(io.StringIO(text))]
    table = [r for r in table if any(r)]
    if not table:
        raise ValueError("빈 파일입니다")
    head = [h.replace(" ", "").lower() for h in table[0]]
    col = {}
    for key, names in HEADERS.items():
        for i, h in enumerate(head):
            if h in {n.replace(" ", "").lower() for n in names}:
                col[key] = i
                break
    if "uid" not in col:
        raise ValueError("첫 줄에 「아이디」 열이 있어야 합니다(양식 받기 참고)")
    out = []
    for n, r in enumerate(table[1:MAX_ROWS + 1], start=2):
        get = lambda k: (r[col[k]] if k in col and col[k] < len(r) else "")
        out.append({"uid": get("uid").lower(), "name": get("name"), "dept": get("dept"), "role": get("role"),
                    "grade": get("grade"), "parent": get("parent"), "line": n})
    return out


def temp_password(n: int = 12) -> str:
    """임시 비밀번호 — 헷갈리는 글자(0 O 1 l I) 없이, 글자·숫자 섞어."""
    alpha = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
    while True:
        pw = "".join(secrets.choice(alpha) for _ in range(n))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def _make_dept(st, name: str, parent: str, by: str, known: set[str]) -> str:
    """없는 부서 만들기 — 오류 문장 또는 「」. 조직 관리의 부서 더하기와 같은 이름 검사·흔적 검사."""
    from zzaimy.app import dept_dictionary, rag_spaces
    from zzaimy.app.org_routes import traces
    dd = getattr(st, "data_dir", None)
    if dd is None:
        return "설정 위치가 없습니다"
    if traces(st, name):
        return f"{name} 은(는) 예전 부서의 권한 기록이 남아 있어 조직 관리에서 만들어야 합니다"
    new_cfg, err = rag_spaces.create_dept_rag(rag_spaces.config(dd), name, by=by)
    if err:
        return err
    rag_spaces.save_config(new_cfg, dd)
    if parent and parent in known:
        dept_dictionary.set_parent(dd, name, parent)
    else:
        dept_dictionary.set_parent(dd, name, "")              # 맨 위(처·단)
    known.add(name)
    return ""


@router.get("/team/accounts/import-template.csv")
def import_template(request: Request):
    from zzaimy.app.user_admin import need_admin
    need_admin(request)
    return Response(("﻿" + TEMPLATE).encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename*=UTF-8''%EC%82%AC%EC%9A%A9%EC%9E%90_%EC%9D%BC%EA%B4%84%EB%93%B1%EB%A1%9D_%EC%96%91%EC%8B%9D.csv"})


@router.post("/team/accounts/import")
@org_lock.serialized
def import_accounts(request: Request, file: UploadFile = File(...), make_depts: str = Form("")):
    """일괄 등록 — JSON {ok, msg, done:[{uid,name,pw}], failed:[{line,uid,why}]}. 임시 비밀번호는 이 응답에만."""
    from zzaimy.app import dept_dictionary, grades
    from zzaimy.app.user_admin import _accounts, _save_one, _user, known_depts, need_admin
    need_admin(request)
    st = request.app.state
    try:
        rows = parse(file.file.read(), file.filename or "")
    except ValueError as e:
        return JSONResponse({"ok": False, "msg": str(e), "done": [], "failed": []}, status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "msg": "파일을 읽지 못했습니다(CSV 또는 xlsx)", "done": [], "failed": []}, status_code=400)
    accounts = _accounts(request)
    me = _user(request)
    known = set(known_depts(st.db, accounts, getattr(st, "data_dir", None)))
    grade_of = {v.replace(" ", ""): k for k, v in grades.GRADES.items()} | {k: k for k in grades.GRADES} | {"": ""}
    dd = getattr(st, "data_dir", None)
    done, failed = [], []
    for r in rows:
        uid = r["uid"]
        role = ROLE_WORDS.get(r["role"].replace(" ", ""), None)
        if role is None:
            failed.append({"line": r["line"], "uid": uid, "why": f"역할 「{r['role']}」 은 교직원·관리자 가운데 하나로 적습니다"})
            continue
        grade = grade_of.get(r["grade"].replace(" ", ""))
        if grade is None:
            failed.append({"line": r["line"], "uid": uid, "why": f"등급 「{r['grade']}」 을 알 수 없습니다"})
            continue
        dept = r["dept"]
        if dept and dd is not None:
            dept = dept_dictionary.load(dd).normalize(dept) or dept   # 별칭(옛 이름·줄임말)은 정식 이름으로
        if dept and dept not in known:
            if not make_depts:
                failed.append({"line": r["line"], "uid": uid, "why": f"부서 「{dept}」 이 없습니다(「없는 부서도 만들기」 를 고르거나 조직 관리에서 먼저 만듭니다)"})
                continue
            why = _make_dept(st, dept, r["parent"], me, known)
            if why:
                failed.append({"line": r["line"], "uid": uid, "why": why})
                continue
        pw = temp_password()
        why = _save_one(st, accounts, me, uid=uid, new=True, name=r["name"], role=role, dept=dept, on=True,
                        new_pw=pw, grade=grade)
        if why:
            failed.append({"line": r["line"], "uid": uid, "why": why})
        else:
            done.append({"uid": uid, "name": r["name"], "pw": pw})
    msg = f"{len(done)}명을 만들었습니다" + (f" · {len(failed)}줄은 만들지 못했습니다" if failed else "")
    return JSONResponse({"ok": bool(done), "msg": msg, "done": done, "failed": failed}, status_code=200 if done or not failed else 400)
