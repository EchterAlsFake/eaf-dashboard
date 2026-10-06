from __future__ import annotations

import io
import json
import socket
import sqlite3
import threading
from pathlib import Path

import pytest

from dashboard.broker_server import (
    Broker,
    BrokerServer,
    BrokerSettings,
    OperationError,
)


def create_server_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE "transaction" (
                session_id TEXT PRIMARY KEY,
                provider_payment_id TEXT,
                provider_reference_type TEXT,
                environment TEXT,
                expected_price_amount TEXT,
                expected_price_currency TEXT,
                expected_pay_amount TEXT,
                expected_pay_currency TEXT,
                status TEXT,
                created_at TEXT,
                finished_at TEXT
            );
            CREATE TABLE license (
                license_key TEXT PRIMARY KEY,
                state TEXT,
                issuance_reference TEXT,
                created_at TEXT,
                keygen_id TEXT
            );
            CREATE TABLE checklist (
                id INTEGER PRIMARY KEY,
                task TEXT,
                is_done INTEGER,
                created_at TEXT
            );
            INSERT INTO "transaction" VALUES
              ('session-1','payment-1','invoice','production','9.99','EUR','0.1','BTC',
               'finished','2099-01-01T10:00:00+00:00','2099-01-01T10:05:00+00:00');
            INSERT INTO license VALUES ('key-1','valid','session-1','2099-01-01',NULL);
            INSERT INTO checklist VALUES (1,'Ship it',0,'2099-01-01');
            """
        )


def create_pocketbase_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE error_logs (id TEXT PRIMARY KEY,message TEXT,created TEXT,updated TEXT);
            INSERT INTO error_logs VALUES ('error-1','redacted report','2099-01-01','2099-01-01');
            """
        )


@pytest.fixture()
def broker(tmp_path: Path) -> Broker:
    server = tmp_path / "server.db"
    pocketbase = tmp_path / "pocketbase.db"
    create_server_database(server)
    create_pocketbase_database(pocketbase)
    return Broker(
        BrokerSettings(
            socket_path=tmp_path / "run" / "broker.sock",
            server_database=server,
            dashboard_database=None,
            pocketbase_database=pocketbase,
            backup_directory=tmp_path / "backups",
            caddy_metrics_url="http://127.0.0.1:2019/metrics",
            services={"web": "pornfetch-web.service"},
        )
    )


def test_broker_reads_and_mutates_allowlisted_records(broker: Broker):
    assert broker.list_payments()[0]["session_id"] == "session-1"
    assert broker.list_licenses()[0]["state"] == "valid"
    assert broker.list_pocketbase("error_logs")[0]["message"] == "redacted report"
    assert broker.list_pocketbase("feedback") == []

    assert broker.toggle_checklist({"id": 1, "is_done": True}) == {"updated": 1}
    assert broker.list_checklist()[0]["is_done"] == 1
    assert broker.delete_pocketbase("error_logs", {"id": "error-1"}) == {"deleted": 1}


def test_broker_rejects_unknown_operations_and_services(broker: Broker):
    with pytest.raises(OperationError, match="not allowed"):
        broker.dispatch("run_shell", {"command": "id"})
    with pytest.raises(OperationError, match="not allowed"):
        broker.restart_service({"service": "attacker-controlled.service"})


def test_caddy_metrics_are_aggregated_once_per_host(monkeypatch, broker: Broker):
    metrics = b"""# TYPE caddy_http_requests_total counter
caddy_http_requests_total{code="200",handler="reverse_proxy",host="pornfetch.to",method="GET",server="srv0"} 12
caddy_http_requests_total{code="404",handler="reverse_proxy",host="pornfetch.to",method="GET",server="srv0"} 3
caddy_http_requests_total{code="200",handler="encode",host="pornfetch.to",method="GET",server="srv0"} 12
caddy_http_requests_total{handler="subroute",host="docs.pornfetch.to",server="srv0"} 9
"""

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(
        "dashboard.broker_server.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(metrics),
    )
    assert broker.caddy_metrics() == [
        {"series": "caddy:http_requests:docs.pornfetch.to", "host": "docs.pornfetch.to", "value": 9.0},
        {"series": "caddy:http_requests:pornfetch.to", "host": "pornfetch.to", "value": 15.0},
    ]


def test_caddy_metrics_do_not_double_count_multiple_handlers(monkeypatch, broker: Broker):
    metrics = b"""caddy_http_requests_total{handler="subroute",host="pornfetch.to",server="srv0"} 20
caddy_http_requests_total{handler="reverse_proxy",host="pornfetch.to",server="srv0"} 18
"""

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(
        "dashboard.broker_server.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(metrics),
    )
    assert broker.caddy_metrics() == [
        {"series": "caddy:http_requests:pornfetch.to", "host": "pornfetch.to", "value": 20.0}
    ]


def test_backup_uses_sqlite_snapshot_api(broker: Broker):
    result = broker.backup()
    backup = Path(result["backup"])
    assert (backup / "server.db").is_file()
    assert (backup / "pocketbase.db").is_file()
    with sqlite3.connect(backup / "server.db") as connection:
        assert connection.execute('SELECT count(*) FROM "transaction"').fetchone()[0] == 1


def test_wire_protocol_matches_dashboard_client(broker: Broker):
    server_side, client_side = socket.socketpair()
    thread = threading.Thread(target=BrokerServer(broker)._handle, args=(server_side,))
    thread.start()
    client_side.sendall(json.dumps({"operation": "list_checklist", "arguments": {}}).encode() + b"\n")
    response = json.loads(client_side.makefile("rb").readline())
    thread.join(timeout=2)
    assert response["ok"] is True
    assert response["result"][0]["task"] == "Ship it"


def test_server_binds_path_as_string(monkeypatch, broker: Broker):
    class FakeListener:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def bind(self, address):
            assert isinstance(address, str)
            raise StopIteration

    monkeypatch.setattr("dashboard.broker_server.socket.socket", lambda *_args: FakeListener())
    with pytest.raises(StopIteration):
        BrokerServer(broker).serve_forever()


def test_system_vitals_returns_metrics(broker: Broker):
    vitals = broker.system_vitals()
    assert "cpu" in vitals
    assert "memory" in vitals
    assert "disk" in vitals
    assert "uptime_seconds" in vitals
    assert vitals["cpu"]["count"] >= 1
    assert vitals["memory"]["total_mb"] >= 0


def test_scan_lan_returns_devices(broker: Broker):
    devices = broker.scan_lan()
    assert isinstance(devices, list)


def test_probe_node_handles_valid_and_invalid(monkeypatch, broker: Broker):
    with pytest.raises(OperationError, match="valid probe url required"):
        broker.probe_node({"url": "invalid-url"})

    class Response(io.BytesIO):
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(
        "dashboard.broker_server.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(b'{"status":"ok"}'),
    )
    result = broker.probe_node({"url": "http://192.168.0.45:3000/health"})
    assert result["ok"] is True
    assert result["status_code"] == 200
    assert result["data"] == {"status": "ok"}

