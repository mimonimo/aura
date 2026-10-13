"""문서 가져오기(/dev/nas) 화면 — 머리 경로·단계 안내·앱/계정 카드·연결 폴더 카드·연결 확인 결과 자리.

구글 API 는 부르지 않는다(연결 확인·미리보기는 가짜로 바꿔 끼운다). 사례 값이 아니라 조건으로 본다:
연결 방식이 무엇이든, 폴더가 몇 개든, 처리 형식 목록이 늘어도 화면이 맞게 따라오는지.
"""
from __future__ import annotations

import re

import pytest

from tests.test_gdocs import docs_env  # noqa: F401  가짜 구글 앱·계정 두 개
from tests.test_gdocs_ui import client_for
from zzaimy import doc_formats
from zzaimy.app import pipeline
from zzaimy.ingest import nas_sync


def _drive(name="시험 폴더", root="root", **kw):
    return nas_sync.add(name, "gdrive", root, "ocr", username="staff@example.ac.kr", **kw)


def _set(sid, **fields):
    sources = nas_sync.list_sources()
    for s in sources:
        if s["id"] == sid:
            s.update(fields)
    nas_sync._save_sources(sources)


# ---- 처리 형식 한 곳 ----

def test_type_groups_cover_every_processable_format_once():
    """묶음은 처리 형식(doc_formats.DOC_EXT + 처리기가 읽는 사진)을 빠짐없이, 겹치지 않게 덮는다."""
    want = {f".{e}" for e in doc_formats.DOC_EXT} | {
        f".{e}" for e in doc_formats.IMAGE_EXT if f".{e}" in pipeline._IMAGE_SUFFIX}
    flat = [e for _k, _l, exts in nas_sync.TYPE_GROUPS for e in exts]
    assert set(flat) == want and len(flat) == len(set(flat))
    assert set(nas_sync.DEFAULT_EXTENSIONS) == want


def test_new_processable_format_joins_groups_without_list_edit(monkeypatch):
    """처리 형식에 새 확장자가 더해지면 「그 밖의 문서」로라도 화면·필터에 들어온다."""
    monkeypatch.setattr(nas_sync, "PROCESSABLE_EXTENSIONS", [*nas_sync.PROCESSABLE_EXTENSIONS, ".zzq"])
    groups = nas_sync._type_groups()
    assert ("other", "그 밖의 문서", [".zzq"]) in groups


def test_old_saved_extension_list_follows_current_groups():
    """옛 고정 목록으로 저장된 원천도 고른 묶음의 지금 형식 전부를 가져온다(엑셀 → xlsm·csv 등)."""
    src = {"extensions": [".pdf", ".xls", ".xlsx"]}
    eff = nas_sync.effective_extensions(src)
    excel = next(exts for k, _l, exts in nas_sync.TYPE_GROUPS if k == "excel")
    assert ".pdf" in eff and set(excel) <= set(eff) and ".hwp" not in eff
    assert [g["key"] for g in nas_sync.extension_groups(src)] == ["pdf", "excel"]
    assert nas_sync._passes_filters(src, "가/표.xlsm", 10, 0) and not nas_sync._passes_filters(src, "가/글.hwp", 10, 0)
    # 묶음 밖 확장자를 직접 적은 원천은 그대로 둔다
    assert ".dat" in nas_sync.effective_extensions({"extensions": [".dat"]})


@pytest.mark.parametrize("backend,root,label", [
    ("gdrive", "root", "내 드라이브 최상위"), ("gdrive", "", "내 드라이브 최상위"),
    ("gdrive", "1AbCdEf", "1AbCdEf"), ("local", "/mnt/root", "/mnt/root")])
def test_root_label_in_plain_words(backend, root, label):
    assert nas_sync.root_label({"backend": backend, "root": root}) == label


# ---- 화면 ----

def test_header_names_only_connected_kinds(docs_env, tmp_path):  # noqa: F811
    client = client_for(tmp_path)
    head = lambda page: re.search(r'</nav>.*?<span class="muted" style="font-size:12.5px;">(.*?)</span>', page, re.S).group(1)
    page = client.get("/dev/nas").text                                     # 예전 주소 → 문서 반입의 Drive 탭
    assert "← 개발 현황" not in page and 'href="/dev/intake?tab=drive" class="on"' in page
    assert "NAS" not in head(page)                                         # 연결 없음 — 기본은 Google Drive
    _drive()
    assert head(client.get("/dev/nas").text) == "Google Drive 폴더 → 라이브러리"
    nas_sync.add("공유", "smb", r"\\nas.example\문서", "ocr")
    assert "NAS(SMB)" in head(client.get("/dev/nas").text)


def test_app_card_hides_client_id_and_account_button_reads_by_state(docs_env, tmp_path, monkeypatch):  # noqa: F811
    from zzaimy.ingest import gdrive

    page = client_for(tmp_path).get("/dev/nas").text
    assert "abc.apps" not in page and "등록 완료" in page                 # 클라이언트 id 는 화면에 없다
    accounts = page.split('id="googleAccounts"', 1)[1].split("</fieldset>", 1)[0]
    # 계정 목록은 사용자·계정 화면 한 곳에 — 여기는 개수와 그 자리 링크, 계정이 있으면 「다른 계정 연결」
    assert "<strong>staff@example.ac.kr" not in accounts and "연결 계정 " in accounts and 'href="/dev/users#google-links"' in accounts
    assert accounts.index("/dev/users#google-links") < accounts.index("다른 계정 연결")
    assert "Google 계정 연결" not in accounts
    monkeypatch.setattr(gdrive, "list_accounts", lambda: [])
    page = client_for(tmp_path).get("/dev/nas").text
    accounts = page.split('id="googleAccounts"', 1)[1].split("</fieldset>", 1)[0]
    assert "Google 계정 연결" in accounts and "다른 계정 연결" not in accounts


def test_next_step_line_only_while_steps_remain(docs_env, tmp_path):  # noqa: F811
    client = client_for(tmp_path)
    page = client.get("/dev/nas").text
    assert page.count('class="gd-next"') == 1 and "가져올 Drive 폴더를 연결하세요." in page
    src = _drive()
    assert "‘지금 가져오기’를 실행하세요." in client.get("/dev/nas").text
    _set(src["id"], last_run="2026-01-02 03:04", last_summary="새로 1")
    page = client.get("/dev/nas").text
    assert 'class="gd-next"' not in page and "연결 설정이 완료되었습니다" not in page
    assert page.count('class="done"') == 4


def test_source_card_actions_formats_and_layout(docs_env, tmp_path):  # noqa: F811
    client = client_for(tmp_path)
    src = _drive(extensions=".pdf .hwp .hwpx")
    page = client.get("/dev/nas").text
    assert 'class="nas-source-grid single"' in page                          # 하나면 넓게
    card = page.split(f'id="source-{src["id"]}"', 1)[1].split("</article>", 1)[0]
    assert "내 드라이브 최상위" in card and ">root<" not in card
    assert re.findall(r'<span title="[^"]*">([^<]+)</span>', card) == ["PDF", "한글"]
    actions = card.split('class="gd-source-actions"', 1)[1]
    assert [b for b in re.findall(r">([^<>]+)</button>", actions)] == ["연결 확인", "미리보기", "지금 가져오기", "수정", "연결 삭제"]
    assert 'style=' not in actions                                            # 단추 모양은 공용 규칙 하나
    delete = actions.split('class="nas-danger"', 1)[1].split(">", 1)[0]
    assert "data-confirm-danger" in delete and f'/dev/nas/{src["id"]}/delete' in delete
    _drive("둘째")
    assert 'class="nas-source-grid"' in client.get("/dev/nas").text


def test_probe_and_plan_results_sit_under_their_card(docs_env, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(nas_sync, "probe", lambda src, limit=30: {"ok": True, "n_all": 4, "n_match": 2, "sample": ["가.pdf"], "error": ""})
    monkeypatch.setattr(nas_sync, "plan", lambda src, limit=20: {"ok": True, "new": 1, "same": 0, "skipped": 0,
                                                                "sample": [{"path": "가.pdf", "size": 2048}], "error": ""})
    client = client_for(tmp_path)
    first, second = _drive("첫째"), _drive("둘째")
    r = client.post(f"/dev/nas/{second['id']}/probe", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"#result-{second['id']}")
    page = client.get(r.headers["location"].split("#")[0]).text
    assert page.count('class="nas-check-result"') == 1
    # 결과는 그 카드 바로 뒤(다음 카드보다 앞), 머리에 무엇을 한 결과인지와 시각
    after = page.split(f'id="source-{second["id"]}"', 1)[1]
    result = after.split(f'id="result-{second["id"]}"', 1)[1].split("</section>", 1)[0]
    assert "</article>" in after.split(f'id="result-{second["id"]}"', 1)[0]
    assert "연결 확인 결과 — 둘째" in result and re.search(r"<time>\d\d:\d\d:\d\d 확인</time>", result)
    assert page.index(f'id="source-{first["id"]}"') < page.index(f'id="source-{second["id"]}"')
    r = client.post(f"/dev/nas/{first['id']}/plan", follow_redirects=False)
    assert r.headers["location"].endswith(f"#result-{first['id']}")
    page = client.get(r.headers["location"].split("#")[0]).text
    head = page.split(f'id="result-{first["id"]}"', 1)[1]
    assert page.index(f'id="result-{first["id"]}"') < page.index(f'id="source-{second["id"]}"')
    assert "미리보기 결과 — 첫째" in head and "계산</time>" in head
