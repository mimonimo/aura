; ZZAIMY 한글 에이전트 설치 프로그램 (Inno Setup 6+)
; 빌드: Windows에서 Inno Setup Compiler 로 이 파일을 컴파일 → setup.exe 생성.
;       payload\ 폴더에 개인화 번들(플랫폼 /dev/hwp/agent.zip 압축해제 결과)을 넣어 둔다.
; 자세한 절차는 installer/BUILD.md 참조.

#define AppName "ZZAIMY 한글 에이전트"
#define AppVer "1.0.0"
#define AppPublisher "영남이공대학교 ZZAIMY"
; payload 폴더 경로(이 .iss 기준 상대경로). 여기에 python\, comtypes\, hwp_agent.py, config.json 등이 있어야 한다.
#define Payload "payload"

[Setup]
; AppId 는 설치/제거를 식별하는 고유값 — 절대 바꾸지 말 것(바꾸면 별개 프로그램으로 취급).
AppId={{7F3B2C6E-9A41-4E7C-B0D2-3E5A9C1D0B27}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher={#AppPublisher}
; 관리자 권한 불필요 — 사용자 폴더에 설치(교내 관리 PC 친화적)
PrivilegesRequired=lowest
DefaultDirName={localappdata}\ZZAIMY-Agent
DefaultGroupName=ZZAIMY
DisableProgramGroupPage=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\python\pythonw.exe
OutputBaseFilename=zzaimy-agent-setup
OutputDir=dist
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 재설치 시 같은 AppId·같은 폴더로 자동 업그레이드(기존 파일 교체). 버전 기록.
VersionInfoVersion={#AppVer}
UsePreviousAppDir=yes
CloseApplications=yes
RestartApplications=no
; 한국어 마법사
[Languages]
Name: "kr"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "autostart"; Description: "Windows 시작 시 에이전트 자동 실행"; GroupDescription: "추가 옵션:"
Name: "desktopicon"; Description: "바탕화면 바로가기 만들기"; GroupDescription: "추가 옵션:"
; 보안 모듈 등록 — 기본 꺼짐(보안 완화이므로 사용자가 의식적으로 선택)
Name: "secmodule"; Description: "한글 자동화 보안 팝업 끄기(보안 모듈 등록) — 파일 접근 경고를 없앱니다"; GroupDescription: "한글 연동:"; Flags: unchecked

[Files]
; 번들 전체를 설치 폴더로 복사(포터블 파이썬 포함 — 사용자에겐 숨겨짐)
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; 보안 모듈 DLL(있을 때만 포함). 한컴 공개 샘플 FilePathCheckerModule.dll 을 payload\ 에 넣으면 함께 설치됨.
Source: "{#Payload}\FilePathCheckerModule.dll"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist; Tasks: secmodule

[Icons]
; 일반 실행(콘솔 없음) + 디버그용(콘솔 표시)
Name: "{group}\ZZAIMY 에이전트"; Filename: "{app}\python\pythonw.exe"; Parameters: "hwp_agent.py"; WorkingDir: "{app}"; IconFilename: "{app}\python\pythonw.exe"
Name: "{group}\ZZAIMY 에이전트(콘솔·문제진단)"; Filename: "{app}\python\python.exe"; Parameters: "hwp_agent.py"; WorkingDir: "{app}"
Name: "{group}\ZZAIMY 에이전트 제거"; Filename: "{uninstallexe}"
Name: "{userdesktop}\ZZAIMY 에이전트"; Filename: "{app}\python\pythonw.exe"; Parameters: "hwp_agent.py"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
; 자동 시작(선택) — HKCU Run. 제거 시 값 삭제.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ZZAIMYAgent"; ValueData: """{app}\python\pythonw.exe"" hwp_agent.py"; Flags: uninsdeletevalue; Tasks: autostart
; 한글 자동화 보안 모듈 등록(선택) — 제거 시 값 삭제. 경로/키는 한글 버전에 따라 다를 수 있음(BUILD.md 참조).
Root: HKCU; Subkey: "Software\HNC\HwpAutomation\Modules"; ValueType: string; ValueName: "FilePathCheckerModule"; ValueData: "{app}\FilePathCheckerModule.dll"; Flags: uninsdeletevalue; Tasks: secmodule

[Run]
; 설치 직후 실행 여부 선택
Filename: "{app}\python\pythonw.exe"; Parameters: "hwp_agent.py"; WorkingDir: "{app}"; Description: "지금 에이전트 실행"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 제거 시 실행 중인 에이전트 종료(있으면). 조용히 실패 허용.
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM pythonw.exe /FI ""WINDOWTITLE eq ZZAIMY*"""; Flags: runhidden; RunOnceId: "killagent"

[UninstallDelete]
; 실행 중 생성된 잔여물 정리
Type: filesandordirs; Name: "{app}\__pycache__"

[Code]
{ 재설치·업그레이드 시 실행 중인 에이전트를 먼저 종료해 파일 교체가 막히지 않게 한다. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/C taskkill /F /IM pythonw.exe', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
