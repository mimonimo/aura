import json
from types import SimpleNamespace
import pytest
from zzaimy.app import subscription_status as module


@pytest.mark.parametrize('provider,payload,expected', [
    ('claude', json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'email': 'private@example.com'}), 'authenticated'),
    ('claude', json.dumps({'loggedIn': True, 'authMethod': 'api_key'}), 'unsupported_auth'),
    ('codex', 'Logged in using ChatGPT', 'authenticated'),
    ('codex', 'Logged in using an API key: secret', 'unsupported_auth'),
])
def test_status_sanitizes_output_and_disallows_api(monkeypatch, provider, payload, expected):
    monkeypatch.setenv('OPENAI_API_KEY', 'secret')
    monkeypatch.setattr(module.shutil, 'which', lambda p: '/bin/' + p)
    def run(args, **kwargs):
        assert kwargs['stdin'] == module.subprocess.DEVNULL
        assert 'OPENAI_API_KEY' not in kwargs['env']
        return SimpleNamespace(returncode=0, stdout=payload, stderr='')
    monkeypatch.setattr(module.subprocess, 'run', run)
    result = module.probe(provider)
    assert result['state'] == expected
    assert 'secret' not in json.dumps(result) and 'email' not in json.dumps(result)


def test_missing_cli_and_invalid_provider(monkeypatch):
    monkeypatch.setattr(module.shutil, 'which', lambda p: None)
    assert module.probe('codex')['state'] == 'missing'
    with pytest.raises(ValueError):
        module.probe('sh')


def test_legacy_api_is_disabled_even_with_old_flag(monkeypatch):
    from zzaimy.app import egress
    monkeypatch.setenv('ZZAIMY_EXTERNAL_ENABLED', '1')
    assert egress.external_status()[0] is False
    with pytest.raises(RuntimeError, match='API 호출 미사용'):
        egress._send_external('must not send')
