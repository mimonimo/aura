@echo off
REM ============================================================================
REM  ZZAIMY 한글 편집 에이전트 - Windows 단일 exe 빌드 스크립트
REM ============================================================================
REM  이 배치는 Windows 에서만 동작한다. macOS/Linux 에서는 exe 를 만들 수 없다
REM  (PyInstaller 는 크로스빌드 불가 - 대상 OS 에서 빌드해야 한다).
REM
REM  하는 일:
REM    1) pyinstaller / pywin32 를 pip 로 설치 (오프라인 환경이면 사전 설치)
REM    2) hwp_agent.spec 로 onefile exe 빌드
REM    3) dist\zzaimy-hwp-agent.exe 생성 안내
REM
REM  사용:  이 파일이 있는 폴더(tools\hwp-agent)에서 더블클릭하거나
REM         명령 프롬프트에서:  build_exe.bat
REM ============================================================================

setlocal
cd /d "%~dp0"

echo [1/3] pyinstaller / pywin32 설치 확인...
python -m pip install pyinstaller pywin32
if errorlevel 1 (
    echo.
    echo [실패] pip 설치에 실패했습니다. 파이썬/네트워크를 확인하세요.
    echo        오프라인 환경이면 pyinstaller, pywin32 를 미리 설치해 두세요.
    goto :end
)

echo.
echo [2/3] onefile exe 빌드 (pyinstaller hwp_agent.spec)...
python -m PyInstaller --noconfirm --clean hwp_agent.spec
if errorlevel 1 (
    echo.
    echo [실패] 빌드에 실패했습니다. 위 로그를 확인하세요.
    goto :end
)

echo.
echo [3/3] 완료.
echo   산출물: %~dp0dist\zzaimy-hwp-agent.exe
echo.
echo 실행 전에 exe 와 같은 폴더에 config.json (서버/토큰/인증서)을 두세요.
echo   - config.json 은 플랫폼 zip 번들 안의 것을 그대로 복사해 쓰면 됩니다.
echo   - 서버 자체 서명 인증서(server.crt)도 config.json 에서 가리키면 옆에 둡니다.
echo 자세한 절차는 README-exe.md 를 보세요.

:end
echo.
pause
endlocal
