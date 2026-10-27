"""SFT 문답 검수자 만들기 — 플랫폼 계정·라벨 스튜디오 계정·검수 몫 나누기(10-27).

  PYTHONPATH=src .venv/bin/python scripts/218_make_reviewers.py kim:김검수 lee:이검수 park:박검수 \\
      --out ~/ops/reviewers-1027.txt [--assign 500] [--apply]

기본은 미리 보기(--dry-run) — 무엇을 할지만 찍고 아무것도 바꾸지 않는다. --apply 를 붙여야 실제로 한다.

(a) 플랫폼 계정: data/platform/accounts.json 에 교직원·일반 등급·「SFT 문답 검수」 권한(grades.PERMS sft_review)으로 만든다.
    비밀번호는 무작위, 앱과 같은 해시(passwords.hash_pw), 첫 로그인 때 바꾸게(must_change). 이미 있는 계정은 권한만 더한다
    (비밀번호는 --reset-password 일 때만 새로). 앱은 계정 표를 메모리에 들고 있으므로 --apply 뒤 바로 앱을 다시 띄운다
    (그 사이 화면에서 계정을 저장하면 이 변경이 덮인다).
(b) 라벨 스튜디오 계정: API(POST /api/users/)로 <아이디>@aura.local(또는 --email) 사용자를 만들고 — 토큰 주인의 조직에 들어간다 —
    비밀번호 설정과 조직 넣기는 ls-venv 헬퍼(68 user-setup, ls_admin.setup_user)로 한다. 헬퍼가 없는 서버면 비밀번호 칸에 그렇게 적는다.
(c) --assign N: 검수 프로젝트에서 표본 N건을 갈래(data.track) 비율대로 뽑아(시드 고정 — 다시 돌려도 같은 표본) data.assignee 를
    검수자에게 돌아가며 적는다(PATCH /api/tasks/{id}, 다른 data 칸은 그대로). 이미 누가 맡은 할 일은 건너뛴다.
비밀번호는 화면에 찍지 않는다 — --out 파일(0600)에만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import secrets
import sys
from datetime import datetime
from pathlib import Path

from zzaimy.app import grades, passwords

PERM = "sft_review"
UID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")          # 조직 관리 화면(user_admin._UID)과 같은 규칙
DEFAULT_PROJECT = "ZZAIMY SFT 5000 검수"


def parse_people(items: list[str]) -> list[tuple[str, str]]:
    out = []
    for it in items:
        uid, _, name = it.partition(":")
        uid = uid.strip().lower()
        if not UID.match(uid):
            raise SystemExit(f"아이디 꼴이 아닙니다: {uid!r} (영문 소문자·숫자 3~32자)")
        out.append((uid, name.strip() or uid))
    if len({u for u, _ in out}) != len(out):
        raise SystemExit("같은 아이디가 두 번 있습니다")
    return out


def new_password() -> str:
    return secrets.token_urlsafe(12)            # 16자 — passwords.MIN_LENGTH 이상


# ---------------------------------------------------------------- (a) 플랫폼 계정

def plan_accounts(accounts: dict, people: list[tuple[str, str]], *, reset: bool = False) -> list[dict]:
    """할 일 — [{uid, name, action: create|grant|reset|keep}]."""
    plan = []
    for uid, name in people:
        a = accounts.get(uid)
        if a is None:
            plan.append({"uid": uid, "name": name, "action": "create"})
        elif a.get("role") not in grades.STAFF:
            raise SystemExit(f"{uid} 는 교직원 계정이 아닙니다(역할 {a.get('role')}) — 손으로 정리한 뒤 다시")
        elif reset:
            plan.append({"uid": uid, "name": name, "action": "reset"})
        elif PERM not in (a.get("perms") or ()):
            plan.append({"uid": uid, "name": name, "action": "grant"})
        else:
            plan.append({"uid": uid, "name": name, "action": "keep"})
    return plan


def apply_accounts(accounts: dict, plan: list[dict], *, dept: str = "") -> dict[str, str]:
    """계정 표를 고친다(메모리) — 새로 정한 비밀번호 {uid: pw}."""
    now = datetime.now().isoformat(timespec="seconds")
    pws: dict[str, str] = {}
    for p in plan:
        uid = p["uid"]
        if p["action"] == "create":
            pw = new_password()
            h, salt = passwords.hash_pw(pw)
            accounts[uid] = {"role": "staff", "name": p["name"], "active": True, "created_at": now, "updated_at": now,
                             "pw_hash": h, "salt": salt, "must_change": True, "dept": dept, "grade": "member",
                             "perms": [PERM], "perms_by": "scripts/218"}
            pws[uid] = pw
            continue
        a = accounts[uid]
        if PERM not in (a.get("perms") or ()):
            a["perms"] = [k for k in grades.PERMS if k in {*(a.get("perms") or ()), PERM}]
            a["perms_by"] = "scripts/218"
        if p["action"] == "reset":
            pw = new_password()
            a["pw_hash"], a["salt"] = passwords.hash_pw(pw)
            a.pop("pw", None)
            a["must_change"] = True
            pws[uid] = pw
        a["updated_at"] = now
    return pws


def save_accounts(path: Path, accounts: dict) -> None:
    """앱과 같은 방식 — 임시 파일(0600)에 쓰고 바꿔 끼운다."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(accounts, ensure_ascii=False, indent=1))
    tmp.chmod(0o600)
    tmp.replace(path)


# ---------------------------------------------------------------- (b) 라벨 스튜디오 계정

def ls_email(uid: str, emails: dict[str, str]) -> str:
    return emails.get(uid) or f"{uid}@aura.local"


def plan_ls_users(existing: list[dict], people: list[tuple[str, str]], emails: dict[str, str]) -> list[dict]:
    have = {str(u.get("email") or "").lower() for u in existing}
    return [{"uid": uid, "name": name, "email": ls_email(uid, emails),
             "action": "keep" if ls_email(uid, emails).lower() in have else "create"} for uid, name in people]


def apply_ls_users(client, plan: list[dict], setup_user) -> dict[str, tuple[str, str]]:
    """만들고 비밀번호를 정한다 — {uid: (비밀번호 또는 빈 값, 상태 한 줄)}. 이미 있는 사용자는 비밀번호를 건드리지 않는다."""
    out: dict[str, tuple[str, str]] = {}
    for p in plan:
        if p["action"] == "keep":
            out[p["uid"]] = ("", "이미 있음(비밀번호 그대로)")
            continue
        client.create_user(p["email"], p["email"], p["name"])
        pw = new_password()
        ok, msg = setup_user(p["email"], pw)
        out[p["uid"]] = (pw, "만듦") if ok else ("", f"만듦 · 비밀번호 미설정({msg}) — 라벨 스튜디오 서버에서 다시")
    return out


# ---------------------------------------------------------------- (c) 검수 몫 나누기

def quotas(counts: dict[str, int], n: int) -> dict[str, int]:
    """갈래별 몫 — 갈래 수에 비례(큰 나머지 순으로 남는 수를 채움). 3,464:1,536 에서 500 → 346:154."""
    total = sum(counts.values())
    if total <= 0 or n <= 0:
        return {k: 0 for k in counts}
    n = min(n, total)
    raw = {k: n * c / total for k, c in counts.items()}
    q = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: (-(raw[k] - q[k]), k))[: n - sum(q.values())]:
        q[k] += 1
    return q


def plan_assign(tasks: list[dict], reviewers: list[str], n: int, seed: int = 20261027) -> dict:
    """표본 N건과 새로 적을 몫 — 갈래마다 할 일 id 를 정렬해 시드로 섞고 앞에서 몫만큼(다시 돌려도 같은 표본, N 을 늘리면 앞 표본을 품는다).
    이미 assignee 가 있는 할 일은 건너뛴다(표본 자리는 차지). 남은 것을 표본 차례대로 검수자에게 돌아가며 준다."""
    by_track: dict[str, list[dict]] = {}
    for t in tasks:
        by_track.setdefault(str((t.get("data") or {}).get("track") or "?"), []).append(t)
    q = quotas({k: len(v) for k, v in by_track.items()}, n)
    sample: list[dict] = []
    for track in sorted(by_track):
        rows = sorted(by_track[track], key=lambda t: int(t["id"]))
        random.Random(f"{seed}:{track}").shuffle(rows)
        sample += rows[: q[track]]
    rng_order = sorted(sample, key=lambda t: int(t["id"]))
    random.Random(seed).shuffle(rng_order)                  # 검수자마다 갈래가 섞이게 — 갈래 비율도 고르게
    todo, kept = [], 0
    for t in rng_order:
        if str((t.get("data") or {}).get("assignee") or "").strip():
            kept += 1
            continue
        todo.append(t)
    assign = [(t, reviewers[i % len(reviewers)]) for i, t in enumerate(todo)] if reviewers else []
    per: dict[str, int] = {r: 0 for r in reviewers}
    per_track: dict[str, dict[str, int]] = {r: {} for r in reviewers}
    for t, r in assign:
        per[r] += 1
        tr = str((t.get("data") or {}).get("track") or "?")
        per_track[r][tr] = per_track[r].get(tr, 0) + 1
    return {"quotas": q, "sample": len(sample), "already": kept, "assign": assign, "per": per, "per_track": per_track}


def apply_assign(client, assign: list[tuple[dict, str]]) -> int:
    n = 0
    for t, uid in assign:
        client.set_task_data(int(t["id"]), {**(t.get("data") or {}), "assignee": uid})
        n += 1
    return n


# ---------------------------------------------------------------- 실행

def write_secrets(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def ls_settings(db_path: Path) -> tuple[str, str]:
    from zzaimy.app.db import Database
    db = Database(db_path)
    return (db.get_setting("labelstudio_url") or "").rstrip("/"), db.get_setting("labelstudio_token") or ""


def project_id(client, title: str) -> int:
    for p in client._projects():
        if p.get("title") == title:
            return int(p["id"])
    raise SystemExit(f"라벨 스튜디오에 「{title}」 프로젝트가 없습니다")


def run(argv: list[str] | None = None, *, client=None, setup_user=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("people", nargs="+", help="아이디:이름 (여럿)")
    ap.add_argument("--out", required=True, help="비밀번호를 적을 파일(0600으로 만든다)")
    ap.add_argument("--data", default="data/platform", help="accounts.json·platform.db 가 있는 폴더")
    ap.add_argument("--dept", default="", help="플랫폼 계정의 소속 부서(비우면 없음)")
    ap.add_argument("--email", action="append", default=[], help="라벨 스튜디오 이메일 아이디=메일 (없으면 아이디@aura.local)")
    ap.add_argument("--project", default=DEFAULT_PROJECT, help="검수 프로젝트 제목")
    ap.add_argument("--assign", type=int, default=0, help="표본 N건을 갈래 비율대로 뽑아 몫을 나눈다")
    ap.add_argument("--seed", type=int, default=20261027)
    ap.add_argument("--reset-password", action="store_true", help="이미 있는 플랫폼 계정도 새 비밀번호로")
    ap.add_argument("--no-ls", action="store_true", help="라벨 스튜디오 계정·몫은 건드리지 않는다")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True, help="미리 보기(기본)")
    g.add_argument("--apply", action="store_true", help="실제로 한다")
    a = ap.parse_args(argv)
    apply = bool(a.apply)
    people = parse_people(a.people)
    emails = {}
    for e in a.email:
        uid, _, mail = e.partition("=")
        if "@" not in mail:
            raise SystemExit(f"--email 은 아이디=메일 꼴입니다: {e!r}")
        emails[uid.strip().lower()] = mail.strip()
    data = Path(a.data)
    acc_path = data / "accounts.json"
    accounts = json.loads(acc_path.read_text(encoding="utf-8")) if acc_path.is_file() else None
    if accounts is None:
        raise SystemExit(f"계정 표가 없습니다: {acc_path}")
    mode = "실행" if apply else "미리 보기(--apply 로 실행)"
    print(f"[{mode}] 검수자 {len(people)}명: " + ", ".join(f"{u}({n})" for u, n in people))

    acc_plan = plan_accounts(accounts, people, reset=a.reset_password)
    label = {"create": "새로 만듦", "grant": "검수 권한 더함", "reset": "권한 확인·비밀번호 새로", "keep": "그대로(이미 권한 있음)"}
    for p in acc_plan:
        print(f"  플랫폼 {p['uid']}: {label[p['action']]}")

    ls_plan, assign = [], None
    if not a.no_ls:
        if client is None:
            from zzaimy.dataset.ls_client import LabelStudioClient
            url, token = ls_settings(data / "platform.db")
            client = LabelStudioClient(url, token)
        ls_plan = plan_ls_users(client.users(), people, emails)
        for p in ls_plan:
            print(f"  라벨 스튜디오 {p['email']}: {'새로 만듦' if p['action'] == 'create' else '이미 있음'}")
        if a.assign:
            pid = project_id(client, a.project)
            tasks = list(client.iter_tasks(pid))
            assign = plan_assign(tasks, [u for u, _ in people], a.assign, a.seed)
            print(f"  몫 나누기 「{a.project}」(id {pid}, 할 일 {len(tasks):,}건): 표본 {assign['sample']}건 "
                  f"(갈래 {', '.join(f'{k} {v}' for k, v in sorted(assign['quotas'].items()))}), "
                  f"이미 맡은 것 {assign['already']}건 건너뜀, 새로 적을 것 {len(assign['assign'])}건")
            for r, k in assign["per"].items():
                tr = ", ".join(f"{t} {c}" for t, c in sorted(assign["per_track"][r].items()))
                print(f"    {r}: {k}건{f' ({tr})' if tr else ''}")

    if not apply:
        print("미리 보기만 했습니다 — 바꾼 것 없음.")
        return 0

    pws = apply_accounts(accounts, acc_plan, dept=a.dept)
    if any(p["action"] != "keep" for p in acc_plan):
        save_accounts(acc_path, accounts)
    ls_pws: dict[str, tuple[str, str]] = {}
    if ls_plan:
        if setup_user is None:
            from zzaimy.dataset import ls_admin
            setup_user = ls_admin.setup_user
        ls_pws = apply_ls_users(client, ls_plan, setup_user)
    done = apply_assign(client, assign["assign"]) if assign else 0

    lines = [f"# 검수자 계정 — {datetime.now():%Y-%m-%d %H:%M} scripts/218. 전달한 뒤 지운다.",
             "# 아이디\t이름\tAURA 첫 비밀번호(첫 로그인 때 바꿈)\t라벨 스튜디오 이메일\t라벨 스튜디오 비밀번호"]
    for uid, name in people:
        email = ls_email(uid, emails)
        ls_pw, ls_note = ls_pws.get(uid, ("", "건드리지 않음"))
        lines.append("\t".join([uid, name, pws.get(uid) or "(그대로)", email if ls_plan else "-", ls_pw or f"({ls_note})"]))
    write_secrets(Path(a.out).expanduser(), lines)
    print(f"플랫폼 계정 {sum(1 for p in acc_plan if p['action'] != 'keep')}건 저장 · 라벨 스튜디오 "
          f"{sum(1 for p in ls_plan if p['action'] == 'create')}건 만듦 · 몫 {done}건 적음")
    print(f"비밀번호는 {Path(a.out).expanduser()} (0600)에만 적었습니다.")
    if any(p["action"] != "keep" for p in acc_plan):
        print("앱이 계정 표를 메모리에 들고 있으니 지금 앱을 다시 띄웁니다(예: bash scripts/99_deploy.sh --restart 또는 서비스 재시작).")
    return 0


if __name__ == "__main__":
    sys.exit(run())
