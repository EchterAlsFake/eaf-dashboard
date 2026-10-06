from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dashboard.app import create_app
from dashboard.auth import PASSWORD_HASHER
from dashboard.config import Settings
from dashboard.store import Store


@pytest.fixture()
def app(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "dashboard.db",
        secret_key="test-secret-key-that-is-long-enough",
        rp_id="echteralsfake.me",
        origin="https://echteralsfake.me",
        broker_socket=tmp_path / "missing.sock",
        secure_cookie=False,
    )
    store = Store(settings.database_path)
    store.initialize()
    store.set_password(PASSWORD_HASHER.hash("correct horse battery staple"))
    application = create_app(settings)
    application.config.update(TESTING=True)
    return application


def test_login_requires_correct_password(app):
    client = app.test_client()
    invalid = client.post(
        "/dashboard/api/auth/password",
        json={"password": "wrong"},
        headers={"Origin": "https://echteralsfake.me"},
    )
    assert invalid.status_code == 401
    valid = client.post(
        "/dashboard/api/auth/password",
        json={"password": "correct horse battery staple"},
        headers={"Origin": "https://echteralsfake.me"},
    )
    assert valid.status_code == 200


def test_cross_origin_mutation_is_rejected(app):
    client = app.test_client()
    response = client.post(
        "/dashboard/api/auth/password",
        json={"password": "correct horse battery staple"},
        headers={"Origin": "https://attacker.invalid"},
    )
    assert response.status_code == 403
    assert client.post(
        "/dashboard/api/auth/password",
        json={"password": "correct horse battery staple"},
    ).status_code == 403


def test_dashboard_and_records_require_authentication(app):
    client = app.test_client()
    assert client.get("/dashboard/api/overview").status_code == 401
    assert client.get("/dashboard/api/errors").status_code == 401
    assert client.get("/dashboard/api/usage").status_code == 401
    assert client.get("/dashboard/api/revenue").status_code == 401


def test_visit_is_host_scoped_and_deduplicated(app):
    client = app.test_client()
    headers = {"Host": "echteralsfake.me", "Origin": "https://echteralsfake.me"}
    assert client.post("/__eaf/visit", json={"route": "home"}, headers=headers).status_code == 204
    duplicate = client.post("/__eaf/visit", json={"route": "home"}, headers=headers)
    assert duplicate.status_code == 204
    assert "Set-Cookie" not in duplicate.headers
    settings = app.extensions["dashboard_settings"]
    usage = Store(settings.database_path).usage(2)
    assert len(usage) == 1
    assert usage[0]["value"] == 1
    assert usage[0]["route_group"] == "page"
    assert client.post(
        "/__eaf/visit",
        json={"route": "schedule"},
        headers={"Host": "vplan.echteralsfake.me", "Origin": "https://vplan.echteralsfake.me"},
    ).status_code == 204
    assert client.get(
        "/dashboard/static/visit.js", headers={"Host": "docs.echteralsfake.me"}
    ).status_code == 200
    assert client.post("/__eaf/visit", json={"route": "home"}, headers={"Host": "brain.echteralsfake.me"}).status_code == 400


def test_visit_accepts_configured_production_domain(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        database_path=tmp_path / "dashboard.db",
        secret_key="test-secret-key-that-is-long-enough",
        rp_id="pornfetch.to",
        origin="https://pornfetch.to",
        broker_socket=tmp_path / "missing.sock",
        secure_cookie=False,
    )
    application = create_app(settings)
    application.config.update(TESTING=True)
    client = application.test_client()
    headers = {"Host": "pornfetch.to", "Origin": "https://pornfetch.to"}
    assert client.post("/__eaf/visit", json={"route": "home"}, headers=headers).status_code == 204
    usage = Store(application.extensions["dashboard_settings"].database_path).usage(1)
    assert any(item["host"] == "pornfetch.to" for item in usage)


def test_bootstrap_tokens_are_single_use(tmp_path):
    store = Store(tmp_path / "state.db")
    store.initialize()
    token = store.new_bootstrap_token()
    assert store.consume_bootstrap_token(token)
    assert store.consume_bootstrap_token(token, mark=True)
    assert not store.consume_bootstrap_token(token)


def authenticated_client(app):
    client = app.test_client()
    store = Store(app.extensions["dashboard_settings"].database_path)
    now = int(time.time())
    with client.session_transaction() as current:
        current["authenticated_at"] = now
        current["last_active_at"] = now
        current["step_up_at"] = now
        current["auth_generation"] = store.session_generation()
        current["csrf_token"] = "test-csrf"
    return client, store


def test_password_change_revokes_existing_sessions(app):
    client, store = authenticated_client(app)
    assert client.get("/dashboard/api/overview").status_code == 200
    store.set_password(PASSWORD_HASHER.hash("a newly rotated administrator password"))
    assert client.get("/dashboard/api/overview").status_code == 401


def test_security_headers_and_host_validation(app):
    response = app.test_client().get("/dashboard/login")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Embedder-Policy"] == "require-corp"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "object-src 'none'" in response.headers["Content-Security-Policy"]
    assert app.test_client().get(
        "/dashboard/login", headers={"Host": "attacker.invalid"}
    ).status_code == 400


def test_inputs_fail_closed_and_broker_errors_are_generic(app):
    client, _ = authenticated_client(app)
    assert client.get("/dashboard/api/usage?days=not-a-number").status_code == 400
    assert client.get("/dashboard/api/usage?days=7301").status_code == 400
    assert client.post(
        "/dashboard/api/actions/backup",
        json=[],
        headers={"Origin": "https://echteralsfake.me", "X-EAF-CSRF": "test-csrf"},
    ).status_code == 400
    overview = client.get("/dashboard/api/overview")
    assert overview.get_json()["error"] == "data_source_unavailable"
    assert client.get("/dashboard/api/metrics/import").status_code == 404


def test_security_events_and_challenges_are_bounded(tmp_path):
    store = Store(tmp_path / "state.db")
    store.initialize()
    store.security_event("password_failure")
    assert store.security_summary()["password_failure"] == 1
    for index in range(120):
        store.put_challenge("login", f"challenge-{index}".encode())
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM login_challenge").fetchone()[0] == 100


def test_store_initialization_is_concurrency_safe(tmp_path):
    database = tmp_path / "concurrent.db"
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _index: Store(database).initialize(), range(24)))
    assert Store(database).session_generation() is None


def test_request_counters_import_initial_value_and_survive_reset(tmp_path):
    store = Store(tmp_path / "metrics.db")
    store.initialize()
    store.record_counter_delta("caddy:http_requests:pornfetch.to", "pornfetch.to", 60)
    store.record_counter_delta("caddy:http_requests:pornfetch.to", "pornfetch.to", 63)
    store.record_counter_delta("caddy:http_requests:pornfetch.to", "pornfetch.to", 2)
    requests = [item for item in store.usage(1) if item["metric"] == "requests"]
    assert requests == [{
        "day": requests[0]["day"],
        "host": "pornfetch.to",
        "route_group": "api",
        "metric": "requests",
        "value": 65,
    }]


def test_metrics_configuration_does_not_require_authentication_secret(monkeypatch, tmp_path):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("EAF_DASHBOARD_DATA_DIR", str(tmp_path))
    settings = Settings.load(require_secret=False)
    assert settings.secret_key == ""
    assert settings.database_path == tmp_path / "dashboard.db"


def test_node_federation_store_and_telemetry(tmp_path):
    store = Store(tmp_path / "federation.db")
    store.initialize()
    # Default origin node should exist
    nodes = store.list_nodes()
    assert any(n["id"] == "node-origin" for n in nodes)

    # Upsert a new node
    created = store.upsert_node(
        id="node-blog-srv",
        name="Blog Server (Acer)",
        address="192.168.0.45",
        role="web-server",
        probe_url="http://192.168.0.45:3000/api/health",
    )
    assert created["id"] == "node-blog-srv"
    assert created["name"] == "Blog Server (Acer)"

    # Record telemetry
    store.record_node_telemetry(
        node_id="node-blog-srv",
        status="online",
        cpu_percent=12.5,
        memory_used_mb=2048,
        memory_total_mb=8192,
        services=[{"name": "Blog", "status": "up", "latency_ms": 4.2}],
        metrics={"active_sessions": 30},
    )

    with_telemetry = store.list_nodes_with_telemetry()
    blog_node = next(n for n in with_telemetry if n["id"] == "node-blog-srv")
    assert blog_node["status"] == "online"
    assert blog_node["telemetry"]["cpu_percent"] == 12.5
    assert blog_node["telemetry"]["services"][0]["name"] == "Blog"

    # Delete node
    assert store.delete_node("node-blog-srv") is True
    # Origin node cannot be deleted
    assert store.delete_node("node-origin") is False


def test_heartbeat_api_and_embed_data(app):
    client = app.test_client()

    # Heartbeat from a remote server (no CSRF or session required)
    heartbeat_payload = {
        "node_id": "remote-app-1",
        "name": "E-Commerce App",
        "address": "192.168.0.80",
        "role": "web-server",
        "status": "online",
        "cpu_percent": 24.1,
        "memory_used_mb": 1500,
        "memory_total_mb": 4096,
        "disk_used_gb": 20.0,
        "disk_total_gb": 100.0,
        "services": [{"name": "Shop", "url": "http://192.168.0.80:80", "status": "up"}],
    }
    resp = client.post("/dashboard/api/nodes/heartbeat", json=heartbeat_payload)
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    # Public embed data endpoint (with CORS headers)
    embed_resp = client.get("/dashboard/api/embed/data")
    assert embed_resp.status_code == 200
    assert embed_resp.headers.get("Access-Control-Allow-Origin") == "*"
    data = embed_resp.get_json()
    assert "cluster" in data
    assert "services" in data

    # Embed view template (iframe-embeddable)
    view_resp = client.get("/dashboard/embed/view?widget=card")
    assert view_resp.status_code == 200
    assert "X-Frame-Options" not in view_resp.headers  # Must allow framing
    assert "frame-ancestors *" in view_resp.headers.get("Content-Security-Policy", "")

