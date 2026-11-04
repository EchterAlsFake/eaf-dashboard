import pytest
from flask import Flask

from dashboard.auth import LoginLimiter
from dashboard.client_address import client_address
from dashboard.config import Settings
from dashboard.store import Store
from dashboard.app import create_app


@pytest.mark.parametrize("peer,forwarded,expected", [
    ("10.89.10.10", "203.0.113.1", "203.0.113.1"),
    ("10.89.10.11", "203.0.113.1", "10.89.10.11"),
    ("127.0.0.1", "2001:db8::1", "2001:db8::1"),
    ("10.89.10.10", "203.0.113.1, 203.0.113.2", "10.89.10.10"),
    ("10.89.10.10", "invalid", "10.89.10.10"),
])
def test_forwarded_identity_requires_trusted_peer(peer, forwarded, expected):
    app = Flask(__name__)
    with app.test_request_context(headers={"X-Forwarded-For": forwarded},
                                  environ_base={"REMOTE_ADDR": peer}):
        assert client_address(("127.0.0.1/32", "10.89.10.10/32")) == expected


def test_container_visitors_are_distinct_and_deduplicated(tmp_path):
    settings = Settings(tmp_path, tmp_path / "dashboard.db", "test-secret-that-is-at-least-32-characters",
                        "pornfetch.to", "https://pornfetch.to", tmp_path / "missing.sock", False,
                        ("10.89.10.10/32",))
    app = create_app(settings)
    client = app.test_client()
    for address in ["203.0.113.1", "203.0.113.2", "203.0.113.1"]:
        response = client.post("/__eaf/visit", json={},
            headers={"Host": "pornfetch.to", "Origin": "https://pornfetch.to", "X-Forwarded-For": address},
            environ_base={"REMOTE_ADDR": "10.89.10.10"})
        assert response.status_code == 204
    assert Store(settings.database_path).usage(1)[0]["value"] == 2


def test_login_limits_use_same_trusted_proxy_identity():
    app = Flask(__name__)
    limiter = LoginLimiter("test-secret", ("10.89.10.10/32",))
    keys = []
    for address in ["203.0.113.1", "203.0.113.2"]:
        with app.test_request_context(headers={"X-Forwarded-For": address},
                                      environ_base={"REMOTE_ADDR": "10.89.10.10"}):
            keys.append(limiter._keys())
    assert keys[0][0] != keys[1][0]
    assert keys[0][1] == keys[1][1]


def test_proxy_config_is_validated(monkeypatch, tmp_path):
    monkeypatch.setenv("EAF_DASHBOARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EAF_DASHBOARD_TRUSTED_PROXIES", "10.89.10.10/32,::1/128")
    assert Settings.load(require_secret=False).trusted_proxies == ("10.89.10.10/32", "::1/128")
    monkeypatch.setenv("EAF_DASHBOARD_TRUSTED_PROXIES", "garbage")
    with pytest.raises(ValueError):
        Settings.load(require_secret=False)
