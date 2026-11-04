from __future__ import annotations

import os
import ipaddress
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_path: Path
    secret_key: str
    rp_id: str
    origin: str
    broker_socket: Path
    secure_cookie: bool
    trusted_proxies: tuple[str, ...] = ("127.0.0.1/32", "::1/128")

    @classmethod
    def load(cls, *, require_secret: bool = True) -> "Settings":
        data_dir = Path(os.environ.get("EAF_DASHBOARD_DATA_DIR", "./data")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        secret_key = os.environ.get("SECRET_KEY", "").strip()
        if require_secret and not secret_key:
            raise RuntimeError("SECRET_KEY must be provided through a runtime credential")
        if require_secret and len(secret_key) < 32:
            raise RuntimeError("Dashboard secret key is invalid")
        rp_id = os.environ.get("EAF_DASHBOARD_RP_ID", "echteralsfake.me").strip().lower()
        origin = os.environ.get(
            "EAF_DASHBOARD_ORIGIN", "https://echteralsfake.me"
        ).strip().rstrip("/")
        trusted_proxies = tuple(
            str(ipaddress.ip_network(value.strip(), strict=True))
            for value in os.environ.get(
                "EAF_DASHBOARD_TRUSTED_PROXIES", "127.0.0.1/32,::1/128"
            ).split(",") if value.strip()
        )
        return cls(
            trusted_proxies=trusted_proxies,
            data_dir=data_dir,
            database_path=data_dir / "dashboard.db",
            secret_key=secret_key,
            rp_id=rp_id,
            origin=origin,
            broker_socket=Path(
                os.environ.get("EAF_DASHBOARD_BROKER_SOCKET", "/run/eaf-dashboard/broker.sock")
            ),
            secure_cookie=os.environ.get("EAF_DASHBOARD_SECURE_COOKIE", "true").lower()
            not in {"0", "false", "no"},
        )
