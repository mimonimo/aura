# ZZAIMY 한글 편집 에이전트 — 단일 exe 빌드·실행

기존 배포는 `zip` 을 풀어 `시작.bat` 을 더블클릭하는 방식이다. 이 문서는 같은
에이전트를 **단일 실행 파일 `zzaimy-hwp-agent.exe`** 로 만들어 더 깔끔하게
실행하는 방법을 설명한다. (zip+bat 경로도 그대로 계속 동작한다 — 회귀 없음.)

## 중요: exe 는 Windows 에서만 만들 수 있다

PyInstaller 는 **크로스빌드를 지원하지 않는다.** 즉 만들려는 exe 와 같은 OS
에서 빌드해야 한다. 개발 머신(macOS)에서는 Windows exe 를 만들 수 없다.

따라서 이 저장소에는 **exe 자체가 아니라 exe 를 만드는 빌드 자산**만 들어 있다:

| 파일 | 역할 |
|---|---|
| `hwp_agent.spec` | PyInstaller onefile 스펙 (콘솔 유지, 이름 `zzaimy-hwp-agent.exe`) |
| `build_exe.bat` | Windows 에서 원클릭 빌드 (pip 설치 → 빌드 → 안내) |
| `hwp_agent.py` | 에이전트 본체 (frozen 대응 config 탐색 포함) |

**실제 exe 는 Windows 워커에서 `build_exe.bat` 로 생성한다.**

## 빌드 절차 (Windows)

전제: Windows + Python 3.9+ 설치. 온프레미스/오프라인 원칙에 따라 인터넷이
없으면 `pyinstaller`·`pywin32` 를 사내에서 미리 설치해 둔다.

1. 이 폴더(`tools\hwp-agent`)를 Windows 로 복사한다.
2. `build_exe.bat` 를 더블클릭한다. 내부적으로:
   ```
   python -m pip install pyinstaller pywin32
   python -m PyInstaller --noconfirm --clean hwp_agent.spec
   ```
3. 완료되면 `dist\zzaimy-hwp-agent.exe` 가 생긴다.

수동으로 하려면 같은 폴더에서:
```
pip install pyinstaller pywin32
pyinstaller hwp_agent.spec
```

## config.json 은 exe '옆'에 둔다

exe 는 서버 주소·토큰을 담고 있지 않다(서버·토큰마다 다르므로 exe 는 공용).
접속 정보는 exe 와 **같은 폴더**의 `config.json` 에서 읽는다.

onefile exe 는 실행 시 내부를 임시 폴더에 풀기 때문에 `__file__` 이 매번
바뀐다. 그래서 `hwp_agent.py` 의 `_base_dir()` 는 frozen(exe) 실행일 때
`__file__` 대신 **exe 가 놓인 폴더(`sys.executable`)** 를 기준으로 `config.json`
을 찾는다. 일반 `python hwp_agent.py` 실행에서는 종전처럼 스크립트 폴더를 본다.

배치 예:
```
zzaimy-hwp-agent.exe
config.json          <- 같은 폴더
server.crt           <- config.json 이 가리키면 같은 폴더에
```

`config.json` 형식(플랫폼 zip 번들 안의 것을 그대로 복사해 쓰면 된다):
```json
{
  "server": "https://<서버 주소>",
  "token": "<발급받은 세션 토큰>",
  "ca_cert": "server.crt"
}
```
`ca_cert` 는 `config.json` 이 있는 폴더 기준 상대경로로 해석된다(자체 서명
인증서 신뢰용). 없으면 생략 가능.

## 실행

1. 한글(한컴오피스)을 먼저 실행해 둔다.
2. `zzaimy-hwp-agent.exe` 를 더블클릭한다.
3. 콘솔 창에 `[에이전트] 서버 연결됨 session=...` 이 뜨면 성공. 플랫폼의
   연결 상태에 나타난다.

`--server` / `--token` 을 직접 넘기면 config.json 없이도 실행된다:
```
zzaimy-hwp-agent.exe --server https://<서버> --token <토큰> --ca-cert server.crt
```
동작 검증(한글 없이 명령 라우팅만):
```
zzaimy-hwp-agent.exe --selftest
```

## exe 는 자동 갱신되지 않는다

zip+bat 배포판은 서버의 최신 코드를 받아 자신을 교체한다(self-update). 그러나
onefile exe 는 소스가 exe 안에 묶여 있어 `.py` 교체가 무의미하므로 **exe 는
자동 갱신을 건너뛴다.** 에이전트 코드가 바뀌면 Windows 에서 `build_exe.bat` 로
**다시 빌드**해 배포한다.
