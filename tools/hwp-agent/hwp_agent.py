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
import base64
import json
import sys
import time
import urllib.request

__version__ = "2026.09.09"

# 편집 명령 화이트리스트 — 이 밖의 op는 거부한다.
ALLOWED_OPS = {
    "ping", "open", "new_doc", "find", "goto", "set_title",
    "insert_text", "replace", "insert_table", "fill_table", "set_format", "set_page",
    "delete_text", "delete_table",
    "get_text", "save", "save_as", "export_artifact", "list_docs", "select_doc",
    "open_bytes",
}
EDITING_OPS = {
    "insert_text", "replace", "insert_table", "fill_table", "set_format", "set_page",
    "delete_text", "delete_table", "set_title", "save", "save_as",
}
# 편집 전에 대상 문서가 확정돼야 하는 op — 엉뚱한 창을 건드리지 않기 위함
TARGETED_OPS = EDITING_OPS | {"find", "get_text"}


def _hwp_color(c) -> int:
    """#RRGGBB -> 한글 색상 정수(0x00BBGGRR). 실패 시 검정(0)."""
    try:
        c = str(c).lstrip("#")
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        return r | (g << 8) | (b << 16)
    except Exception:
        return 0


class HwpBackend:
    """편집 백엔드 인터페이스. COM/목이 이를 구현한다."""

    def ping(self) -> dict: raise NotImplementedError
    def new_doc(self) -> dict: raise NotImplementedError
    def open(self, path: str, format: str = "hwpx") -> dict: raise NotImplementedError
    def open_bytes(self, name: str = "", b64: str = "", format: str = "hwpx") -> dict: raise NotImplementedError
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
    def export_artifact(self, format: str = "pdf") -> dict: raise NotImplementedError


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

    def open_bytes(self, name: str = "upload.hwpx", b64: str = "", format: str = "hwpx") -> dict:
        did = self._next_id
        self._next_id += 1
        self.docs[did] = {"text": "", "path": name}
        self._active = did
        self._bound = did
        self.caret = 0
        return {"opened": name, "bound": did}

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

    def delete_text(self, text: str) -> dict:
        self._ensure_target()
        n = self.text.count(text)
        self.text = self.text.replace(text, "")
        return {"deleted": text, "n": n}

    def delete_table(self) -> dict:
        self._ensure_target()
        import re as _re

        self.text = _re.sub(r"\[표 \d+x\d+\]", "", self.text, count=1)
        return {"deleted": "table"}

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

    def fill_table(self, cells: list) -> dict:
        self._ensure_target()
        flat = [str(v) for row in cells for v in row if str(v)]
        self.text += "\n[표내용: " + " | ".join(flat) + "]"
        return {"filled": len(flat)}

    def set_format(self, find=None, bold=None, italic=None, underline=None,
                   size=None, font=None, color=None, align=None,
                   line_spacing=None) -> dict:
        self._ensure_target()
        keys = dict(bold=bold, italic=italic, underline=underline, size=size,
                    font=font, color=color, align=align, line_spacing=line_spacing)
        return {"applied": {k: v for k, v in keys.items() if v is not None},
                "target": find}

    def set_page(self, top=None, bottom=None, left=None, right=None,
                 orientation=None, paper=None) -> dict:
        self._ensure_target()
        keys = dict(top=top, bottom=bottom, left=left, right=right,
                    orientation=orientation, paper=paper)
        return {"applied": {k: v for k, v in keys.items() if v is not None}}

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

    def export_artifact(self, format: str = "pdf") -> dict:
        """산출물(미리보기용 PDF 등)을 임시 파일로 저장해 서버가 회수하게 한다."""
        self._ensure_target()
        import os
        import tempfile

        ext = {"pdf": "pdf", "hwpx": "hwpx", "hwp": "hwp"}.get(format, "pdf")
        path = os.path.join(tempfile.gettempdir(),
                            f"zzaimy_export_{int(time.time() * 1000)}.{ext}")
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.text)  # 목: 실제 렌더 대신 텍스트만
        return {"artifact_path": path, "format": format, "ok": True,
                "name": os.path.basename(path)}


class ComBackend(HwpBackend):
    """실행 중인 한글을 COM으로 조작한다 (Windows 전용).

    한컴 자동화(HWPFrame.HwpObject) 기준. 메서드명은 한컴 자동화 문서 기준이며
    실장비에서 1회 확인 권장(특히 HParameterSet 필드명).
    """

    _FMT = {"hwpx": "HWPX", "hwp": "HWP", "pdf": "PDF"}

    def __init__(self, visible: bool = True) -> None:
        # pywin32가 있으면 우선, 없으면 comtypes(순수 파이썬 — 내장 배포판용).
        # 플랫폼에서 내려받는 번들은 설치 없는 comtypes 경로로 동작한다.
        # 사용자가 이미 띄워 둔 한글에 먼저 붙는다(GetActiveObject) — 그래야
        # 작업이 눈앞의 한글에서 일어나고, 열어둔 다른 문서창과 공존한다.
        # 떠 있는 한글이 없으면 그때만 새 인스턴스를 띄운다.
        try:
            import win32com.client  # pywin32 — 설치형 환경

            try:
                self.hwp = win32com.client.GetActiveObject("HWPFrame.HwpObject")
                self.attached = True
            except Exception:
                self.hwp = win32com.client.Dispatch("HWPFrame.HwpObject")
                self.attached = False
        except ImportError:
            import comtypes.client  # 순수 파이썬 COM

            try:
                self.hwp = comtypes.client.GetActiveObject("HWPFrame.HwpObject")
                self.attached = True
            except Exception:
                self.hwp = comtypes.client.CreateObject("HWPFrame.HwpObject")
                self.attached = False
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
        return {"backend": "com", "agent": __version__,
                "hwp_version": str(getattr(self.hwp, "Version", "?")),
                "attached": bool(getattr(self, "attached", False)),
                "open_docs": int(self.hwp.XHwpDocuments.Count),
                "bound": self._bound}

    def open(self, path: str, format: str = "hwpx") -> dict:
        """특정 파일을 열어 대상으로 바인딩한다. 이미 열려 있으면 그 문서로."""
        ok = self.hwp.Open(path, self._FMT.get(format, ""), "")
        self._bound = self._active_id()
        return {"opened": bool(ok), "path": path, "bound": self._bound}

    def open_bytes(self, name: str = "upload.hwpx", b64: str = "", format: str = "hwpx") -> dict:
        """업로드된 파일을 PC 임시폴더에 저장 후 열어 대상으로 바인딩한다."""
        import base64 as _b64, tempfile as _tf, os as _os
        data = _b64.b64decode(b64 or "")
        bad = set('\\/:*?"<>|')
        safe = "".join(c for c in (name or "upload") if c not in bad) or "upload.hwpx"
        tmp = _os.path.join(_tf.gettempdir(), safe)
        with open(tmp, "wb") as fh:
            fh.write(data)
        ok = self.hwp.Open(tmp, self._FMT.get(format, ""), "")
        self._bound = self._active_id()
        return {"opened": bool(ok), "name": name, "path": tmp, "bound": self._bound}

    def goto(self, where: str = "start") -> dict:
        """캐럿 이동 — start(문서 처음)·end(끝). 제목 삽입 등 위치 지정용."""
        self._ensure_target()
        self.hwp.Run("MoveDocBegin" if where == "start" else "MoveDocEnd")
        return {"moved": where}

    def set_title(self, text: str) -> dict:
        """문서 맨 앞에 제목 문단을 넣는다 (처음으로 이동 → 삽입)."""
        self._ensure_target()
        self.hwp.Run("MoveDocBegin")
        self._action("InsertText", {"Text": text})
        self.hwp.Run("BreakPara")
        return {"title": text}

    def find(self, text: str, nth: int = 1) -> dict:
        self._ensure_target()
        found = False
        for _ in range(max(1, nth)):
            found = bool(self._action("RepeatFind", {"FindString": text, "IgnoreMessage": 1}))
            if not found:
                break
        return {"found": found}

    def delete_text(self, text: str) -> dict:
        """특정 문구를 찾아 지운다 (빈 문자열로 치환) — 안전한 삭제."""
        self._ensure_target()
        self._action("AllReplace", {
            "FindString": text, "ReplaceString": "",
            "ReplaceMode": 1, "IgnoreMessage": 1,
        })
        return {"deleted": text}

    def delete_table(self) -> dict:
        """캐럿이 놓인 표를 통째로 지운다. 표 안에 캐럿이 있어야 한다."""
        self._ensure_target()
        # 표 안으로 진입 후 표 삭제 (한컴 자동화 표준 액션)
        self.hwp.Run("TableDeleteTable")
        return {"deleted": "table"}

    def insert_text(self, text: str) -> dict:
        """텍스트 삽입. \n 은 실제 문단 나눔(BreakPara)으로 처리한다 —
        통짜 InsertText 는 한글에서 줄글로 붙어 문단이 나뉘지 않는다."""
        self._ensure_target()
        parts = text.split("\n")
        for i, part in enumerate(parts):
            if part:
                self._action("InsertText", {"Text": part})
            if i < len(parts) - 1:
                self.hwp.Run("BreakPara")
        return {"inserted": len(text), "paras": text.count("\n") + 1}

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

    def fill_table(self, cells: list) -> dict:
        """방금 만든(또는 캐럿이 놓인) 표를 행 우선으로 채운다.

        cells: 2차원 배열 [[행1칸들], [행2칸들]...]. 첫 셀로 이동 후
        오른쪽/다음 행으로 이동하며 입력한다.
        """
        self._ensure_target()
        self.hwp.Run("TableColBegin")   # 표 첫 셀로
        self.hwp.Run("TableColPageUp")
        n = 0
        for r, row in enumerate(cells):
            for c, val in enumerate(row):
                if str(val):
                    self._action("InsertText", {"Text": str(val)})
                    n += 1
                if c < len(row) - 1:
                    self.hwp.Run("TableRightCell")
            if r < len(cells) - 1:
                # 다음 행 첫 칸으로: 현재 행 끝에서 오른쪽이면 자동으로 다음 행 첫 칸
                self.hwp.Run("TableRightCell")
        return {"filled": n}

    def set_format(self, find=None, bold=None, italic=None, underline=None,
                   size=None, font=None, color=None, align=None,
                   line_spacing=None) -> dict:
        """글자·문단 서식. find가 있으면 그 문구를 찾아 선택 후 적용, 없으면
        현재 선택/캐럿에 적용. 글자: bold/italic/underline, size(pt), font(글꼴명),
        color(#RRGGBB). 문단: align(left/center/right/justify), line_spacing(%).
        실장비 확인: CharShape/ParaShape 필드명은 한컴 자동화 문서 기준.
        """
        self._ensure_target()
        applied = {}
        if find:
            self._action("RepeatFind", {"FindString": find, "IgnoreMessage": 1})
        h = self.hwp
        if any(v is not None for v in (bold, italic, underline, size, font, color)):
            pset = h.HParameterSet.HCharShape
            h.HAction.GetDefault("CharShape", pset.HSet)
            if size is not None:
                pset.Height = int(float(size) * 100); applied["size"] = size
            if bold is not None:
                pset.Bold = 1 if bold else 0; applied["bold"] = bool(bold)
            if italic is not None:
                pset.Italic = 1 if italic else 0; applied["italic"] = bool(italic)
            if underline is not None:
                pset.UnderlineType = 1 if underline else 0
                applied["underline"] = bool(underline)
            if font:
                for a in ("FaceNameHangul", "FaceNameLatin", "FaceNameHanja",
                          "FaceNameJapanese", "FaceNameOther", "FaceNameSymbol",
                          "FaceNameUser"):
                    try:
                        setattr(pset, a, font)
                    except Exception:
                        pass
                applied["font"] = font
            if color:
                pset.TextColor = _hwp_color(color); applied["color"] = color
            h.HAction.Execute("CharShape", pset.HSet)
        if align is not None:
            # 정렬은 파라미터셋(ParaShape.AlignType)이 잘 먹지 않는다 —
            # 한컴 정렬 전용 Run 명령이 선택/현재 문단에 확실히 적용된다.
            amap = {"justify": "ParagraphShapeAlignJustify",
                    "left": "ParagraphShapeAlignLeft",
                    "right": "ParagraphShapeAlignRight",
                    "center": "ParagraphShapeAlignCenter",
                    "distribute": "ParagraphShapeAlignDistribute",
                    "양쪽": "ParagraphShapeAlignJustify",
                    "왼쪽": "ParagraphShapeAlignLeft",
                    "오른쪽": "ParagraphShapeAlignRight",
                    "가운데": "ParagraphShapeAlignCenter",
                    "중앙": "ParagraphShapeAlignCenter",
                    "배분": "ParagraphShapeAlignDistribute"}
            run = amap.get(str(align).strip().lower()) or amap.get(str(align).strip())
            if run:
                h.Run(run)
                applied["align"] = align
        if line_spacing is not None:
            pset = h.HParameterSet.HParaShape
            h.HAction.GetDefault("ParaShape", pset.HSet)
            try:
                pset.LineSpacing = int(line_spacing)
                h.HAction.Execute("ParaShape", pset.HSet)
                applied["line_spacing"] = line_spacing
            except Exception:
                pass
        return {"applied": applied, "target": find}

    def set_page(self, top=None, bottom=None, left=None, right=None,
                 orientation=None, paper=None) -> dict:
        """페이지 설정 — 여백(mm)·용지 방향. top/bottom/left/right는 mm.
        orientation: portrait(세로)/landscape(가로).
        실장비 확인: PageSetup/HSecDef.PageDef 필드 경로는 한컴 문서 기준.
        """
        self._ensure_target()

        def mm(v):
            return int(round(float(v) * 7200 / 25.4))  # mm -> HWPUNIT

        h = self.hwp
        pset = h.HParameterSet.HSecDef
        h.HAction.GetDefault("PageSetup", pset.HSet)
        pd = pset.PageDef
        applied = {}
        if top is not None:
            pd.TopMargin = mm(top); applied["top_mm"] = top
        if bottom is not None:
            pd.BottomMargin = mm(bottom); applied["bottom_mm"] = bottom
        if left is not None:
            pd.LeftMargin = mm(left); applied["left_mm"] = left
        if right is not None:
            pd.RightMargin = mm(right); applied["right_mm"] = right
        if orientation is not None:
            pd.Landscape = 1 if str(orientation).lower() in ("landscape", "가로") else 0
            applied["orientation"] = orientation
        h.HAction.Execute("PageSetup", pset.HSet)
        return {"applied": applied}

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

    def export_artifact(self, format: str = "pdf") -> dict:
        """바인딩 문서를 임시 파일로 저장해 서버가 회수하게 한다(미리보기 PDF·
        산출물). 사용자가 지정한 저장 경로·문서는 건드리지 않는다.

        실장비 확인: 한컴 PDF 저장 포맷 문자열은 "PDF". SaveAs가 대화상자를
        띄우지 않도록 위 RegisterModule(보안) 설정에 의존한다.
        """
        self._ensure_target()
        import os
        import tempfile

        ext = {"pdf": "pdf", "hwpx": "hwpx", "hwp": "hwp"}.get(format, "pdf")
        path = os.path.join(tempfile.gettempdir(),
                            f"zzaimy_export_{int(time.time() * 1000)}.{ext}")
        ok = self.hwp.SaveAs(path, self._FMT.get(format, "PDF"), "")
        return {"artifact_path": path, "format": format, "ok": bool(ok),
                "name": os.path.basename(path)}


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
        reg = _post(f"{base}/hwp/agent/register",
                    {"token": token, "version": __version__})
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


# 실행 상태 — 트레이 아이콘·콘솔 표시에 공유한다.
_STATUS = {"connected": False, "server": "", "last_poll": 0.0}


def run_loop(server: str, token: str, backend: HwpBackend,
             confirm: bool = False, no_update: bool = False) -> None:
    """서버에 아웃바운드로 붙어 명령을 롱폴·실행·회신한다.

    서버가 재시작되면 세션이 사라진다(403) — 자동으로 재등록해 이어간다.
    5분마다 최신 코드를 확인해 바뀌었으면 자신을 교체·재실행한다.
    """
    import urllib.error

    base = server.rstrip("/")
    _STATUS["server"] = base
    session, cursor = _register(base, token)
    _STATUS["connected"] = True
    last_update_check = time.time()
    last_cmd_time = 0.0   # 마지막으로 명령을 처리한 시각(유휴 판정용)
    while True:
        # 자동 갱신: 코드가 바뀌었을 때만, 그리고 최근 60초간 작업이 없을 때만
        # (작업 중 말없이 재시작하지 않는다). 15분 간격으로 확인.
        idle = time.time() - last_cmd_time > 60
        if not no_update and idle and time.time() - last_update_check > 900:
            last_update_check = time.time()
            _self_update(base)  # 변경 없으면 무동작, 바뀌었을 때만 재시작
        try:
            resp = _get(f"{base}/hwp/agent/commands?session={session}&after={cursor}")
        except urllib.error.HTTPError as e:
            if e.code == 403:
                sys.stderr.write("[세션 만료] 서버 재시작 감지 — 재등록\n")
                _STATUS["connected"] = False
                time.sleep(2)
                session, cursor = _register(base, token)
                _STATUS["connected"] = True
                continue
            _STATUS["connected"] = False
            sys.stderr.write(f"[폴링 실패] {e} — 5초 후 재시도\n")
            time.sleep(5)
            continue
        except Exception as e:
            _STATUS["connected"] = False
            sys.stderr.write(f"[폴링 실패] {e} — 5초 후 재시도\n")
            time.sleep(5)
            continue
        _STATUS["connected"] = True
        _STATUS["last_poll"] = time.time()
        cursor = resp.get("cursor", cursor)
        for command in resp.get("commands", []):
            result = dispatch(backend, command, confirm=confirm)
            last_cmd_time = time.time()
            # 산출물(PDF·hwpx)이 생겼으면 서버로 회수해 미리보기·다운로드에 쓴다.
            _upload_artifact_if_any(base, session, result)
            try:
                _post(f"{base}/hwp/agent/result", {"session": session, **result})
            except Exception as e:
                sys.stderr.write(f"[회신 실패] {e}\n")


def _upload_artifact_if_any(base: str, session: str, result: dict) -> None:
    """결과에 artifact_path가 있으면 그 파일을 서버로 업로드하고, 결과에
    회수 URL(artifact_url)을 채운다. 로컬 임시 파일은 업로드 후 지운다."""
    res = result.get("result")
    if not isinstance(res, dict):
        return
    path = res.get("artifact_path")
    if not path:
        return
    import os

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        res["artifact_error"] = f"파일 읽기 실패: {e}"
        return
    try:
        up = _post(f"{base}/hwp/agent/artifact", {
            "session": session,
            "cmd_id": result.get("id"),
            "name": res.get("name") or os.path.basename(path),
            "format": res.get("format", "pdf"),
            "b64": base64.b64encode(data).decode("ascii"),
        }, timeout=60)
        res["artifact_url"] = up.get("url")
        res["artifact_bytes"] = len(data)
    except Exception as e:
        res["artifact_error"] = f"업로드 실패: {e}"
        return
    try:
        os.remove(path)   # 임시 파일 정리(서버에 회수됨)
    except OSError:
        pass


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
        {"id": "8", "op": "export_artifact", "args": {"format": "pdf"}},
        {"id": "9", "op": "danger", "args": {}},  # 화이트리스트 밖 → 거부돼야
    ]
    results = [dispatch(b, c) for c in script]
    ok_flags = [r["ok"] for r in results]
    text = b.get_text()["text"]
    art = results[7].get("result", {})

    # 여러 문서 안전 — 대상 미확정 상태에서 편집 거부, new_doc/select_doc 후 허용
    b2 = MockBackend()
    b2.docs[99] = {"text": "다른 사용자 문서", "path": "/other.hwp"}  # 두 번째 문서
    guard_multi = dispatch(b2, {"id": "g1", "op": "insert_text",
                                "args": {"text": "x"}})
    made = dispatch(b2, {"id": "g2", "op": "new_doc", "args": {}})
    after_bind = dispatch(b2, {"id": "g3", "op": "insert_text",
                               "args": {"text": "초안 본문"}})
    other_untouched = b2.docs[99]["text"] == "다른 사용자 문서"
    fmt = dispatch(b2, {"id": "f1", "op": "set_format", "args": {
        "find": "초안", "bold": True, "font": "함초롬바탕",
        "color": "#C00000", "align": "center", "size": 14}})
    pg = dispatch(b2, {"id": "p1", "op": "set_page", "args": {
        "top": 20, "bottom": 20, "left": 25, "right": 25,
        "orientation": "portrait"}})

    checks = [
        ("전 명령 처리", len(results) == 9),
        ("허용 op 성공", all(ok_flags[:8])),
        ("화이트리스트 밖 거부", results[8]["ok"] is False),
        ("치환 반영", "6,000,000" in text and "5,000,000" not in text),
        ("표 삽입", "[표 3x2]" in text),
        ("산출물 export_artifact 경로 반환", bool(art.get("artifact_path"))),
        ("대상 미확정 시 편집 거부", guard_multi["ok"] is False),
        ("새 문서 생성·바인딩 후 편집 허용", made["ok"] and after_bind["ok"]),
        ("다른 문서 안 건드림", other_untouched),
        ("서식 op(글꼴·색·정렬) 처리", fmt["ok"] and "applied" in fmt.get("result", {})),
        ("페이지 여백 op 처리", pg["ok"] and "applied" in pg.get("result", {})),
    ]
    all_ok = True
    all_ok = True
    for name, passed in checks:
        sys.stderr.write(f"  [{'OK' if passed else '실패'}] {name}\n")
        all_ok = all_ok and passed
    sys.stderr.write("selftest " + ("통과\n" if all_ok else "실패\n"))
    return 0 if all_ok else 1


def _self_update(server: str) -> None:
    """서버의 최신 에이전트 코드를 받아 자신을 교체하고 한 번 재실행한다.

    - 내용이 같으면 아무것도 안 한다(재실행 루프 방지).
    - 받은 코드가 파이썬으로 컴파일되지 않으면 교체하지 않는다(손상 방어).
    - HWP_AGENT_UPDATED=1로 자식 프로세스를 표시해 무한 재실행을 막는다.
    """
    import os
    import urllib.request

    # onefile exe(sys.frozen)는 소스가 exe 안에 묶여 있어 .py 교체·재실행이
    # 무의미하다(__file__ 은 임시 추출 폴더). exe 는 build_exe.bat 로 다시 빌드해
    # 갱신한다 — 자동 갱신을 건너뛴다. zip+bat 경로는 종전대로 자동 갱신한다.
    if getattr(sys, "frozen", False):
        return

    try:
        req = urllib.request.Request(f"{server.rstrip('/')}/hwp/agent/latest.py")
        with urllib.request.urlopen(req, timeout=20, context=_SSL_CONTEXT) as r:
            code = r.read().decode("utf-8")
    except Exception as e:
        sys.stderr.write(f"[자동 갱신 생략] 최신 코드를 못 받음: {e}\n")
        return
    self_path = os.path.abspath(__file__)
    try:
        current = open(self_path, encoding="utf-8").read()
    except OSError:
        return
    if code.strip() == current.strip():
        return  # 이미 최신
    try:
        compile(code, self_path, "exec")  # 손상·미완성 코드 방어
    except SyntaxError:
        sys.stderr.write("[자동 갱신 생략] 받은 코드가 유효하지 않음\n")
        return
    try:
        with open(self_path, "w", encoding="utf-8") as f:
            f.write(code)
    except OSError as e:
        sys.stderr.write(f"[자동 갱신 생략] 파일 교체 실패: {e}\n")
        return
    sys.stderr.write("[자동 갱신] 최신 코드로 교체 — 재실행합니다.\n")
    os.environ["HWP_AGENT_UPDATED"] = "1"
    os.execv(sys.executable, [sys.executable] + sys.argv)


def _base_dir() -> str:
    """config.json·server.crt 를 찾을 기준 폴더.

    - 일반 실행(zip+bat, `python hwp_agent.py`): 이 스크립트가 놓인 폴더.
    - PyInstaller onefile exe(sys.frozen): __file__ 은 매 실행마다 바뀌는 임시
      추출 폴더(_MEIPASS)를 가리키므로 쓸 수 없다. exe 가 실제로 놓인 폴더
      (sys.executable)를 우선한다 — 사용자가 exe 옆에 config.json 을 둔다.
    """
    import os

    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _start_tray(server: str):
    """Windows 알림영역(트레이) 아이콘 — 상태 표시·플랫폼 열기·폴더 열기·종료.

    infi.systray 가 있으면 백그라운드 스레드로 띄운다(폴링 루프는 건드리지
    않는다). 없거나 실패하면 None 을 돌려주고, 에이전트는 콘솔만으로 계속 돈다.
    """
    import os

    if os.name != "nt":
        return None
    try:
        from infi.systray import SysTrayIcon
    except Exception:
        sys.stderr.write("[트레이 생략] infi.systray 없음 — 콘솔로 동작합니다.\n")
        return None
    ico = os.path.join(_base_dir(), "zzaimy.ico")
    if not os.path.exists(ico):
        return None

    def _open_server(_st):
        import webbrowser
        try:
            webbrowser.open(server)
        except Exception:
            pass

    def _open_folder(_st):
        try:
            os.startfile(_base_dir())  # type: ignore[attr-defined]
        except Exception:
            pass

    def _show_status(_st):
        """상태·버전을 메시지 상자로 보여준다(추가 의존성 없이 Win32 API)."""
        state = "연결됨" if _STATUS.get("connected") else "연결 끊김"
        lp = _STATUS.get("last_poll") or 0
        ago = f"{int(time.time() - lp)}초 전" if lp else "-"
        msg = ("ZZAIMY 한글 에이전트\n\n"
               f"버전: {__version__}\n"
               f"서버: {_STATUS.get('server', '')}\n"
               f"상태: {state}\n"
               f"마지막 통신: {ago}")
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "에이전트 상태", 0x40)
        except Exception:
            sys.stderr.write(msg + "\n")

    def _open_config(_st):
        """config.json 을 기본 편집기로 연다 — 서버 주소·연결 키(토큰) 수정용.
        수정 후에는 트레이 종료 → 다시 실행하면 새 설정으로 붙는다."""
        cfg = os.path.join(_base_dir(), "config.json")
        try:
            os.startfile(cfg)  # type: ignore[attr-defined]
        except Exception:
            try:
                import subprocess
                subprocess.Popen(["notepad.exe", cfg])
            except Exception:
                pass

    def _quit(_st):
        os._exit(0)

    menu = (("상태 보기", None, _show_status),
            ("연결 키·주소 설정", None, _open_config),
            ("플랫폼 열기", None, _open_server),
            ("설치 폴더 열기", None, _open_folder))
    try:
        tray = SysTrayIcon(ico, f"ZZAIMY {__version__} — 연결 중…", menu, on_quit=_quit)
        tray.start()
    except Exception as e:
        sys.stderr.write(f"[트레이 생략] {e}\n")
        return None

    def _update_loop():
        while True:
            time.sleep(3)
            state = "연결됨" if _STATUS.get("connected") else "연결 끊김"
            try:
                tray.update(hover_text=f"ZZAIMY {__version__} — {state}")
            except Exception:
                return

    import threading
    threading.Thread(target=_update_loop, daemon=True).start()
    return tray


def main() -> int:
    ap = argparse.ArgumentParser(description="한글 실시간 편집 에이전트 (COM)")
    ap.add_argument("--server", help="서버 base URL")
    ap.add_argument("--token", help="세션 토큰(사용자 인증에 묶임)")
    ap.add_argument("--confirm", action="store_true", help="편집성 명령을 콘솔 확인 후 실행")
    ap.add_argument("--selftest", action="store_true", help="한글 없이 디스패처 검증")
    ap.add_argument("--no-visible", action="store_true", help="한글 창 숨김")
    ap.add_argument("--ca-cert", help="서버 인증서 파일(자체 서명 신뢰용)")
    ap.add_argument("--no-update", action="store_true", help="시작 시 자동 갱신 생략")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    # 플랫폼 배포 번들은 config.json에 접속 정보를 심어 보낸다 — 인자 불필요
    if not (args.server and args.token):
        import os

        cfg_path = os.path.join(_base_dir(), "config.json")
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

    # 자동 갱신 — 서버의 최신 코드를 받아 자신을 교체하고 한 번만 재실행한다.
    # 코드가 바뀌어도 재다운로드 없이 시작.bat 재실행만으로 최신이 된다.
    if not args.no_update and os.environ.get("HWP_AGENT_UPDATED") != "1":
        _self_update(args.server)

    try:
        backend: HwpBackend = ComBackend(visible=not args.no_visible)
    except Exception as e:
        sys.stderr.write(f"한글 COM 백엔드를 열 수 없습니다: {e}\n"
                         "Windows + 정품 한글 + pywin32 환경에서 실행하세요.\n")
        return 2

    # 실행 표시 — 트레이 아이콘(있으면) + 콘솔 배너. "떠 있으면 동작 중".
    _STATUS["server"] = args.server
    tray = _start_tray(args.server)
    sys.stderr.write(
        "\n" + "=" * 52 + "\n"
        f"  ZZAIMY 한글 에이전트 실행 중  (v{__version__})\n"
        f"  서버: {args.server}\n"
        + ("  상태: 알림영역(트레이) 아이콘으로 확인 — 우클릭 메뉴에서 종료\n"
           if tray else
           "  상태: 이 창이 떠 있으면 동작 중 — 창을 닫으면 종료됩니다\n")
        + "=" * 52 + "\n")
    run_loop(args.server, args.token, backend,
             confirm=args.confirm, no_update=args.no_update)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
