# ZZAIMY 한글 에이전트 — 설치 프로그램 빌드·배포

`setup.exe`(설치/제거 지원)를 만드는 절차. **Windows PC에서 한 번만 빌드**하면,
나온 `setup.exe`를 다른 어떤 Windows PC에 복사해 실행해도 **동일하게 설치**된다.

---

## 1. 준비물 (빌드 PC = Windows)

- Windows 10/11 PC 한 대
- **Inno Setup 6** (무료): https://jrsoftware.org/isdl.php → 설치
  - 한국어 마법사(`Korean.isl`)가 없다는 컴파일 오류가 나면, `.iss` 의 `[Languages]`
    줄을 `Name: "en"; MessagesFile: "compiler:Default.isl"` 로 바꾸면 된다
    (마법사 안내 문구는 그대로 한글로 표시됨).
- 플랫폼에서 내려받은 **개인화 번들** (접속 주소·토큰·인증서 포함)

## 2. payload 채우기

1. 플랫폼 `개발 현황 → 한글 에이전트`에서 **에이전트 zip**을 내려받는다
   (`/dev/hwp/agent.zip` — config.json·서버 인증서가 이미 들어 있음).
2. 압축을 풀어 그 **내용물 전체**를 이 폴더의 `payload\` 안에 넣는다:

   ```
   installer\
     zzaimy-agent.iss
     payload\
       python\            (포터블 파이썬)
       comtypes\
       hwp_agent.py
       protocol.md
       config.json        ← 접속 주소·토큰·인증서 (필수)
       시작.bat           (설치본은 안 써도 됨)
       README.txt
   ```

3. (선택) **보안 팝업 끄기**를 쓰려면 한컴 공개 보안 모듈
   `FilePathCheckerModule.dll` 을 `payload\` 에 함께 넣는다.
   없으면 설치 시 "보안 모듈" 체크박스는 무시된다(등록 안 함).

## 3. 컴파일

1. Inno Setup Compiler 로 `zzaimy-agent.iss` 를 연다.
2. **Build → Compile** (Ctrl+F9).
3. 결과물: `installer\dist\zzaimy-agent-setup.exe`

## 4. 배포·설치

- `zzaimy-agent-setup.exe` 를 대상 PC에 복사(USB·사내 공유·다운로드) 후 실행.
- 관리자 권한 불필요(사용자 폴더 `%LOCALAPPDATA%\ZZAIMY-Agent` 에 설치).
- 설치 마법사 옵션:
  - Windows 시작 시 자동 실행
  - 바탕화면 바로가기
  - **한글 자동화 보안 팝업 끄기(보안 모듈 등록)** — 기본 꺼짐
- 설치 후: 시작 메뉴 **"ZZAIMY 에이전트"** 실행 → 한글에 연결.
  문제 진단이 필요하면 **"…(콘솔·문제진단)"** 바로가기로 로그를 본다.

## 5. 제거

- Windows **설정 → 앱 → 설치된 앱 → ZZAIMY 한글 에이전트 → 제거**
  (또는 시작 메뉴의 "제거" 바로가기).
- 설치 파일 + 자동시작 등록 + 보안 모듈 레지스트리 값까지 **모두 원복**된다.

---

## 알아둘 점

- **동일 배포**: setup.exe 는 독립 실행 파일이라 여러 PC에 그대로 뿌리면 된다.
  대상 PC엔 Inno Setup·파이썬 등 개발도구가 필요 없다.
- **재설치 = 업그레이드**: 같은 AppId 라서, 새 setup.exe 를 이미 설치된 PC에서
  실행하면 **기존 설치 폴더에 덮어쓰기(업그레이드)** 된다(중복 설치 아님).
  설치 전 실행 중인 에이전트를 자동 종료한 뒤 교체한다.
- **상태·버전 확인**: 설치 후 트레이 아이콘 우클릭 → "상태 보기"에서 버전·서버·
  연결 상태를 확인. 서버 주소나 연결 키(토큰)를 바꾸려면 "연결 키·주소 설정"으로
  config.json 을 열어 수정하고 트레이에서 종료 후 다시 실행한다.
- **토큰 재발급 시 재빌드**: config.json 에 토큰이 박혀 있으므로, 플랫폼에서
  토큰을 재발급하면 payload 를 새 zip으로 교체하고 다시 컴파일해야 한다.
- **자동 업데이트**: 설치된 에이전트는 실행 중 서버에서 최신 `hwp_agent.py` 를
  스스로 받아 갱신한다. 따라서 에이전트 로직이 바뀌어도 보통 재빌드가 필요 없고,
  **재빌드는 토큰·인증서·파이썬/comtypes 가 바뀔 때만** 하면 된다.
- **미서명 경고**: 코드서명 인증서가 없으면 첫 실행 시 SmartScreen "알 수 없는
  게시자" 경고가 뜬다 → "추가 정보 → 실행". 인증서로 서명하면 사라진다(선택).
- **보안 모듈 주의**: 팝업을 끄는 것은 "외부 프로그램의 한글 파일 접근 허용"이라
  보안을 완화한다. 레지스트리 경로(`HKCU\Software\HNC\HwpAutomation\Modules`)와
  모듈명은 **한글 버전에 따라 다를 수 있다** — 팝업이 계속 뜨면 실장비에서 실제
  키 경로를 확인해 `.iss` 의 [Registry] 항목을 맞춰야 한다. 교내 관리 PC는
  레지스트리 쓰기·미서명 DLL 이 정책으로 막힐 수 있으니 보안팀과 사전 확인.
