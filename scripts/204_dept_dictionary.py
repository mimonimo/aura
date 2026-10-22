#!/usr/bin/env python3
"""부서 사전 만들기 — 직제·사무분장 규정과 학칙에서 조직 단위를 뽑아 data/platform/dept_dictionary.json 에 둔다(app/dept_dictionary).

운영 DB 는 읽기만 한다(읽기 전용 연결). 쓰는 것은 결과 json 하나(파생 결과물 — 입력 지문이 같으면 다시 만들지 않는다,
있던 파일은 history/ 에 사본)와, --create --apply 일 때의 부서 설정(rag_spaces.json — 바꾸기 전 history/ 에 사본,
save_config 가 .bak 도 남긴다). 애매한 이름은 --role review 를 주면 27B 가 조직인지·갈래를 본다(.env.local 을 읽는다).

--create: 사전의 정식 이름으로 부서를 만든다 — 사용자·계정 화면의 부서 만들기와 같은 함수(rag_spaces.create_dept_rag).
대상은 행정 조직(처·국·본부·실·단·원·관·센터·부·과·팀)과 사업 조직. 학과·계열·대학·위원회, 갈래를 모르는 기관, 애매한 이름
(같은 이름이 서로 다른 상위 아래), 다른 기관(법인) 조직, 검토 전인 이름(조 제목에서만 나옴)은 만들지 않는다. 이미 있는 부서
(이름이나 별칭이 같은 것)는 건너뛰고 계정의 부서 값은 바꾸지 않는다. 기본은 --dry-run(만들 목록만), --apply 로 실제.

사용(VM):
  PYTHONPATH=src .venv/bin/python scripts/204_dept_dictionary.py --role review              # 사전만
  PYTHONPATH=src .venv/bin/python scripts/204_dept_dictionary.py --role review --create     # 만들 부서 목록(바꾸지 않음)
  PYTHONPATH=src .venv/bin/python scripts/204_dept_dictionary.py --create --apply           # 부서 만들기
시험(결과를 다른 곳에): --out /tmp/out.json --rag-dir /tmp/rag (rag-dir 에 rag_spaces.json 이 없으면 운영 설정을 읽기만)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from zzaimy.app import dept_dictionary as dd  # noqa: E402
from zzaimy.app.database_backend import connect  # noqa: E402


class ReadOnlyDB:
    """운영 DB 를 읽기 전용으로 — Database() 는 열 때 표 만들기·칸 더하기를 돌리므로 쓰지 않는다."""

    def __init__(self, path: Path):
        self.path = str(path)

    def _conn(self):
        return connect(self.path, readonly=True)


def load_env_file(path: Path) -> None:
    if not path.is_file():
        print(f"경고: {path.name} 없음 — 27B 연결 설정을 못 읽으면 검토 없이 만든다", file=sys.stderr)
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            os.environ.setdefault(k, v)


def make_complete(role: str):
    """(complete, 모델 정보) — 운영 연결(VllmClient, 생각 끄기·외부 가림 관문 포함)로 JSON 답을 받는다."""
    from zzaimy.generate import model_info
    from zzaimy.generate.client import VllmClient
    c = VllmClient(role=role)

    def complete(messages: list[dict]) -> str:
        r = c.client.chat.completions.create(model=c.model, messages=messages, temperature=0.0, max_tokens=200,
                                             response_format={"type": "json_object"}, extra_body=c._extra)
        return r.choices[0].message.content or ""
    return complete, model_info.stamp(c)


def known_depts(rag_dir: Path, accounts_path: Path) -> list[str]:
    from zzaimy.app import rag_spaces
    try:
        accounts = json.loads(accounts_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        accounts = {}
    return rag_spaces.known_depts(rag_spaces.config(rag_dir), accounts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", default=os.environ.get("ZZAIMY_DATA_DIR", str(ROOT / "data" / "platform")))
    ap.add_argument("--db", default="", help="기본 data-dir/platform.db")
    ap.add_argument("--out", default="", help="기본 data-dir/dept_dictionary.json")
    ap.add_argument("--role", default="", help="27B 검토 연결 용도(예: review). 비우면 검토 없이(지난 판정만)")
    ap.add_argument("--force", action="store_true", help="지문이 같아도 다시 만든다")
    ap.add_argument("--create", action="store_true", help="사전으로 부서 만들기 목록(기본 dry-run)")
    ap.add_argument("--apply", action="store_true", help="--create 를 실제로")
    ap.add_argument("--rag-dir", default="", help="부서 설정(rag_spaces.json) 위치 — 기본 data-dir")
    ap.add_argument("--env-file", default=str(ROOT / ".env.local"))
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    db_path = Path(args.db) if args.db else data_dir / "platform.db"
    out = Path(args.out) if args.out else data_dir / dd.FILE
    complete, model = None, None
    if args.role:
        load_env_file(Path(args.env_file))
        os.environ.setdefault("ZZAIMY_DATA_DIR", str(data_dir))     # 모델 연결 설정(llm_connections)을 읽는 자리
        try:
            complete, model = make_complete(args.role)
        except Exception as e:                     # 연결이 없으면 검토 없이 — 애매한 것은 애매함으로 남는다
            print(f"27B 연결 실패({type(e).__name__}) — 검토 없이 만든다", file=sys.stderr)
    t0 = time.time()
    data = dd.build(ReadOnlyDB(db_path), data_dir, complete=complete, model=model, out=out, force=args.force)
    st = data["stats"]
    print(f"사전: {out} · 입력 {len(data['docs'])}건 · 단위 {st['units']} · 애매함 {st['ambiguous']} · 27B 검토 {st['reviewed']}"
          f"(조직 아님 {st['rejected']}) · 상위 있음 {st['with_parent']} · 별칭 있음 {st['with_alias']} · {time.time() - t0:.1f}초")
    print("갈래별: " + ", ".join(f"{dd.KINDS.get(k, k)} {n}" for k, n in sorted(st["by_kind"].items(), key=lambda x: -x[1])))
    for d in data["docs"]:
        print(f"  입력 {d['id']} {d['title']}")
    amb = [u for u in data["units"] if u["ambiguous"]]
    for u in amb:
        print(f"  애매함: {u['name']} — {u['ambiguous']}")

    if not args.create:
        return 0
    rag_dir = Path(args.rag_dir) if args.rag_dir else data_dir
    cfg_dir = rag_dir if (rag_dir / "rag_spaces.json").is_file() else data_dir
    existing = known_depts(cfg_dir, data_dir / "accounts.json")
    dic = dd.Dictionary(data, dd.parents_override(data_dir))
    plan = dd.plan_create(dic, existing)
    print(f"\n부서 만들기 — 있는 부서 {len(existing)} · 만들 것 {len(plan['create'])} · 건너뜀 {len(plan['skip'])}"
          + ("" if args.apply else " (dry-run — 바꾸지 않음, --apply 로 실제)"))
    for n in plan["create"]:
        print(f"  만들 것: {dic.line(n)}")
    for s in plan["skip"]:
        print(f"  건너뜀: {s['name']} — {s['why']}")
    if not args.apply or not plan["create"]:
        return 0
    from zzaimy.app import rag_spaces
    cfg = rag_spaces.config(cfg_dir)
    src = cfg_dir / "rag_spaces.json"
    if src.is_file():
        hist = rag_dir / "history"
        hist.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, hist / f"rag_spaces.{time.strftime('%Y%m%d%H%M%S')}.json")
    made = []
    for n in plan["create"]:
        cfg, err = rag_spaces.create_dept_rag(cfg, n, by="dept_dictionary")
        if err:
            print(f"  못 만듦: {n} — {err}")
        else:
            made.append(n)
    rag_spaces.save_config(cfg, rag_dir)
    print(f"만듦 {len(made)}: {', '.join(made)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
