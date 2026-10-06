#!/usr/bin/env python3
"""EAF Multi-Server Telemetry Agent.

A zero-dependency, lightweight agent for servers and websites running on the same
LAN router or remote networks. Collects host resource vitals (CPU, RAM, Disk, Uptime)
and probes local websites/services, reporting them to the centralized EAF Dashboard.

Usage:
  python3 eaf-node-agent.py --dashboard-url http://192.168.0.11:18010
  python3 eaf-node-agent.py --test
  sudo python3 eaf-node-agent.py --install-systemd --dashboard-url http://192.168.0.11:18010
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any


def detect_lan_ip() -> str:
    """Detect the local LAN IPv4 address on the Ethernet / router interface."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # Connect to a dummy LAN router address to identify outbound interface
            s.connect(("192.168.0.1", 80))
            return s.getsockname()[0]
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def collect_vitals() -> dict[str, Any]:
    """Collect system CPU, RAM, Disk, and Uptime metrics without external dependencies."""
    # 1. Load averages & CPU
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1 = load5 = load15 = 0.0

    cpu_count = os.cpu_count() or 1
    cpu_percent = min(100.0, round((load1 / cpu_count) * 100, 1))

    # 2. Memory from /proc/meminfo
    mem: dict[str, int] = {}
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
    mem_used = max(0, mem_total - mem_avail)
    mem_percent = round((mem_used / max(1, mem_total)) * 100, 1) if mem_total else 0.0

    # 3. Disk usage
    try:
        disk = shutil.disk_usage("/")
        disk_total_gb = round(disk.total / (1024**3), 1)
        disk_used_gb = round(disk.used / (1024**3), 1)
        disk_percent = round((disk.used / max(1, disk.total)) * 100, 1)
    except OSError:
        disk_total_gb = disk_used_gb = disk_percent = 0.0

    # 4. Uptime
    uptime_seconds = 0
    try:
        with open("/proc/uptime") as f:
            uptime_seconds = int(float(f.readline().split()[0]))
    except (OSError, ValueError):
        pass

    return {
        "load_1m": round(load1, 2),
        "load_5m": round(load5, 2),
        "load_15m": round(load15, 2),
        "cpu_percent": cpu_percent,
        "cpu_count": cpu_count,
        "memory_used_mb": mem_used,
        "memory_total_mb": mem_total,
        "memory_percent": mem_percent,
        "disk_used_gb": disk_used_gb,
        "disk_total_gb": disk_total_gb,
        "disk_percent": disk_percent,
        "uptime_seconds": uptime_seconds,
    }


def check_service(url: str, name: str, timeout: float = 2.5) -> dict[str, Any]:
    """Probe a local website or HTTP service and return health state & latency."""
    start = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "EAF-Node-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            return {
                "name": name,
                "url": url,
                "status": "up" if resp.status < 400 else "down",
                "status_code": resp.status,
                "latency_ms": latency_ms,
            }
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 1)
        return {
            "name": name,
            "url": url,
            "status": "down",
            "error": str(exc),
            "latency_ms": latency_ms,
        }


class HealthServer(http.server.ThreadingHTTPServer):
    def __init__(self, server_address, RequestHandlerClass, agent_instance):
        super().__init__(server_address, RequestHandlerClass)
        self.agent = agent_instance


class HealthHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence routine access logs
        pass

    def do_GET(self):
        if self.path in {"/health", "/metrics.json", "/"}:
            telemetry = self.server.agent.build_payload()
            encoded = json.dumps(telemetry, indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        else:
            self.send_response(404)
            self.end_headers()


class NodeAgent:
    def __init__(self, config: argparse.Namespace):
        self.config = config
        self.node_id = config.node_id or f"node-{socket.gethostname().lower()}"
        self.name = config.name or socket.gethostname()
        self.address = config.address or detect_lan_ip()
        self.role = config.role
        self.dashboard_url = config.dashboard_url.rstrip("/")
        self.interval = config.interval
        self.services_to_probe = []
        if config.probe_service:
            for item in config.probe_service:
                name, sep, url = item.partition("=")
                if sep and name and url:
                    self.services_to_probe.append({"name": name.strip(), "url": url.strip()})
                else:
                    self.services_to_probe.append({"name": item.strip(), "url": item.strip()})

    def build_payload(self) -> dict[str, Any]:
        vitals = collect_vitals()
        probed_services = [check_service(s["url"], s["name"]) for s in self.services_to_probe]
        overall_status = "online"
        if any(s["status"] == "down" for s in probed_services):
            overall_status = "degraded"

        return {
            "node_id": self.node_id,
            "name": self.name,
            "address": self.address,
            "role": self.role,
            "status": overall_status,
            **vitals,
            "services": probed_services,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def send_heartbeat(self) -> bool:
        payload = self.build_payload()
        encoded = json.dumps(payload).encode("utf-8")
        target_url = f"{self.dashboard_url}/dashboard/api/nodes/heartbeat"
        req = urllib.request.Request(
            target_url,
            data=encoded,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "EAF-Node-Agent/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status in {200, 201}:
                    return True
                print(f"[!] Heartbeat returned status {resp.status}")
                return False
        except urllib.error.URLError as exc:
            print(f"[!] Heartbeat failed to reach {target_url}: {exc.reason}")
            return False
        except Exception as exc:
            print(f"[!] Heartbeat unexpected error: {exc}")
            return False

    def run_forever(self):
        print(f"[*] Starting EAF Node Agent [{self.node_id}]")
        print(f"    Name:        {self.name}")
        print(f"    LAN Address: {self.address}")
        print(f"    Dashboard:   {self.dashboard_url}")
        print(f"    Interval:    {self.interval}s")

        if self.config.serve_port:
            port = int(self.config.serve_port)
            server = HealthServer(("0.0.0.0", port), HealthHandler, self)
            srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
            srv_thread.start()
            print(f"    Pull HTTP:   Listening on http://0.0.0.0:{port}/health")

        while True:
            success = self.send_heartbeat()
            if success:
                print(f"[{time.strftime('%H:%M:%S')}] Heartbeat successfully dispatched to central dashboard")
            time.sleep(self.interval)


def install_systemd_service(config: argparse.Namespace) -> None:
    """Install this script as a systemd background service on Linux."""
    if os.geteuid() != 0:
        print("ERROR: Root permissions required to install systemd service. Run with sudo.")
        sys.exit(1)

    script_path = os.path.abspath(__file__)
    target_script = "/usr/local/bin/eaf-node-agent.py"
    shutil.copy2(script_path, target_script)
    os.chmod(target_script, 0o755)

    args = [f"--dashboard-url {config.dashboard_url}"]
    if config.name:
        args.append(f"--name '{config.name}'")
    if config.role:
        args.append(f"--role '{config.role}'")
    if config.serve_port:
        args.append(f"--serve-port {config.serve_port}")
    for s in config.probe_service or []:
        args.append(f"--probe-service '{s}'")

    unit_content = f"""[Unit]
Description=EAF Multi-Server Telemetry Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 {target_script} {" ".join(args)}
Restart=always
RestartSec=5s
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    unit_path = "/etc/systemd/system/eaf-node-agent.service"
    with open(unit_path, "w") as f:
        f.write(unit_content)

    os.system("systemctl daemon-reload")
    os.system("systemctl enable --now eaf-node-agent.service")
    print(f"[✓] Systemd unit installed to {unit_path} and activated!")
    print("    Check status with: systemctl status eaf-node-agent.service")


def main() -> int:
    parser = argparse.ArgumentParser(description="EAF Multi-Server Telemetry Agent")
    parser.add_argument(
        "--dashboard-url",
        default=os.environ.get("EAF_DASHBOARD_URL", "http://192.168.0.11:18010"),
        help="Base URL of central EAF dashboard (default: http://192.168.0.11:18010)",
    )
    parser.add_argument("--node-id", default=os.environ.get("EAF_NODE_ID"), help="Unique node identifier")
    parser.add_argument("--name", default=os.environ.get("EAF_NODE_NAME"), help="Human-readable server name")
    parser.add_argument("--address", default=os.environ.get("EAF_NODE_ADDRESS"), help="LAN IP address")
    parser.add_argument("--role", default=os.environ.get("EAF_NODE_ROLE", "web-server"), help="Server role")
    parser.add_argument("--interval", type=int, default=15, help="Heartbeat interval in seconds (default: 15)")
    parser.add_argument("--serve-port", type=int, default=9100, help="Port to serve /health endpoint (default: 9100)")
    parser.add_argument(
        "--probe-service",
        action="append",
        help="Local website/service to probe in format 'Name=URL' (e.g. 'Blog=http://localhost:3000')",
    )
    parser.add_argument("--test", action="store_true", help="Print collected metrics and exit")
    parser.add_argument("--install-systemd", action="store_true", help="Install as systemd service and start")

    args = parser.parse_args()

    if args.install_systemd:
        install_systemd_service(args)
        return 0

    agent = NodeAgent(args)

    if args.test:
        payload = agent.build_payload()
        print(json.dumps(payload, indent=2))
        return 0

    agent.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
