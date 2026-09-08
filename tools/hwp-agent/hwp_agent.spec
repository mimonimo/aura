# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onefile 스펙 — ZZAIMY 한글 편집 에이전트.

hwp_agent.py 를 단일 실행 파일(zzaimy-hwp-agent.exe)로 묶는다.

  - onefile: 부속 파일 없이 exe 하나로 배포(내부는 실행 시 임시 폴더에 풀린다).
  - console=True: 콘솔 창을 유지해 서버 연결·명령 처리 로그가 그대로 보인다.
  - config.json 은 exe 에 넣지 않는다. 사용자가 exe '옆'에 두면 실행 시 읽는다
    (hwp_agent._base_dir() 가 sys.frozen 일 때 sys.executable 폴더를 본다).
    서버·토큰마다 config.json 이 다르므로 exe 는 config-불포함 상태로 재사용한다.

빌드 환경(중요):
  이 스펙은 반드시 Windows(대상 OS)에서 빌드해야 한다. PyInstaller 는
  크로스빌드를 지원하지 않는다 — macOS/Linux 에서 만들면 Windows exe 가 아니다.
  개발 머신(macOS)에서는 exe 를 만들 수 없고, 이 스펙과 build_exe.bat 만
  준비한다. 실제 exe 는 Windows 워커에서 build_exe.bat 로 생성한다.

실행:  pyinstaller hwp_agent.spec      (또는 build_exe.bat 더블클릭)
산출:  dist/zzaimy-hwp-agent.exe
"""

block_cipher = None


a = Analysis(
    ['hwp_agent.py'],
    pathex=[],
    binaries=[],
    datas=[],
    # COM 백엔드가 쓰는 win32com/comtypes 는 동적 임포트라 자동 탐지가
    # 놓칠 수 있어 명시한다. 둘 중 설치된 것만 번들에 포함된다.
    hiddenimports=['win32com', 'win32com.client', 'pythoncom', 'pywintypes'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 서버·토큰은 config.json(외부 파일)에서 읽으므로 아무것도 심지 않는다.
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='zzaimy-hwp-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # 로그가 보이도록 콘솔 유지
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
