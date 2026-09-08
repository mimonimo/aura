"""한글 실시간 편집 에이전트 (Windows COM 계층).

실행 중인 한글(한컴오피스) 창을 COM으로 조작해, 서버가 보낸 편집 명령을
화면에 실시간 반영한다. 서버측 hwpx-plugin(한컴 없이 HWPX 직접 편집)과
같은 명령 계약(protocol.md)을 해석해 공존한다.

이 계층은 "지시 → 눈앞의 한글이 바뀜"이 필요할 때만 쓴다. 화면 동기화가
필요 없으면 서버측 경로가 기본이다.

사용:
  python hwp_agent.py --server https://<서버> --token <세션토큰>
  python hwp_agent.py --selftest        # 한글 없이 디스패처·계약 검증
  python hwp_agent.py --confirm ...      # 편집성 명령은 콘솔 확인 후 실행

COM 백엔드는 Windows + 정품 한글이 있어야 동작한다. 그 외 환경에서는
--selftest(목 백엔드)로 명령 라우팅만 검증한다. COM API 호출은 한컴 자동화
문서 기준으로 작성했으며, 실장비에서 1회 확인이 필요하다(주석 참조).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

# 편집 명령 화이트리스트 — 이 밖의 op는 거부한다.
ALLOWED_OPS = {
    "ping", "open", "new_doc", "find", "goto", "set_title",
    "insert_text", "replace", "insert_table", "get_text", "save", "save_as",
    "list_docs", "select_doc",
}
EDITING_OPS = {
    "insert_text", "replace", "insert_table", "set_title", "save", "save_as",
}
# 편집 전에 대상 문서가 확정돼야 하는 op — 엉뚱한 창을 건드리지 않기 위함
TARGETED_OPS = EDITING_OPS | {"find", "get_text"}


class HwpBackend:
    """편집 백엔드 인터페이스. COM/목이 이를 구현한다."""

    def ping(self) -> dict: raise NotImplementedError
    def new_doc(self) -> dict: raise NotImplementedError
    def open(self, path: str, format: str = "hwpx") -> dict: raise NotImplementedError
    def list_docs(self) -> dict: raise NotImplementedError
    def select_doc(self, index=None, path=None, id=None) -> dict: raise NotImplementedError
    def goto(self, where: str = "start") -> dict: raise NotImplementedError
    def set_title(self, text: str) -> dict: raise NotImplementedError
    def find(self, text: str, nth: int = 1) -> dict: raise NotImplementedError
    def insert_text(self, text: str) -> dict: raise NotImplementedError
    def replace(self, find: str, replace: str, all: bool = True) -> dict: raise NotImplementedError
    def insert_table(self, rows: int, cols: int) -> dict: raise NotImplementedError
    def get_text(self, scope: str = "all") -> dict: raise NotImplementedError
    def save(self) -> dict: raise NotImplementedError
    def save_as(self, path: str, format: str = "hwpx") -> dict: raise NotImplementedError


class MockBackend(HwpBackend):
    """한글 없이 명령 라우팅·계약을 검증하기 위한 인메모리 문서.

    caret 위치의 문자열 편집만 흉내낸다. 실제 서식·표는 모사하지 않는다.
    """

    def __init__(self) -> None:
        # 여러 문서를 흉내낸다 — {id: {text, path}}. 안전 로직 검증용.
        self.docs: dict[int, dict] = {0: {"text": "", "path": ""}}
        self._active = 0
        self._next_id = 1
        self.caret = 0
        self._bound: int | None = None

    @property
    def text(self) -> str:
        return self.docs[self._active]["text"]

    @text.setter
    def text(self, v: str) -> None:
        self.docs[self._active]["text"] = v

    def _ensure_target(self) -> None:
        if self._bound is not None:
            if self._bound not in self.docs:
                raise RuntimeError("작업 대상 문서가 닫혔습니다. 다시 띄워 주세요.")
            self._active = self._bound
            return
        if len(self.docs) > 1:
            raise RuntimeError("문서가 여러 개 — 대상이 확정되지 않았습니다.")
        self._bound = self._active

    def ping(self) -> dict:
        return {"backend": "mock", "version": "mock-1"}

    def new_doc(self) -> dict:
        did = self._next_id
        self._next_id += 1
        self.docs[did] = {"text": "", "path": ""}
        self._active = did
        self._bound = did
        self.caret = 0
        return {"created": True, "bound": did}

    def list_docs(self) -> dict:
        docs = [
            {"id": did, "path": d["path"],
             "name": (d["path"].rsplit("/", 1)[-1] or "빈 문서"),
             "active": did == self._active, "bound": did == self._bound}
            for did, d in self.docs.items()
        ]
        return {"count": len(docs), "docs": docs, "bound": self._bound}

    def select_doc(self, index=None, path=None, id=None) -> dict:
        did = int(id) if id is not None else index
        if did not in self.docs:
            raise RuntimeError("해당 문서를 찾을 수 없습니다")
        self._active = did
        self._bound = did
        return {"selected": {"id": did}}

    def goto(self, where: str = "start") -> dict:
        self._ensure_target()
        self.caret = 0 if where == "start" else len(self.text)
        return {"moved": where}

    def set_title(self, text: str) -> dict:
        self._ensure_target()
        self.text = text + "\n" + self.text
        return {"title": text}

    def open(self, path: str, format: str = "hwpx") -> dict:
        did = self._next_id
        self._next_id += 1
        self.docs[did] = {"text": "", "path": path}
        self._active = did
        self._bound = did
        self.caret = 0
        return {"opened": path, "bound": did}

    def find(self, text: str, nth: int = 1) -> dict:
        self._ensure_target()
        idx, start, count = -1, 0, 0
        while count < nth:
            idx = self.text.find(text, start)
            if idx < 0:
                return {"found": False}
            count += 1
            start = idx + 1
        self.caret = idx + len(text)   # 일치 끝으로 캐럿(선택 흉내)
        return {"found": True, "at": idx}

    def insert_text(self, text: str) -> dict:
        self._ensure_target()
        self.text = self.text[:self.caret] + text + self.text[self.caret:]
        self.caret += len(text)
        return {"inserted": len(text)}

    def replace(self, find: str, replace: str, all: bool = True) -> dict:
        self._ensure_target()
        n = self.text.count(find) if all else (1 if find in self.text else 0)
        self.text = self.text.replace(find, replace, -1 if all else 1)
        return {"replaced": n}

    def insert_table(self, rows: int, cols: int) -> dict:
        self._ensure_target()
        marker = f"[표 {rows}x{cols}]"
        self.text = self.text[:self.caret] + marker + self.text[self.caret:]
        self.caret += len(marker)
        return {"table": [rows, cols]}

    def get_text(self, scope: str = "all") -> dict:
        self._ensure_target()
        return {"scope": scope, "text": self.text}

    def save(self) -> dict:
        self._ensure_target()
        return {"saved": self.docs[self._active]["path"]}

    def save_as(self, path: str, format: str = "hwpx") -> dict:
        self._ensure_target()
        self.docs[self._active]["path"] = path
        return {"saved_as": path, "format": format}


class ComBackend(HwpBackend):
    """실행 중인 한글을 COM으로 조작한다 (Windows 전용).

    한컴 자동화(HWPFrame.HwpObject) 기준. 메서드명은 한컴 자동화 문서 기준이며
    실장비에서 1회 확인 권장(특히 HParameterSet 필드명).
    """

    _FMT = {"hwpx": "HWPX", "hwp": "HWP", "pdf": "PDF"}

    def __init__(self, visible: bool = True) -> None:
        # pywin32가 있으면 우선, 없으면 comtypes(순수 파이썬 — 내장 배포판용).
        # 플랫폼에서 내려받는 번들은 설치 없는 comtypes 경로로 동작한다.
        try:
            import win32com.client  # pywin32 — 설치형 환경

            self.hwp = win32com.client.Dispatch("HWPFrame.HwpObject")
        except ImportError:
            import comtypes.client  # 순수 파이썬 COM

            self.hwp = comtypes.client.CreateObject("HWPFrame.HwpObject")
        # 파일 접근 보안 대화상자 억제(자동화 표준 관용구). 없으면 열기 시 팝업.
        try:
            self.hwp.RegisterModule("FilePathCheckDLL", "SecurityModule")
        except Exception:
            pass
        try:
            self.hwp.XHwpWindows.Item(0).Visible = visible
        except Exception:
            pass
        # 편집 대상으로 확정된 문서 ID. 프로젝트가 초안을 띄우면(new_doc/open)
        # 그 문서에 바인딩되고, 이후 편집은 그 문서로만 간다 — 사용자가 열어둔
        # 다른 한글 창은 절대 건드리지 않는다. 미확정 상태에서 문서가 여럿이면
        # 편집을 거부한다(fail-closed).
        self._bound: int | None = None

    # ── 여러 문서 안전 ──────────────────────────────────────────
    def _doc_id(self, d) -> int:
        return int(getattr(d, "DocumentID", -1))

    def _docs(self) -> list:
        """열린 문서 목록 (id·경로·이름·활성/바인딩 여부)."""
        col = self.hwp.XHwpDocuments
        active_id = self._active_id()
        out = []
        for i in range(int(col.Count)):
            d = col.Item(i)
            path = getattr(d, "FullName", "") or getattr(d, "Path", "") or ""
            did = self._doc_id(d)
            out.append({
                "index": i,
                "id": did,
                "path": path,
                "name": path.replace("\\", "/").rsplit("/", 1)[-1] or "빈 문서",
                "active": did == active_id,
                "bound": did == self._bound,
            })
        return out

    def _active_id(self) -> int:
        try:
            return self._doc_id(self.hwp.XHwpDocuments.Active_XHwpDocument)
        except Exception:
            return -1

    def _activate(self, doc_id: int) -> bool:
        col = self.hwp.XHwpDocuments
        for i in range(int(col.Count)):
            if self._doc_id(col.Item(i)) == doc_id:
                col.Item(i).SetActive_XHwpDocument()
                return True
        return False

    def _ensure_target(self) -> None:
        """편집 전 바인딩된 문서를 활성화한다. 미확정+여러 개면 거부."""
        if self._bound is not None:
            if not self._activate(self._bound):
                raise RuntimeError("작업 대상 문서가 닫혔습니다. 다시 띄워 주세요.")
            return
        if int(self.hwp.XHwpDocuments.Count) > 1:
            raise RuntimeError(
                "문서가 여러 개 열려 있어 대상이 확정되지 않았습니다. 프로젝트에서"
                " 초안을 띄우거나 대상 문서를 선택하세요(select_doc)."
            )
        # 문서 하나뿐 — 그 문서를 대상으로 바인딩
        self._bound = self._active_id()

    def new_doc(self) -> dict:
        """빈 문서를 새로 만들어 대상으로 바인딩한다 (초안 띄우기용)."""
        try:
            self.hwp.XHwpDocuments.Add(0)  # 0=새 창
        except Exception:
            self.hwp.Run("FileNew")
        self._bound = self._active_id()
        return {"created": True, "bound": self._bound}

    def list_docs(self) -> dict:
        docs = self._docs()
        return {"count": len(docs), "docs": docs, "bound": self._bound}

    def select_doc(self, index: int | None = None,
                   path: str | None = None, id: int | None = None) -> dict:
        docs = self._docs()
        target = None
        if id is not None:
            target = next((d for d in docs if d["id"] == int(id)), None)
        elif path:
            target = next((d for d in docs if d["path"] == path
                           or d["name"] == path), None)
        elif index is not None:
            target = next((d for d in docs if d["index"] == int(index)), None)
        if target is None:
            raise RuntimeError("해당 문서를 찾을 수 없습니다")
        self._activate(target["id"])
        self._bound = target["id"]
        return {"selected": target}

    # 내부 헬퍼 — HAction 파라미터셋 실행
    def _action(self, op: str, fields: dict):
        act = self.hwp.HAction
        pset = getattr(self.hwp.HParameterSet, self._PSET[op])
        act.GetDefault(op, pset.HSet)
        for k, v in fields.items():
            setattr(pset, k, v)
        return act.Execute(op, pset.HSet)

    _PSET = {
        "InsertText": "HInsertText",
        "AllReplace": "HFindReplace",
        "RepeatFind": "HFindReplace",
        "TableCreate": "HTableCreation",
    }

    def ping(self) -> dict:
        return {"backend": "com", "version": str(getattr(self.hwp, "Version", "?"))}

    def open(self, path: str, format: str = "hwpx") -> dict:
        """특정 파일을 열어 대상으로 바인딩한다. 이미 열려 있으면 그 문서로."""
        ok = self.hwp.Open(path, self._FMT.get(format, ""), "")
        self._bound = self._active_id()
        return {"opened": bool(ok), "path": path, "bound": self._bound}

    def goto(self, where: str = "start") -> dict:
        """캐럿 이동 — start(문서 처음)·end(끝). 제목 삽입 등 위치 지정용."""
        self._ensure_target()
        self.hwp.Run("MoveDocBegin" if where == "start" else "MoveDocEnd")
        return {"moved": where}

    def set_title(self, text: str) -> dict:
        """문서 맨 앞에 제목 문단을 넣는다 (처음으로 이동 → 삽입)."""
        self._ensure_target()
        self.hwp.Run("MoveDocBegin")
        self._action("InsertText", {"Text": text + "\r\n"})
        return {"title": text}

    def find(self, text: str, nth: int = 1) -> dict:
        self._ensure_target()
        found = False
        for _ in range(max(1, nth)):
            found = bool(self._action("RepeatFind", {"FindString": text, "IgnoreMessage": 1}))
            if not found:
                break
        return {"found": found}

    def insert_text(self, text: str) -> dict:
        self._ensure_target()
        self._action("InsertText", {"Text": text})
        return {"inserted": len(text)}

    def replace(self, find: str, replace: str, all: bool = True) -> dict:
        self._ensure_target()
        self._action("AllReplace", {
            "FindString": find, "ReplaceString": replace,
            "ReplaceMode": 1, "IgnoreMessage": 1,
        })
        return {"replaced": "all" if all else 1}

    def insert_table(self, rows: int, cols: int) -> dict:
        self._ensure_target()
        self._action("TableCreate", {"Rows": rows, "Cols": cols})
        return {"table": [rows, cols]}

    def get_text(self, scope: str = "all") -> dict:
        self._ensure_target()
        if scope == "selection":
            try:
                return {"scope": scope, "text": self.hwp.GetSelectedText()}
            except Exception:
                pass
        return {"scope": "all", "text": self.hwp.GetTextFile("TEXT", "")}

    def save(self) -> dict:
        self._ensure_target()
        return {"saved": bool(self.hwp.Save())}

    def save_as(self, path: str, format: str = "hwpx") -> dict:
        ok = self.hwp.SaveAs(path, self._FMT.get(format, "HWPX"), "")
        return {"saved_as": path, "ok": bool(ok)}


def dispatch(backend: HwpBackend, command: dict, confirm: bool = False) -> dict:
    """명령 봉투 하나를 백엔드로 라우팅한다. 계약(protocol.md) 준수."""
    cid = command.get("id")
    op = command.get("op")
    args = command.get("args") or {}
    if op not in ALLOWED_OPS:
        return {"id": cid, "ok": False, "error": f"허용되지 않은 op: {op}"}
    if confirm and op in EDITING_OPS:
        sys.stderr.write(f"[확인] {op} {json.dumps(args, ensure_ascii=False)} 실행? [y/N] ")
        if input().strip().lower() != "y":
            return {"id": cid, "ok": False, "error": "사용자가 거부함"}
    try:
        method = getattr(backend, op)
        result = method(**args)
        return {"id": cid, "ok": True, "result": result}
    except TypeError as e:
        return {"id": cid, "ok": False, "error": f"인자 오류: {e}"}
    except Exception as e:
        return {"id": cid, "ok": False, "error": f"{type(e).__name__}: {e}"}


# 운영 서버는 자체 서명 인증서다 — TLS 검증은 항상 켠 채로, 서버 인증서
# 파일을 --ca-cert 로 신뢰시킨다 (검증 생략 옵션은 두지 않는다).
_SSL_CONTEXT: "object | None" = None


def set_tls(ca_cert: str | None) -> None:
    global _SSL_CONTEXT
    import ssl

    if ca_cert:
        _SSL_CONTEXT = ssl.create_default_context(cafile=ca_cert)


def _post(url: str, payload: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as r:
        return json.loads(r.read() or b"{}")


def _get(url: str, timeout: int = 60) -> dict:
    with urllib.request.urlopen(url, timeout=timeout, context=_SSL_CONTEXT) as r:
        return json.loads(r.read() or b"{}")


def _register(base: str, token: str) -> tuple[str, int]:
    """등록. 토큰이 틀리면 사람이 알 수 있는 메시지로 종료한다."""
    import urllib.error

    try:
        reg = _post(f"{base}/hwp/agent/register", {"token": token})
    except urllib.error.HTTPError as e:
        if e.code == 403:
            sys.stderr.write(
                "[등록 거부] 토큰이 맞지 않습니다 — 토큰이 재발급된 경우입니다.\n"
                "플랫폼에서 에이전트 zip을 다시 내려받아 실행하세요.\n"
            )
            raise SystemExit(3)
        raise
    session = reg.get("session", token)
    sys.stderr.write(f"[에이전트] 서버 연결됨 session={session}\n")
    return session, int(reg.get("poll_after", 0))


def run_loop(server: str, token: str, backend: HwpBackend, confirm: bool = False) -> None:
    """서버에 아웃바운드로 붙어 명령을 롱폴·실행·회신한다.

    서버가 재시작되면 세션이 사라진다(403) — 자동으로 재등록해 이어간다.
    """
    import urllib.error

    base = server.rstrip("/")
    session, cursor = _register(base, token)
    while True:
        try:
            resp = _get(f"{base}/hwp/agent/commands?session={session}&after={cursor}")
        except urllib.error.HTTPError as e:
            if e.code == 403:
                sys.stderr.write("[세션 만료] 서버 재시작 감지 — 재등록\n")
                time.sleep(2)
                session, cursor = _register(base, token)
                continue
            sys.stderr.write(f"[폴링 실패] {e} — 5초 후 재시도\n")
            time.sleep(5)
            continue
        except Exception as e:
            sys.stderr.write(f"[폴링 실패] {e} — 5초 후 재시도\n")
            time.sleep(5)
            continue
        cursor = resp.get("cursor", cursor)
        for command in resp.get("commands", []):
            result = dispatch(backend, command, confirm=confirm)
            try:
                _post(f"{base}/hwp/agent/result", {"session": session, **result})
            except Exception as e:
                sys.stderr.write(f"[회신 실패] {e}\n")


def _selftest() -> int:
    """한글 없이 디스패처·계약을 검증한다."""
    b = MockBackend()
    script = [
        {"id": "1", "op": "open", "args": {"path": "draft.hwpx"}},
        {"id": "2", "op": "insert_text", "args": {"text": "사업 개요\n예산 총액 5,000,000원"}},
        {"id": "3", "op": "find", "args": {"text": "5,000,000"}},
        {"id": "4", "op": "replace",
         "args": {"find": "5,000,000", "replace": "6,000,000", "all": True}},
        {"id": "5", "op": "insert_table", "args": {"rows": 3, "cols": 2}},
        {"id": "6", "op": "get_text", "args": {"scope": "all"}},
        {"id": "7", "op": "save", "args": {}},
        {"id": "8", "op": "danger", "args": {}},  # 화이트리스트 밖 → 거부돼야
    ]
    results = [dispatch(b, c) for c in script]
    ok_flags = [r["ok"] for r in results]
    text = b.get_text()["text"]

    # 여러 문서 안전 — 대상 미확정 상태에서 편집 거부, new_doc/select_doc 후 허용
    b2 = MockBackend()
    b2.docs[99] = {"text": "다른 사용자 문서", "path": "/other.hwp"}  # 두 번째 문서
    guard_multi = dispatch(b2, {"id": "g1", "op": "insert_text",
                                "args": {"text": "x"}})
    made = dispatch(b2, {"id": "g2", "op": "new_doc", "args": {}})
    after_bind = dispatch(b2, {"id": "g3", "op": "insert_text",
                               "args": {"text": "초안 본문"}})
    other_untouched = b2.docs[99]["text"] == "다른 사용자 문서"

    checks = [
        ("전 명령 처리", len(results) == 8),
        ("허용 op 성공", all(ok_flags[:7])),
        ("화이트리스트 밖 거부", results[7]["ok"] is False),
        ("치환 반영", "6,000,000" in text and "5,000,000" not in text),
        ("표 삽입", "[표 3x2]" in text),
        ("대상 미확정 시 편집 거부", guard_multi["ok"] is False),
        ("새 문서 생성·바인딩 후 편집 허용", made["ok"] and after_bind["ok"]),
        ("다른 문서 안 건드림", other_untouched),
    ]
    all_ok = True
    all_ok = True
    for name, passed in checks:
        sys.stderr.write(f"  [{'OK' if passed else '실패'}] {name}\n")
        all_ok = all_ok and passed
    sys.stderr.write("selftest " + ("통과\n" if all_ok else "실패\n"))
    return 0 if all_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="한글 실시간 편집 에이전트 (COM)")
    ap.add_argument("--server", help="서버 base URL")
    ap.add_argument("--token", help="세션 토큰(사용자 인증에 묶임)")
    ap.add_argument("--confirm", action="store_true", help="편집성 명령을 콘솔 확인 후 실행")
    ap.add_argument("--selftest", action="store_true", help="한글 없이 디스패처 검증")
    ap.add_argument("--no-visible", action="store_true", help="한글 창 숨김")
    ap.add_argument("--ca-cert", help="서버 인증서 파일(자체 서명 신뢰용)")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    # 플랫폼 배포 번들은 config.json에 접속 정보를 심어 보낸다 — 인자 불필요
    if not (args.server and args.token):
        import os

        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
            args.server = args.server or cfg.get("server")
            args.token = args.token or cfg.get("token")
            ca = cfg.get("ca_cert")
            if ca and not args.ca_cert:
                args.ca_cert = os.path.join(os.path.dirname(cfg_path), ca)

    set_tls(args.ca_cert)
    if not (args.server and args.token):
        ap.error("--server 와 --token 이 필요합니다 (또는 config.json / --selftest)")
    try:
        backend: HwpBackend = ComBackend(visible=not args.no_visible)
    except Exception as e:
        sys.stderr.write(f"한글 COM 백엔드를 열 수 없습니다: {e}\n"
                         "Windows + 정품 한글 + pywin32 환경에서 실행하세요.\n")
        return 2
    run_loop(args.server, args.token, backend, confirm=args.confirm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
