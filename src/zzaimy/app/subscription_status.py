"""구독 CLI 인증 상태만 조회한다. 인증 토큰·원 출력·계정 정보는 반환하지 않는다."""
import json
import os
import shutil
import subprocess


def probe(provider: str) -> dict:
    if provider not in ('claude', 'codex'):
        raise ValueError('지원하지 않는 제공자')
    executable = shutil.which(provider)
    result = {'provider': provider, 'installed': bool(executable), 'state': 'missing'}
    if not executable:
        return result
    env = {k: v for k, v in os.environ.items() if k not in (
        'OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_API_KEY',
        'ANTHROPIC_AUTH_TOKEN', 'CODEX_ACCESS_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN',
    )}
    args = ['auth', 'status', '--json'] if provider == 'claude' else ['login', 'status']
    try:
        response = subprocess.run([executable, *args], capture_output=True, text=True,
                                  timeout=5, env=env, stdin=subprocess.DEVNULL)
        if provider == 'claude':
            data = json.loads(response.stdout)
            method = str(data.get('authMethod', '')).lower()
            signed_in = data.get('loggedIn') is True and response.returncode == 0
            subscription = signed_in and method in ('claude.ai', 'oauth')
        else:
            # CLI status may write to stderr. Never expose either stream to the page.
            text = (response.stdout + response.stderr).lower()
            signed_in = response.returncode == 0
            subscription = signed_in and 'chatgpt' in text and 'api key' not in text
        result['state'] = 'authenticated' if subscription else ('unsupported_auth' if signed_in else 'signed_out')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        result['state'] = 'unknown'
    return result
