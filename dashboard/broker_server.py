"""Least-privileged local data and service broker for the dashboard.

The web application runs in a locked-down container.  This broker runs as the
same unprivileged host user that owns the rootless Podman services and exposes
only the small, allowlisted JSON protocol used by :mod:`dashboard.broker_client`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


MAX_REQUEST = 64 * 1024
MAX_RESPONSE = 4 * 1024 * 1024
DEFAULT_SERVICES = {
    "web": "pornfetch-web.service",
    "dashboard": "eaf-dashboard.service",
    "licensing-api": "keygen-api-runtime.service",
    "licensing-worker": "keygen-worker.service",
    "licensing-portal": "keygen-portal.service",
    "error-intake": "pocketbase.service",
    "database": "keygen-postgres.service",
    "queue": "keygen-redis.service",
}
PROMETHEUS_SAMPLE = re.compile(
    r'^caddy_http_requests_total\{(?P<labels>[^}]*)\}\s+(?P<value>[0-9.eE+-]+)$'
)
PROMETHEUS_LABEL = re.compile(r'(\w+)="((?:\\.|[^"\\])*)"')


class OperationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BrokerSettings:
    socket_path: Path
    server_database: Path
    dashboard_database: Path | None
    pocketbase_database: Path | None
    backup_directory: Path
    caddy_metrics_url: str
    services: dict[str, str]

    @classmethod
    def load(cls) -> "BrokerSettings":
        raw_services = os.environ.get("EAF_BROKER_SERVICES", "").strip()
        services = dict(DEFAULT_SERVICES)
        if raw_services:
            services = {}
            for entry in raw_services.split(","):
                name, separator, unit = entry.partition("=")
                if not separator or not name.strip() or not unit.strip():
                    raise RuntimeError("EAF_BROKER_SERVICES must contain name=unit entries")
                services[name.strip()] = unit.strip()

        def optional_path(name: str) -> Path | None:
            value = os.environ.get(name, "").strip()
            return Path(value) if value else None

        return cls(
            socket_path=Path(
                os.environ.get("EAF_BROKER_SOCKET", "/run/user/1001/eaf-dashboard/broker.sock")
            ),
            server_database=Path(
                os.environ.get("EAF_BROKER_SERVER_DB", "/var/lib/pornfetch/server.db")
            ),
            dashboard_database=optional_path("EAF_BROKER_DASHBOARD_DB"),
            pocketbase_database=optional_path("EAF_BROKER_POCKETBASE_DB"),
            backup_directory=Path(
                os.environ.get("EAF_BROKER_BACKUP_DIR", "/var/lib/pornfetch/backups")
            ),
            caddy_metrics_url=os.environ.get(
                "EAF_BROKER_CADDY_METRICS_URL", "http://127.0.0.1:2019/metrics"
            ),
            services=services,
        )


def _connect(path: Path, *, writable: bool = False) -> sqlite3.Connection:
    if not path.is_file():
        raise OperationError(f"database is unavailable: {path.name}")
    if writable:
        connection = sqlite3.connect(path, timeout=15)
    else:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
        connection.execute("PRAGMA query_only=ON")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    return connection


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _rows(connection: sqlite3.Connection, query: str, parameters: tuple = ()) -> list[dict]:
    return [dict(row) for row in connection.execute(query, parameters)]


def _count_by(connection: sqlite3.Connection, table: str, field: str) -> list[dict]:
    if not _table_exists(connection, table):
        return []
    return _rows(
        connection,
        f'SELECT "{field}" AS status,count(*) AS count FROM "{table}" '
        f'GROUP BY "{field}" ORDER BY "{field}"',
    )


class Broker:
    def __init__(self, settings: BrokerSettings):
        self.settings = settings

    def dispatch(self, operation: str, arguments: dict[str, Any]) -> Any:
        handlers = {
            "overview": self.overview,
            "system_vitals": self.system_vitals,
            "probe_node": lambda: self.probe_node(arguments),
            "scan_lan": self.scan_lan,
            "list_payments": self.list_payments,
            "list_licenses": self.list_licenses,
            "list_checklist": self.list_checklist,
            "list_errors": lambda: self.list_pocketbase("error_logs"),
            "list_feedback": lambda: self.list_pocketbase("feedback"),
            "delete_error": lambda: self.delete_pocketbase("error_logs", arguments),
            "delete_feedback": lambda: self.delete_pocketbase("feedback", arguments),
            "toggle_checklist": lambda: self.toggle_checklist(arguments),
            "delete_checklist": lambda: self.delete_checklist(arguments),
            "restart_service": lambda: self.restart_service(arguments),
            "backup": self.backup,
            "caddy_metrics": self.caddy_metrics,
            "revenue": self.revenue,
        }
        handler = handlers.get(operation)
        if handler is None:
            raise OperationError("operation is not allowed")
        return handler()

    def service_status(self) -> list[dict]:
        result = []
        for name, unit in self.settings.services.items():
            command = subprocess.run(
                ["systemctl", "--user", "show", unit, "--property=ActiveState,SubState,MemoryCurrent", "--no-pager"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            properties = {"ActiveState": "unknown", "SubState": "unavailable", "MemoryCurrent": "0"}
            for line in command.stdout.splitlines():
                key, separator, value = line.partition("=")
                if separator and key in properties:
                    properties[key] = value
            mem_bytes = int(properties.get("MemoryCurrent") or 0)
            mem_mb = round(mem_bytes / (1024 * 1024), 1) if mem_bytes else 0
            result.append({
                "name": name,
                "unit": unit,
                "ActiveState": properties["ActiveState"],
                "SubState": properties["SubState"],
                "memory_mb": mem_mb,
            })
        return result

    def system_vitals(self) -> dict:
        try:
            load1, load5, load15 = os.getloadavg()
        except OSError:
            load1 = load5 = load15 = 0.0

        mem = {}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        mem[parts[0].strip()] = int(parts[1].strip().split()[0])
        except OSError:
            pass
        mem_total = mem.get("MemTotal", 0) // 1024
        mem_avail = mem.get("MemAvailable", 0) // 1024
        mem_free = mem.get("MemFree", 0) // 1024
        mem_buffers = mem.get("Buffers", 0) // 1024
        mem_cached = mem.get("Cached", 0) // 1024
        mem_used = max(0, mem_total - mem_avail)
        swap_total = mem.get("SwapTotal", 0) // 1024
        swap_free = mem.get("SwapFree", 0) // 1024
        swap_used = max(0, swap_total - swap_free)

        try:
            disk = shutil.disk_usage("/")
            disk_total_gb = round(disk.total / (1024**3), 1)
            disk_used_gb = round(disk.used / (1024**3), 1)
            disk_free_gb = round(disk.free / (1024**3), 1)
            disk_percent = round((disk.used / max(1, disk.total)) * 100, 1)
        except OSError:
            disk_total_gb = disk_used_gb = disk_free_gb = disk_percent = 0.0

        uptime_seconds = 0
        try:
            with open("/proc/uptime") as f:
                uptime_seconds = int(float(f.readline().split()[0]))
        except (OSError, ValueError):
            pass

        cpu_count = os.cpu_count() or 1
        cpu_percent = min(100.0, round((load1 / cpu_count) * 100, 1))

        interfaces = {}
        try:
            with open("/proc/net/dev") as f:
                for line in f:
                    if ":" in line:
                        iface, rest = line.split(":", 1)
                        iface = iface.strip()
                        fields = rest.split()
                        if len(fields) >= 16:
                            interfaces[iface] = {
                                "rx_bytes": int(fields[0]),
                                "tx_bytes": int(fields[8]),
                                "rx_mb": round(int(fields[0]) / (1024 * 1024), 1),
                                "tx_mb": round(int(fields[8]) / (1024 * 1024), 1),
                            }
        except OSError:
            pass

        return {
            "hostname": socket.gethostname(),
            "os": "Arch Linux",
            "lan_ip": "192.168.0.11",
            "uptime_seconds": uptime_seconds,
            "cpu": {
                "count": cpu_count,
                "percent": cpu_percent,
                "load_1m": round(load1, 2),
                "load_5m": round(load5, 2),
                "load_15m": round(load15, 2),
            },
            "memory": {
                "total_mb": mem_total,
                "used_mb": mem_used,
                "free_mb": mem_free,
                "avail_mb": mem_avail,
                "buffers_mb": mem_buffers,
                "cached_mb": mem_cached,
                "percent": round((mem_used / max(1, mem_total)) * 100, 1) if mem_total else 0,
                "swap_total_mb": swap_total,
                "swap_used_mb": swap_used,
            },
            "disk": {
                "total_gb": disk_total_gb,
                "used_gb": disk_used_gb,
                "free_gb": disk_free_gb,
                "percent": disk_percent,
            },
            "network": interfaces,
        }

    def probe_node(self, arguments: dict[str, Any]) -> dict:
        url = arguments.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise OperationError("valid probe url required")
        start = time.perf_counter()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "EAF-Node-Probe/1.0"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
                body = resp.read(64 * 1024).decode("utf-8", "ignore")
                is_json = "application/json" in resp.headers.get("Content-Type", "")
                data = None
                if is_json:
                    try:
                        data = json.loads(body)
                    except ValueError:
                        pass
                return {
                    "ok": True,
                    "status_code": resp.status,
                    "latency_ms": elapsed_ms,
                    "data": data,
                }
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            return {
                "ok": False,
                "error": str(exc),
                "latency_ms": elapsed_ms,
            }

    def scan_lan(self) -> list[dict]:
        devices = []
        try:
            with open("/proc/net/arp") as f:
                lines = f.readlines()
                for line in lines[1:]:
                    parts = line.split()
                    if len(parts) >= 6:
                        ip = parts[0]
                        mac = parts[3]
                        device = parts[5]
                        if ip.startswith("192.168.0.") and mac != "00:00:00:00:00:00":
                            devices.append({
                                "ip": ip,
                                "mac": mac,
                                "interface": device,
                                "is_gateway": (ip == "192.168.0.1"),
                            })
        except OSError:
            pass
        return devices

    def overview(self) -> dict:
        with _connect(self.settings.server_database) as connection:
            payments = _count_by(connection, "transaction", "status")
            license_rows = _count_by(connection, "license", "state")
        errors = self.pocketbase_count("error_logs")
        feedback = self.pocketbase_count("feedback")
        return {
            "services": self.service_status(),
            "payments": payments,
            "licenses": {row["status"]: row["count"] for row in license_rows},
            "errors": errors,
            "feedback": feedback,
            "vitals": self.system_vitals(),
        }

    def list_payments(self) -> list[dict]:
        with _connect(self.settings.server_database) as connection:
            if not _table_exists(connection, "transaction"):
                return []
            return _rows(
                connection,
                'SELECT session_id,provider_payment_id,provider_reference_type,environment,'
                'expected_price_amount,expected_price_currency,expected_pay_amount,'
                'expected_pay_currency,status,created_at,finished_at FROM "transaction" '
                "ORDER BY created_at DESC LIMIT 500",
            )

    def list_licenses(self) -> list[dict]:
        with _connect(self.settings.server_database) as connection:
            if not _table_exists(connection, "license"):
                return []
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(license)")}
            selected = ["license_key", "state", "issuance_reference", "created_at"]
            if "keygen_id" in columns:
                selected.append("keygen_id")
            return _rows(
                connection,
                f"SELECT {','.join(selected)} FROM license ORDER BY created_at DESC LIMIT 500",
            )

    def list_checklist(self) -> list[dict]:
        with _connect(self.settings.server_database) as connection:
            if not _table_exists(connection, "checklist"):
                return []
            return _rows(
                connection,
                "SELECT id,task,is_done,created_at FROM checklist ORDER BY created_at,id",
            )

    def list_pocketbase(self, table: str) -> list[dict]:
        path = self.settings.pocketbase_database
        if path is None or not path.is_file():
            return []
        with _connect(path) as connection:
            if not _table_exists(connection, table):
                return []
            columns = {row["name"] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            selected = [name for name in ("id", "message", "created", "updated") if name in columns]
            if not selected:
                return []
            order = "created DESC" if "created" in columns else "rowid DESC"
            return _rows(connection, f'SELECT {",".join(selected)} FROM "{table}" ORDER BY {order} LIMIT 500')

    def pocketbase_count(self, table: str) -> int:
        path = self.settings.pocketbase_database
        if path is None or not path.is_file():
            return 0
        with _connect(path) as connection:
            if not _table_exists(connection, table):
                return 0
            return int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])

    @staticmethod
    def _id(arguments: dict[str, Any]) -> str:
        identifier = arguments.get("id")
        if not isinstance(identifier, (str, int)) or not str(identifier):
            raise OperationError("a record id is required")
        return str(identifier)

    def delete_pocketbase(self, table: str, arguments: dict[str, Any]) -> dict:
        path = self.settings.pocketbase_database
        if path is None:
            raise OperationError("record database is unavailable")
        with _connect(path, writable=True) as connection:
            if not _table_exists(connection, table):
                raise OperationError("record collection is unavailable")
            deleted = connection.execute(
                f'DELETE FROM "{table}" WHERE id=?', (self._id(arguments),)
            ).rowcount
            connection.commit()
        if deleted != 1:
            raise OperationError("record was not found")
        return {"deleted": deleted}

    def toggle_checklist(self, arguments: dict[str, Any]) -> dict:
        value = arguments.get("is_done")
        if type(value) is not bool:
            raise OperationError("is_done must be a boolean")
        with _connect(self.settings.server_database, writable=True) as connection:
            changed = connection.execute(
                "UPDATE checklist SET is_done=? WHERE id=?",
                (int(value), self._id(arguments)),
            ).rowcount
            connection.commit()
        if changed != 1:
            raise OperationError("checklist item was not found")
        return {"updated": changed}

    def delete_checklist(self, arguments: dict[str, Any]) -> dict:
        with _connect(self.settings.server_database, writable=True) as connection:
            deleted = connection.execute(
                "DELETE FROM checklist WHERE id=?", (self._id(arguments),)
            ).rowcount
            connection.commit()
        if deleted != 1:
            raise OperationError("checklist item was not found")
        return {"deleted": deleted}

    def restart_service(self, arguments: dict[str, Any]) -> dict:
        name = arguments.get("service")
        unit = self.settings.services.get(name) if isinstance(name, str) else None
        if unit is None:
            raise OperationError("service is not allowed")
        result = subprocess.run(
            ["systemctl", "--user", "restart", "--no-block", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode:
            raise OperationError("service restart failed")
        return {"service": name}

    @staticmethod
    def _backup_database(source: Path, destination: Path) -> None:
        with _connect(source) as source_db, sqlite3.connect(destination) as destination_db:
            source_db.backup(destination_db)
        destination.chmod(0o600)

    def backup(self) -> dict:
        root = self.settings.backup_directory
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        final = root / stamp
        if final.exists():
            raise OperationError("a backup already exists for this second")
        temporary = Path(tempfile.mkdtemp(prefix=f".{stamp}-", dir=root))
        try:
            sources = {
                "server.db": self.settings.server_database,
                "dashboard.db": self.settings.dashboard_database,
                "pocketbase.db": self.settings.pocketbase_database,
            }
            for name, source in sources.items():
                if source is not None and source.is_file():
                    self._backup_database(source, temporary / name)
            if not any(temporary.iterdir()):
                raise OperationError("no databases were available to back up")
            temporary.rename(final)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return {"backup": str(final)}

    def caddy_metrics(self) -> list[dict]:
        try:
            with urllib.request.urlopen(self.settings.caddy_metrics_url, timeout=10) as response:
                body = response.read(4 * 1024 * 1024 + 1)
        except OSError as exc:
            raise OperationError("Caddy metrics are unavailable") from exc
        if len(body) > 4 * 1024 * 1024:
            raise OperationError("Caddy metrics response is too large")
        # Caddy labels the counter with the HTTP handler that recorded it.  The
        # exact terminal handler depends on the rendered route shape (the live
        # configuration currently exposes ``subroute`` rather than
        # ``reverse_proxy``).  Keep separate handler totals and use the largest
        # one for each host so one request is not counted once per middleware.
        handler_totals: dict[tuple[str, str], float] = {}
        for line in body.decode("utf-8", "strict").splitlines():
            match = PROMETHEUS_SAMPLE.match(line)
            if not match:
                continue
            labels = {
                key: bytes(value, "utf-8").decode("unicode_escape")
                for key, value in PROMETHEUS_LABEL.findall(match.group("labels"))
            }
            host = labels.get("host", "").split(":", 1)[0].lower()
            handler = labels.get("handler", "")
            if not host or not handler:
                continue
            key = (host, handler)
            handler_totals[key] = handler_totals.get(key, 0.0) + float(match.group("value"))
        totals: dict[str, float] = {}
        for (host, _handler), value in handler_totals.items():
            totals[host] = max(totals.get(host, 0.0), value)
        return [
            {"series": f"caddy:http_requests:{host}", "host": host, "value": value}
            for host, value in sorted(totals.items())
        ]

    @staticmethod
    def _cents(value: Any) -> int:
        try:
            return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except (InvalidOperation, ValueError):
            return 0

    def revenue(self) -> dict:
        today = datetime.now(UTC).date()
        daily: dict[str, dict[str, int]] = {}
        periods = {
            "today": today,
            "week": today - timedelta(days=6),
            "month": today - timedelta(days=29),
            "all": None,
        }
        totals = {
            name: {
                "grossCents": 0, "sales": 0, "cryptoGrossCents": 0, "cryptoSales": 0,
                "patreonGrossCents": 0, "patreonSales": 0, "patreonNetEstimateCents": 0,
                "sandboxGrossCents": 0, "sandboxSales": 0,
            }
            for name in periods
        }
        with _connect(self.settings.server_database) as connection:
            transactions = _rows(
                connection,
                'SELECT environment,expected_price_amount,expected_price_currency,status,'
                'created_at,finished_at FROM "transaction" WHERE status=?',
                ("finished",),
            ) if _table_exists(connection, "transaction") else []
            patreon_deliveries = _rows(
                connection,
                "SELECT status,created_at,sent_at FROM patreon_license_deliveries WHERE status=?",
                ("sent",),
            ) if _table_exists(connection, "patreon_license_deliveries") else []
        for payment in transactions:
            if str(payment.get("expected_price_currency", "")).upper() != "EUR":
                continue
            cents = self._cents(payment.get("expected_price_amount"))
            raw_date = payment.get("finished_at") or payment.get("created_at") or ""
            try:
                day = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00")).date()
            except ValueError:
                continue
            sandbox = payment.get("environment") == "sandbox"
            bucket = daily.setdefault(day.isoformat(), {
                "day": day.isoformat(), "cryptoGrossCents": 0,
                "patreonGrossCents": 0, "sandboxGrossCents": 0,
            })
            bucket["sandboxGrossCents" if sandbox else "cryptoGrossCents"] += cents
            for name, cutoff in periods.items():
                if cutoff is not None and day < cutoff:
                    continue
                current = totals[name]
                if sandbox:
                    current["sandboxGrossCents"] += cents
                    current["sandboxSales"] += 1
                else:
                    current["grossCents"] += cents
                    current["sales"] += 1
                    current["cryptoGrossCents"] += cents
                    current["cryptoSales"] += 1
        assumptions = {
            "priceCents": 999,
            "platformRatePercent": 10,
            "processingRatePercent": 3.4,
            "processingFixedCents": 35,
            "estimatedFeeCents": 135,
            "estimatedNetCents": 864,
            "excludes": "currency conversion, payout and tax fees",
        }
        for delivery in patreon_deliveries:
            raw_date = delivery.get("sent_at") or delivery.get("created_at") or ""
            try:
                day = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00")).date()
            except ValueError:
                continue
            gross = assumptions["priceCents"]
            net = assumptions["estimatedNetCents"]
            bucket = daily.setdefault(day.isoformat(), {
                "day": day.isoformat(), "cryptoGrossCents": 0,
                "patreonGrossCents": 0, "sandboxGrossCents": 0,
            })
            bucket["patreonGrossCents"] += gross
            for name, cutoff in periods.items():
                if cutoff is not None and day < cutoff:
                    continue
                current = totals[name]
                current["grossCents"] += gross
                current["sales"] += 1
                current["patreonGrossCents"] += gross
                current["patreonSales"] += 1
                current["patreonNetEstimateCents"] += net
        return {"totals": totals, "daily": list(sorted(daily.values(), key=lambda row: row["day"])), "patreonAssumptions": assumptions}


class BrokerServer:
    def __init__(self, broker: Broker):
        self.broker = broker

    def _handle(self, connection: socket.socket) -> None:
        try:
            connection.settimeout(20)
            buffer = bytearray()
            while b"\n" not in buffer:
                chunk = connection.recv(8192)
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > MAX_REQUEST:
                    raise OperationError("request is too large")
            request = json.loads(bytes(buffer).split(b"\n", 1)[0])
            if not isinstance(request, dict) or not isinstance(request.get("arguments", {}), dict):
                raise OperationError("invalid request")
            operation = request.get("operation")
            if not isinstance(operation, str):
                raise OperationError("invalid operation")
            response = {"ok": True, "result": self.broker.dispatch(operation, request["arguments"])}
        except (OperationError, ValueError, TypeError, sqlite3.Error) as exc:
            response = {"ok": False, "error": str(exc)}
        encoded = json.dumps(response, separators=(",", ":"), default=str).encode() + b"\n"
        if len(encoded) > MAX_RESPONSE:
            encoded = b'{"ok":false,"error":"response is too large"}\n'
        try:
            connection.sendall(encoded)
        finally:
            connection.close()

    def serve_forever(self) -> None:
        path = self.broker.settings.socket_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        path.parent.chmod(0o755)
        if path.exists() or path.is_socket():
            path.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            path.chmod(0o666)
            listener.listen(16)
            while True:
                connection, _ = listener.accept()
                threading.Thread(target=self._handle, args=(connection,), daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the EAF dashboard host broker")
    parser.parse_args()
    BrokerServer(Broker(BrokerSettings.load())).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
