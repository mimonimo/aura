from html.parser import HTMLParser

from fastapi.testclient import TestClient

from zzaimy.app.main import create_app
from test_app import FakeDrafter, FakeProcessor


def test_login_has_no_preset_credentials(tmp_path):
    class Inputs(HTMLParser):
        def __init__(self):
            super().__init__()
            self.fields = {}

        def handle_starttag(self, tag, attrs):
            if tag == "input":
                field = dict(attrs)
                self.fields[field.get("name")] = field

    app = create_app(db_path=tmp_path / "login.db", inbox_dir=tmp_path / "inbox",
                     processor=FakeProcessor(), drafter=FakeDrafter(), password="test-only-password")
    client = TestClient(app)
    for url in ("/login", "/login?err=1"):
        response = client.get(url)
        assert response.status_code == 200
        parser = Inputs()
        parser.feed(response.text)
        for name in ("username", "pw"):
            assert not parser.fields[name].get("value")
        assert "autofocus" in parser.fields["username"]
        assert "autofocus" not in parser.fields["pw"]
        assert parser.fields["username"]["autocomplete"] == "username"
