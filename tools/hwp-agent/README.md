# 한글 실시간 편집 에이전트 (Windows COM 계층)

"채팅으로 지시 → 눈앞의 한글 문서가 실시간으로 수정" 을 위한 Windows 쪽
자동화 에이전트다. 서버(리눅스)가 보낸 편집 명령을 실행 중인 한글(한컴오피스)에
COM으로 적용한다.

```
[Windows PC · 한글] ── 에이전트(이 코드) ──▶ 서버로 아웃바운드 연결
     ▲                                         │  편집 명령(JSON) 수신
     └── 한글 창이 화면에서 실시간으로 바뀜 ◀───┘  결과 회신
```

## 위치 (두 경로 중 ②)

한글 편집에는 두 경로가 있고 이 도구는 후자다.

- **① hwpx-plugin (서버측, 기본)** — 한컴 없이 python-hwpx로 HWPX를 직접
  편집. 화면 동기화가 필요 없는 대부분의 작업은 이쪽.
- **② 이 COM 에이전트 (Windows 전용)** — 실행 중인 한글 창을 조작해 화면에
  실시간 반영. "지시하면 눈앞에서 바뀌는" 감각이 필요할 때만 쓴다.

두 경로는 같은 명령 계약(`protocol.md`)을 해석한다.

## 필요 환경

- Windows + 정품 한컴오피스(한글) 설치
- Python 3.10+ 와 `pywin32` (`pip install -r requirements.txt`)
- 서버로 나가는 아웃바운드 연결(인바운드 개방 불필요)

## 실행

한글이 없어도 되는 자기검증(디스패처·계약):

```
python hwp_agent.py --selftest
```

실제 구동(Windows, 한글 실행 상태 권장):

```
python hwp_agent.py --server https://<서버주소> --token <세션토큰>
python hwp_agent.py --server ... --token ... --confirm   # 편집성 명령 확인 후 실행
```

`--token` 은 서버에서 발급한 사용자 세션 토큰이다. 에이전트는 그 사용자
컨텍스트에 묶여 그 사람 문서만 편집한다.

## 안전

- op 화이트리스트(protocol.md) 밖의 명령은 거부한다.
- `--confirm` 에서 편집성 명령(insert/replace/save)은 콘솔 확인 후 실행.
- 실패는 서버에 사유를 회신하고, 문서를 임의로 닫거나 저장하지 않는다.
- 서버가 세척·검증을 거친 값만 명령에 실어 보낸다(내부 정보 유출 경계는
  서버 책임 — ADR-0008 이그레스 게이트웨이).

## 남은 통합 (서버측 담당)

- `/hwp/agent/register`, `/hwp/agent/commands`(롱폴), `/hwp/agent/result`
  엔드포인트(protocol.md). 서버가 사용자 세션에 명령을 라우팅한다.
- 자연어 지시 → 편집 명령 변환은 LLM(서빙) 연결 후. 그전에는 정해진 명령을
  폼/버튼으로 보내 실시간 반영을 검증할 수 있다.

## 실장비 확인 사항

COM API 호출(HParameterSet 필드명, Open/SaveAs 포맷 문자열 등)은 한컴 자동화
문서 기준으로 작성했다. Windows 실장비에서 최초 1회, 각 op(open/insert_text/
find/replace/insert_table/save)가 실제 한글에서 동작하는지 확인한 뒤 배포한다.
`ComBackend` 한 클래스에 COM 호출을 격리해 두었으니 조정은 그 안에서만 한다.
