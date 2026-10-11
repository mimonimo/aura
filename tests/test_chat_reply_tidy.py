"""답 글 다듬기 — 예외 이름은 화면에 내보내지 않고, 되풀이된 문장은 한 번만, 「필요한 값 입력」으로 가는 길. 가상 값으로 본다."""
import json

from fastapi.testclient import TestClient

from tests.test_app import FakeDrafter, FakeProcessor, FakeResponder
from zzaimy.app import asks, main
from zzaimy.app.korean_text import reason, tidy_reply


def _client(tmp_path):
    app = main.create_app(db_path=tmp_path / 'chat.db', inbox_dir=tmp_path / 'inbox',
                          processor=FakeProcessor(), drafter=FakeDrafter(), responder=FakeResponder())
    return TestClient(app)


def test_tidy_reply_drops_exception_names_and_repeats():
    assert tidy_reply("적용 실패(ValueError): 절에 표 6 이 없습니다") == "적용 실패: 절에 표 6 이 없습니다"
    assert tidy_reply("문서를 만들지 못했습니다(HttpError 403). 다시 해 주세요.") == "문서를 만들지 못했습니다. 다시 해 주세요."
    assert tidy_reply("요청을 끝내지 못했습니다 — 정해진 걸음 안에 요청을 끝내지 못했습니다.") == "정해진 걸음 안에 요청을 끝내지 못했습니다."
    assert tidy_reply("가상 값을 찾았습니다. 다음 절로 갑니다. 가상 값을 찾았습니다.") == "가상 값을 찾았습니다. 다음 절로 갑니다."
    # 표 줄·목록 항목·짧은 말은 되풀이돼도 그대로
    table = "| 항목 | 해당 없음 |\n| 항목 | 해당 없음 |"
    assert tidy_reply(table) == table
    assert tidy_reply("- 확인 필요\n- 확인 필요") == "- 확인 필요\n- 확인 필요"
    assert tidy_reply("네. 네.") == "네. 네."
    assert tidy_reply("첫 문단입니다.\n\n둘째 문단입니다.") == "첫 문단입니다.\n\n둘째 문단입니다."
    # 서로 다른 말을 잇는 줄표는 그대로
    assert tidy_reply("작업본을 만들었습니다 — 원본 서식은 그대로 둡니다.") == "작업본을 만들었습니다 — 원본 서식은 그대로 둡니다."


def test_reason_maps_exceptions_without_class_names(caplog):
    assert reason(ValueError("절에 표 6 이 없습니다")) == "절에 표 6 이 없습니다"
    assert reason(PermissionError("insufficient scope")) == "권한이 없거나 계정 연결이 끊겼습니다"
    assert reason(TimeoutError()) == "응답이 너무 늦었습니다"

    class SomeHttpError(Exception):
        pass
    assert reason(SomeHttpError("<html>quota</html>")) == "외부 서비스가 요청을 처리하지 못했습니다"
    assert reason(KeyError("x")) == "예상하지 못한 오류가 났습니다"
    assert "KeyError" in caplog.text            # 클래스 이름은 기록에만


def test_saved_and_old_replies_are_tidied(tmp_path):
    client = _client(tmp_path)
    db = client.app.state.db
    sid = db.create_chat_session('합성 대화')
    db.add_chat(sid, 'user', '표 채워 줘')
    db.add_chat(sid, 'assistant', '적용 실패(ValueError): 가상 절에 표 9 가 없습니다')
    assert db.list_chats(sid)[-1]['content'] == '적용 실패: 가상 절에 표 9 가 없습니다'
    # 저장 전 다듬기를 거치지 않은 옛 글도 화면에서는 다듬어 보인다
    with db._conn() as conn:
        conn.execute("UPDATE chat_messages SET content = ? WHERE session_id = ? AND role = 'assistant'",
                     ('요청을 끝내지 못했습니다 — 정해진 걸음 안에 요청을 끝내지 못했습니다. (RuntimeError)', sid))
    page = client.get(f'/chat/{sid}').text
    assert 'RuntimeError' not in page and 'ValueError' not in page
    assert page.count('끝내지 못했습니다') == 1


def test_value_form_is_reshown_from_pending_asks_with_jump(tmp_path):
    client = _client(tmp_path)
    db = client.app.state.db
    sid = db.create_chat_session('합성 대화')
    db.add_chat(sid, 'user', '1절 써 줘')
    db.add_chat(sid, 'assistant', '빈 값 2개는 「필요한 값 입력」에서 채울 수 있습니다.')
    db.add_chat(sid, 'user', '다른 질문')
    db.add_chat(sid, 'assistant', '다른 답입니다.')
    asks.set_pending(db, sid, [{'name': '가상 사업단명', 'hint': '표지에 쓰임'}])
    db.set_setting(f'chat_options:{sid}', json.dumps([{'kind': 'ask', 'text': '다른 일', 'question': '다른 일'}], ensure_ascii=False))
    page = client.get(f'/chat/{sid}').text
    assert 'id="chatValueForm"' in page and '가상 사업단명' in page
    assert 'data-jump-form>필요한 값 입력으로 이동</button>' in page
    assert '>다른 일</button>' in page
    # 자료에서 찾은 확인 전 값이 있으면 다시 띄운 칸에도 미리 채운 값·출처 안내·「확인」이 그대로(답을 만들 때와 같은 form_option)
    db.set_setting(f'chat_facts:{sid}:suggested', json.dumps(
        {'가상 사업단명': {'value': '가상 미래교육 사업단', 'title': '가상 공고 제2099-1호'}}, ensure_ascii=False))
    page = client.get(f'/chat/{sid}').text
    form = page.split('id="chatValueForm"', 1)[1].split('</form>', 1)[0]
    assert 'value="가상 미래교육 사업단"' in form
    assert '자료에서 찾음 · 확인 전 — 《가상 공고 제2099-1호》' in form
    assert '>확인</button>' in form
    # 저장된 옛 칸(값 없음)이 있어도 지금 값으로 다시 만든 칸 하나만
    db.set_setting(f'chat_options:{sid}', json.dumps([asks.form_option([{'name': '가상 사업단명', 'hint': ''}])], ensure_ascii=False))
    page = client.get(f'/chat/{sid}').text
    assert page.count('id="chatValueForm"') == 1 and 'value="가상 미래교육 사업단"' in page
    # 받을 값이 없으면 칸도 이동 단추도 없다
    asks.set_pending(db, sid, [])
    page = client.get(f'/chat/{sid}').text
    assert 'id="chatValueForm"' not in page and 'data-jump-form' not in page
